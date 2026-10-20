"""Tiers 2-3 store: Postgres + pgvector + full-text search, with row-level security.

Two layers enforce access:
  1. The same WHERE clauses as SQLite (built in base.Store).
  2. Postgres row-level security. The Gateway connects as `gateway_app`, which does NOT own the
     tables, so policies always apply. Every transaction sets who is asking:
        app.user_id, app.principals ("user:x|group:y|everyone"), app.unlocked, app.system
     User-facing reads run with the user's settings; only ingestion/maintenance code paths run
     with app.system = on. Neither is ever taken from request input.
Schema creation and policies need the owner role: GATEWAY_DATABASE_ADMIN_URL.
"""

from __future__ import annotations

import os
import queue
import re
from contextlib import contextmanager

import numpy as np
import psycopg
from psycopg.rows import dict_row

from ..embed import STOPWORDS
from .base import Store, UserCtx

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, display_name TEXT, role TEXT, groups_json TEXT, created_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT UNIQUE, owner_id TEXT, default_sensitivity INTEGER,
  retention_days INTEGER, purpose TEXT, created_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS collection_acl(collection_id TEXT, principal TEXT, permission TEXT, PRIMARY KEY(collection_id, principal));
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, collection_id TEXT, path TEXT, sha256 TEXT, mime TEXT, title TEXT,
  author TEXT, created_at DOUBLE PRECISION, modified_at DOUBLE PRECISION, ingested_at DOUBLE PRECISION, sensitivity INTEGER,
  pii_json TEXT, tags_json TEXT, deleted INTEGER);
CREATE INDEX IF NOT EXISTS documents_path ON documents(collection_id, path);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT, collection_id TEXT, ord INTEGER, text TEXT,
  heading_path TEXT, tokens INTEGER, sensitivity INTEGER, pii_json TEXT, modified_at DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS chunks_col ON chunks(collection_id, sensitivity);
CREATE TABLE IF NOT EXISTS chunk_vectors(chunk_id TEXT PRIMARY KEY, vec vector);
CREATE TABLE IF NOT EXISTS chunks_fts(chunk_id TEXT PRIMARY KEY, tsv tsvector);
CREATE INDEX IF NOT EXISTS chunks_fts_gin ON chunks_fts USING gin(tsv);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user_id TEXT, external_id TEXT, title TEXT,
  created_at DOUBLE PRECISION, updated_at DOUBLE PRECISION, sensitivity INTEGER);
CREATE INDEX IF NOT EXISTS conv_user ON conversations(user_id, external_id);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, role TEXT, content TEXT, model TEXT,
  model_digest TEXT, sensitivity INTEGER, created_at DOUBLE PRECISION, seq BIGINT);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id);
CREATE TABLE IF NOT EXISTS message_vectors(message_id TEXT PRIMARY KEY, user_id TEXT, vec vector);
CREATE TABLE IF NOT EXISTS messages_fts(message_id TEXT PRIMARY KEY, user_id TEXT, tsv tsvector);
CREATE INDEX IF NOT EXISTS messages_fts_gin ON messages_fts USING gin(tsv);
CREATE TABLE IF NOT EXISTS retrievals(message_id TEXT, chunk_id TEXT, score DOUBLE PRECISION, rank INTEGER);
CREATE TABLE IF NOT EXISTS summaries(conversation_id TEXT PRIMARY KEY, user_id TEXT, text TEXT, source_ids_json TEXT,
  sensitivity INTEGER, updated_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS memory_cards(id TEXT PRIMARY KEY, user_id TEXT, text TEXT, status TEXT, source_message_id TEXT,
  sensitivity INTEGER, created_at DOUBLE PRECISION, approved_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS api_tokens(token_hash TEXT PRIMARY KEY, user_id TEXT, label TEXT, created_at DOUBLE PRECISION,
  expires_at DOUBLE PRECISION, revoked INTEGER);
CREATE TABLE IF NOT EXISTS restricted_unlocks(user_id TEXT PRIMARY KEY, expires_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS audit_log(id TEXT PRIMARY KEY, ts DOUBLE PRECISION, actor TEXT, action TEXT, object TEXT,
  detail_json TEXT, model_digest TEXT);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS model_registry(id TEXT PRIMARY KEY, name TEXT, digest TEXT, license TEXT, status TEXT, kind TEXT,
  notes TEXT, created_at DOUBLE PRECISION, promoted_at DOUBLE PRECISION, eval_run_id TEXT);
CREATE TABLE IF NOT EXISTS eval_runs(id TEXT PRIMARY KEY, model TEXT, ts DOUBLE PRECISION, metrics_json TEXT, golden_hash TEXT);
CREATE TABLE IF NOT EXISTS connectors(id TEXT PRIMARY KEY, type TEXT, config_json TEXT, scope TEXT, enabled INTEGER,
  last_accessed DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS pending_actions(id TEXT PRIMARY KEY, user_id TEXT, action TEXT, payload_json TEXT, status TEXT,
  created_at DOUBLE PRECISION, decided_at DOUBLE PRECISION, result TEXT);
"""

POLICIES = """
CREATE OR REPLACE FUNCTION gw_system() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.system', true), '') = 'on' $$;
CREATE OR REPLACE FUNCTION gw_uid() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.user_id', true), '') $$;
CREATE OR REPLACE FUNCTION gw_unlocked() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.unlocked', true), '') = 'on' $$;
CREATE OR REPLACE FUNCTION gw_readable(col text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT gw_system()
      OR EXISTS (SELECT 1 FROM collections c WHERE c.id = col AND c.owner_id = gw_uid() AND gw_uid() <> '')
      OR EXISTS (SELECT 1 FROM collection_acl a WHERE a.collection_id = col
                 AND a.principal = ANY (string_to_array(coalesce(current_setting('app.principals', true), ''), '|')))
$$;
"""

CONTENT_TABLES = {   # table -> USING clause for reads
    "documents": "gw_readable(collection_id) AND (sensitivity < 3 OR gw_unlocked() OR gw_system())",
    "chunks": "gw_readable(collection_id) AND (sensitivity < 3 OR gw_unlocked() OR gw_system())",
    "chunks_fts": "gw_system() OR EXISTS (SELECT 1 FROM chunks c WHERE c.id = chunk_id)",
    "chunk_vectors": "gw_system() OR EXISTS (SELECT 1 FROM chunks c WHERE c.id = chunk_id)",
    "retrievals": "gw_system() OR EXISTS (SELECT 1 FROM messages m WHERE m.id = message_id)",
}
OWNED_TABLES = ["conversations", "messages", "message_vectors", "messages_fts", "summaries", "memory_cards",
                "pending_actions", "restricted_unlocks"]
WORD = re.compile(r"[A-Za-z0-9]+")


def _tsquery(text: str) -> str:
    terms = [t for t in WORD.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS][:32]
    return " | ".join(terms)


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6g}" for x in v) + "]"


def _unvec(s: str) -> np.ndarray:
    return np.asarray([float(x) for x in s.strip("[]").split(",")], dtype=np.float32)


class _X:
    def __init__(self, cur):
        self.cur = cur

    @staticmethod
    def _q(sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s")

    def run(self, sql, params=()):
        self.cur.execute(self._q(sql), tuple(params))

    def all(self, sql, params=()):
        self.cur.execute(self._q(sql), tuple(params))
        return list(self.cur.fetchall()) if self.cur.description else []

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def raw(self, sql, params=()):
        """psycopg-native placeholders (%s); used for vector / tsquery SQL."""
        self.cur.execute(sql, tuple(params))
        return list(self.cur.fetchall()) if self.cur.description else []


class PostgresStore(Store):
    dialect = "postgres"

    def __init__(self, url: str, admin_url: str | None = None, pool_size: int = 8):
        self.url = url
        self.admin_url = admin_url or os.environ.get("GATEWAY_DATABASE_ADMIN_URL")
        self.pool: queue.Queue = queue.Queue()
        self.pool_size = pool_size
        self._made = 0
        self._local = __import__("threading").local()

    def _conn(self):
        try:
            return self.pool.get_nowait()
        except queue.Empty:
            if self._made < self.pool_size:
                self._made += 1
                return psycopg.connect(self.url, row_factory=dict_row, autocommit=False)
            return self.pool.get()

    def init(self) -> None:
        if self.admin_url:
            with psycopg.connect(self.admin_url, autocommit=True) as c:
                c.execute(SCHEMA)
                c.execute(POLICIES)
                for t, using in CONTENT_TABLES.items():
                    c.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
                    c.execute(f"DROP POLICY IF EXISTS gw_read ON {t}")
                    c.execute(f"DROP POLICY IF EXISTS gw_write ON {t}")
                    c.execute(f"CREATE POLICY gw_read ON {t} FOR SELECT USING ({using})")
                    c.execute(f"CREATE POLICY gw_write ON {t} FOR ALL USING (gw_system()) WITH CHECK (gw_system())")
                for t in OWNED_TABLES:
                    c.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
                    c.execute(f"DROP POLICY IF EXISTS gw_owner ON {t}")
                    c.execute(f"CREATE POLICY gw_owner ON {t} FOR ALL USING (gw_system() OR user_id = gw_uid()) "
                              f"WITH CHECK (gw_system() OR user_id = gw_uid())")
                role = psycopg.conninfo.conninfo_to_dict(self.url).get("user")
                if role:
                    c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
                    c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{role}"')

    @contextmanager
    def tx(self, ctx: UserCtx | None = None):
        depth = getattr(self._local, "depth", 0)
        if depth:                                    # nested: reuse the outer transaction
            self._local.depth += 1
            try:
                yield self._local.x
            finally:
                self._local.depth -= 1
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                if ctx is None:
                    cur.execute("SELECT set_config('app.system','on',true), set_config('app.user_id','',true)")
                else:
                    cur.execute("SELECT set_config('app.system','off',true), set_config('app.user_id',%s,true), "
                                "set_config('app.principals',%s,true), set_config('app.unlocked',%s,true)",
                                (ctx.id, "|".join(ctx.principals), "on" if ctx.unlocked else "off"))
                x = _X(cur)
                self._local.x, self._local.depth = x, 1
                try:
                    yield x
                finally:
                    self._local.depth = 0
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self.pool.put(conn)

    # ---------------------------------------------------------------- chunks
    def _insert_chunk(self, x, row, vec):
        x.run("INSERT INTO chunks(id, document_id, collection_id, ord, text, heading_path, tokens, sensitivity, pii_json, modified_at)"
              " VALUES (?,?,?,?,?,?,?,?,?,?)",
              (row["id"], row["document_id"], row["collection_id"], row["ord"], row["text"], row["heading_path"],
               row["tokens"], row["sensitivity"], row["pii_json"], row["modified_at"]))
        x.raw("INSERT INTO chunks_fts(chunk_id, tsv) VALUES (%s, to_tsvector('english', %s))",
              (row["id"], (row["heading_path"] or "") + " " + row["text"]))
        if vec is not None:
            x.raw("INSERT INTO chunk_vectors(chunk_id, vec) VALUES (%s, %s::vector)", (row["id"], _vec(vec)))

    def _delete_chunk_index(self, x, ids):
        x.raw("DELETE FROM chunks_fts WHERE chunk_id = ANY(%s)", (ids,))
        x.raw("DELETE FROM chunk_vectors WHERE chunk_id = ANY(%s)", (ids,))

    def _pg_where(self, ctx, filters):
        where, params = self._filters_sql(ctx, filters)
        return _X._q(where), params

    def _fts_chunk_ids(self, x, ctx, query, filters, n):
        q = _tsquery(query)
        if not q:
            return []
        where, params = self._pg_where(ctx, filters)
        rows = x.raw(f"SELECT f.chunk_id AS id FROM chunks_fts f JOIN chunks c ON c.id=f.chunk_id "
                     f"WHERE f.tsv @@ to_tsquery('english', %s) AND {where} "
                     f"ORDER BY ts_rank(f.tsv, to_tsquery('english', %s)) DESC LIMIT %s", (q, *params, q, n))
        return [r["id"] for r in rows]

    def _vec_chunk_ids(self, x, ctx, qvec, filters, n):
        where, params = self._pg_where(ctx, filters)
        v = _vec(qvec)
        rows = x.raw(f"SELECT v.chunk_id AS id, 1 - (v.vec <=> %s::vector) AS sim FROM chunk_vectors v "
                     f"JOIN chunks c ON c.id=v.chunk_id WHERE vector_dims(v.vec) = %s AND {where} "
                     f"ORDER BY v.vec <=> %s::vector LIMIT %s", (v, len(qvec), *params, v, n))
        return [(r["id"], float(r["sim"])) for r in rows]

    def _vecs_for_chunks(self, x, ids):
        if not ids:
            return {}
        return {r["chunk_id"]: _unvec(r["vec"]) for r in
                x.raw("SELECT chunk_id, vec::text AS vec FROM chunk_vectors WHERE chunk_id = ANY(%s)", (ids,))}

    # ---------------------------------------------------------------- messages
    def _index_message(self, x, msg_id, conv_id, user_id, content, vec):
        x.raw("INSERT INTO messages_fts(message_id, user_id, tsv) VALUES (%s, %s, to_tsvector('english', %s))",
              (msg_id, user_id, content))
        if vec is not None:
            x.raw("INSERT INTO message_vectors(message_id, user_id, vec) VALUES (%s, %s, %s::vector)", (msg_id, user_id, _vec(vec)))

    def _delete_message_index(self, x, ids):
        x.raw("DELETE FROM messages_fts WHERE message_id = ANY(%s)", (ids,))
        x.raw("DELETE FROM message_vectors WHERE message_id = ANY(%s)", (ids,))

    def _msg_where(self, ctx, since, until):
        w, p = ["m.user_id=%s"], [ctx.id]
        if not ctx.unlocked:
            w.append("m.sensitivity<3")
        if since:
            w.append("m.created_at>=%s")
            p.append(since)
        if until:
            w.append("m.created_at<=%s")
            p.append(until)
        return " AND ".join(w), p

    def _fts_message_ids(self, x, ctx, query, n, since, until):
        q = _tsquery(query)
        if not q:
            return []
        where, params = self._msg_where(ctx, since, until)
        rows = x.raw(f"SELECT f.message_id AS id FROM messages_fts f JOIN messages m ON m.id=f.message_id "
                     f"WHERE f.tsv @@ to_tsquery('english', %s) AND f.user_id=%s AND {where} "
                     f"ORDER BY ts_rank(f.tsv, to_tsquery('english', %s)) DESC LIMIT %s", (q, ctx.id, *params, q, n))
        return [r["id"] for r in rows]

    def _vec_message_ids(self, x, ctx, qvec, n, since, until):
        where, params = self._msg_where(ctx, since, until)
        v = _vec(qvec)
        rows = x.raw(f"SELECT v.message_id AS id, 1 - (v.vec <=> %s::vector) AS sim FROM message_vectors v "
                     f"JOIN messages m ON m.id=v.message_id WHERE v.user_id=%s AND vector_dims(v.vec) = %s AND {where} "
                     f"ORDER BY v.vec <=> %s::vector LIMIT %s", (v, ctx.id, len(qvec), *params, v, n))
        return [(r["id"], float(r["sim"])) for r in rows]

    def _vecs_for_messages(self, x, ids):
        if not ids:
            return {}
        return {r["message_id"]: _unvec(r["vec"]) for r in
                x.raw("SELECT message_id, vec::text AS vec FROM message_vectors WHERE message_id = ANY(%s)", (ids,))}
