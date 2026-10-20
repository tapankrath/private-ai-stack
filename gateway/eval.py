"""Golden-set evaluation through the Gateway API (the same path users take).

golden.yaml:
  questions:
    - q: "What is our refund window?"
      expect_paths: ["policies/refunds.md"]     # any of these must be retrieved / cited
    - q: "What is the CEO's shoe size?"
      answerable: false                         # the right answer is "I don't know"

Metrics (all reported, none invented):
  recall_at_k          share of answerable questions whose expected source is in the top k results
  citation_accuracy    share of answerable answers whose cited sources include an expected path
  faithfulness         share of answer sentences whose content words mostly appear in the cited
                       source text (a lexical heuristic, labelled as such)
  idk_precision        share of "I don't know" answers that were for unanswerable questions
  idk_recall           share of unanswerable questions answered with "I don't know"
  latency_p50_s        median end-to-end answer latency
"""

from __future__ import annotations

import hashlib
import re
import statistics
import time
from pathlib import Path

import yaml

IDK = re.compile(r"\bI don't know\b|\bI do not know\b", re.I)
WORD = re.compile(r"[a-z0-9]{4,}")


def load_golden(path: str) -> tuple[list[dict], str]:
    raw = Path(path).read_bytes()
    return yaml.safe_load(raw)["questions"], hashlib.sha256(raw).hexdigest()


def _faithful(answer: str, cited_texts: list[str]) -> float | None:
    body = answer.split("\n---\nSources:")[0]
    sents = [s for s in re.split(r"(?<=[.!?])\s+", body) if len(WORD.findall(s.lower())) >= 3]
    if not sents or not cited_texts:
        return None
    vocab = set(WORD.findall(" ".join(cited_texts).lower()))
    ok = 0
    for s in sents:
        words = WORD.findall(re.sub(r"\[\d+\]", "", s).lower())
        if words and sum(w in vocab for w in words) / len(words) >= 0.5:
            ok += 1
    return ok / len(sents)


def run_eval(client, model: str, questions: list[dict], k: int = 5, headers: dict | None = None) -> dict:
    """`client` is any httpx-compatible client pointed at the Gateway (TestClient works too)."""
    headers = headers or {}
    hits, cites, faith, lat = [], [], [], []
    idk_tp = idk_fp = idk_fn = 0
    rows = []
    for item in questions:
        q, answerable = item["q"], item.get("answerable", True)
        expect = set(item.get("expect_paths", []))
        sr = client.get("/recall/search", params={"q": q, "k": k}, headers=headers).json()["results"]
        paths = [r.get("path") for r in sr]
        t0 = time.time()
        resp = client.post("/v1/chat/completions", headers=headers, json={
            "model": model, "messages": [{"role": "user", "content": q}], "stream": False}).json()
        lat.append(time.time() - t0)
        answer = resp["choices"][0]["message"]["content"]
        said_idk = bool(IDK.search(answer))
        cited_paths = re.findall(r"^\[\d+\] (.+?) — ", answer, re.M)
        row = {"q": q, "answerable": answerable, "retrieved": paths, "idk": said_idk, "cited": cited_paths}
        if answerable:
            hit = any(p in expect for p in paths)
            hits.append(hit)
            cites.append(any(p in expect for p in cited_paths))
            idk_fn += 0
            if said_idk:
                idk_fp += 1
            texts = [r["text"] for r in sr if r.get("path") in cited_paths]
            f = _faithful(answer, texts)
            if f is not None:
                faith.append(f)
            row["hit"] = hit
        else:
            if said_idk:
                idk_tp += 1
            else:
                idk_fn += 1
        rows.append(row)
    n_unans = sum(1 for q in questions if not q.get("answerable", True))
    return {
        "model": model,
        "questions": len(questions),
        "recall_at_k": round(sum(hits) / len(hits), 3) if hits else None,
        "k": k,
        "citation_accuracy": round(sum(cites) / len(cites), 3) if cites else None,
        "faithfulness_lexical": round(sum(faith) / len(faith), 3) if faith else None,
        "idk_precision": round(idk_tp / (idk_tp + idk_fp), 3) if (idk_tp + idk_fp) else None,
        "idk_recall": round(idk_tp / n_unans, 3) if n_unans else None,
        "latency_p50_s": round(statistics.median(lat), 3) if lat else None,
        "rows": rows,
    }


def no_worse(candidate: dict, current: dict | None, tolerance: float = 0.0) -> tuple[bool, list[str]]:
    """Promotion rule (ADR-008): the candidate may not be worse on any quality metric."""
    if not current:
        return True, ["no current version: first promotion"]
    problems = []
    for key in ("recall_at_k", "citation_accuracy", "faithfulness_lexical", "idk_recall"):
        c, b = candidate.get(key), current.get(key)
        if b is not None and (c is None or c + tolerance < b):
            problems.append(f"{key}: {c} < current {b}")
    return not problems, problems or ["no regression"]
