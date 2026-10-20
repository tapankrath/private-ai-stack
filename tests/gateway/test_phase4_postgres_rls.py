"""Phase 4: Postgres row-level security as a second, independent layer.

These tests issue raw SQL WITHOUT the application's WHERE clauses, as a buggy code path would,
and check that Postgres itself still hides rows the user may not see.
Run with GATEWAY_TEST_PG / GATEWAY_TEST_PG_ADMIN set (see docs); skipped otherwise.
"""

import psycopg
import pytest

from gateway.embed import HashEmbedder
from gateway.store import UserCtx
from gwhelpers import PG_ADMIN, PG_APP, fresh_store

pytestmark = pytest.mark.skipif(not PG_APP, reason="needs a Postgres test database (GATEWAY_TEST_PG)")


@pytest.fixture
def pg():
    store = fresh_store()
    e = HashEmbedder()
    store.upsert_user("ann", "Ann", "member", [])
    store.upsert_user("bob", "Bob", "member", [])
    col = store.create_collection("clients", "ann", 2)
    fin = store.create_collection("finance", "ann", 3)
    for cid, text, sens in ((col, "Acme settlement is 2.4 million", 2), (fin, "Payroll account ends 7781", 3)):
        store.upsert_document(cid, f"{cid}.md", "sha" + cid, {}, sens, [],
                              [{"ord": 0, "text": text, "heading_path": "", "tokens": 5, "sensitivity": sens}], e.embed([text]))
    ann = UserCtx("ann")
    conv = store.get_or_create_conversation(ann, "x", "private chat")
    store.add_message(ann, conv, "user", "my private note", None, None, 1, e.embed(["my private note"])[0])
    return store, col, fin


def raw(store, ctx, sql, params=()):
    with store.tx(ctx) as x:
        return x.raw(sql, params)


def test_gateway_role_does_not_own_tables(pg):
    with psycopg.connect(PG_ADMIN) as c:
        owners = {r[0] for r in c.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='public'")}
    app_role = psycopg.conninfo.conninfo_to_dict(PG_APP)["user"]
    assert app_role not in owners, "RLS does not apply to table owners"


def test_rls_hides_other_users_chunks_without_where(pg):
    store, col, fin = pg
    assert raw(store, UserCtx("bob"), "SELECT count(*) AS n FROM chunks")[0]["n"] == 0
    assert raw(store, UserCtx("bob"), "SELECT count(*) AS n FROM chunk_vectors")[0]["n"] == 0
    assert raw(store, UserCtx("bob"), "SELECT count(*) AS n FROM documents")[0]["n"] == 0


def test_rls_restricted_needs_unlock(pg):
    store, col, fin = pg
    locked = raw(store, UserCtx("ann"), "SELECT text FROM chunks")
    assert [r["text"] for r in locked] == ["Acme settlement is 2.4 million"]
    unlocked = raw(store, UserCtx("ann", unlocked=True), "SELECT text FROM chunks")
    assert len(unlocked) == 2


def test_rls_group_grant(pg):
    store, col, fin = pg
    store.grant(col, "group:partners", "read")
    rows = raw(store, UserCtx("bob", groups=["partners"]), "SELECT text FROM chunks")
    assert [r["text"] for r in rows] == ["Acme settlement is 2.4 million"]
    assert raw(store, UserCtx("bob", groups=["sales"]), "SELECT count(*) AS n FROM chunks")[0]["n"] == 0


def test_rls_messages_owner_only(pg):
    store, *_ = pg
    assert raw(store, UserCtx("bob"), "SELECT count(*) AS n FROM messages")[0]["n"] == 0
    assert raw(store, UserCtx("bob"), "SELECT count(*) AS n FROM messages_fts")[0]["n"] == 0
    assert raw(store, UserCtx("ann"), "SELECT count(*) AS n FROM messages")[0]["n"] == 1


def test_rls_blocks_user_context_writes_to_content(pg):
    store, col, fin = pg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(store, UserCtx("bob"), "INSERT INTO chunks(id, collection_id, text, sensitivity) VALUES ('x', %s, 'planted', 0)", (col,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        raw(store, UserCtx("bob"), "INSERT INTO messages(id, user_id, conversation_id, content) VALUES ('m', 'ann', 'c', 'spoof')")


def test_rls_no_settings_means_no_rows(pg):
    """A connection that forgot to set who is asking sees nothing."""
    with psycopg.connect(PG_APP) as c:
        assert c.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
