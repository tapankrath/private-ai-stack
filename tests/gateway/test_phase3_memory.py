"""Phase 3: memory cards, summaries, strictest-label rule, retention, export, delete, web UI."""

import time

from gateway.ingest import Ingestor
from gateway.store import UserCtx
from gwhelpers import H, chat, make

RECALL = "qwen3:8b + Recall"


def test_remember_proposes_card_and_approval_injects_it(gw):
    client, rt, store, _ = gw
    chat(client, "Remember that I prefer answers in British English.")
    cards = client.get("/memory", headers=H()).json()["cards"]
    assert cards and cards[0]["status"] == "proposed"
    chat(client, "hello again", chat_id="c2")
    assert "British English" not in str(rt.requests[-1][1]["messages"])          # proposed != used
    client.patch(f"/memory/{cards[0]['id']}", headers=H(), json={"status": "approved"})
    chat(client, "and again", chat_id="c3")
    system = rt.requests[-1][1]["messages"][0]
    assert system["role"] == "system" and "British English" in system["content"]


def test_memory_edit_delete_and_no_secrets(gw):
    client, *_ = gw
    cid = client.post("/memory", headers=H(), json={"text": "My dog is called Pixel", "approve": True}).json()["id"]
    client.patch(f"/memory/{cid}", headers=H(), json={"text": "My dog is called Byte"})
    assert client.get("/memory", headers=H()).json()["cards"][0]["text"] == "My dog is called Byte"
    assert client.post("/memory", headers=H(), json={"text": "my password: s3cretpass1"}).status_code == 400
    assert client.delete(f"/memory/{cid}", headers=H()).status_code == 200
    assert client.get("/memory", headers=H()).json()["cards"] == []


def test_remember_with_secret_not_proposed(gw):
    client, *_ = gw
    chat(client, "remember that my api key is sk-proj-abcdefghijklmnopqrstuvwxyz0123")
    assert client.get("/memory", headers=H()).json()["cards"] == []


def test_rolling_summary_every_n_messages(gw):
    client, rt, store, s = gw      # summary_every_messages = 4 in tests
    chat(client, "We decided to use the blue vendor.", chat_id="v")
    chat(client, "Confirm the blue vendor decision.", chat_id="v",
         history=[{"role": "user", "content": "We decided to use the blue vendor."},
                  {"role": "assistant", "content": "Hello from the local model."}])
    conv = client.get("/history/conversations", headers=H()).json()["conversations"][0]
    c = client.get(f"/history/conversations/{conv['id']}", headers=H()).json()
    assert c["summary"] and "blue vendor" in c["summary"]["text"]


def test_restricted_content_never_in_lower_summary():
    client, rt, store, s = make({"summary_every_messages": 2})
    h = H()
    fin = store.get_collection(store.create_collection("finance", "owner", 3))
    store.upsert_user("owner", "owner", "admin", [])
    Ingestor(store, client.app.state.embedder, s).ingest_bytes(fin, "pay.md", b"# Payroll\nPayroll account ends 7781.")
    client.post("/recall/unlock", headers=h, json={"minutes": 5})
    chat(client, "Which digits does the payroll account end in?", model=RECALL, chat_id="r")
    full = UserCtx("owner", unlocked=True)
    conv = store.list_conversations(full)[0]
    summary = store.get_conversation(full, conv["id"])["summary"]
    assert summary is not None
    with store.tx() as x:
        row = x.one("SELECT sensitivity FROM summaries WHERE conversation_id=?", (conv["id"],))
    assert row["sensitivity"] == 3
    client.post("/recall/lock", headers=h)
    assert client.get(f"/history/conversations/{conv['id']}", headers=h).status_code == 404


def test_export_json_and_markdown(gw):
    client, *_ = gw
    chat(client, "Plan the spring newsletter.")
    client.post("/memory", headers=H(), json={"text": "Newsletter goes out monthly", "approve": True})
    j = client.get("/me/export", headers=H()).json()
    assert j["conversations"][0]["messages"][0]["content"] == "Plan the spring newsletter."
    md = client.get("/me/export", params={"format": "markdown"}, headers=H()).text
    assert "# Export for owner" in md and "Plan the spring newsletter." in md and "Newsletter goes out monthly" in md


def test_delete_my_data_removes_everything(gw):
    client, rt, store, _ = gw
    chat(client, "Our secret recipe uses cardamom.")
    assert client.post("/me/delete", headers=H(), json={"confirm": "nope"}).status_code == 400
    assert client.post("/me/delete", headers=H(), json={"confirm": "DELETE MY DATA"}).json()["conversations"] == 1
    assert client.get("/history/search", params={"q": "cardamom"}, headers=H()).json()["results"] == []
    with store.tx() as x:
        assert x.one("SELECT COUNT(*) AS n FROM messages")["n"] == 0
        assert x.one("SELECT COUNT(*) AS n FROM messages_fts")["n"] == 0
        assert x.one("SELECT COUNT(*) AS n FROM message_vectors")["n"] == 0


def test_deleting_document_removes_chunks_vectors_and_index(gw):
    client, rt, store, s = gw
    store.upsert_user("owner", "owner", "admin", [])
    col = store.get_collection("personal:owner")
    ing = Ingestor(store, client.app.state.embedder, s)
    ing.ingest_bytes(col, "plan.md", b"# Plan\nThe launch date is June 3.")
    assert client.delete(f"/collections/{col['id']}/documents", params={"path": "plan.md"}, headers=H()).json()["deleted"]
    with store.tx() as x:
        for t in ("documents", "chunks", "chunks_fts", "chunk_vectors"):
            assert x.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0, t


def test_retention_hard_deletes_old(gw):
    client, rt, store, s = gw
    ctx = UserCtx("owner")
    store.upsert_user("owner", "owner", "admin", [])
    old = store.get_or_create_conversation(ctx, "old", "old")
    store.add_message(ctx, old, "user", "ancient history", None, None, 1, None, created_at=time.time() - 400 * 86400)
    with store.tx() as x:
        x.run("UPDATE conversations SET updated_at=? WHERE id=?", (time.time() - 400 * 86400, old))
    chat(client, "fresh topic")
    res = client.post("/admin/retention", headers=H(), json={"conversation_days": 365}).json()
    assert res["conversations"] == 1
    titles = [c["title"] for c in client.get("/history/conversations", headers=H()).json()["conversations"]]
    assert titles == ["fresh topic"]


def test_collection_retention(gw):
    client, rt, store, s = gw
    store.upsert_user("owner", "owner", "admin", [])
    cid = store.create_collection("tmp", "owner", 1, retention_days=30)
    col = store.get_collection(cid)
    Ingestor(store, client.app.state.embedder, s).ingest_bytes(col, "a.md", b"temporary note")
    with store.tx() as x:
        x.run("UPDATE documents SET ingested_at=?", (time.time() - 40 * 86400,))
    assert store.apply_retention(None)["documents"] == 1


def test_upload_requires_write_access():
    client, rt, store, s = make({"auth_mode": "proxy_header"})
    store.upsert_user("ann@firm.test", "ann", "member", [])
    cid = store.create_collection("clients", "ann@firm.test", 2)
    store.grant(cid, "user:bob@firm.test", "read")
    r = client.put(f"/collections/{cid}/documents", params={"path": "x.md"}, content=b"hi", headers=H("bob@firm.test"))
    assert r.status_code == 403
    r = client.put(f"/collections/{cid}/documents", params={"path": "x.md"}, content=b"# Note\nhello there", headers=H("ann@firm.test"))
    assert r.status_code == 200 and r.json()["status"] == "added"


def test_web_ui_served_and_token_login(gw):
    client, rt, store, _ = gw
    html = client.get("/ui").text
    assert "Private AI" in html and "gwu_" in html and "http" not in html.split("<script>")[0].split("<style>")[0][200:]
    store.upsert_user("owner", "owner", "admin", [])
    tok = store.create_token("owner", "test")
    me = client.get("/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["id"] == "owner" and me["role"] == "admin"
    store.revoke_tokens("owner")
    assert client.get("/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_ui_has_no_external_resources():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "gateway" / "ui" / "index.html").read_text()
    for marker in ("<script src=", "<link ", "@import", "https://", "http://"):
        assert marker not in html, marker
