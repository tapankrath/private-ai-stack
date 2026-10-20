"""Gateway settings, read from environment variables (12-factor) with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _secret(name: str) -> str | None:
    """VAR or VAR_FILE (Docker secrets). Never logged."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.environ.get(f"{name}_FILE")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip()
    return None


@dataclass
class Settings:
    # storage
    database_url: str = "sqlite:///data/gateway.db"
    # model runtime (OpenAI-compatible): Ollama /v1, vLLM /v1, llama.cpp /v1
    runtime_url: str = "http://ollama:11434/v1"
    runtime_key: str | None = None
    default_model: str = "qwen3:8b"
    fast_model: str | None = None              # summaries; falls back to default_model
    embed_url: str | None = None               # defaults to runtime_url
    embed_model: str = "nomic-embed-text"
    embedder: str = "http"                     # http (runtime embeddings) | hash (offline keyword-level fallback)
    embed_dim: int = 768
    rerank_url: str | None = None              # llama.cpp-style /v1/rerank; optional
    rerank_model: str = "bge-reranker-base"
    # auth: single | proxy_header | oidc
    auth_mode: str = "single"
    gateway_key: str | None = None             # shared secret the UI / proxy presents
    single_user_id: str = "owner"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_groups_claim: str = "groups"
    admin_groups: tuple = ("admins", "authentik Admins")
    abliterated_groups: tuple = ()             # groups allowed to use a registered abliterated model (ADR-007)
    # recall
    recall_suffix: str = " + Recall"
    recall_top_k: int = 6
    recall_min_score: float = 0.30             # below this: "I don't know"
    restricted_unlock_minutes: int = 60
    # history
    history_index: bool = True
    summary_every_messages: int = 12
    # policy
    policy_path: str | None = None
    air_gapped: bool = False
    # phase 5
    searxng_url: str | None = None
    fetcher_url: str | None = None
    whisper_url: str | None = None
    cloud_burst_url: str | None = None
    cloud_burst_key: str | None = None
    cloud_burst_model: str | None = None
    cloud_burst_zero_retention: bool = False
    # misc
    data_dir: str = "data"
    model_pins: str | None = None              # generated/model-pins.txt (ollama list output)
    background_tasks: bool = True              # summaries in a background thread (False = inline, for tests)
    request_timeout_s: float = 300.0
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        for f in s.__dataclass_fields__:
            env = "GATEWAY_" + f.upper()
            if f in {"runtime_key", "gateway_key", "cloud_burst_key"}:
                v = _secret(env)
                if v:
                    setattr(s, f, v)
                continue
            if env in os.environ:
                cur = getattr(s, f)
                raw = os.environ[env]
                if isinstance(cur, bool):
                    setattr(s, f, _bool(env, cur))
                elif isinstance(cur, int):
                    setattr(s, f, int(raw))
                elif isinstance(cur, float):
                    setattr(s, f, float(raw))
                elif isinstance(cur, tuple):
                    setattr(s, f, tuple(x.strip() for x in raw.split(",") if x.strip()))
                else:
                    setattr(s, f, raw)
        return s
