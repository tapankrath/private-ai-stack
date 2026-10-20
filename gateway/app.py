"""Gateway HTTP API (FastAPI).

OpenAI-compatible endpoints so Open WebUI (or any client) points here instead of the runtime:
  GET  /v1/models                 runtime models + "<model> + Recall" variants
  POST /v1/chat/completions       stores history, applies memory + Recall, audits, proxies
  POST /v1/embeddings             audited passthrough
Gateway endpoints (JSON; the web UI at /ui uses them):
  /history/*, /recall/*, /memory/*, /collections/*, /me/export, /me/delete, /admin/*,
  /health, /metrics
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import SENSITIVITY_NAMES, __version__, sens_level
from . import recall as R
from .auth import AuthError, identify, user_ctx
from .embed import HashEmbedder, HttpEmbedder, Reranker
from .redact import redact_secrets, sensitivity_for
from .settings import Settings
from .store import Store, UserCtx, open_store

UI_DIR = Path(__file__).parent / "ui"


class Metrics:
    def __init__(self):
        self.counters: dict[str, float] = {}

    def inc(self, name: str, v: float = 1.0, **labels):
        key = name + ("{" + ",".join(f'{k}="{val}"' for k, val in sorted(labels.items())) + "}" if labels else "")
        self.counters[key] = self.counters.get(key, 0.0) + v

    def render(self, gauges: dict) -> str:
        lines = [f"gateway_{k} {v}" for k, v in sorted(self.counters.items())]
        lines += [f"gateway_{k} {v}" for k, v in sorted(gauges.items()) if isinstance(v, (int, float))]
        return "\n".join(lines) + "\n"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _model_pins(path: str | None) -> dict[str, str]:
    """Parse `ollama list` output (generated/model-pins.txt): NAME ID SIZE MODIFIED."""
    pins = {}
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                pins[parts[0]] = parts[1]
    return pins


def create_app(settings: Settings | None = None, store: Store | None = None, embedder=None, reranker=None,
               runtime_transport: httpx.AsyncBaseTransport | None = None, jwks_client=None, extras=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or open_store(settings.database_url)
    if embedder is None:
        embedder = HashEmbedder() if settings.embedder == "hash" else \
            HttpEmbedder(settings.embed_url or settings.runtime_url, settings.embed_model, settings.runtime_key)
    reranker = reranker if reranker is not None else Reranker(settings.rerank_url, settings.rerank_model)
    rt_headers = {"Authorization": f"Bearer {settings.runtime_key}"} if settings.runtime_key else {}
    runtime = httpx.AsyncClient(base_url=settings.runtime_url.rstrip("/"), timeout=settings.request_timeout_s,
                                transport=runtime_transport, headers=rt_headers)
    metrics = Metrics()
    pins = _model_pins(settings.model_pins)

    if extras is None:
        from .extensions import Extensions

        extras = Extensions(settings, store, embedder, runtime_transport, metrics)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await runtime.aclose()

    app = FastAPI(title="Private AI Gateway", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.embedder = settings, store, embedder
    app.state.reranker, app.state.runtime, app.state.metrics = reranker, runtime, metrics

    # ------------------------------------------------------------------ helpers
    async def auth(request: Request) -> UserCtx:
        try:
            ident = identify(request.headers, settings, jwks_client, store)
        except AuthError as e:
            metrics.inc("auth_failures_total")
            raise HTTPException(e.status, e.message)
        return await run_in_threadpool(user_ctx, ident, store)

    def require_admin(ctx: UserCtx):
        if not ctx.is_admin:
            raise HTTPException(403, "Admin only.")

    def digest(model: str) -> str | None:
        if model in pins:
            return pins[model]
        reg = store.active_model(model)
        return reg["digest"] if reg else None

    def embed_one(text: str):
        try:
            return embedder.embed([text])[0]
        except Exception:
            metrics.inc("embedding_failures_total")
            return None

    app.state.helpers = {"auth": auth, "require_admin": require_admin, "digest": digest}

    @app.exception_handler(PermissionError)
    async def _perm(_, exc):
        return JSONResponse({"detail": str(exc) or "not found"}, status_code=404)

    # ------------------------------------------------------------------ health & metrics
    @app.get("/health")
    async def health():
        info = {"status": "ok", "version": __version__, "auth_mode": settings.auth_mode, "store": store.dialect}
        try:
            r = await runtime.get("/models", timeout=5)
            info["runtime"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
        except httpx.HTTPError as e:
            info["runtime"] = f"unreachable ({type(e).__name__})"
            info["status"] = "degraded"
        return info

    @app.get("/metrics")
    async def prom(request: Request):
        await auth(request)
        return PlainTextResponse(metrics.render(await run_in_threadpool(store.stats)))

    # ------------------------------------------------------------------ OpenAI-compatible
    @app.get("/v1/models")
    async def models(request: Request):
        await auth(request)
        try:
            r = await runtime.get("/models")
            r.raise_for_status()
            data = r.json().get("data", [])
        except httpx.HTTPError:
            data = [{"id": settings.default_model, "object": "model", "owned_by": "local"}]
        data = [m for m in data if not str(m.get("id", "")).endswith(":cloud")]   # never offer cloud-hosted tags
        out = []
        for m in data:
            out.append(m)
            out.append({**m, "id": m["id"] + settings.recall_suffix, "owned_by": "gateway"})
        if extras and getattr(extras, "extra_models", None):
            out += extras.extra_models([m["id"] for m in data])
        return {"object": "list", "data": out}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        ctx = await auth(request)
        body = await request.json()
        r = await runtime.post("/embeddings", json=body)
        await run_in_threadpool(store.audit, ctx.id, "embeddings", body.get("model", ""), {"inputs": len(body.get("input") or [])})
        return Response(r.content, status_code=r.status_code, media_type="application/json")

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        t0 = time.time()
        ctx = await auth(request)
        body = await request.json()
        if extras and getattr(extras, "handles", None) and extras.handles(body.get("model", "")):
            return await extras.handle_chat(request, ctx, body)
        model = body.get("model") or settings.default_model
        use_recall = model.endswith(settings.recall_suffix)
        base_model = model[: -len(settings.recall_suffix)] if use_recall else model
        if base_model.endswith(":cloud"):
            raise HTTPException(400, "Cloud-hosted model tags are not allowed (egress policy).")
        reg = await run_in_threadpool(store.get_model, base_model)
        if reg and reg["kind"] == "abliterated":                       # ADR-007 guardrail
            if not (ctx.is_admin or set(ctx.groups) & set(settings.abliterated_groups)):
                await run_in_threadpool(store.audit, ctx.id, "abliterated_model_refused", base_model, {})
                raise HTTPException(403, "This model is restricted (ADR-007).")
        messages = list(body.get("messages") or [])
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            raise HTTPException(400, "No user message.")
        question = _text_of(user_msgs[-1].get("content"))
        ext_id = (request.headers.get("x-openwebui-chat-id") or request.headers.get("x-conversation-id")
                  or body.get("chat_id") or hashlib.sha256((ctx.id + _text_of(user_msgs[0].get("content"))[:500]).encode()).hexdigest())
        title = redact_secrets(_text_of(user_msgs[0].get("content")).strip().split("\n")[0])[0][:80] or "Conversation"
        stored_q, red_counts = redact_secrets(question)
        q_sens, q_pii = sensitivity_for(question, 1)
        if red_counts:
            q_sens = max(q_sens, 2)

        def store_user():
            conv = store.get_or_create_conversation(ctx, ext_id, title)
            last = store.last_message(ctx, conv, role="user")
            if (last and last["content"] == stored_q
                    and store.message_count(ctx, conv, role="user") >= len(user_msgs)):   # regenerate: don't duplicate
                return conv, last["id"], 0
            mid = store.add_message(ctx, conv, "user", stored_q, None, None, q_sens,
                                    embed_one(stored_q) if settings.history_index else None)
            return conv, mid, 0

        conv_id, user_mid, n_before = await run_in_threadpool(store_user)
        if extras and getattr(extras, "on_user_message", None):
            await run_in_threadpool(extras.on_user_message, ctx, conv_id, user_mid, stored_q, q_sens)

        # approved memory cards -> system note (only cards the session may see)
        cards = await run_in_threadpool(store.list_memory, ctx, "approved")
        sources: list[dict] = []
        out_messages = list(messages)
        if cards:
            note = "Facts the user asked you to remember (approved by them):\n" + "\n".join(f"- {c['text']}" for c in cards[:30])
            out_messages = [{"role": "system", "content": note}] + out_messages

        if use_recall:
            sources = await run_in_threadpool(lambda: R.retrieve(store, ctx, embedder, reranker, question,
                                                                 k=settings.recall_top_k, now=time.time(),
                                                                 exclude_conversation=conv_id))
            confident = bool(sources) and sources[0]["score"] >= settings.recall_min_score
            metrics.inc("recall_requests_total", confident=str(confident).lower())
            if not confident:
                answer = R.dont_know(sources)
                await run_in_threadpool(_finish, ctx, conv_id, answer, base_model, q_sens, [], t0, "dont_know")
                return _canned(answer, model, body.get("stream", False))
            out_messages = [{"role": "system", "content": R.SYSTEM + "\n" + R.build_context(sources)}] + out_messages
            body.pop("tools", None)            # retrieved text can never trigger tools
            body.pop("tool_choice", None)
            body.pop("functions", None)

        fwd = {k: v for k, v in body.items() if k not in {"chat_id", "metadata", "features", "files", "session_id", "id"}}
        fwd["model"], fwd["messages"] = base_model, out_messages
        out_sens = max([q_sens] + [s.get("sensitivity", 1) for s in sources])   # strictest-label rule
        metrics.inc("chat_requests_total", recall=str(use_recall).lower())

        if body.get("stream"):
            return StreamingResponse(_stream(ctx, conv_id, fwd, model, base_model, sources, out_sens, t0),
                                     media_type="text/event-stream")
        try:
            r = await runtime.post("/chat/completions", json=fwd)
        except httpx.HTTPError as e:
            metrics.inc("runtime_errors_total")
            raise HTTPException(502, f"Model runtime unreachable: {type(e).__name__}")
        if r.status_code != 200:
            metrics.inc("runtime_errors_total")
            return Response(r.content, status_code=r.status_code, media_type="application/json")
        data = r.json()
        answer = data["choices"][0]["message"].get("content") or ""
        if sources:
            answer += R.citations_footer(answer, sources)
            data["choices"][0]["message"]["content"] = answer
        data["model"] = model
        await run_in_threadpool(_finish, ctx, conv_id, answer, base_model, out_sens, sources, t0, "ok")
        return data

    def _finish(ctx, conv_id, answer, base_model, sens, sources, t0, outcome):
        stored, _ = redact_secrets(answer)
        mid = store.add_message(ctx, conv_id, "assistant", stored, base_model, digest(base_model), sens,
                                embed_one(stored) if settings.history_index else None)
        if sources:
            store.record_retrievals(mid, [s for s in sources if s.get("kind") == "document"])
        store.audit(ctx.id, "chat", conv_id, {
            "model": base_model, "outcome": outcome, "latency_s": round(time.time() - t0, 3),
            "sources": [{"id": s["id"], "kind": s.get("kind"), "path": s.get("path"), "score": round(s.get("score", 0), 4)} for s in sources],
            "restricted_unlocked": ctx.unlocked}, digest(base_model))
        metrics.inc("chat_latency_seconds_sum", time.time() - t0)
        metrics.inc("chat_latency_seconds_count")
        if extras and getattr(extras, "after_answer", None):
            extras.after_answer(ctx, conv_id)

    async def _stream(ctx, conv_id, fwd, model, base_model, sources, sens, t0):
        parts: list[str] = []
        ok = False
        try:
            async with runtime.stream("POST", "/chat/completions", json=fwd) as r:
                if r.status_code != 200:
                    err = (await r.aread()).decode(errors="replace")[:500]
                    yield _sse({"error": {"message": f"runtime error {r.status_code}: {err}"}})
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        ok = True
                        break
                    try:
                        obj = json.loads(payload)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                        if delta:
                            parts.append(delta)
                        obj["model"] = model
                        yield _sse(obj)
                    except (ValueError, KeyError, IndexError):
                        yield f"data: {payload}\n\n"
            if sources:
                footer = R.citations_footer("".join(parts), sources)
                parts.append(footer)
                yield _sse({"object": "chat.completion.chunk", "model": model,
                            "choices": [{"index": 0, "delta": {"content": footer}, "finish_reason": None}]})
            yield "data: [DONE]\n\n"
        except httpx.HTTPError as e:
            metrics.inc("runtime_errors_total")
            yield _sse({"error": {"message": f"Model runtime unreachable: {type(e).__name__}"}})
        finally:
            if parts:
                await run_in_threadpool(_finish, ctx, conv_id, "".join(parts), base_model, sens, sources, t0,
                                        "ok" if ok else "partial")

    def _sse(obj) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    def _canned(answer: str, model: str, stream: bool):
        cid = "chatcmpl-gw-" + hashlib.sha1(answer.encode()).hexdigest()[:12]
        if not stream:
            return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}

        async def gen():
            yield _sse({"id": cid, "object": "chat.completion.chunk", "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}]})
            yield _sse({"id": cid, "object": "chat.completion.chunk", "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/me")
    async def me(request: Request):
        ctx = await auth(request)
        return {"id": ctx.id, "name": ctx.display_name, "role": ctx.role, "groups": ctx.groups,
                "restricted_unlocked": ctx.unlocked, "unlocked_until": await run_in_threadpool(store.unlocked_until, ctx.id)}

    # ------------------------------------------------------------------ history
    @app.get("/history/search")
    async def history_search(request: Request, q: str, k: int = 10, since: float | None = None, until: float | None = None):
        ctx = await auth(request)
        if since is None and until is None:
            since, until = R.date_window(q, time.time())
        qvec = await run_in_threadpool(embed_one, q)
        rows = await run_in_threadpool(store.search_history, ctx, q, qvec, k, since, until)
        await run_in_threadpool(store.audit, ctx.id, "history_search", "", {"q_len": len(q), "results": len(rows)})
        return {"results": rows}

    @app.get("/history/conversations")
    async def conversations(request: Request, since: float | None = None, until: float | None = None, limit: int = 100):
        ctx = await auth(request)
        rows = await run_in_threadpool(store.list_conversations, ctx, since, until, limit)
        for r in rows:
            r["sensitivity_name"] = SENSITIVITY_NAMES[r["sensitivity"]]
        return {"conversations": rows}

    @app.get("/history/conversations/{conv_id}")
    async def conversation(request: Request, conv_id: str):
        ctx = await auth(request)
        c = await run_in_threadpool(store.get_conversation, ctx, conv_id)
        if not c:
            raise HTTPException(404, "Not found (or Restricted and locked).")
        return c

    @app.delete("/history/conversations/{conv_id}")
    async def delete_conv(request: Request, conv_id: str):
        ctx = await auth(request)
        ok = await run_in_threadpool(store.delete_conversation, ctx, conv_id)
        await run_in_threadpool(store.audit, ctx.id, "delete_conversation", conv_id, {"ok": ok})
        if not ok:
            raise HTTPException(404, "Not found.")
        return {"deleted": conv_id}

    # ------------------------------------------------------------------ restricted unlock
    @app.post("/recall/unlock")
    async def unlock(request: Request):
        ctx = await auth(request)
        body = await request.json() if (await request.body()) else {}
        minutes = min(int(body.get("minutes", settings.restricted_unlock_minutes)), 8 * 60)
        reason = str(body.get("reason", ""))[:200]
        until = await run_in_threadpool(store.unlock_restricted, ctx, minutes)
        await run_in_threadpool(store.audit, ctx.id, "restricted_unlock", "", {"minutes": minutes, "reason": reason})
        return {"unlocked_until": until}

    @app.post("/recall/lock")
    async def lock(request: Request):
        ctx = await auth(request)
        await run_in_threadpool(store.lock_restricted, ctx)
        await run_in_threadpool(store.audit, ctx.id, "restricted_lock", "", {})
        return {"locked": True}

    @app.get("/recall/search")
    async def recall_search(request: Request, q: str, k: int = 8, collection: str | None = None):
        ctx = await auth(request)
        filters = {}
        if collection:
            col = await run_in_threadpool(store.get_collection, collection)
            if not col:
                raise HTTPException(404, "No such collection.")
            filters["collection_ids"] = [col["id"]]
        res = await run_in_threadpool(R.retrieve, store, ctx, embedder, reranker, q, k=k, now=time.time(), filters=filters)
        await run_in_threadpool(store.audit, ctx.id, "recall_search", "", {"results": [r["id"] for r in res]})
        return {"results": [{k2: v for k2, v in r.items() if k2 in {"id", "kind", "path", "title", "heading_path", "text", "score",
                                                                       "collection", "modified_at", "doc_modified_at", "sensitivity"}}
                            for r in res], "confident": bool(res) and res[0]["score"] >= settings.recall_min_score}

    # ------------------------------------------------------------------ memory cards
    @app.get("/memory")
    async def memory_list(request: Request, status: str | None = None):
        ctx = await auth(request)
        return {"cards": await run_in_threadpool(store.list_memory, ctx, status)}

    @app.post("/memory")
    async def memory_add(request: Request):
        ctx = await auth(request)
        body = await request.json()
        text, counts = redact_secrets(str(body.get("text", "")).strip())
        if not text:
            raise HTTPException(400, "text required")
        if counts:
            raise HTTPException(400, "Memory cards cannot contain secrets.")
        sens, _ = sensitivity_for(text, 1)
        cid = await run_in_threadpool(store.propose_memory, ctx, text, body.get("source_message_id"), sens)
        if body.get("approve"):
            await run_in_threadpool(lambda: store.set_memory(ctx, cid, status="approved"))
        await run_in_threadpool(store.audit, ctx.id, "memory_add", cid, {"approved": bool(body.get("approve"))})
        return {"id": cid}

    @app.patch("/memory/{card_id}")
    async def memory_edit(request: Request, card_id: str):
        ctx = await auth(request)
        body = await request.json()
        text = body.get("text")
        if text is not None:
            text, counts = redact_secrets(text)
            if counts:
                raise HTTPException(400, "Memory cards cannot contain secrets.")
        ok = await run_in_threadpool(lambda: store.set_memory(ctx, card_id, text=text, status=body.get("status")))
        if not ok:
            raise HTTPException(404, "Not found.")
        await run_in_threadpool(store.audit, ctx.id, "memory_edit", card_id, {"status": body.get("status")})
        return {"ok": True}

    @app.delete("/memory/{card_id}")
    async def memory_delete(request: Request, card_id: str):
        ctx = await auth(request)
        if not await run_in_threadpool(store.delete_memory, ctx, card_id):
            raise HTTPException(404, "Not found.")
        await run_in_threadpool(store.audit, ctx.id, "memory_delete", card_id, {})
        return {"deleted": card_id}

    # ------------------------------------------------------------------ collections & documents
    @app.get("/collections")
    async def collections(request: Request):
        ctx = await auth(request)
        return {"collections": await run_in_threadpool(store.collections_for, ctx)}

    @app.post("/collections")
    async def collection_create(request: Request):
        ctx = await auth(request)
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name or name.startswith("personal:"):
            raise HTTPException(400, "Invalid collection name.")
        if store.get_collection(name) and not ctx.is_admin:
            raise HTTPException(409, "Collection exists.")
        cid = await run_in_threadpool(store.create_collection, name, ctx.id, sens_level(body.get("sensitivity", "personal")),
                                      body.get("retention_days"), body.get("purpose", ""))
        await run_in_threadpool(store.audit, ctx.id, "collection_create", cid, {"name": name})
        return {"id": cid}

    @app.post("/collections/{cid}/grants")
    async def collection_grant(request: Request, cid: str):
        ctx = await auth(request)
        col = await run_in_threadpool(store.get_collection, cid)
        if not col or (col["owner_id"] != ctx.id and not ctx.is_admin):
            raise HTTPException(403, "Only the owner or an admin can share a collection.")
        body = await request.json()
        principal, perm = body.get("principal", ""), body.get("permission", "read")
        if not principal.startswith(("user:", "group:")):
            raise HTTPException(400, "principal must be user:<id> or group:<name>")
        if body.get("revoke"):
            await run_in_threadpool(store.revoke, col["id"], principal)
        else:
            await run_in_threadpool(store.grant, col["id"], principal, perm)
        await run_in_threadpool(store.audit, ctx.id, "collection_grant", col["id"], {"principal": principal, "permission": perm,
                                                                                     "revoke": bool(body.get("revoke"))})
        return {"ok": True}

    @app.put("/collections/{cid}/documents")
    async def upload(request: Request, cid: str, path: str):
        ctx = await auth(request)
        col = await run_in_threadpool(store.get_collection, cid)
        if not col or not await run_in_threadpool(store.can_write, ctx, col["id"]):
            raise HTTPException(403, "No write access to this collection.")
        data = await request.body()
        if len(data) > 200 * 1024 * 1024:
            raise HTTPException(413, "File too large (200 MB max).")
        from .ingest import Ingestor

        ing = Ingestor(store, embedder, settings)
        res = await run_in_threadpool(ing.ingest_bytes, col, path, data)
        await run_in_threadpool(store.audit, ctx.id, "document_upload", col["id"], {"path": path, **res})
        return res

    @app.delete("/collections/{cid}/documents")
    async def delete_document(request: Request, cid: str, path: str):
        ctx = await auth(request)
        col = await run_in_threadpool(store.get_collection, cid)
        if not col or not await run_in_threadpool(store.can_write, ctx, col["id"]):
            raise HTTPException(403, "No write access to this collection.")
        ok = await run_in_threadpool(store.delete_document, col["id"], path)
        await run_in_threadpool(store.audit, ctx.id, "document_delete", col["id"], {"path": path, "ok": ok})
        return {"deleted": ok}

    # ------------------------------------------------------------------ export & delete (one command each)
    @app.get("/me/export")
    async def export(request: Request, format: str = "json"):
        ctx = await auth(request)
        full = UserCtx(ctx.id, ctx.role, ctx.groups, ctx.display_name, True)   # your own data, all of it
        data = await run_in_threadpool(store.export_user, full)
        await run_in_threadpool(store.audit, ctx.id, "export", "", {"format": format, "conversations": len(data["conversations"])})
        if format == "markdown":
            from .exporter import to_markdown

            return PlainTextResponse(to_markdown(data), media_type="text/markdown")
        return data

    @app.post("/me/delete")
    async def delete_me(request: Request):
        ctx = await auth(request)
        body = await request.json()
        if body.get("confirm") != "DELETE MY DATA":
            raise HTTPException(400, 'Send {"confirm": "DELETE MY DATA"} to proceed.')
        res = await run_in_threadpool(store.delete_user_data, ctx)
        await run_in_threadpool(store.audit, ctx.id, "delete_user_data", "", res)
        return res

    # ------------------------------------------------------------------ admin
    @app.get("/admin/audit")
    async def audit(request: Request, limit: int = 200, actor: str | None = None, action: str | None = None):
        ctx = await auth(request)
        require_admin(ctx)
        return {"entries": await run_in_threadpool(store.list_audit, limit, actor, action)}

    @app.get("/admin/stats")
    async def stats(request: Request):
        ctx = await auth(request)
        require_admin(ctx)
        return await run_in_threadpool(store.stats)

    @app.post("/admin/retention")
    async def retention(request: Request):
        ctx = await auth(request)
        require_admin(ctx)
        body = await request.json() if (await request.body()) else {}
        res = await run_in_threadpool(store.apply_retention, body.get("conversation_days"))
        await run_in_threadpool(store.audit, ctx.id, "retention_run", "", res)
        return res

    # ------------------------------------------------------------------ web UI (phase 3)
    @app.get("/ui", response_class=HTMLResponse)
    async def ui():
        index = UI_DIR / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8") if index.exists() else "<p>UI not built.</p>")

    app.state.extras = extras
    if extras and getattr(extras, "register", None):
        extras.register(app)

    return app


def main():  # pragma: no cover - entry point for `python -m gateway.app`
    import uvicorn

    uvicorn.run("gateway.app:create_app", factory=True, host="0.0.0.0", port=8080)


if __name__ == "__main__":  # pragma: no cover
    main()
