"""Phase 1: Gateway proxy, auth, redaction, conversation history, audit, health/metrics."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from gwhelpers import KEY, H, chat, make


# ------------------------------------------------------------------ auth
def test_requires_credentials(gw):
    client, rt, *_ = gw
    assert client.post("/v1/chat/completions", json={}).status_code == 401
    assert client.get("/v1/models", headers=H(key="wrong")).status_code == 401
    assert not rt.requests, "nothing reaches the runtime without valid credentials"


def test_refuses_to_run_without_key():
    client, *_ = make({"gateway_key": None})
    assert client.get("/v1/models", headers=H()).status_code == 500


def test_proxy_headers_ignored_without_key():
    client, *_ = make({"auth_mode": "proxy_header"})
    r = client.get("/history/conversations", headers={"X-OpenWebUI-User-Email": "boss@example.com"})
    assert r.status_code == 401


def test_proxy_mode_needs_user_header():
    client, *_ = make({"auth_mode": "proxy_header"})
    assert client.get("/history/conversations", headers=H()).status_code == 401


def test_oidc_jwt_verified():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": priv.public_key()})()

    from fastapi.testclient import TestClient
    import httpx
    from gwhelpers import FakeRuntime
    from gateway.app import create_app
    from gateway.embed import HashEmbedder
    from gateway.settings import Settings
    s = Settings(database_url="sqlite://", gateway_key=KEY, auth_mode="oidc", oidc_issuer="https://idp.local",
                 oidc_audience="gateway", runtime_url="http://runtime/v1", embedder="hash", background_tasks=False)
    client = TestClient(create_app(s, embedder=HashEmbedder(), runtime_transport=httpx.MockTransport(FakeRuntime()),
                                   jwks_client=FakeJWKS()))
    good = jwt.encode({"sub": "u1", "email": "ann@firm.test", "iss": "https://idp.local", "aud": "gateway",
                       "exp": int(time.time()) + 300, "groups": ["admins"]}, priv, algorithm="RS256")
    r = client.get("/admin/stats", headers={"Authorization": f"Bearer {good}"})
    assert r.status_code == 200, r.text                       # admins group -> admin role
    expired = jwt.encode({"sub": "u1", "iss": "https://idp.local", "aud": "gateway", "exp": int(time.time()) - 10},
                         priv, algorithm="RS256")
    assert client.get("/history/conversations", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    wrong_aud = jwt.encode({"sub": "u1", "iss": "https://idp.local", "aud": "other", "exp": int(time.time()) + 300},
                           priv, algorithm="RS256")
    assert client.get("/history/conversations", headers={"Authorization": f"Bearer {wrong_aud}"}).status_code == 401


# ------------------------------------------------------------------ proxy + storage
def test_chat_proxied_and_stored(gw):
    client, rt, store, _ = gw
    r = chat(client, "What is a good name for a cat?")
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello from the local model."
    convs = client.get("/history/conversations", headers=H()).json()["conversations"]
    assert len(convs) == 1 and convs[0]["title"].startswith("What is a good name")
    c = client.get(f"/history/conversations/{convs[0]['id']}", headers=H()).json()
    assert [m["role"] for m in c["messages"]] == ["user", "assistant"]


def test_streaming_proxied_and_stored(gw):
    client, rt, *_ = gw
    with client.stream("POST", "/v1/chat/completions", headers={**H(), "X-OpenWebUI-Chat-Id": "s1"},
                       json={"model": "qwen3:8b", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "".join(r.iter_text())
    assert "data: [DONE]" in body
    convs = client.get("/history/conversations", headers=H()).json()["conversations"]
    msgs = client.get(f"/history/conversations/{convs[0]['id']}", headers=H()).json()["messages"]
    assert msgs[-1]["content"] == "Hello from the local model."


def test_regenerate_does_not_duplicate(gw):
    client, *_ = gw
    chat(client, "same question")
    chat(client, "same question")
    convs = client.get("/history/conversations", headers=H()).json()["conversations"]
    msgs = client.get(f"/history/conversations/{convs[0]['id']}", headers=H()).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]


def test_secrets_redacted_before_storage(gw):
    client, rt, store, _ = gw
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    chat(client, f"my api key is {secret} and my password: hunter2pass please keep it")
    sent = rt.requests[-1][1]["messages"][-1]["content"]
    assert secret in sent                                   # the local model may see it ...
    export = client.get("/me/export", headers=H()).json()
    dump = str(export)
    assert secret not in dump and "hunter2pass" not in dump  # ... but it is never stored
    assert "[REDACTED" in dump
    hits = client.get("/history/search", params={"q": secret}, headers=H()).json()["results"]
    assert all(secret not in h["content"] for h in hits)


def test_cloud_tags_never_offered_or_used(gw):
    client, *_ = gw
    ids = [m["id"] for m in client.get("/v1/models", headers=H()).json()["data"]]
    assert "qwen3:8b" in ids and "qwen3:8b + Recall" in ids
    assert not any(i.endswith(":cloud") or ":cloud " in i for i in ids)
    assert chat(client, "hi", model="glm-5.3-flash:cloud").status_code == 400


def test_users_are_isolated():
    client, *_ = make({"auth_mode": "proxy_header"})
    chat(client, "my secret project is called Bluebird", headers=H("ann@firm.test"), chat_id="a")
    r = client.get("/history/search", params={"q": "Bluebird"}, headers=H("bob@firm.test")).json()
    assert r["results"] == []
    assert client.get("/history/search", params={"q": "Bluebird"}, headers=H("ann@firm.test")).json()["results"]
    convs = client.get("/history/conversations", headers=H("ann@firm.test")).json()["conversations"]
    assert client.get(f"/history/conversations/{convs[0]['id']}", headers=H("bob@firm.test")).status_code == 404


# ------------------------------------------------------------------ history search acceptance (8 of 10)
SEEDED = [
    ("vendor", "We decided to go with Northwind as the cleaning vendor because of the price.", "Which cleaning vendor did we choose?"),
    ("budget", "The marketing budget for Q3 is capped at forty thousand dollars.", "What is the Q3 marketing budget cap?"),
    ("hire", "We agreed to hire a part-time bookkeeper starting in January.", "When does the bookkeeper start?"),
    ("office", "The office lease renewal deadline is March fifteenth.", "lease renewal deadline"),
    ("laptop", "Replace the reception laptop with a refurbished ThinkPad.", "What replaces the reception laptop?"),
    ("backup", "Backups go to the encrypted NAS every night at two.", "When do the backups run?"),
    ("client", "Client Acme prefers invoices by post, not email.", "How does Acme want invoices?"),
    ("holiday", "The office closes from December twenty-fourth to January second.", "holiday closure dates"),
    ("wifi", "Guest wifi must be on a separate network from the file server.", "guest wifi separate network"),
    ("printer", "The printer toner is ordered from Office Depot on the first Monday.", "Where do we order printer toner?"),
]


def test_history_search_finds_8_of_10(gw):
    client, *_ = gw
    for cid, text, _ in SEEDED:
        chat(client, text, chat_id=cid)
    found = 0
    for cid, text, question in SEEDED:
        res = client.get("/history/search", params={"q": question, "k": 3}, headers=H()).json()["results"]
        if any(text[:30] in r["content"] for r in res):
            found += 1
    assert found >= 8, found


def test_date_filter_last_month(gw):
    client, rt, store, _ = gw
    from gateway.store import UserCtx
    ctx = UserCtx("owner", "admin")
    old = store.get_or_create_conversation(ctx, "old", "old")
    store.add_message(ctx, old, "user", "the zebra plan was rejected", None, None, 1, None,
                      created_at=time.time() - 90 * 86400)
    chat(client, "the zebra plan is approved now", chat_id="new")
    res = client.get("/history/search", params={"q": "zebra plan last month"}, headers=H()).json()["results"]
    assert res and all("approved" in r["content"] or r["role"] == "assistant" for r in res)


# ------------------------------------------------------------------ audit, health, metrics
def test_every_request_audited_with_digest():
    client, rt, store, s = make()
    store.register_model("qwen3:8b", "sha256:abc123", status="active")
    chat(client, "hello")
    entries = client.get("/admin/audit", params={"action": "chat"}, headers=H()).json()["entries"]
    assert entries and entries[0]["model_digest"] == "sha256:abc123"


def test_audit_admin_only():
    client, *_ = make({"auth_mode": "proxy_header"})
    assert client.get("/admin/audit", headers=H("bob@firm.test")).status_code == 403
    assert client.get("/admin/audit", headers=H("ann@firm.test", **{"X-OpenWebUI-User-Role": "admin"})).status_code == 200


def test_health_and_metrics(gw):
    client, *_ = gw
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["runtime"] == "ok"
    chat(client, "hi")
    m = client.get("/metrics", headers=H()).text
    assert "gateway_chat_requests_total" in m and "gateway_messages" in m


def test_runtime_down_is_reported():
    import httpx
    from fastapi.testclient import TestClient
    from gateway.app import create_app
    from gateway.embed import HashEmbedder
    from gateway.settings import Settings

    def down(request):
        raise httpx.ConnectError("down")
    s = Settings(database_url="sqlite://", gateway_key=KEY, runtime_url="http://runtime/v1", embedder="hash", background_tasks=False)
    client = TestClient(create_app(s, embedder=HashEmbedder(), runtime_transport=httpx.MockTransport(down)))
    assert client.get("/health").json()["status"] == "degraded"
    assert chat(client, "hi").status_code == 502
