"""Storage shared by SQLite (tier 1) and Postgres + pgvector (tiers 2-3).

Access rules are applied INSIDE every query (the WHERE clause built by `_visible_chunks`),
never by filtering results afterwards. On Postgres the same rules are also enforced by
row-level security policies (defense in depth).
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np

from .. import SENSITIVITY_NAMES


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class UserCtx:
    id: str
    role: str = "member"                  # member | admin
    groups: list[str] = field(default_factory=list)
    display_name: str = ""
    unlocked: bool = False                 # Restricted unlocked for this session (set by the Gateway)

    @property
    def principals(self) -> list[str]:
        return [f"user:{self.id}", "everyone"] + [f"group:{g}" for g in self.groups]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion of several ranked id lists."""
    scores: dict[str, float] = {}
    for lst in rank_lists:
        for i, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + i + 1)
    return scores


class Store:
    """Backend-independent logic. Subclasses implement the dialect hooks marked `backend`."""

    dialect = "base"

    # ------------------------------------------------------------------ backend hooks
    @contextmanager
    def tx(self, ctx: UserCtx | None = None):          # backend
        raise NotImplementedError
        yield

    def init(self) -> None:                             # backend
        raise NotImplementedError

    def _fts_chunk_ids(self, x, ctx, query, filters, n) -> list[str]:          # backend
        raise NotImplementedError

    def _vec_chunk_ids(self, x, ctx, qvec, filters, n) -> list[tuple[str, float]]:   # backend
        raise NotImplementedError

    def _index_message(self, x, msg_id, conv_id, user_id, content, vec) -> None:  # backend
        raise NotImplementedError

    def _fts_message_ids(self, x, ctx, query, n, since, until) -> list[str]:  # backend
        raise NotImplementedError

    def _vec_message_ids(self, x, ctx, qvec, n, since, until) -> list[tuple[str, float]]:  # backend
        raise NotImplementedError

    def _insert_chunk(self, x, row: dict, vec) -> None:  # backend
        raise NotImplementedError

    def _delete_chunk_index(self, x, chunk_ids: list[str]) -> None:  # backend
        raise NotImplementedError

    def _delete_message_index(self, x, message_ids: list[str]) -> None:  # backend
        raise NotImplementedError

    def _vecs_for_chunks(self, x, ids: list[str]) -> dict[str, np.ndarray]:  # backend
        raise NotImplementedError

    def _vecs_for_messages(self, x, ids: list[str]) -> dict[str, np.ndarray]:  # backend
        raise NotImplementedError

    # ------------------------------------------------------------------ users & collections
    def upsert_user(self, uid: str, display_name: str = "", role: str = "member", groups: list[str] | None = None) -> None:
        with self.tx() as x:
            row = x.one("SELECT id FROM users WHERE id=?", (uid,))
            g = json.dumps(sorted(groups or []))
            if row:
                x.run("UPDATE users SET display_name=?, role=?, groups_json=? WHERE id=?", (display_name, role, g, uid))
            else:
                x.run("INSERT INTO users(id, display_name, role, groups_json, created_at) VALUES (?,?,?,?,?)",
                      (uid, display_name, role, g, now()))
        self.ensure_personal_collection(uid)

    def get_user(self, uid: str) -> dict | None:
        with self.tx() as x:
            r = x.one("SELECT * FROM users WHERE id=?", (uid,))
        if r:
            r["groups"] = json.loads(r.pop("groups_json") or "[]")
        return r

    def ensure_personal_collection(self, uid: str) -> str:
        return self.create_collection(f"personal:{uid}", owner_id=uid, default_sensitivity=1,
                                      purpose="Private notes and files of one user")

    def create_collection(self, name: str, owner_id: str, default_sensitivity: int = 1,
                          retention_days: int | None = None, purpose: str = "") -> str:
        with self.tx() as x:
            r = x.one("SELECT id FROM collections WHERE name=?", (name,))
            if r:
                x.run("UPDATE collections SET default_sensitivity=?, retention_days=?, purpose=? WHERE id=?",
                      (default_sensitivity, retention_days, purpose, r["id"]))
                return r["id"]
            cid = new_id()
            x.run("INSERT INTO collections(id, name, owner_id, default_sensitivity, retention_days, purpose, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (cid, name, owner_id, default_sensitivity, retention_days, purpose, now()))
            return cid

    def get_collection(self, name_or_id: str) -> dict | None:
        with self.tx() as x:
            return x.one("SELECT * FROM collections WHERE id=? OR name=?", (name_or_id, name_or_id))

    def grant(self, collection_id: str, principal: str, permission: str = "read") -> None:
        if permission not in {"read", "write", "admin"}:
            raise ValueError("permission must be read, write or admin")
        with self.tx() as x:
            x.run("DELETE FROM collection_acl WHERE collection_id=? AND principal=?", (collection_id, principal))
            x.run("INSERT INTO collection_acl(collection_id, principal, permission) VALUES (?,?,?)",
                  (collection_id, principal, permission))

    def revoke(self, collection_id: str, principal: str) -> None:
        with self.tx() as x:
            x.run("DELETE FROM collection_acl WHERE collection_id=? AND principal=?", (collection_id, principal))

    def _readable_sql(self, ctx: UserCtx) -> tuple[str, list]:
        ph = ",".join("?" for _ in ctx.principals)
        return (f"(SELECT id FROM collections WHERE owner_id=? UNION "
                f"SELECT collection_id FROM collection_acl WHERE principal IN ({ph}))", [ctx.id, *ctx.principals])

    def collections_for(self, ctx: UserCtx) -> list[dict]:
        sub, params = self._readable_sql(ctx)
        with self.tx(ctx) as x:
            rows = x.all(f"SELECT c.*, (SELECT COUNT(*) FROM documents d WHERE d.collection_id=c.id AND d.deleted=0) AS documents"
                         f" FROM collections c WHERE c.id IN {sub} ORDER BY c.name", params)
        for r in rows:
            r["sensitivity"] = SENSITIVITY_NAMES[r["default_sensitivity"]]
        return rows

    def can_write(self, ctx: UserCtx, collection_id: str) -> bool:
        with self.tx(ctx) as x:
            c = x.one("SELECT owner_id FROM collections WHERE id=?", (collection_id,))
            if not c:
                return False
            if c["owner_id"] == ctx.id:
                return True
            ph = ",".join("?" for _ in ctx.principals)
            r = x.one(f"SELECT 1 AS ok FROM collection_acl WHERE collection_id=? AND principal IN ({ph}) "
                      f"AND permission IN ('write','admin')", (collection_id, *ctx.principals))
            return bool(r)

    # ------------------------------------------------------------------ personal access tokens (web UI, scripts)
    def create_token(self, uid: str, label: str = "", days: int = 90) -> str:
        import hashlib
        import secrets as _secrets

        raw = "gwu_" + _secrets.token_urlsafe(32)
        with self.tx() as x:
            x.run("INSERT INTO api_tokens(token_hash, user_id, label, created_at, expires_at, revoked) VALUES (?,?,?,?,?,0)",
                  (hashlib.sha256(raw.encode()).hexdigest(), uid, label, now(), now() + days * 86400))
        return raw          # shown once; only the hash is stored

    def resolve_token(self, raw: str) -> dict | None:
        import hashlib

        with self.tx() as x:
            t = x.one("SELECT user_id, expires_at, revoked FROM api_tokens WHERE token_hash=?",
                      (hashlib.sha256(raw.encode()).hexdigest(),))
        if not t or t["revoked"] or t["expires_at"] < now():
            return None
        return self.get_user(t["user_id"])

    def revoke_tokens(self, uid: str) -> None:
        with self.tx() as x:
            x.run("UPDATE api_tokens SET revoked=1 WHERE user_id=?", (uid,))

    # ------------------------------------------------------------------ restricted unlock
    def unlock_restricted(self, ctx: UserCtx, minutes: int) -> float:
        until = now() + minutes * 60
        with self.tx() as x:
            x.run("DELETE FROM restricted_unlocks WHERE user_id=?", (ctx.id,))
            x.run("INSERT INTO restricted_unlocks(user_id, expires_at) VALUES (?,?)", (ctx.id, until))
        return until

    def lock_restricted(self, ctx: UserCtx) -> None:
        with self.tx() as x:
            x.run("DELETE FROM restricted_unlocks WHERE user_id=?", (ctx.id,))

    def unlocked_until(self, uid: str) -> float | None:
        with self.tx() as x:
            r = x.one("SELECT expires_at FROM restricted_unlocks WHERE user_id=?", (uid,))
        return r["expires_at"] if r and r["expires_at"] > now() else None

    def is_unlocked(self, uid: str) -> bool:
        with self.tx() as x:
            r = x.one("SELECT expires_at FROM restricted_unlocks WHERE user_id=?", (uid,))
        return bool(r and r["expires_at"] > now())

    # ------------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, obj: str = "", detail: dict | None = None, model_digest: str | None = None) -> None:
        with self.tx() as x:
            x.run("INSERT INTO audit_log(id, ts, actor, action, object, detail_json, model_digest) VALUES (?,?,?,?,?,?,?)",
                  (new_id(), now(), actor, action, obj, json.dumps(detail or {}, default=str), model_digest))

    def list_audit(self, limit: int = 200, actor: str | None = None, action: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM audit_log WHERE 1=1", []
        if actor:
            q, p = q + " AND actor=?", p + [actor]
        if action:
            q, p = q + " AND action=?", p + [action]
        with self.tx() as x:
            rows = x.all(q + " ORDER BY ts DESC LIMIT ?", (*p, limit))
        for r in rows:
            r["detail"] = json.loads(r.pop("detail_json") or "{}")
        return rows

    # ------------------------------------------------------------------ conversations
    def get_or_create_conversation(self, ctx: UserCtx, external_id: str, title: str) -> str:
        with self.tx(ctx) as x:
            r = x.one("SELECT id FROM conversations WHERE user_id=? AND external_id=?", (ctx.id, external_id))
            if r:
                return r["id"]
            cid = new_id()
            x.run("INSERT INTO conversations(id, user_id, external_id, title, created_at, updated_at, sensitivity)"
                  " VALUES (?,?,?,?,?,?,?)", (cid, ctx.id, external_id, title[:200], now(), now(), 1))
            return cid

    def add_message(self, ctx: UserCtx, conv_id: str, role: str, content: str, model: str | None,
                    model_digest: str | None, sensitivity: int, vec: np.ndarray | None,
                    created_at: float | None = None) -> str:
        mid = new_id()
        ts = created_at or now()
        with self.tx(ctx) as x:
            own = x.one("SELECT sensitivity FROM conversations WHERE id=? AND user_id=?", (conv_id, ctx.id))
            if not own:
                raise PermissionError("conversation not found")
            x.run("INSERT INTO messages(id, conversation_id, user_id, role, content, model, model_digest, sensitivity, created_at, seq)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (mid, conv_id, ctx.id, role, content, model, model_digest, sensitivity, ts, time.time_ns()))
            # strictest-label rule: a conversation is as sensitive as its most sensitive message
            x.run("UPDATE conversations SET updated_at=?, sensitivity=? WHERE id=?",
                  (ts, max(own["sensitivity"], sensitivity), conv_id))
            self._index_message(x, mid, conv_id, ctx.id, content, vec)
        return mid

    def last_message(self, ctx: UserCtx, conv_id: str, role: str | None = None) -> dict | None:
        """Internal lookup (ignores the Restricted lock; never returned to clients directly)."""
        q, p = "SELECT id, role, content FROM messages WHERE conversation_id=? AND user_id=?", [conv_id, ctx.id]
        if role:
            q, p = q + " AND role=?", p + [role]
        with self.tx(ctx) as x:
            return x.one(q + " ORDER BY created_at DESC, seq DESC LIMIT 1", p)

    def message_count(self, ctx: UserCtx, conv_id: str, role: str | None = None) -> int:
        q, p = "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND user_id=?", [conv_id, ctx.id]
        if role:
            q, p = q + " AND role=?", p + [role]
        with self.tx(ctx) as x:
            return x.one(q, p)["n"]

    def raise_conversation_sensitivity(self, ctx: UserCtx, conv_id: str, level: int) -> None:
        with self.tx(ctx) as x:
            x.run("UPDATE conversations SET sensitivity=CASE WHEN sensitivity < ? THEN ? ELSE sensitivity END "
                  "WHERE id=? AND user_id=?", (level, level, conv_id, ctx.id))

    def list_conversations(self, ctx: UserCtx, since: float | None = None, until: float | None = None,
                           limit: int = 100) -> list[dict]:
        q, p = "SELECT id, title, created_at, updated_at, sensitivity FROM conversations WHERE user_id=?", [ctx.id]
        if since:
            q, p = q + " AND updated_at>=?", p + [since]
        if until:
            q, p = q + " AND created_at<=?", p + [until]
        if not ctx.unlocked:
            q += " AND sensitivity<3"
        with self.tx(ctx) as x:
            return x.all(q + " ORDER BY updated_at DESC LIMIT ?", (*p, limit))

    def get_conversation(self, ctx: UserCtx, conv_id: str) -> dict | None:
        with self.tx(ctx) as x:
            c = x.one("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, ctx.id))
            if not c or (c["sensitivity"] >= 3 and not ctx.unlocked):
                return None
            c["messages"] = x.all("SELECT id, role, content, model, model_digest, sensitivity, created_at FROM messages "
                                  "WHERE conversation_id=? AND user_id=? ORDER BY created_at, seq", (conv_id, ctx.id))
            c["summary"] = x.one("SELECT text, updated_at FROM summaries WHERE conversation_id=?", (conv_id,))
        return c

    def search_history(self, ctx: UserCtx, query: str, qvec: np.ndarray | None, k: int = 10,
                       since: float | None = None, until: float | None = None, min_similarity: float = 0.2,
                       exclude_conversation: str | None = None) -> list[dict]:
        with self.tx(ctx) as x:
            fts = self._fts_message_ids(x, ctx, query, 50, since, until) if query.strip() else []
            vec = self._vec_message_ids(x, ctx, qvec, 50, since, until) if qvec is not None else []
            vec = [(i, s) for i, s in vec if s >= min_similarity or i in fts]    # drop unrelated vector-only hits
            fused = rrf([fts, [i for i, _ in vec]])
            sims = dict(vec)
            ids = sorted(fused, key=fused.get, reverse=True)[: k * 3]
            if not ids:
                return []
            ph = ",".join("?" for _ in ids)
            rows = x.all(f"SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, m.sensitivity, c.title "
                         f"FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                         f"WHERE m.id IN ({ph}) AND m.user_id=? AND c.user_id=? AND c.id<>?"
                         + ("" if ctx.unlocked else " AND m.sensitivity<3 AND c.sensitivity<3"),
                         (*ids, ctx.id, ctx.id, exclude_conversation or ""))
        for r in rows:
            r["score"] = round(fused[r["id"]], 5)
            r["similarity"] = round(float(sims.get(r["id"], 0.0)), 4)
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows[:k]

    # ------------------------------------------------------------------ documents & chunks
    def get_document(self, collection_id: str, path: str) -> dict | None:
        with self.tx() as x:
            return x.one("SELECT * FROM documents WHERE collection_id=? AND path=? AND deleted=0", (collection_id, path))

    def upsert_document(self, collection_id: str, path: str, sha256: str, meta: dict, sensitivity: int,
                        pii: list[str], chunks: list[dict], vecs: list[np.ndarray]) -> str:
        """Replace a document and all of its chunks/vectors atomically."""
        with self.tx() as x:
            old = x.one("SELECT id FROM documents WHERE collection_id=? AND path=? AND deleted=0", (collection_id, path))
            if old:
                self._hard_delete_document(x, old["id"])
            did = new_id()
            x.run("INSERT INTO documents(id, collection_id, path, sha256, mime, title, author, created_at, modified_at,"
                  " ingested_at, sensitivity, pii_json, tags_json, deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                  (did, collection_id, path, sha256, meta.get("mime"), meta.get("title"), meta.get("author"),
                   meta.get("created_at"), meta.get("modified_at"), now(), sensitivity, json.dumps(pii),
                   json.dumps(meta.get("tags", []))))
            for ch, v in zip(chunks, vecs):
                row = {"id": new_id(), "document_id": did, "collection_id": collection_id, "ord": ch["ord"],
                       "text": ch["text"], "heading_path": ch.get("heading_path", ""), "tokens": ch.get("tokens", 0),
                       "sensitivity": ch["sensitivity"], "pii_json": json.dumps(ch.get("pii", [])),
                       "modified_at": meta.get("modified_at") or now()}
                self._insert_chunk(x, row, v)
            return did

    def _hard_delete_document(self, x, doc_id: str) -> None:
        ids = [r["id"] for r in x.all("SELECT id FROM chunks WHERE document_id=?", (doc_id,))]
        if ids:
            self._delete_chunk_index(x, ids)
            ph = ",".join("?" for _ in ids)
            x.run(f"DELETE FROM retrievals WHERE chunk_id IN ({ph})", ids)
        x.run("DELETE FROM chunks WHERE document_id=?", (doc_id,))
        x.run("DELETE FROM documents WHERE id=?", (doc_id,))

    def delete_document(self, collection_id: str, path: str) -> bool:
        with self.tx() as x:
            r = x.one("SELECT id FROM documents WHERE collection_id=? AND path=?", (collection_id, path))
            if not r:
                return False
            self._hard_delete_document(x, r["id"])
            return True

    def documents_in(self, collection_id: str) -> list[dict]:
        with self.tx() as x:
            return x.all("SELECT id, path, sha256, sensitivity, ingested_at FROM documents WHERE collection_id=? AND deleted=0",
                         (collection_id,))

    def _filters_sql(self, ctx: UserCtx, filters: dict, alias: str = "c") -> tuple[str, list]:
        sub, params = self._readable_sql(ctx)
        where = [f"{alias}.collection_id IN {sub}"]
        if not ctx.unlocked:
            where.append(f"{alias}.sensitivity < 3")
        if filters.get("collection_ids"):
            ph = ",".join("?" for _ in filters["collection_ids"])
            where.append(f"{alias}.collection_id IN ({ph})")
            params += list(filters["collection_ids"])
        if filters.get("since"):
            where.append(f"{alias}.modified_at >= ?")
            params.append(filters["since"])
        if filters.get("until"):
            where.append(f"{alias}.modified_at <= ?")
            params.append(filters["until"])
        if filters.get("source"):
            where.append(f"{alias}.document_id IN (SELECT id FROM documents WHERE path LIKE ?)")
            params.append(f"%{filters['source']}%")
        return " AND ".join(where), params

    def search_chunks(self, ctx: UserCtx, query: str, qvec: np.ndarray | None, filters: dict | None = None,
                      n: int = 50) -> list[dict]:
        """Hybrid BM25 + vector candidates, access rules inside both queries. Fused by RRF."""
        filters = filters or {}
        with self.tx(ctx) as x:
            fts = self._fts_chunk_ids(x, ctx, query, filters, n) if query.strip() else []
            vec = self._vec_chunk_ids(x, ctx, qvec, filters, n) if qvec is not None else []
            fused = rrf([fts, [i for i, _ in vec]])
            if not fused:
                return []
            sims = dict(vec)
            ids = sorted(fused, key=fused.get, reverse=True)[:n]
            ph = ",".join("?" for _ in ids)
            where, params = self._filters_sql(ctx, filters)
            rows = x.all(f"SELECT c.id, c.text, c.heading_path, c.sensitivity, c.collection_id, c.ord, c.modified_at,"
                         f" d.path, d.title, d.modified_at AS doc_modified_at, d.ingested_at, col.name AS collection "
                         f"FROM chunks c JOIN documents d ON d.id=c.document_id JOIN collections col ON col.id=c.collection_id "
                         f"WHERE c.id IN ({ph}) AND d.deleted=0 AND {where}", (*ids, *params))
            if qvec is not None:
                missing = [r["id"] for r in rows if r["id"] not in sims]
                for cid, v in self._vecs_for_chunks(x, missing).items():
                    sims[cid] = float(np.dot(v, qvec))
        for r in rows:
            r["fusion"] = fused[r["id"]]
            r["similarity"] = float(sims.get(r["id"], 0.0))
            r["keyword_hit"] = r["id"] in fts
        rows.sort(key=lambda r: r["fusion"], reverse=True)
        return rows

    def record_retrievals(self, message_id: str, chunks: list[dict]) -> None:
        with self.tx() as x:
            for rank, c in enumerate(chunks):
                x.run("INSERT INTO retrievals(message_id, chunk_id, score, rank) VALUES (?,?,?,?)",
                      (message_id, c["id"], c.get("score", 0.0), rank))

    # ------------------------------------------------------------------ summaries & memory
    def upsert_summary(self, ctx: UserCtx, conv_id: str, text: str, source_ids: list[str], sensitivity: int) -> None:
        with self.tx(ctx) as x:
            if not x.one("SELECT 1 AS ok FROM conversations WHERE id=? AND user_id=?", (conv_id, ctx.id)):
                raise PermissionError("conversation not found")
            x.run("DELETE FROM summaries WHERE conversation_id=?", (conv_id,))
            x.run("INSERT INTO summaries(conversation_id, user_id, text, source_ids_json, sensitivity, updated_at)"
                  " VALUES (?,?,?,?,?,?)", (conv_id, ctx.id, text, json.dumps(source_ids), sensitivity, now()))

    def propose_memory(self, ctx: UserCtx, text: str, source_message_id: str | None, sensitivity: int) -> str:
        mid = new_id()
        with self.tx(ctx) as x:
            if source_message_id:
                src = x.one("SELECT sensitivity FROM messages WHERE id=? AND user_id=?", (source_message_id, ctx.id))
                if not src:
                    raise PermissionError("source message not found")
                sensitivity = max(sensitivity, src["sensitivity"])          # strictest-label rule
            x.run("INSERT INTO memory_cards(id, user_id, text, status, source_message_id, sensitivity, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (mid, ctx.id, text, "proposed", source_message_id, sensitivity, now()))
        return mid

    def set_memory(self, ctx: UserCtx, card_id: str, *, text: str | None = None, status: str | None = None) -> bool:
        with self.tx(ctx) as x:
            r = x.one("SELECT id FROM memory_cards WHERE id=? AND user_id=?", (card_id, ctx.id))
            if not r:
                return False
            if text is not None:
                x.run("UPDATE memory_cards SET text=? WHERE id=?", (text, card_id))
            if status is not None:
                if status not in {"proposed", "approved"}:
                    raise ValueError("status must be proposed or approved")
                x.run("UPDATE memory_cards SET status=?, approved_at=? WHERE id=?",
                      (status, now() if status == "approved" else None, card_id))
            return True

    def delete_memory(self, ctx: UserCtx, card_id: str) -> bool:
        with self.tx(ctx) as x:
            r = x.one("SELECT id FROM memory_cards WHERE id=? AND user_id=?", (card_id, ctx.id))
            if r:
                x.run("DELETE FROM memory_cards WHERE id=?", (card_id,))
            return bool(r)

    def list_memory(self, ctx: UserCtx, status: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM memory_cards WHERE user_id=?", [ctx.id]
        if status:
            q, p = q + " AND status=?", p + [status]
        if not ctx.unlocked:
            q += " AND sensitivity<3"
        with self.tx(ctx) as x:
            return x.all(q + " ORDER BY created_at DESC", p)

    # ------------------------------------------------------------------ retention, export, delete
    def delete_conversation(self, ctx: UserCtx, conv_id: str) -> bool:
        with self.tx(ctx) as x:
            if not x.one("SELECT 1 AS ok FROM conversations WHERE id=? AND user_id=?", (conv_id, ctx.id)):
                return False
            self._purge_conversations(x, [conv_id])
            return True

    def _purge_conversations(self, x, conv_ids: list[str]) -> int:
        if not conv_ids:
            return 0
        ph = ",".join("?" for _ in conv_ids)
        mids = [r["id"] for r in x.all(f"SELECT id FROM messages WHERE conversation_id IN ({ph})", conv_ids)]
        if mids:
            self._delete_message_index(x, mids)
            mph = ",".join("?" for _ in mids)
            x.run(f"DELETE FROM retrievals WHERE message_id IN ({mph})", mids)
            x.run(f"UPDATE memory_cards SET source_message_id=NULL WHERE source_message_id IN ({mph})", mids)
        x.run(f"DELETE FROM messages WHERE conversation_id IN ({ph})", conv_ids)
        x.run(f"DELETE FROM summaries WHERE conversation_id IN ({ph})", conv_ids)
        x.run(f"DELETE FROM conversations WHERE id IN ({ph})", conv_ids)
        return len(conv_ids)

    def apply_retention(self, conversation_days: int | None, at: float | None = None) -> dict:
        """Hard-delete expired conversations and documents (per-collection retention). Returns counts."""
        at = at or now()
        out = {"conversations": 0, "documents": 0}
        with self.tx() as x:
            if conversation_days:
                old = [r["id"] for r in x.all("SELECT id FROM conversations WHERE updated_at < ?",
                                               (at - conversation_days * 86400,))]
                out["conversations"] = self._purge_conversations(x, old)
            for col in x.all("SELECT id, retention_days FROM collections WHERE retention_days IS NOT NULL"):
                docs = x.all("SELECT id FROM documents WHERE collection_id=? AND ingested_at < ?",
                             (col["id"], at - col["retention_days"] * 86400))
                for d in docs:
                    self._hard_delete_document(x, d["id"])
                out["documents"] += len(docs)
        return out

    def export_user(self, ctx: UserCtx) -> dict:
        with self.tx(ctx) as x:
            convs = x.all("SELECT * FROM conversations WHERE user_id=? ORDER BY created_at", (ctx.id,))
            for c in convs:
                c["messages"] = x.all("SELECT role, content, model, model_digest, created_at, sensitivity FROM messages "
                                      "WHERE conversation_id=? ORDER BY created_at, seq", (c["id"],))
                c["summary"] = x.one("SELECT text FROM summaries WHERE conversation_id=?", (c["id"],))
            cards = x.all("SELECT text, status, sensitivity, created_at, approved_at FROM memory_cards WHERE user_id=?", (ctx.id,))
            cols = x.all("SELECT c.name, d.path, d.sha256, d.ingested_at, d.sensitivity FROM documents d "
                         "JOIN collections c ON c.id=d.collection_id WHERE c.owner_id=? AND d.deleted=0", (ctx.id,))
        return {"user": ctx.id, "exported_at": now(), "conversations": convs, "memory_cards": cards, "owned_documents": cols}

    def delete_user_data(self, ctx: UserCtx) -> dict:
        with self.tx(ctx) as x:
            convs = [r["id"] for r in x.all("SELECT id FROM conversations WHERE user_id=?", (ctx.id,))]
            n = self._purge_conversations(x, convs)
            x.run("DELETE FROM memory_cards WHERE user_id=?", (ctx.id,))
            x.run("DELETE FROM restricted_unlocks WHERE user_id=?", (ctx.id,))
        return {"conversations": n}

    def delete_collection(self, collection_id: str) -> int:
        with self.tx() as x:
            docs = x.all("SELECT id FROM documents WHERE collection_id=?", (collection_id,))
            for d in docs:
                self._hard_delete_document(x, d["id"])
            x.run("DELETE FROM collection_acl WHERE collection_id=?", (collection_id,))
            x.run("DELETE FROM collections WHERE id=?", (collection_id,))
        return len(docs)

    # ------------------------------------------------------------------ model registry & evals
    def register_model(self, name: str, digest: str | None, status: str = "candidate", license_: str | None = None,
                       notes: str = "", kind: str = "base") -> str:
        mid = new_id()
        with self.tx() as x:
            x.run("INSERT INTO model_registry(id, name, digest, license, status, kind, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (mid, name, digest, license_, status, kind, notes, now()))
        return mid

    def active_model(self, name: str | None = None) -> dict | None:
        with self.tx() as x:
            if name:
                return x.one("SELECT * FROM model_registry WHERE status='active' AND name=? ORDER BY promoted_at DESC", (name,))
            return x.one("SELECT * FROM model_registry WHERE status='active' AND kind='base' ORDER BY promoted_at DESC")

    def get_model(self, mid: str) -> dict | None:
        with self.tx() as x:
            return x.one("SELECT * FROM model_registry WHERE id=? OR name=? ORDER BY created_at DESC", (mid, mid))

    def set_model_status(self, mid: str, status: str, eval_run_id: str | None = None) -> None:
        with self.tx() as x:
            if status == "active":
                kind = x.one("SELECT kind FROM model_registry WHERE id=?", (mid,))["kind"]
                x.run("UPDATE model_registry SET status='retired' WHERE status='active' AND kind=?", (kind,))
            x.run("UPDATE model_registry SET status=?, promoted_at=?, eval_run_id=COALESCE(?, eval_run_id) WHERE id=?",
                  (status, now() if status == "active" else None, eval_run_id, mid))

    def list_models(self) -> list[dict]:
        with self.tx() as x:
            return x.all("SELECT * FROM model_registry ORDER BY created_at DESC")

    def save_eval(self, model: str, metrics: dict, golden_hash: str) -> str:
        rid = new_id()
        with self.tx() as x:
            x.run("INSERT INTO eval_runs(id, model, ts, metrics_json, golden_hash) VALUES (?,?,?,?,?)",
                  (rid, model, now(), json.dumps(metrics), golden_hash))
        return rid

    # ------------------------------------------------------------------ connectors & pending actions (phase 5)
    def upsert_connector(self, cid: str, ctype: str, config: dict, scope: str = "read-only", enabled: bool = True) -> None:
        with self.tx() as x:
            x.run("DELETE FROM connectors WHERE id=?", (cid,))
            x.run("INSERT INTO connectors(id, type, config_json, scope, enabled, last_accessed) VALUES (?,?,?,?,?,NULL)",
                  (cid, ctype, json.dumps(config), scope, 1 if enabled else 0))

    def set_connector_enabled(self, cid: str, enabled: bool) -> bool:
        with self.tx() as x:
            if not x.one("SELECT id FROM connectors WHERE id=?", (cid,)):
                return False
            x.run("UPDATE connectors SET enabled=? WHERE id=?", (1 if enabled else 0, cid))
            return True

    def touch_connector(self, cid: str) -> None:
        with self.tx() as x:
            x.run("UPDATE connectors SET last_accessed=? WHERE id=?", (now(), cid))

    def list_connectors(self) -> list[dict]:
        with self.tx() as x:
            rows = x.all("SELECT * FROM connectors ORDER BY id")
        for r in rows:
            r["config"] = json.loads(r.pop("config_json") or "{}")
            r["enabled"] = bool(r["enabled"])
        return rows

    def create_pending_action(self, ctx: UserCtx, action: str, payload: dict) -> str:
        aid = new_id()
        with self.tx(ctx) as x:
            x.run("INSERT INTO pending_actions(id, user_id, action, payload_json, status, created_at) VALUES (?,?,?,?,?,?)",
                  (aid, ctx.id, action, json.dumps(payload, sort_keys=True), "pending", now()))
        return aid

    def get_pending_action(self, ctx: UserCtx, aid: str) -> dict | None:
        with self.tx(ctx) as x:
            r = x.one("SELECT * FROM pending_actions WHERE id=? AND user_id=?", (aid, ctx.id))
        if r:
            r["payload"] = json.loads(r.pop("payload_json"))
        return r

    def decide_pending_action(self, ctx: UserCtx, aid: str, status: str, result: str = "") -> None:
        with self.tx(ctx) as x:
            x.run("UPDATE pending_actions SET status=?, decided_at=?, result=? WHERE id=? AND user_id=?",
                  (status, now(), result, aid, ctx.id))

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        with self.tx() as x:
            out = {}
            for t in ("users", "collections", "documents", "chunks", "conversations", "messages", "memory_cards", "audit_log"):
                out[t] = x.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            last = x.one("SELECT MAX(ingested_at) AS t FROM documents")
            out["last_ingest"] = last["t"] if last else None
        return out
