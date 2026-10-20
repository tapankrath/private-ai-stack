"""Hooks around the chat path: memory proposals and rolling summaries (Phase 3),
plus Phase 5 features registered by gateway.phase5 when enabled."""

from __future__ import annotations

import re
import threading

import httpx

from .redact import redact_secrets
from .store import Store, UserCtx

REMEMBER = re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?[:,]?\s+(.{3,400})$", re.I | re.S)

SUMMARY_PROMPT = ("Summarize this conversation in at most 6 bullet points: decisions, facts, open questions. "
                  "Do not include secrets. Write in the user's language.")


class Extensions:
    def __init__(self, settings, store: Store, embedder, transport, metrics):
        self.settings, self.store, self.embedder, self.metrics = settings, store, embedder, metrics
        headers = {"Authorization": f"Bearer {settings.runtime_key}"} if settings.runtime_key else {}
        self.sync_runtime = httpx.Client(base_url=settings.runtime_url.rstrip("/"), timeout=settings.request_timeout_s,
                                         transport=transport, headers=headers)
        self.phase5 = None

    # ------------------------------------------------------------------ memory proposals
    def on_user_message(self, ctx: UserCtx, conv_id: str, msg_id: str, stored_text: str, sens: int) -> None:
        m = REMEMBER.match(stored_text.strip())
        if m and "[REDACTED" not in m.group(1):
            self.store.propose_memory(ctx, m.group(1).strip().rstrip("."), msg_id, sens)
            self.store.audit(ctx.id, "memory_proposed", conv_id, {"source_message_id": msg_id})

    # ------------------------------------------------------------------ rolling summaries
    def after_answer(self, ctx: UserCtx, conv_id: str) -> None:
        every = self.settings.summary_every_messages
        if not every or self.store.message_count(ctx, conv_id) % every != 0:
            return
        if self.settings.background_tasks:
            threading.Thread(target=self.summarize, args=(ctx, conv_id), daemon=True).start()
        else:
            self.summarize(ctx, conv_id)

    def summarize(self, ctx: UserCtx, conv_id: str) -> str | None:
        full = UserCtx(ctx.id, ctx.role, ctx.groups, ctx.display_name, True)
        conv = self.store.get_conversation(full, conv_id)
        if not conv:
            return None
        msgs = conv["messages"][-60:]
        transcript = "\n".join(f"{m['role']}: {m['content'][:2000]}" for m in msgs)
        try:
            r = self.sync_runtime.post("/chat/completions", json={
                "model": self.settings.fast_model or self.settings.default_model, "stream": False,
                "messages": [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": transcript}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError):
            self.metrics.inc("summary_failures_total")
            return None
        text, _ = redact_secrets(text)
        # strictest-label rule: the summary is as sensitive as the most sensitive message it covers
        sens = max(m["sensitivity"] for m in msgs)
        self.store.upsert_summary(ctx, conv_id, text, [m["id"] for m in msgs], sens)
        self.store.audit(ctx.id, "summary", conv_id, {"messages": len(msgs), "sensitivity": sens})
        return text

    # ------------------------------------------------------------------ phase 5 delegation
    def register(self, app) -> None:
        from .phase5 import Phase5

        self.phase5 = Phase5(self.settings, self.store, app, self.metrics)
        self.phase5.register()

    def extra_models(self, base_ids: list[str]):
        return self.phase5.extra_models(base_ids) if self.phase5 else []

    def handles(self, model: str) -> bool:
        return bool(self.phase5 and self.phase5.handles(model))

    async def handle_chat(self, request, ctx, body):
        return await self.phase5.handle_chat(request, ctx, body)
