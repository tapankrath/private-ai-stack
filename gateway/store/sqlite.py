"""Tier 1 store: one SQLite file (FTS5 for keywords, float32 blobs + numpy for vectors).

Keep the file on an encrypted disk. Vector search is brute force over rows the user may see,
which is fine at personal scale (tens of thousands of chunks); sqlite-vec can replace it.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ..embed import STOPWORDS, from_blob, to_blob
from .base import Store, UserCtx

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, display_name TEXT, role TEXT, groups_json TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT UNIQUE, owner_id TEXT, default_sensitivity INTEGER,
  retention_days INTEGER, purpose TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS collection_acl(collection_id TEXT, principal TEXT, permission TEXT, PRIMARY KEY(collection_id, principal));
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, collection_id TEXT, path TEXT, sha256 TEXT, mime TEXT, title TEXT,
  author TEXT, created_at REAL, modified_at REAL, ingested_at REAL, sensitivity INTEGER, pii_json TEXT, tags_json TEXT, deleted INTEGER);
CREATE INDEX IF NOT EXISTS documents_path ON documents(collection_id, path);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT, collection_id TEXT, ord INTEGER, text TEXT,
  heading_path TEXT, tokens INTEGER, sensitivity INTEGER, pii_json TEXT, modified_at REAL);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS chunks_col ON chunks(collection_id, sensitivity);
CREATE TABLE IF NOT EXISTS chunk_vectors(chunk_id TEXT PRIMARY KEY, vec BLOB);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading_path, chunk_id UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user_id TEXT, external_id TEXT, title TEXT, created_at REAL,
  updated_at REAL, sensitivity INTEGER);
CREATE INDEX IF NOT EXISTS conv_user ON conversations(user_id, external_id);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, role TEXT, content TEXT, model TEXT,
  model_digest TEXT, sensitivity INTEGER, created_at REAL, seq INTEGER);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id);
CREATE TABLE IF NOT EXISTS message_vectors(message_id TEXT PRIMARY KEY, user_id TEXT, vec BLOB);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, message_id UNINDEXED, user_id UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS retrievals(message_id TEXT, chunk_id TEXT, score REAL, rank INTEGER);
CREATE TABLE IF NOT EXISTS summaries(conversation_id TEXT PRIMARY KEY, user_id TEXT, text TEXT, source_ids_json TEXT,
  sensitivity INTEGER, updated_at REAL);
CREATE TABLE IF NOT EXISTS memory_cards(id TEXT PRIMARY KEY, user_id TEXT, text TEXT, status TEXT, source_message_id TEXT,
  sensitivity INTEGER, created_at REAL, approved_at REAL);
CREATE TABLE IF NOT EXISTS api_tokens(token_hash TEXT PRIMARY KEY, user_id TEXT, label TEXT, created_at REAL, expires_at REAL, revoked INTEGER);
CREATE TABLE IF NOT EXISTS restricted_unlocks(user_id TEXT PRIMARY KEY, expires_at REAL);
CREATE TABLE IF NOT EXISTS audit_log(id TEXT PRIMARY KEY, ts REAL, actor TEXT, action TEXT, object TEXT, detail_json TEXT, model_digest TEXT);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS model_registry(id TEXT PRIMARY KEY, name TEXT, digest TEXT, license TEXT, status TEXT, kind TEXT,
  notes TEXT, created_at REAL, promoted_at REAL, eval_run_id TEXT);
CREATE TABLE IF NOT EXISTS eval_runs(id TEXT PRIMARY KEY, model TEXT, ts REAL, metrics_json TEXT, golden_hash TEXT);
CREATE TABLE IF NOT EXISTS connectors(id TEXT PRIMARY KEY, type TEXT, config_json TEXT, scope TEXT, enabled INTEGER, last_accessed REAL);
CREATE TABLE IF NOT EXISTS pending_actions(id TEXT PRIMARY KEY, user_id TEXT, action TEXT, payload_json TEXT, status TEXT,
  created_at REAL, decided_at REAL, result TEXT);
"""

WORD = re.compile(r"[\w]+", re.UNICODE)


def fts_query(text: str) -> str:
    """Safe FTS5 query: quoted terms OR-ed (no syntax errors from user punctuation)."""
    terms = [t for t in WORD.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS][:32]
    return " OR ".join(f'"{t}"' for t in terms)


class _X:
    """Tiny executor returning dict rows."""

    def __init__(self, conn: sqlite3.Connection):
        self.c = conn

    def run(self, sql, params=()):
        self.c.execute(sql, tuple(params))

    def all(self, sql, params=()):
        cur = self.c.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None


class SQLiteStore(Store):
    dialect = "sqlite"

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA secure_delete=ON")      # deleted content is overwritten on disk
        self.lock = threading.RLock()
        self._depth = 0

    def init(self) -> None:
        with self.lock:
            self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self, ctx: UserCtx | None = None):
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield _X(self.conn)
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise

    # ---------------------------------------------------------------- chunks
    def _insert_chunk(self, x, row, vec):
        x.run("INSERT INTO chunks(id, document_id, collection_id, ord, text, heading_path, tokens, sensitivity, pii_json, modified_at)"
              " VALUES (?,?,?,?,?,?,?,?,?,?)",
              (row["id"], row["document_id"], row["collection_id"], row["ord"], row["text"], row["heading_path"],
               row["tokens"], row["sensitivity"], row["pii_json"], row["modified_at"]))
        x.run("INSERT INTO chunks_fts(text, heading_path, chunk_id) VALUES (?,?,?)", (row["text"], row["heading_path"], row["id"]))
        if vec is not None:
            x.run("INSERT INTO chunk_vectors(chunk_id, vec) VALUES (?,?)", (row["id"], to_blob(vec)))

    def _delete_chunk_index(self, x, ids):
        ph = ",".join("?" for _ in ids)
        x.run(f"DELETE FROM chunks_fts WHERE chunk_id IN ({ph})", ids)
        x.run(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({ph})", ids)

    def _fts_chunk_ids(self, x, ctx, query, filters, n):
        q = fts_query(query)
        if not q:
            return []
        where, params = self._filters_sql(ctx, filters)
        rows = x.all(f"SELECT f.chunk_id AS id FROM chunks_fts f JOIN chunks c ON c.id=f.chunk_id "
                     f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25(chunks_fts) LIMIT ?", (q, *params, n))
        return [r["id"] for r in rows]

    def _vec_chunk_ids(self, x, ctx, qvec, filters, n):
        where, params = self._filters_sql(ctx, filters)
        rows = x.all(f"SELECT v.chunk_id AS id, v.vec FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id WHERE {where}", params)
        return self._top(rows, qvec, n)

    def _vecs_for_chunks(self, x, ids):
        if not ids:
            return {}
        ph = ",".join("?" for _ in ids)
        return {r["chunk_id"]: from_blob(r["vec"]) for r in x.all(f"SELECT chunk_id, vec FROM chunk_vectors WHERE chunk_id IN ({ph})", ids)}

    @staticmethod
    def _top(rows, qvec, n):
        if not rows:
            return []
        mat = np.stack([from_blob(r["vec"]) for r in rows])
        if mat.shape[1] != qvec.shape[0]:
            return []          # embedding model changed; re-index needed
        sims = mat @ qvec
        order = np.argsort(-sims)[:n]
        return [(rows[i]["id"], float(sims[i])) for i in order]

    # ---------------------------------------------------------------- messages
    def _index_message(self, x, msg_id, conv_id, user_id, content, vec):
        x.run("INSERT INTO messages_fts(content, message_id, user_id) VALUES (?,?,?)", (content, msg_id, user_id))
        if vec is not None:
            x.run("INSERT INTO message_vectors(message_id, user_id, vec) VALUES (?,?,?)", (msg_id, user_id, to_blob(vec)))

    def _delete_message_index(self, x, ids):
        ph = ",".join("?" for _ in ids)
        x.run(f"DELETE FROM messages_fts WHERE message_id IN ({ph})", ids)
        x.run(f"DELETE FROM message_vectors WHERE message_id IN ({ph})", ids)

    def _msg_where(self, ctx, since, until):
        w, p = ["m.user_id=?"], [ctx.id]
        if not ctx.unlocked:
            w.append("m.sensitivity<3")
        if since:
            w.append("m.created_at>=?")
            p.append(since)
        if until:
            w.append("m.created_at<=?")
            p.append(until)
        return " AND ".join(w), p

    def _fts_message_ids(self, x, ctx, query, n, since, until):
        q = fts_query(query)
        if not q:
            return []
        where, params = self._msg_where(ctx, since, until)
        rows = x.all(f"SELECT f.message_id AS id FROM messages_fts f JOIN messages m ON m.id=f.message_id "
                     f"WHERE messages_fts MATCH ? AND f.user_id=? AND {where} ORDER BY bm25(messages_fts) LIMIT ?",
                     (q, ctx.id, *params, n))
        return [r["id"] for r in rows]

    def _vec_message_ids(self, x, ctx, qvec, n, since, until):
        where, params = self._msg_where(ctx, since, until)
        rows = x.all(f"SELECT v.message_id AS id, v.vec FROM message_vectors v JOIN messages m ON m.id=v.message_id "
                     f"WHERE v.user_id=? AND {where}", (ctx.id, *params))
        return self._top(rows, qvec, n)

    def _vecs_for_messages(self, x, ids):
        if not ids:
            return {}
        ph = ",".join("?" for _ in ids)
        return {r["message_id"]: from_blob(r["vec"]) for r in x.all(f"SELECT message_id, vec FROM message_vectors WHERE message_id IN ({ph})", ids)}
