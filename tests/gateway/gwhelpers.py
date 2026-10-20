import json
import os
import re
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gateway.app import create_app  # noqa: E402
from gateway.embed import HashEmbedder  # noqa: E402
from gateway.settings import Settings  # noqa: E402
from gateway.store import open_store  # noqa: E402

KEY = "test-gateway-key"


class FakeRuntime:
    """Stand-in for Ollama/vLLM. Records every request it receives."""

    def __init__(self):
        self.requests = []
        self.embedder = HashEmbedder()
        self.answer = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "qwen3:8b", "object": "model"}, {"id": "glm-5.3-flash:cloud", "object": "model"}]})
        if request.url.path.endswith("/embeddings"):
            vecs = self.embedder.embed(body["input"] if isinstance(body["input"], list) else [body["input"]])
            return httpx.Response(200, json={"data": [{"index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)]})
        if request.url.path.endswith("/chat/completions"):
            sys_text = " ".join(m["content"] for m in body["messages"] if m["role"] == "system")
            if self.answer:
                text = self.answer
            elif "<<<SOURCES" in sys_text:
                first = re.search(r"\[1\][^\n]*\n(.+?)(?:\n\n\[2\]|\n\nSOURCES>>>)", sys_text, re.S)
                text = f"Based on your files: {first.group(1).strip()[:200]} [1]"
            elif "Summarize" in sys_text:
                text = "- Decided to use the blue vendor."
            else:
                text = "Hello from the local model."
            if body.get("stream"):
                chunks = [text[i:i + 10] for i in range(0, len(text), 10)]
                sse = "".join("data: " + json.dumps({"object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": {"content": c}, "finish_reason": None}]}) + "\n\n" for c in chunks)
                sse += "data: [DONE]\n\n"
                return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": body["model"],
                                             "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                          "finish_reason": "stop"}]})
        return httpx.Response(404)


PG_APP = os.environ.get("GATEWAY_TEST_PG")          # e.g. postgresql://gateway_app:pw@127.0.0.1:55432/gateway
PG_ADMIN = os.environ.get("GATEWAY_TEST_PG_ADMIN")  # e.g. postgresql://admin@127.0.0.1:55432/gateway
_PG = None


def fresh_store(url="sqlite://"):
    """SQLite in memory by default; a truncated Postgres store when GATEWAY_TEST_PG is set."""
    global _PG
    if not PG_APP:
        return open_store(url)
    import psycopg
    from gateway.store.postgres import PostgresStore
    if _PG is None:
        _PG = PostgresStore(PG_APP, PG_ADMIN)
        _PG.init()
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
        c.execute("TRUNCATE " + ", ".join(tables))
    return _PG


def make(settings_over=None, store=None):
    rt = FakeRuntime()
    s = Settings(database_url="sqlite://", gateway_key=KEY, runtime_url="http://runtime/v1", embedder="hash",
                 background_tasks=False, summary_every_messages=4, recall_min_score=0.2)
    for k, v in (settings_over or {}).items():
        setattr(s, k, v)
    st = store or fresh_store(s.database_url)
    app = create_app(s, store=st, embedder=HashEmbedder(), runtime_transport=httpx.MockTransport(rt))
    return TestClient(app), rt, st, s


def H(user=None, key=KEY, **extra):
    h = {"Authorization": f"Bearer {key}"}
    if user:
        h["X-OpenWebUI-User-Email"] = user
        h["X-OpenWebUI-User-Name"] = user.split("@")[0]
    h.update(extra)
    return h


def chat(client, text, model="qwen3:8b", headers=None, chat_id="c1", stream=False, history=None, **body):
    msgs = (history or []) + [{"role": "user", "content": text}]
    return client.post("/v1/chat/completions", headers={**(headers or H()), "X-OpenWebUI-Chat-Id": chat_id},
                       json={"model": model, "messages": msgs, "stream": stream, **body})
