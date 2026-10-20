"""Store factory: sqlite:///path (tier 1) or postgresql://... (tiers 2-3)."""

from __future__ import annotations

from .base import Store, UserCtx, rrf  # noqa: F401


def open_store(url: str) -> Store:
    if url.startswith("sqlite:///"):
        from .sqlite import SQLiteStore

        s = SQLiteStore(url[len("sqlite:///"):] or ":memory:")
    elif url == "sqlite://" or url == "sqlite:///:memory:":
        from .sqlite import SQLiteStore

        s = SQLiteStore(":memory:")
    elif url.startswith(("postgresql://", "postgres://")):
        from .postgres import PostgresStore

        s = PostgresStore(url)
    else:
        raise ValueError(f"unsupported database url: {url.split(':')[0]}")
    s.init()
    return s
