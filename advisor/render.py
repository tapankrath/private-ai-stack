"""Turn a recommendation into recommendation.md (EXPLAIN format, deterministic) and config files."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

QUANT_HINT = {
    "Q4_K_M": "default tag (Q4_K_M)",
    "Q5_K_M": "pick the q5_K_M tag on the model's Ollama page",
    "Q8_0": "pick the q8_0 tag on the model's Ollama page",
}
ROLE_LABEL = {"general": "General assistant", "code": "Coding help", "vision": "Photo and chart questions",
              "embeddings": "Search by meaning", "reranker": "Better search ranking", "stt": "Speech to text",
              "fast_small": "Quick summaries and routing"}
ROLE_DOES = {"general": "Answers questions, writes and summarizes",
             "code": "Writes and explains code", "vision": "Answers questions about photos, charts and diagrams",
             "embeddings": "Finds documents and past chats by meaning, not just keywords",
             "reranker": "Puts the most relevant passages first", "stt": "Turns meetings and voice notes into text",
             "fast_small": "Summarizes conversations so the main model stays free"}
EXCLUDE_IDS_SHOWN_ONCE = {"stt", "whisper"}


def _gb(x) -> str:
    return f"{x:.1f} GB"


def _usd(x) -> str:
    return f"${x:,.0f}"


def _models(rec):
    p = rec["plan"]
    return ([p["general"]] if p["general"] else []) + p["specialists"] + p["aux"]


def _model_why(m: dict) -> str:
    if "memory" in m:
        tps = f"~{m['tokens_per_sec_est']:g} tok/s (estimate)" if m.get("tokens_per_sec_est") else "speed unknown"
        moe = (f"; MoE: {m['total_params_b']:g}B total sets memory, {m['active_params_b']:g}B active sets speed"
               if m.get("architecture") == "moe" else "")
        where = "" if m.get("resident", True) else "; loaded on demand"
        covers = f"; also covers {', '.join(m['covers'])}" if m.get("covers") else ""
        return (f"{m['quant']}, {m['context_tokens']:,}-token context, {_gb(m['memory']['total_gb'])}, {tps}{moe}{where}{covers}")
    return f"{_gb(m['memory_gb'])}, small and local"


def in_one_sentence(rec: dict) -> str:
    g = rec["plan"]["general"]
    if not g:
        return "No listed machine can run this profile well; see Options If You Want More."
    where = ("on your computer" if rec["tier"] == 1 else "on one server on your network" if rec["tier"] == 2
             else "on your own servers")
    extras = []
    roles = {m["role"] for m in _models(rec)}
    if "embeddings" in roles:
        extras.append("searches your own files and past conversations")
    if "stt" in roles:
        extras.append("transcribes meetings")
    if "code" in roles:
        extras.append("helps with code")
    if "vision" in rec["capabilities"]["enabled"]:
        extras.append("answers questions about photos")
    if len(extras) > 1:
        extras = [", ".join(extras[:-1]) + " and " + extras[-1]]
    tail = ("; it also " + extras[0]) if extras else ""
    size = "small" if g["total_params_b"] <= 9 else "mid-size" if g["total_params_b"] <= 40 else "large"
    return f"One {size} general AI model {where} handles your questions and writing{tail}. Nothing leaves your control."


def setup_steps(rec: dict) -> list[str]:
    skill = rec["skill"]
    native = rec["plan"]["runtime_mode"] == "native"
    air = rec["privacy"]["egress"].startswith("None, ever")
    pull = ("Copy the models from the offline drive (see models-ollama.txt)" if air else
            "Run `make pull-native` to download only the listed models" if native else
            "Run `make pull` to download only the listed models (the only step that goes online)")
    if skill == "non_technical":
        steps = ["Turn on disk encryption (FileVault on Mac, BitLocker on Windows).",
                 "Install Docker Desktop" + (" and the Ollama app" if native else "") + ".",
                 "Open a terminal in this folder and run `make secrets`.",
                 pull + ".",
                 "Run `make up`, then open https://localhost:8443 in your browser."]
    elif skill == "power_user":
        steps = ["Enable full-disk encryption and a default-deny outbound firewall.",
                 ("Install Ollama natively (Docker on macOS cannot use the GPU); " if native else "") + "install Docker + Compose.",
                 "`cp generated/.env .env && make secrets`.",
                 pull + ".",
                 "`make up` (UI on https://localhost:8443)."]
    else:
        steps = ["Full-disk encryption on the host; nftables/ufw `default deny outgoing`; record image digests in .env.",
                 "Review generated/.env (COMPOSE_PROFILES, runtime settings) and generated/config/policy.yaml.",
                 "`make secrets` (writes ./secrets/* and .env.secrets, mode 600).",
                 pull + "; record model digests (generated/model-pins.txt).",
                 "Bind Caddy to the LAN IP (BIND_ADDRESS); only port 8443 is published. Ollama 11434, vLLM 8000, Postgres 5432 stay on the internal network.",
                 "`make up`; once it is running, `make egress-test` must pass before real data goes in."]
    return steps


def render_markdown(rec: dict, diff_lines: list[str] | None = None) -> str:
    p = rec["plan"]
    L: list[str] = ["# Your Private AI Setup", "",
                    f"> {rec['estimates_note']} Model names are catalog examples; check licenses before use.", ""]

    L += ["## In One Sentence", "", in_one_sentence(rec), ""]
    if any(b["blocker"] == "cost" for b in rec["blockers"]):
        L += ["Cost is a concern you raised, so start with **Cost Comparison (Honest View)** below.", ""]
    for b in rec["blockers"]:
        L.append(f"- *{b['blocker'].replace('_', ' ').capitalize()}*: {b['guidance']}")
    if rec["blockers"]:
        L.append("")

    if diff_lines:
        L += ["**Changes since last run:**", ""] + [f"- {d}" for d in diff_lines] + [""]

    # ------------------------------------------------------------ What You Need
    L += ["## What You Need", "", "| Component | What it does for you | Model / tool | Why this one |", "|---|---|---|---|"]
    for m in _models(rec):
        L.append(f"| {ROLE_LABEL.get(m['role'], m['role'])} | {ROLE_DOES.get(m['role'], '')} | {m['name']} | {_model_why(m)} |")
    for c in rec["components"]["included"]:
        alt = f" Alternatives: {', '.join(c['alternatives'])}." if c["alternatives"] else ""
        L.append(f"| {c['name']} | {c['reason']} | {c['id']} | {('Default.' + alt).strip()} |")
    L.append("")
    if rec.get("procurement"):
        pr = rec["procurement"]
        L += [f"**Hardware to buy:** {pr['name']} ({_usd(pr['price_usd'][0])}-{_usd(pr['price_usd'][1])}, estimate; "
              f"price last verified: {pr['last_verified'] or 'never'}). It is the smallest listed option that meets this profile.", ""]
    if rec["licenses"]:
        L += ["### Licenses (commercial use)", ""]
        for ln in rec["licenses"]:
            osi = "OSI open source" if ln["osi_open_source"] else "open-weight, NOT OSI open source"
            L.append(f"- **{ln['model']}**: {ln['license']} ({osi}); commercial use: {ln['commercial_use']}."
                     + (f" Conditions: {ln['conditions']}" if ln.get("conditions") else ""))
            for t in ln["triggers"]:
                L.append(f"  - Trigger `{t['id']}` ({t['status']}{', blocking' if t['blocking'] else ''}): {t['effect']}")
        L.append("")
    cust = rec["customization"]
    planned = {k: v for k, v in cust.items() if v["status"] in {"planned", "approved"}}
    if planned:
        L += ["### Customize Track (optional)", ""]
        if "post_training" in planned:
            v = planned["post_training"]
            L.append(f"- **Post-training (LoRA) on {v['base_model']}**: {v['gate']} Requires: {', '.join(v['requires'])}.")
        if "distillation" in planned:
            v = planned["distillation"]
            L.append(f"- **Distillation** from {v['teacher']} ({v['teacher_license']}, distillation allowed: {v['teacher_distillation_allowed']}) "
                     f"into {v['student']}. {v['note']} Requires: {', '.join(v['requires'])}.")
        if "abliteration" in planned:
            v = planned["abliteration"]
            L.append(f"- **Abliteration (admin-approved)**: {v['justification']} Guardrails that stay on: {', '.join(v['guardrails'])}.")
        L.append("")

    # ------------------------------------------------------------ What You Don't Need
    L += ["## What You Don't Need", ""]
    seen = set()
    for e in rec["components"]["excluded"] + rec["excluded_models"]:
        if e["reason"] in seen:
            continue
        seen.add(e["reason"])
        L.append(f"- {e['name']} — {e['reason']}")
    L.append("")

    # ------------------------------------------------------------ Why Local
    L += ["## Why Local (Beyond Price)", ""]
    for w in rec["why_local"]:
        L.append(f"- **{w['benefit'].capitalize()}**: {w['text']}")
    L.append("")

    # ------------------------------------------------------------ Hardware fit
    g = p["general"]
    head = round(100 - (p["memory_use_pct"] or 0), 1)
    L += ["## Your Hardware Fit", "",
          f"- Machine: {p['hardware']['platform']} ({p['hardware']['hw_key'] or 'unknown model'}), compute class {p['hardware']['compute_class']}; "
          f"market tier: {rec['market_tier'].replace('_', ' ')}",
          f"- Runtime: {p['runtime_name']}{' (native)' if p['runtime_mode'] == 'native' else ''}. {p['runtime_why']}",
          f"- Memory: needs {_gb(p['memory_used_gb'])} of {_gb(p['usable_memory_gb'])} usable ({p['usable_memory_basis']}); "
          f"headroom {head}% (at least 20% required)"]
    if g:
        tps = f"~{g['tokens_per_sec_est']:g} tokens/sec (decode, estimate)" if g["tokens_per_sec_est"] else "unknown"
        L.append(f"- Writing speed: {tps}; target for '{rec['latency'].replace('_', ' ')}' is {p['tokens_per_sec_target']} tok/s")
        if p.get("ttft_s_est") is not None:
            L.append(f"- Reading a typical prompt before answering (prefill): ~{p['ttft_s_est']}s (estimate)")
        L.append(f"- Context: {g['context_tokens']:,} tokens; sessions in parallel: {g['sessions']}")
    L.append(f"- Disk: ~{rec['disk_estimate_gb']} GB including a year of growth (estimate)")
    L.append(f"- Setup effort: {rec['setup_effort']}")
    L.append("")

    # ------------------------------------------------------------ Privacy
    pr = rec["privacy"]
    L += ["## Privacy Settings", "",
          f"- Outbound traffic: {pr['egress']}",
          f"- Encryption: {pr['encryption_at_rest']} In transit: {pr['encryption_in_transit']}.",
          f"- Remote access: {pr['remote_access']}",
          f"- Retention: conversations {pr['retention_days']} days; documents {pr['documents_retention'].replace('_', ' ')}; memory cards {pr['memory_cards'].replace('_', ' ')}",
          f"- Restricted tier: {'on (excluded from search unless unlocked for the session)' if pr['restricted_tier_enforced'] else 'available, not required'}",
          f"- Access control: {pr['acl_enforcement']}",
          f"- Connectors: {pr['connectors']}; web research: {pr['web_research']}; cloud burst: {pr['cloud_burst']}"]
    if pr["compliance_note"]:
        L.append(f"- {pr['compliance_note']}")
    L.append("")

    # ------------------------------------------------------------ Cost
    t = rec["tco"]
    o = t["options"]
    L += ["## Cost Comparison (Honest View)", "",
          "| | Local | API (open-weight model) | API (frontier model) | Subscription |", "|---|---|---|---|---|",
          f"| 3-year cost | {_usd(o['local']['total_usd'])} | {_usd(o['api_open']['total_usd'])} | {_usd(o['api_frontier']['total_usd'])} | {_usd(o['subscription']['total_usd'])} |",
          f"| Low estimate | {_usd(o['local']['low_usd'])} | {_usd(o['api_open']['low_usd'])} | {_usd(o['api_frontier']['low_usd'])} | {_usd(o['subscription']['low_usd'])} |",
          f"| Router fee (included) | – | {_usd(o['api_open'].get('fee_usd', 0))} | – | – |",
          "| Where your prompts go | stay on your machine | " + " | ".join(f"sent to {o[k]['data_goes_to']}" for k in ("api_open", "api_frontier", "subscription")) + " |",
          "", f"3-year estimates. {t['verdict']}", "",
          f"Local breakdown: hardware {_usd(o['local']['breakdown']['hardware'])}, power {_usd(o['local']['breakdown']['power'])}, "
          f"admin time {_usd(o['local']['breakdown']['admin_time'])}, networking {_usd(o['local']['breakdown']['networking'])}."]
    if o["api_open"].get("fee_note"):
        L.append(f"The open-weight API figure {o['api_open']['fee_note']}.")
    if o["subscription"].get("note"):
        L.append(f"Subscription {o['subscription']['note']}.")
    L.append("Assumptions: " + "; ".join(a.rstrip(".") for a in t["assumptions"]) + ".")
    if t["stale_or_unverified_prices"]:
        L.append(f"Prices not verified in the last 60 days: {', '.join(t['stale_or_unverified_prices'])}. Update config/pricing.yaml.")
    L.append("")

    # ------------------------------------------------------------ Options
    opts = rec["upgrade_options"] or rec["good_better_best"]
    if opts:
        L += ["## Options If You Want More", ""]
        if rec["upgrade_options"]:
            L.append("Your current hardware does not fit this profile well. Cheapest options that do:")
            labels = ["Option 1", "Option 2", "Option 3"]
        else:
            L.append("Not needed today; these only buy a stronger general model.")
            labels = ["Good", "Better", "Best"]
        for lab, x in zip(labels, opts):
            L.append(f"- **{lab}: {x['name']}** ({_usd(x['price_usd'][0])}-{_usd(x['price_usd'][1])}, estimate) -> {x['general_model']}, "
                     f"~{x['tokens_per_sec_est']} tok/s, {x['context_tokens']:,} context")
        L.append("")

    # ------------------------------------------------------------ Setup
    L += ["## Setup Steps", ""]
    if rec["pilot_plan"]:
        where = "before buying, on a machine you already have if possible" if rec.get("procurement") else "on the hardware you have"
        L += [f"Start with a two-week pilot {where}:", ""] + [f"- {x}" for x in rec["pilot_plan"]] + [""]
    L += [f"{i}. {s}" for i, s in enumerate(setup_steps(rec), 1)] + [""]

    # ------------------------------------------------------------ Double-check
    L += ["## Things to Double-Check", ""] + [f"- {w}" for w in rec["warnings"]]
    if "security_review_packet" in rec["generate"]:
        L.append("- A security review packet was generated: SECURITY_REVIEW_PACKET.md.")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ config files

def compose_profiles(rec: dict) -> list[str]:
    out = []
    for c in rec["components"]["included"]:
        prof = c.get("compose_profile")
        if prof and prof not in out:
            out.append(prof)
    return out


def render_env(rec: dict) -> str:
    p = rec["plan"]
    g = p["general"] or {}
    tier = rec["tier"]
    engine = p["runtime"]
    vllm = engine in {"vllm", "sglang", "tensorrt_llm"}
    lines = [
        "# Generated by the Right-Size Advisor. Re-run `make advise` instead of editing by hand.",
        "# No secrets here: passwords and keys live in ./secrets/ (created by `make secrets`).",
        f"COMPOSE_PROFILES={','.join(compose_profiles(rec))}",
        "BIND_ADDRESS=127.0.0.1" + ("" if tier == 1 else "   # set to this server's LAN IP to let others connect"),
        "HTTPS_PORT=8443",
        "WEBUI_AUTH=true",
    ]
    if p["runtime_mode"] == "native":
        lines.append("# Ollama runs natively on macOS. Apply the OLLAMA_* values with `launchctl setenv` and restart Ollama.")
    lines += [
        f"OLLAMA_NUM_PARALLEL={1 if vllm else g.get('sessions', 1)}",
        f"OLLAMA_KV_CACHE_TYPE={'f16' if vllm else g.get('kv_cache_type', 'f16')}",
        "OLLAMA_FLASH_ATTENTION=1",
        f"OLLAMA_CONTEXT_LENGTH={8192 if vllm else g.get('context_tokens', 8192)}",
        f"OLLAMA_MAX_LOADED_MODELS={1 + len(p['aux']) + sum(1 for s in p['specialists'] if s.get('resident'))}",
        "OLLAMA_KEEP_ALIVE=-1   # always-on agents: keep models loaded" if "agent_watchdog" in rec.get("tokens", []) else "OLLAMA_KEEP_ALIVE=30m",
        f"GENERAL_MODEL={g.get('pull', '')}",
    ]
    if p["hardware"]["platform"] in AMD_PLATFORMS and not vllm:
        lines.append("OLLAMA_IMAGE=ollama/ollama:rocm   # AMD GPU build of Ollama")
    if vllm:
        share = min(0.9, round(g["memory"]["total_gb"] / p["usable_memory_gb"] + 0.02, 2))
        lines += [
            f"VLLM_MODEL={g['pull']}",
            f"VLLM_QUANTIZATION={'fp8' if g['quant'] == 'FP8' else 'awq'}",
            f"VLLM_MAX_MODEL_LEN={g['context_tokens']}",
            f"VLLM_KV_CACHE_DTYPE={'fp8' if g['kv_cache_type'] != 'f16' else 'auto'}",
            f"VLLM_MAX_NUM_SEQS={max(8, g['sessions'])}",
            f"VLLM_GPU_MEMORY_UTILIZATION={share}   # leaves room for the Ollama sidecar models",
            "VLLM_TENSOR_PARALLEL=1   # set to the GPU count for multi-GPU servers",
        ]
        if engine != "vllm":
            lines.append(f"# Runtime chosen: {p['runtime_name']}; the Compose skeleton ships vLLM. Swap the image if you use it.")
    stt = [a for a in p["aux"] if a["role"] == "stt"]
    if stt:
        lines.append(f"WHISPER_MODEL={stt[0]['pull']}")
    if tier >= 2:
        lines.append("# Postgres connection string is written to .env.secrets by `make secrets` (never committed).")
    if tier >= 3:
        lines.append("# Tier 3: this Compose file is for a one-node pilot. Production uses k3s (IMPLEMENTATION_PLAN Phase 4).")
    return "\n".join(lines) + "\n"


def render_env_gateway(rec: dict) -> str:
    """Settings for the Gateway container (no secrets)."""
    p = rec["plan"]
    g = p["general"] or {}
    engine = p["runtime"]
    vllm = engine in {"vllm", "sglang", "tensorrt_llm"}
    if p["runtime_mode"] == "native":
        runtime = "http://ollama-relay:11434/v1"
    elif vllm:
        runtime = "http://vllm:8000/v1"
    else:
        runtime = "http://ollama:11434/v1"
    ollama = "http://ollama-relay:11434/v1" if p["runtime_mode"] == "native" else "http://ollama:11434/v1"
    roles = {m["role"]: m for m in p["aux"] + p["specialists"]}
    enabled = rec["capabilities"]["enabled"]
    lines = [
        "# Generated by the Right-Size Advisor: Gateway settings (no secrets).",
        f"GATEWAY_RUNTIME_URL={runtime}",
        f"GATEWAY_DEFAULT_MODEL={g.get('pull', '')}",
        f"GATEWAY_EMBED_URL={ollama}",
        f"GATEWAY_EMBED_MODEL={roles['embeddings']['pull'] if 'embeddings' in roles else 'nomic-embed-text'}",
        f"GATEWAY_AUTH_MODE={'single' if rec['tier'] == 1 else ('oidc' if 'sso' in rec['tokens'] else 'proxy_header')}",
        f"GATEWAY_AIR_GAPPED={'true' if rec['privacy']['egress'].startswith('None, ever') else 'false'}",
    ]
    if rec["tier"] == 1:
        lines.append("GATEWAY_DATABASE_URL=sqlite:////data/gateway.db")
    else:
        lines.append("# GATEWAY_DATABASE_URL (Postgres, as the non-owner gateway_app role) is in .env.secrets")
    if "fast_small" in roles:
        lines.append(f"GATEWAY_FAST_MODEL={roles['fast_small']['pull']}")
    if "reranker" in roles:
        lines.append("# GATEWAY_RERANK_URL=http://reranker:8080/v1   # optional llama.cpp reranker; fusion scores are used without it")
    if "stt" in roles:
        lines.append("GATEWAY_WHISPER_URL=http://whisper:8080")
    if "web_research" in enabled:
        lines += ["GATEWAY_SEARXNG_URL=http://searxng:8080", "GATEWAY_FETCHER_URL=http://fetcher:8090"]
    if rec["cloud_burst"]["enabled"]:
        lines.append("# Cloud burst requested: set GATEWAY_CLOUD_BURST_URL / _MODEL / _ZERO_RETENTION=true and the key file")
    return "\n".join(lines) + "\n"


def render_override(rec: dict) -> str:
    p = rec["plan"]
    doc: dict = {"services": {}}
    if p["runtime_mode"] == "native":
        # Ollama runs as a Mac app. A fixed relay is the only container with a route to the Mac;
        # the Gateway stays on internal networks and reaches the app through the relay.
        doc["networks"] = {"hostlink": {}}
        doc["services"]["ollama-relay"] = {
            "image": "private-ai/gateway:local",
            "command": ["python", "-m", "gateway.relay", "--listen", "0.0.0.0:11434",
                        "--target", "host.docker.internal:11434"],
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
            "restart": "unless-stopped",
            "networks": ["models", "hostlink"],
        }
    if "authentik" in {c["id"] for c in rec["components"]["included"]}:
        # Sign-in pages (Authentik) on their own port, through Caddy's local TLS.
        doc["services"]["caddy"] = {"ports": ["${BIND_ADDRESS:-127.0.0.1}:${AUTH_PORT:-9443}:9443"]}
    if "agent_watchdog" in rec["tokens"]:
        target = "vllm" if p["runtime"] == "vllm" else ("ollama" if p["runtime_mode"] != "native" else None)
        if target:
            doc["services"][target] = {"restart": "always"}
    plat = p["hardware"]["platform"]
    if p["runtime"] == "ollama" and p["runtime_mode"] != "native" and plat in NO_NVIDIA_PLATFORMS:
        # The base file reserves an NVIDIA GPU; drop that reservation on CPU-only and AMD hosts.
        svc = doc["services"].setdefault("ollama", {})
        svc["deploy"] = _RESET
        if plat in AMD_PLATFORMS:
            svc["devices"] = ["/dev/kfd", "/dev/dri"]      # ROCm needs the kernel GPU devices
            svc["group_add"] = ["video", "render"]
    if not doc["services"]:
        doc["services"] = {"open-webui": {"environment": {"ADVISOR_GENERATED": "true"}}}
    text = yaml.safe_dump(doc, sort_keys=False).replace(f"deploy: {_RESET}", "deploy: !reset null")
    return "# Generated by the Right-Size Advisor - per-profile tweaks only.\n" + text


_RESET = "__compose_reset__"
NO_NVIDIA_PLATFORMS = {"cpu", "amd", "amd_unified"}
AMD_PLATFORMS = {"amd", "amd_unified"}


def render_policy(rec: dict) -> str:
    pr = rec["privacy"]
    air = pr["egress"].startswith("None, ever")
    allow = []
    if not air:
        allow.append({"name": "model-downloads", "when": "only during `make pull` (model-pull job)",
                      "hosts": ["registry.ollama.ai", "*.ollama.ai", "huggingface.co", "*.hf.co"], "audited": True})
        if "web_research" in rec["capabilities"]["enabled"]:
            allow.append({"name": "private-web-research", "service": "searxng", "hosts": ["configured search engines"],
                          "audited": True, "note": "Fetched pages are untrusted input: no tool calls from retrieved text."})
    cust = rec["customization"]
    doc = {
        "schema_version": 1,
        "egress": {"default": "deny", "air_gapped": air, "allowlist": allow,
                   "forbidden": ["Ollama ':cloud' model tags", "telemetry endpoints", "hosted model APIs unless cloud_burst"]},
        "cloud_burst": {"enabled": rec["cloud_burst"]["enabled"],
                        "requires": ["per-request opt-in", "PII redaction", "zero-retention provider", "audit log"]},
        "sensitivity_tiers": {
            "Public": {"retrievable": True}, "Personal": {"retrievable": True},
            "Confidential": {"retrievable": True, "acl_required": rec["tier"] >= 2},
            "Restricted": {"retrievable": False, "unlock": "explicit per-session unlock by the owner", "enforced": pr["restricted_tier_enforced"]},
        },
        "derived_data": {"inherit_strictest_label": True,
                         "applies_to": ["chunks", "embeddings", "conversation summaries", "memory cards", "history index"]},
        "retention": {"conversations_days": pr["retention_days"], "documents": pr["documents_retention"],
                      "memory_cards": pr["memory_cards"], "hard_delete_includes": ["rows", "vectors", "backups on rotation"]},
        "access_control": {"enforced_in": "database query filter" if rec["tier"] >= 2 else "single user", "collections": pr["collections"]},
        "secrets": {"store_passwords": False, "source": "OS keychain or vault; short-lived scoped tokens only"},
        "connectors": {"enabled": [] if pr["connectors"] == "none" else [c.strip() for c in pr["connectors"].replace(" (read-only)", "").split(",")],
                       "default_scope": "read-only", "kill_switch": True, "log_last_accessed": True},
        "confirmations": {"write_actions": "always", "show_exact_payload": True,
                          "actions": ["send_email", "post", "purchase", "delete", "share"]},
        "model_pinning": {"auto_upgrade": False, "verify_checksums": True,
                          "promotion_requires": "passing golden-set eval (recall@k, faithfulness, citation accuracy)"},
        "agents": {"watchdog": "agent_watchdog" in rec["tokens"], "pinned_models_only": True},
        "customization": {k: v["status"] for k, v in cust.items()},
        "audit": {"log": ["retrievals", "tool_calls", "connector_access", "egress_events", "model_changes"], "location": "local"},
    }
    return "# Generated by the Right-Size Advisor.\n" + yaml.safe_dump(doc, sort_keys=False)


def render_model_lists(rec: dict) -> tuple[str, str]:
    p = rec["plan"]
    vllm = p["runtime"] in {"vllm", "sglang", "tensorrt_llm"}
    air = rec["privacy"]["egress"].startswith("None, ever")
    head = ("# AIR-GAPPED: pull these on a separate connected machine, verify checksums, and import them "
            "from an offline drive. This machine never downloads." if air else
            "# Pulled by `make pull` (the only step with internet access). Never add ':cloud' tags.")
    ollama, hf = [head], ["# Hugging Face repos (vLLM weights, reranker). Pin a revision before download."]
    for m in ([p["general"]] if p["general"] else []) + p["specialists"]:
        if vllm and m is p["general"]:
            hf.append(m["pull"])
        else:
            ollama.append(f"# {m['name']}: {QUANT_HINT.get(m['quant'], m['quant'])}")
            ollama.append(m["pull"])
    for a in p["aux"]:
        if a["role"] in {"embeddings", "fast_small"}:
            ollama.append(a["pull"])
        elif a["role"] == "reranker":
            hf.append(f"{a['pull']}   # reranker, used by the Gateway (Phase 2)")
        elif a["role"] == "stt":
            ollama.append(f"# whisper.cpp model (download separately into the whisper-models volume): {a['pull']}")
    return "\n".join(ollama) + "\n", "\n".join(hf) + "\n"


def render_security_packet(rec: dict, template: str) -> str:
    p = rec["plan"]
    rows = []
    for m in _models(rec):
        rows.append(f"| {m['name']} | {m['role']} | {m.get('license', '')} | {m.get('commercial_use', '')} | {m.get('pull', '')} | pin at install |")
    lic = "\n".join(f"- {ln['model']}: {ln['license']}; OSI open source: {'yes' if ln['osi_open_source'] else 'no'}; "
                    f"triggers: {', '.join(t['id'] + ' (' + t['status'] + ')' for t in ln['triggers']) or 'none'}"
                    for ln in rec["licenses"]) or "- Personal use: commercial license terms not evaluated."
    comps = "\n".join(f"- {c['name']}: {c['reason']}" for c in rec["components"]["included"])
    pr = rec["privacy"]
    values = {
        "{{PROFILE}}": rec["profile_name"], "{{TIER}}": f"{rec['tier']} - {rec['tier_name']}",
        "{{RUNTIME}}": p["runtime_name"], "{{MODEL_TABLE}}": "\n".join(rows), "{{LICENSES}}": lic,
        "{{COMPONENTS}}": comps, "{{EGRESS}}": pr["egress"], "{{ACL}}": pr["acl_enforcement"],
        "{{RETENTION}}": f"conversations {pr['retention_days']} days; documents {pr['documents_retention']}",
        "{{RESTRICTED}}": "enforced" if pr["restricted_tier_enforced"] else "available",
        "{{CONNECTORS}}": pr["connectors"], "{{CLOUD_BURST}}": pr["cloud_burst"],
        "{{CUSTOMIZATION}}": ", ".join(f"{k}: {v['status']}" for k, v in rec["customization"].items()),
        "{{COMPLIANCE}}": pr["compliance_note"] or "No regulated data declared.",
    }
    out = template
    for k, v in values.items():
        out = out.replace(k, str(v))
    return out


def write_outputs(rec: dict, out_dir: Path, diff_lines: list[str] | None = None, packet_template: str | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config").mkdir(exist_ok=True)
    o, v = render_model_lists(rec)
    files = {
        "recommendation.md": render_markdown(rec, diff_lines),
        "recommendation.json": json.dumps(rec, indent=2),
        ".env": render_env(rec),
        ".env.gateway": render_env_gateway(rec),
        "docker-compose.override.yml": render_override(rec),
        "config/policy.yaml": render_policy(rec),
        "models-ollama.txt": o,
        "models-hf.txt": v,
    }
    if "security_review_packet" in rec["generate"] and packet_template:
        files["SECURITY_REVIEW_PACKET.md"] = render_security_packet(rec, packet_template)
    paths = []
    for name, text in files.items():
        path = out_dir / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths
