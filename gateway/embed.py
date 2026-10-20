"""Embeddings and reranking via the local runtime (OpenAI-compatible), with an offline fallback."""

from __future__ import annotations

import hashlib
import math
import re

import httpx
import numpy as np

TOKEN = re.compile(r"[A-Za-z0-9]+")
STOPWORDS = frozenset("""a an and are as at be by can did do does for from has have how i in is it its me my of on or our
so that the their them there these they this to was we were what when where which who why will with you your""".split())


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder.

    Used in tests and as a degraded mode when no embedding model is available: keyword-level
    similarity only, but it never sends text anywhere and never fails.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.model = f"hash-{dim}"

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for tok in TOKEN.findall(t.lower()):
                if len(tok) < 2 or tok in STOPWORDS:
                    continue
                h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return out


class HttpEmbedder:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for i in range(0, len(texts), 32):
            batch = texts[i:i + 32]
            r = self.client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": batch},
                                 headers=self.headers)
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            for d in data:
                v = np.asarray(d["embedding"], dtype=np.float32)
                n = np.linalg.norm(v)
                out.append(v / n if n else v)
        return out


class Reranker:
    """llama.cpp / Jina-style POST {base}/rerank -> {results:[{index, relevance_score}]}."""

    def __init__(self, base_url: str | None, model: str, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.model = model
        self.client = httpx.Client(timeout=60.0, transport=transport) if base_url else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def rerank(self, query: str, docs: list[str]) -> list[float] | None:
        if not self.client or not docs:
            return None
        try:
            r = self.client.post(f"{self.base_url}/rerank", json={"model": self.model, "query": query, "documents": docs})
            r.raise_for_status()
            scores = [0.0] * len(docs)
            for item in r.json()["results"]:
                s = item.get("relevance_score", item.get("score", 0.0))
                scores[item["index"]] = 1 / (1 + math.exp(-s)) if s > 1 or s < 0 else s   # logits -> 0..1
            return scores
        except (httpx.HTTPError, KeyError, ValueError):
            return None   # degrade to fusion scores, never fail the request


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
