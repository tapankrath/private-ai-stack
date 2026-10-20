"""Local-LLM helpers: EXPLAIN (word the engine's result) and INTAKE (questions -> profile.yaml).

Rules enforced in code, not just in the prompt:
  * Only loopback endpoints are allowed (no egress).
  * EXPLAIN output is rejected (and the deterministic recommendation.md is used instead) if it
    names a catalog model or capability the engine did not select, calls local "cheaper" when
    the TCO says otherwise, mentions abliteration that was never requested, or drops a
    required section.
  * INTAKE output must be a schema-valid profile; the LLM never chooses components.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yaml

from .config import AdvisorConfig
from .profile import ProfileError, SCHEMA_PATH, profile_from_dict

PROMPT = Path(__file__).parent / "prompts" / "system_prompt.md"
REQUIRED_HEADERS = ["# Your Private AI Setup", "## In One Sentence", "## What You Need", "## What You Don't Need",
                    "## Why Local (Beyond Price)", "## Your Hardware Fit", "## Privacy Settings",
                    "## Cost Comparison (Honest View)", "## Setup Steps", "## Things to Double-Check"]
CHEAPER = re.compile(
    r"\blocal(ly)?\b[^.\n]{0,60}\b(is|are|will be|would be|comes out|works out|costs)\b[^.\n]{0,20}\b(cheaper|cheapest|less expensive|less)\b"
    r"|\b(cheaper|less expensive)\b (to run |running )?(it |this )?locally\b"
    r"|\blocal(ly)?\b[^.\n]{0,40}\bsaves? (you )?money\b", re.I)
CAPABILITY_WORDS = {"vision": [r"vision[- ]language model", r"\bvlm\b"], "stt": [r"whisper", r"speech[- ]to[- ]text model"],
                    "code": [r"code[- ]tuned model", r"coding model", r"\bcoder\b"]}


class EgressRefused(RuntimeError):
    pass


def assert_loopback(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise EgressRefused(f"Refusing to send anything to {host!r}: only localhost endpoints are allowed.")


def _chat(base_url: str, model: str, messages: list[dict], timeout: int = 180) -> str:
    assert_loopback(base_url)
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


# ------------------------------------------------------------------ EXPLAIN

def engine_result(rec: dict) -> dict:
    """The facts the explainer may use (ENGINE_RESULT in the system prompt)."""
    p = rec["plan"]
    sel = ([p["general"]] if p["general"] else []) + p["specialists"] + p["aux"]
    return {
        "tier": rec["tier"], "runtime": p["runtime_name"],
        "selected_models": [{"name": m["name"], "role": m["role"], "architecture": m.get("architecture"),
                             "total_params_b": m.get("total_params_b"), "active_params_b": m.get("active_params_b"),
                             "quant": m.get("quant"), "license": m.get("license")} for m in sel],
        "enabled_capabilities": list(rec["capabilities"]["enabled"]),
        "excluded_capabilities": [{k: v} for k, v in rec["capabilities"]["excluded"].items()],
        "services": [c["name"] for c in rec["components"]["included"]],
        "excluded_services": [{"name": e["name"], "reason": e["reason"]} for e in rec["components"]["excluded"]],
        "memory_fit": {"used_gb": p["memory_used_gb"], "usable_gb": p["usable_memory_gb"], "use_pct": p["memory_use_pct"]},
        "estimates": {"tokens_per_sec": (p["general"] or {}).get("tokens_per_sec_est"), "ttft_s": p.get("ttft_s_est"),
                      "context_tokens": (p["general"] or {}).get("context_tokens"), "disk_gb": rec["disk_estimate_gb"]},
        "upgrade_options": [{"hardware": o["name"], "general_model": o["general_model"], "price_usd": o["price_usd"]}
                            for o in rec["upgrade_options"] + rec["good_better_best"]],
        "procurement": (rec.get("procurement") or {}).get("name"),
        "tco": {k: v["total_usd"] for k, v in rec["tco"]["options"].items()} | {"verdict": rec["tco"]["verdict"],
                                                                                "local_is_cheapest": rec["tco"]["local_is_cheapest"]},
        "why_local": rec["why_local"], "privacy": rec["privacy"], "licenses": rec["licenses"],
        "cloud_burst": rec["cloud_burst"],
        "customization": {k: {"status": v["status"], "approved": v["status"] == "approved",
                              **{x: v[x] for x in ("teacher", "student", "base_model") if x in v}}
                          for k, v in rec["customization"].items()},
        "warnings": rec["warnings"],
    }


def build_explain_messages(rec: dict, profile_raw: dict, cfg: AdvisorConfig) -> list[dict]:
    er = engine_result(rec)
    ids = [m["id"] for m in ([rec["plan"]["general"]] if rec["plan"]["general"] else []) + rec["plan"]["specialists"] + rec["plan"]["aux"]]
    entries = [{k: v for k, v in cfg.model(i).items() if k not in {"sources"}} for i in ids]
    raw = {k: v for k, v in profile_raw.items() if k != "expected"}
    user = ("MODE: EXPLAIN\n\nPROFILE:\n```yaml\n" + yaml.safe_dump(raw, sort_keys=False) + "```\n\n"
            "ENGINE_RESULT:\n```json\n" + json.dumps(er, indent=1) + "\n```\n\n"
            "CATALOG_ENTRIES:\n```json\n" + json.dumps(entries, indent=1) + "\n```")
    return [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")}, {"role": "user", "content": user}]


def _mentions(alias: str, text: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-]|\.\w)", text) is not None


def _aliases(m: dict) -> set[str]:
    name = m["name"].lower()
    short = re.sub(r"\s*(instruct|\(.*\))\s*$", "", name).strip()
    return {name, short, m["id"].lower(), m["family"].lower()}


def forbidden_mentions(text: str, rec: dict, cfg: AdvisorConfig) -> list[str]:
    """Catalog models named in the text but absent from the engine result."""
    facts = json.dumps(engine_result(rec)).lower()
    low = text.lower()
    bad = []
    for m in cfg.models:
        for alias in _aliases(m):
            if not _mentions(alias, facts) and _mentions(alias, low):
                bad.append(m["name"])
                break
    return bad


def check_explanation(text: str, rec: dict, cfg: AdvisorConfig) -> list[str]:
    problems = []
    bad = forbidden_mentions(text, rec, cfg)
    if bad:
        problems.append(f"names models the engine did not select: {', '.join(bad)}")
    if not rec["tco"]["local_is_cheapest"] and CHEAPER.search(text):
        problems.append("calls local cheaper, but the cost comparison says it is not")
    if rec["customization"]["abliteration"]["status"] == "not_requested" and re.search(r"abliterat", text, re.I):
        problems.append("mentions abliteration, which was not requested")
    enabled = rec["capabilities"]["enabled"]
    low = text.lower()
    dont = low.split("## what you don't need")[0] if "## what you don't need" in low else low
    for cap, pats in CAPABILITY_WORDS.items():
        if cap not in enabled and any(re.search(pt, dont) for pt in pats):
            problems.append(f"presents excluded capability '{cap}' outside 'What You Don't Need'")
    missing = [h for h in REQUIRED_HEADERS if h.lower() not in low]
    if missing:
        problems.append(f"missing sections: {', '.join(missing)}")
    return problems


def explain(rec: dict, cfg: AdvisorConfig, base_url: str, model: str, profile_raw: dict | None = None,
            timeout: int = 180) -> tuple[str, bool, list[str]]:
    """Return (text, used_llm, problems). Falls back to the deterministic markdown on any problem."""
    from .render import render_markdown

    assert_loopback(base_url)
    try:
        text = _chat(base_url, model, build_explain_messages(rec, profile_raw or {}, cfg), timeout)
    except Exception as exc:  # network, JSON, schema
        return render_markdown(rec), False, [f"local model unavailable: {exc}"]
    problems = check_explanation(text, rec, cfg)
    if problems:
        return render_markdown(rec), False, problems
    return text, True, []


# ------------------------------------------------------------------ INTAKE

YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)


def run_intake(base_url: str, model: str, ask: Callable[[str], str] = input, detected: dict | None = None,
               max_turns: int = 20, say: Callable[[str], None] = print) -> dict:
    """Plain-language Q&A with a local model until it emits a schema-valid profile."""
    assert_loopback(base_url)
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    msgs = [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
            {"role": "user", "content": "MODE: INTAKE\n\nDETECTED_HARDWARE:\n```yaml\n" + yaml.safe_dump(detected or {"detection": "failed"})
             + "```\n\nPROFILE_SCHEMA:\n```yaml\n" + schema_text + "```\n\nStart the interview."}]
    for _ in range(max_turns):
        reply = _chat(base_url, model, msgs)
        msgs.append({"role": "assistant", "content": reply})
        m = YAML_BLOCK.search(reply)
        if m:
            try:
                raw = yaml.safe_load(m.group(1))
                profile_from_dict(raw, detected)
                return raw
            except (ProfileError, yaml.YAMLError, TypeError, KeyError) as exc:
                msgs.append({"role": "user", "content": f"That profile is not valid:\n{exc}\nFix it and output only the yaml block."})
                continue
        say(reply)
        msgs.append({"role": "user", "content": ask("> ")})
    raise RuntimeError("Intake did not produce a valid profile; use `advise wizard` instead.")
