"""Deterministic recommendation engine.

profile -> capabilities -> runtime -> hardware fit (or what to buy) -> components
        -> licenses -> customization gates -> TCO -> recommendation dict

The engine makes every choice. The optional LLM explainer (explain.py) only words the
result, and its output is checked so it cannot introduce anything the engine did not pick.
"""

from __future__ import annotations

from dataclasses import replace

from . import sizing
from .config import AdvisorConfig
from .profile import Hardware, Profile

ALL_CAPABILITIES = [
    "chat", "long_context", "embeddings", "reranker", "document_parsing", "ocr", "vision",
    "stt", "code", "tool_calling", "multilingual", "web_research",
]

EXCLUDE_HINTS = {
    "stt": "No meetings, voice notes or audio to process.",
    "code": "Coding is not a primary job; the general model handles occasional code questions.",
    "embeddings": "No document search or conversation recall job; keyword search covers history.",
    "reranker": "Only needed for searching your own documents.",
    "ocr": "No scans or images to read.",
    "document_parsing": "No documents to ingest.",
    "long_context": "Standard context (about 8K tokens) covers this use.",
    "multilingual": "All declared languages are English.",
    "tool_calling": "No agent tasks or agents.",
}

FP8_GPUS = {"rtx_4060_ti", "rtx_4070", "rtx_4080", "rtx_4090", "rtx_5090", "rtx_6000_ada", "l40s",
            "h100_pcie", "dgx_spark", "dgx_station"}
VLLM_FAMILY = {"vllm", "sglang", "tensorrt_llm"}
NVIDIA = {"nvidia", "nvidia_unified"}


# =========================================================================== capabilities

def derive_capabilities(p: Profile, cfg: AdvisorConfig) -> tuple[dict, dict]:
    rules = cfg.rules
    enabled: dict[str, list[str]] = {"chat": ["every setup needs one general model to answer questions"]}

    def add(cap, why):
        enabled.setdefault(cap, [])
        if why not in enabled[cap]:
            enabled[cap].append(why)

    for job in p.jobs:
        for cap in rules["jobs"][job]:
            add(cap, f"job '{job}'")
    if "knowledge_recall" in p.jobs:
        for t in p.data_types:
            for cap in rules["data_types"][t]:
                add(cap, f"data type '{t}'")
    if p.context_needs in rules["long_context_needs"]:
        add("long_context", f"context_needs '{p.context_needs}'")
    langs = [lang for lang in p.languages if not lang.lower().startswith("en")]
    if langs:
        add("multilingual", f"languages {langs}")
    if p.agents_count:
        add("tool_calling", f"{p.agents_count} agent(s)")

    excluded: dict[str, str] = {}
    if p.web_research:
        if p.air_gapped:
            excluded["web_research"] = "Air-gapped: no outbound traffic is allowed, so private web search is off."
        else:
            add("web_research", "features.web_research: true")
    else:
        excluded["web_research"] = "Web research is off (features.web_research: false)."
    if "vision" not in enabled:
        excluded["vision"] = ("No job needs to understand photos or charts." if "ocr" not in enabled else
                              "Reading text from scans and images is enough (OCR); no photo or diagram questions selected.")
    for cap in ALL_CAPABILITIES:
        if cap not in enabled and cap not in excluded:
            excluded[cap] = EXCLUDE_HINTS.get(cap, "No selected job or data type requires it.")
    return enabled, excluded


def choose_tier(p: Profile, cfg: AdvisorConfig) -> int:
    for tier in sorted(cfg.rules["tiers"]):
        if p.users <= cfg.rules["tiers"][tier]["max_users"]:
            return tier
    return 3


# =========================================================================== runtime

def choose_runtime(p: Profile, hw: Hardware, cfg: AdvisorConfig) -> dict:
    engines = cfg.runtimes["engines"]
    notes: list[str] = []
    pref = p.runtime_prefs.get("preferred")
    if pref:
        e = engines.get(pref)
        reason = None
        if not e:
            reason = "unknown engine"
        elif e.get("never_recommend"):
            reason = f"{e['name']} is in maintenance mode and is never recommended"
        elif e.get("requires") == "allow_experimental" and not p.runtime_prefs.get("allow_experimental"):
            reason = f"{e['name']} is experimental; set runtime_preferences.allow_experimental: true to use it"
        elif e.get("requires") == "accept_closed_source" and not p.runtime_prefs.get("accept_closed_source"):
            reason = f"{e['name']} is free but closed source; set runtime_preferences.accept_closed_source: true to use it"
        elif hw.platform not in e["platforms"]:
            reason = f"{e['name']} does not support this platform"
        if reason:
            notes.append(f"Preferred runtime '{pref}' not used: {reason}.")
        else:
            mode = "native" if hw.platform == "apple" else "container"
            if e.get("open_source") is False:
                notes.append(f"{e['name']} is closed source (accepted in the profile); this departs from the open-source principle.")
            if e.get("status") == "experimental":
                notes.append(f"{e['name']} is EXPERIMENTAL: speedups are self-reported. Keep a fallback runtime.")
            return {"engine": pref, "name": e["name"], "mode": mode, "alternatives": [],
                    "why": "Chosen in runtime_preferences.", "notes": notes}

    for rule in cfg.runtimes["rules"]:
        w = rule["when"]
        if "platform" in w and hw.platform not in w["platform"]:
            continue
        if "min_sessions" in w and p.sessions < w["min_sessions"]:
            continue
        if "skill" in w and p.skill not in w["skill"]:
            continue
        eng = rule["choose"]
        assert not engines[eng].get("never_recommend"), "rules must never choose a maintenance-mode engine"
        if p.sessions >= 8 and eng not in VLLM_FAMILY:
            notes.append(f"{p.sessions} concurrent sessions would normally use vLLM, which needs NVIDIA; "
                         "using Ollama with parallel requests instead. Expect queueing at peak.")
        mode = rule.get("mode", "native" if hw.platform == "apple" else "container")
        return {"engine": eng, "name": engines[eng]["name"], "mode": mode,
                "alternatives": [engines[a]["name"] for a in rule.get("alternatives", [])],
                "why": rule["why"], "notes": notes}
    raise RuntimeError("no runtime rule matched")


# =========================================================================== licenses

def trigger_applies(t: dict, p: Profile) -> bool | None:
    """True = applies, False = does not, None = unknown (scale not given)."""
    if p.licensing_use != "commercial":
        return False
    s = p.licensing_scale
    typ = t["type"]
    if typ in {"commercial_use", "attribution", "use_policy"}:
        return True
    if typ == "mau_over":
        return None if "monthly_active_users" not in s else s["monthly_active_users"] >= t["threshold"]
    if typ == "mau_or_monthly_revenue_over":
        if "monthly_active_users" not in s and "monthly_revenue_usd" not in s:
            return None
        return (s.get("monthly_active_users", 0) >= t["threshold_mau"] or
                s.get("monthly_revenue_usd", 0) >= t["threshold_monthly_revenue_usd"])
    if typ == "maas_revenue_over":
        if not s.get("offers_model_as_service"):
            return False
        return None if "annual_revenue_usd" not in s else s["annual_revenue_usd"] >= t["threshold"]
    return None


def license_ok(m: dict, p: Profile) -> bool:
    if p.licensing_use == "personal":
        return True
    if m["commercial_use"] == "no":
        return False
    for t in m.get("commercial_triggers", []):
        if t.get("blocking") and trigger_applies(t, p) and t["id"] not in p.acknowledged_triggers:
            return False
    return True


def license_notes(models: list[dict], p: Profile, cfg: AdvisorConfig) -> list[dict]:
    out = []
    for sel in models:
        m = cfg.model(sel["id"])
        triggers = []
        for t in m.get("commercial_triggers", []):
            applies = trigger_applies(t, p)
            if applies is False:
                continue
            triggers.append({"id": t["id"], "effect": t["effect"], "blocking": t.get("blocking", False),
                             "status": "acknowledged" if t["id"] in p.acknowledged_triggers else
                                       ("applies" if applies else "check: depends on your scale")})
        out.append({"model": m["name"], "license": m["license"], "open_weight": m.get("open_weight", True),
                    "osi_open_source": m.get("osi_open_source", False), "commercial_use": m["commercial_use"],
                    "conditions": m.get("license_conditions"), "triggers": triggers,
                    "last_verified": m.get("last_verified")})
    return out


# =========================================================================== model fitting

def _quants(model: dict, engine: str, hw: Hardware, cfg: AdvisorConfig) -> list[str]:
    rt = model.get("runtimes", {})
    if engine in VLLM_FAMILY:
        if "vllm" not in rt:
            return []
        prefs = list(cfg.rules["sizing"]["vllm_quant_preference"])
        if hw.hw_key not in FP8_GPUS:
            prefs = [q for q in prefs if q != "FP8"]
    else:
        if engine == "ollama" and "ollama" not in rt:
            return []
        if "mlx" in str(rt.get("ollama", "")) and hw.platform != "apple":
            return []   # MLX builds run only on Apple Silicon
        prefs = cfg.rules["sizing"]["ollama_quant_preference"]
    return [q for q in prefs if q in model["quantizations"]]


def _pick_aux(role: str, p: Profile, cfg: AdvisorConfig, usable_gb: float, multilingual: bool) -> dict | None:
    cands = [m for m in cfg.models if m["role"] == role and license_ok(m, p)]
    if not cands:
        return None
    if role == "stt":
        large = usable_gb >= cfg.rules["sizing"]["stt_large_min_usable_gb"]
        return sorted(cands, key=lambda m: m["memory_gb"], reverse=large)[0]
    if multilingual:
        multi = [m for m in cands if "multilingual" in m["capabilities"]]
        if multi:
            return min(multi, key=lambda m: m["memory_gb"])
    return min(cands, key=lambda m: m["memory_gb"])


def _context_options(target: int, model: dict, cfg: AdvisorConfig) -> list[int]:
    floor = cfg.rules["sizing"]["min_context_tokens"]
    top = min(target, model.get("context_max", target))
    opts = [top]
    while opts[-1] // 2 >= floor:
        opts.append(opts[-1] // 2)
    return opts


def _rank(m: dict, p: Profile, cfg: AdvisorConfig) -> float:
    s = cfg.rules["sizing"]
    w = s["moe_bonus_when"]
    wants_speed = (p.latency in w["latency"] or p.shape in w["shape"] or
                   (w.get("agents_always_on") and p.agents_always_on))
    return m["quality_rank"] + (s["moe_rank_bonus"] if wants_speed and m["architecture"] == "moe" else 0)


def _pull_ref(m: dict, engine: str) -> str:
    rt = m.get("runtimes", {})
    if engine in VLLM_FAMILY and "vllm" in rt:
        return rt["vllm"]
    if engine == "ollama" and "ollama" in rt:
        return rt["ollama"]
    if "whisper_cpp" in rt:
        return rt["whisper_cpp"]
    return rt.get("hf", m["id"])


def _fit_llm(cands, *, budget_left, sessions, target_ctx, engine, hw, cfg, p, tps_target):
    """First fit in search order: model (rank) -> context -> kv type -> quant."""
    rejected = []
    for m in sorted(cands, key=lambda m: _rank(m, p, cfg), reverse=True):
        quants = _quants(m, engine, hw, cfg)
        if not quants:
            continue
        too_slow = False
        for ctx in _context_options(target_ctx, m, cfg):
            for kv in cfg.rules["sizing"]["kv_cache_types"]:
                for q in quants:
                    fp = sizing.llm_footprint(m, q, ctx, sessions, kv, cfg)
                    if fp["total_gb"] > budget_left:
                        continue
                    tps = sizing.tokens_per_sec(m, q, hw.bandwidth_gbps, cfg)
                    if tps_target and tps is not None and tps < tps_target:
                        too_slow = True
                        continue
                    return {
                        "id": m["id"], "name": m["name"], "role": m["role"], "quant": q,
                        "architecture": m["architecture"], "total_params_b": m["total_params_b"],
                        "active_params_b": m["active_params_b"],
                        "context_tokens": ctx, "kv_cache_type": kv, "sessions": sessions, "memory": fp,
                        "tokens_per_sec_est": tps,
                        "prefill_tokens_per_sec_est": sizing.prefill_tokens_per_sec(m, hw.tflops_fp16, cfg),
                        "capabilities": m["capabilities"], "license": m["license"],
                        "commercial_use": m["commercial_use"], "pull": _pull_ref(m, engine),
                    }, rejected
        rejected.append(f"{m['name']}: {'too slow for the latency target' if too_slow else 'does not fit in memory'}")
    return None, rejected


def _covers_specialists(plan: dict, enabled: dict) -> bool:
    roles = {m["role"] for m in plan["specialists"]}
    g = plan["general"] or {}
    need_code = "code" in enabled and "code" not in roles
    need_vision = "vision" in enabled and "vision" not in roles and "vision" not in g.get("covers", [])
    return bool(plan["general"]) and not need_code and not need_vision


def plan_for_hardware(p: Profile, hw: Hardware, cfg: AdvisorConfig, enabled: dict, tier: int) -> dict:
    plan = _plan(p, hw, cfg, enabled, tier)
    if plan["runtime"] in VLLM_FAMILY and not _covers_specialists(plan, enabled):
        alt = _plan(p, hw, cfg, enabled, tier, force_engine="ollama")
        if _covers_specialists(alt, enabled) and alt["acceptable"]:
            alt["notes"].insert(0, "vLLM would not leave room for the required specialist model, so this plan uses "
                                   "Ollama (fewer parallel sessions; the rest queue).")
            return alt
    return plan


def _plan(p: Profile, hw: Hardware, cfg: AdvisorConfig, enabled: dict, tier: int, force_engine: str | None = None) -> dict:
    s = cfg.rules["sizing"]
    hw = sizing.resolve(hw, cfg)
    rt = choose_runtime(p, hw, cfg)
    if force_engine:
        e = cfg.runtimes["engines"][force_engine]
        rt = {"engine": force_engine, "name": e["name"], "mode": "native" if hw.platform == "apple" else "container",
              "alternatives": [], "why": "Fallback so every required model fits.", "notes": []}
    engine = rt["engine"]
    usable = sizing.usable_memory(hw, cfg)
    budget = usable.gb * (1 - s["headroom_fraction"])
    tps_target = s["tokens_per_sec"]["targets"][p.latency]
    multilingual = "multilingual" in enabled
    notes = list(rt["notes"])
    if hw.bandwidth_gbps is None:
        notes.append("Memory bandwidth unknown, so speed was not checked. Set hardware.override.bandwidth_gbps.")
    if hw.platform == "cpu":
        notes.append("CPU-only: expect slow responses. A GPU or unified-memory machine is strongly preferred.")
    if not (p.hardware.hw_key or p.hardware.bandwidth_gbps) and hw.source in {"profile", "detected"}:
        notes.append(f"Exact chip/GPU not given; speed assumes a conservative {hw.hw_key}.")

    aux = []
    for cap, role in (("embeddings", "embeddings"), ("reranker", "reranker"), ("stt", "stt")):
        if cap in enabled:
            m = _pick_aux(role, p, cfg, usable.gb, multilingual)
            if m:
                aux.append({"id": m["id"], "name": m["name"], "role": role, "memory_gb": m["memory_gb"],
                            "license": m["license"], "commercial_use": m["commercial_use"],
                            "pull": _pull_ref(m, "ollama"), "resident": True})
    aux_gb = sum(a["memory_gb"] for a in aux)

    fast = None
    if tier >= 3:
        cands = [m for m in cfg.models if m["role"] == "fast_small" and license_ok(m, p)]
        fast, _ = _fit_llm(cands, budget_left=budget - aux_gb, sessions=1, target_ctx=8192, engine="ollama",
                           hw=hw, cfg=cfg, p=p, tps_target=None)
        if fast:
            fast["resident"] = True
            fast["note"] = "Small sidecar model for summaries and routing."
            aux_gb += fast["memory"]["total_gb"]

    sessions = p.sessions if engine in VLLM_FAMILY else min(p.sessions, cfg.rules["runtime"]["ollama_parallel_max"])
    if p.sessions > sessions:
        notes.append(f"{p.sessions} sessions planned but this runtime serves {sessions} in parallel; the rest queue.")
    target_ctx = cfg.rules["context_tokens"][p.context_needs]
    general_cands = [m for m in cfg.models if m["role"] == "general" and license_ok(m, p)]
    min_params = cfg.rules["tiers"][tier]["min_general_params_b"]
    general, rejected = _fit_llm(general_cands, budget_left=budget - aux_gb, sessions=sessions, target_ctx=target_ctx,
                                 engine=engine, hw=hw, cfg=cfg, p=p, tps_target=tps_target)

    # Coding as a primary job: keep the code model resident next to the general model.
    code_pick = None
    spec_engine = "ollama" if engine in VLLM_FAMILY else engine
    if "code" in enabled and general:
        code_cands = [m for m in cfg.models if m["role"] == "code" and license_ok(m, p)]
        big_enough = [m for m in general_cands if m["total_params_b"] >= min_params]
        best = None
        for c in code_cands:
            for q in _quants(c, spec_engine, hw, cfg):
                for kv in s["kv_cache_types"]:
                    ctx = min(16384, c.get("context_max", 16384))
                    fp = sizing.llm_footprint(c, q, ctx, sessions, kv, cfg)
                    tps = sizing.tokens_per_sec(c, q, hw.bandwidth_gbps, cfg)
                    if fp["total_gb"] > budget - aux_gb or (tps is not None and tps < tps_target):
                        continue
                    g, _ = _fit_llm(big_enough, budget_left=budget - aux_gb - fp["total_gb"], sessions=sessions,
                                    target_ctx=target_ctx, engine=engine, hw=hw, cfg=cfg, p=p, tps_target=tps_target)
                    if not g:
                        continue
                    score = (c["quality_rank"] + cfg.model(g["id"])["quality_rank"], -(fp["total_gb"] + g["memory"]["total_gb"]))
                    if best is None or score > best[0]:
                        cp = {"id": c["id"], "name": c["name"], "role": "code", "quant": q, "architecture": c["architecture"],
                              "total_params_b": c["total_params_b"], "active_params_b": c["active_params_b"],
                              "context_tokens": ctx, "kv_cache_type": kv, "sessions": sessions, "memory": fp,
                              "tokens_per_sec_est": tps, "capabilities": c["capabilities"], "license": c["license"],
                              "commercial_use": c["commercial_use"], "pull": _pull_ref(c, spec_engine), "resident": True}
                        best = (score, cp, g)
        if best:
            _, code_pick, general = best
            if engine in VLLM_FAMILY:
                code_pick["note"] = "Served by an Ollama sidecar; vLLM serves the general model."

    used = aux_gb + (general["memory"]["total_gb"] if general else 0) + (code_pick["memory"]["total_gb"] if code_pick else 0)
    if general:
        general["resident"] = True
        if general["context_tokens"] < target_ctx:
            notes.append(f"Context reduced from {target_ctx:,} to {general['context_tokens']:,} tokens to fit memory.")
        if general["kv_cache_type"] != "f16":
            notes.append("KV cache quantized to q8_0 to fit memory (needs flash attention; small quality cost).")

    specialists = [code_pick] if code_pick else []
    for cap, role, ctx in (("code", "code", 16384), ("vision", "vision", 8192)):
        if cap not in enabled or not general or (role == "code" and code_pick):
            continue
        if role == "vision" and "vision" in general["capabilities"]:
            general["covers"] = general.get("covers", []) + ["vision"]
            continue   # one capable general model beats a second specialist
        cands = [m for m in cfg.models if m["role"] == role and license_ok(m, p)]
        sess = 1 if role == "vision" else sessions
        pick, _ = _fit_llm(cands, budget_left=budget - used, sessions=sess, target_ctx=ctx, engine=spec_engine,
                           hw=hw, cfg=cfg, p=p, tps_target=tps_target)
        if pick:
            pick["resident"] = True
            used += pick["memory"]["total_gb"]
        elif engine not in VLLM_FAMILY:   # vLLM reserves its GPU memory: no swapping alongside it
            pick, _ = _fit_llm(cands, budget_left=budget - aux_gb, sessions=sess, target_ctx=ctx, engine=spec_engine,
                               hw=hw, cfg=cfg, p=p, tps_target=tps_target)
            if pick:
                pick["resident"] = False
                pick["note"] = "Loaded on demand: swaps with the general model (a few seconds of load time)."
        if pick:
            specialists.append(pick)
        else:
            notes.append("No code model fits; the general model handles coding." if role == "code" else
                         "No vision model fits this hardware; OCR still extracts text from images.")

    ttft = None
    if general and general.get("prefill_tokens_per_sec_est"):
        prompt_tokens = cfg.pricing["usage_assumptions"]["input_tokens_per_query"][p.context_needs]
        ttft = round(prompt_tokens / general["prefill_tokens_per_sec_est"], 1)
        if ttft > s["ttft_targets_s"][p.latency]:
            notes.append(f"Reading a typical {prompt_tokens:,}-token prompt takes ~{ttft}s before the answer starts (prefill, estimate)."
                         + (" Prompt-heavy work favors higher-compute GPUs." if p.shape == "prompt_heavy" else ""))

    acceptable = bool(general) and general["total_params_b"] >= min_params
    if general and not acceptable:
        notes.append(f"Best fitting model ({general['name']}) is below the {min_params}B minimum for tier {tier}.")
    return {
        "runtime": engine, "runtime_name": rt["name"], "runtime_mode": rt["mode"],
        "runtime_alternatives": rt["alternatives"], "runtime_why": rt["why"],
        "hardware": {"platform": hw.platform, "hw_key": hw.hw_key, "gpu_count": hw.gpu_count, "source": hw.source,
                     "compute_class": sizing.compute_class(hw, cfg)},
        "usable_memory_gb": usable.gb, "usable_memory_basis": usable.basis,
        "memory_budget_gb": round(budget, 2), "memory_used_gb": round(used, 2),
        "memory_use_pct": round(100 * used / usable.gb, 1) if usable.gb else None,
        "bandwidth_gbps": hw.bandwidth_gbps, "tflops_fp16": hw.tflops_fp16,
        "tokens_per_sec_target": tps_target, "ttft_s_est": ttft,
        "general": general, "specialists": specialists, "aux": aux + ([fast] if fast else []),
        "rejected": rejected, "acceptable": acceptable, "notes": notes,
    }


# =========================================================================== hardware to buy

def _option_hw(opt: dict) -> Hardware:
    return Hardware(known=True, platform=opt["platform"], hw_key=opt["hw_key"], usable_gb=opt["usable_gb"],
                    gpu_count=opt.get("gpu_count", 1), vram_gb=opt["usable_gb"] if opt["platform"] in {"nvidia", "amd"} else 0,
                    ram_gb=opt["usable_gb"], source=opt["id"])


def _shape_score(opt: dict, plan: dict, p: Profile, cfg: AdvisorConfig) -> float:
    rules = cfg.rules["workload_shape"]
    score = float(opt["price_usd"][0])
    shape = rules.get(p.shape, {})
    if shape.get("prefer") == "compute" and plan["hardware"]["compute_class"] != shape["penalty_unless_compute_class"]:
        score *= shape["penalty"]
    if shape.get("prefer") == "bandwidth" and (plan["bandwidth_gbps"] or 0) < shape["penalty_below_bandwidth_gbps"]:
        score *= shape["penalty"]
    if p.sessions >= 8 and opt["platform"] not in NVIDIA:
        score *= rules["vllm_needs_nvidia_penalty"]
    return score


def evaluate_options(p: Profile, cfg: AdvisorConfig, enabled: dict, tier: int, cap_usd: float | None,
                     min_rank: float | None = None) -> list[dict]:
    out = []
    for opt in cfg.pricing["hardware_options"]:
        if opt["max_tier"] < tier:
            continue
        if cap_usd is not None and opt["price_usd"][1] > cap_usd:
            continue
        plan = plan_for_hardware(p, _option_hw(opt), cfg, enabled, tier)
        if not plan["acceptable"]:
            continue
        g = plan["general"]
        tps = g["tokens_per_sec_est"]
        if tps is not None and tps < plan["tokens_per_sec_target"]:
            continue
        rank = cfg.model(g["id"])["quality_rank"]
        if min_rank is not None and rank < min_rank:
            continue
        out.append({"id": opt["id"], "name": opt["name"], "price_usd": opt["price_usd"], "market_tier": opt["market_tier"],
                    "last_verified": opt.get("last_verified"), "general_model": f"{g['name']} {g['quant']}",
                    "tokens_per_sec_est": tps, "context_tokens": g["context_tokens"], "rank": rank,
                    "score": _shape_score(opt, plan, p, cfg), "plan": plan, "option": opt})
    out.sort(key=lambda o: (o["score"], -o["rank"]))
    return out


def _public(o: dict) -> dict:
    return {k: v for k, v in o.items() if k not in {"plan", "option", "score"}}


# =========================================================================== components and tokens

def choose_components(p: Profile, tier: int, plan: dict, enabled: dict, excluded_caps: dict, cfg: AdvisorConfig):
    comp = cfg.rules["components"]
    inc, exc = [], []

    def include(cid, name, why, compose=None, alternatives=None):
        inc.append({"id": cid, "name": name, "reason": why, "compose_profile": compose, "alternatives": alternatives or []})

    def exclude(cid, name, why):
        exc.append({"id": cid, "name": name, "reason": why})

    engine, mode = plan["runtime"], plan["runtime_mode"]
    if engine in VLLM_FAMILY:
        include(engine, f"{plan['runtime_name']} (OpenAI-compatible)", plan["runtime_why"], "vllm", plan["runtime_alternatives"])
        include("ollama", "Ollama sidecar", "Serves embeddings and any small or specialist models.", "ollama")
    else:
        native = mode == "native"
        include(engine if engine != "ollama" else ("ollama-native" if native else "ollama"),
                f"{plan['runtime_name']}{' (installed natively on macOS)' if native else ''}",
                plan["runtime_why"], None if native else "ollama", plan["runtime_alternatives"])
        exclude("vllm", "vLLM / SGLang", f"Only {p.sessions} concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.")

    include("open-webui", "Open WebUI", "Chat UI bound to localhost/LAN, telemetry disabled.", "ui", ["AnythingLLM", "custom thin UI on the Gateway"])
    include("caddy", "Caddy reverse proxy", comp["reverse_proxy"], "ui", ["Traefik"])
    include("gateway", "Gateway", "The only path to the model: policy, access rules, audit log, Recall, memory and searchable history.", "ui")
    include("backups", "Encrypted backups (restic)", comp["backups"], None, ["borg"])
    include("model-pinning", "Model pinning", "Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion.")

    if tier >= 2:
        include("postgres", "Postgres", "Shared conversation, metadata and ACL store for multiple users.", "db")
        include("accounts", "Per-user accounts", "Every person has their own login and history.")
    else:
        exclude("postgres", "Postgres server", "Single user: SQLite on the encrypted disk is enough.")
    if "embeddings" in enabled:
        vs = comp["vector_store"][tier]
        include("vector-store", vs["default"], "Semantic + keyword (hybrid) search over documents and history.", "db" if tier >= 2 else None, vs["alternatives"])
    else:
        exclude("vector-store", "Vector database", EXCLUDE_HINTS["embeddings"])
    if "document_parsing" in enabled:
        include("docling", "Docling document parser", "Parses PDFs, Office files and email exports locally.", "ingest", ["Unstructured", "Apache Tika"])
    else:
        exclude("docling", "Document parser", EXCLUDE_HINTS["document_parsing"])
    if "ocr" in enabled:
        include("ocr", "Local OCR pipeline (Tesseract via Docling)", "Scans and images need text extraction.", "ingest", ["PaddleOCR"])
    else:
        exclude("ocr", "OCR pipeline", EXCLUDE_HINTS["ocr"])
    if "stt" in enabled:
        include("whisper", "whisper.cpp server", "Meetings and voice notes need local speech-to-text.", "voice")
    else:
        exclude("whisper", "Speech-to-text (whisper.cpp)", EXCLUDE_HINTS["stt"])
    if "web_research" in enabled:
        why = "Private metasearch: searches are not tied to your identity. The only service allowed outbound, and only to search engines."
        if p.regulated:
            why += " Regulated data is present, so this is a named, audited egress exception you opted into."
        include("searxng", "SearXNG (private web research)", why, "research")
    else:
        exclude("searxng", "SearXNG web research", excluded_caps["web_research"])

    if p.sso or tier >= 3:
        include("authentik", "Authentik (OIDC SSO + MFA)", "Central login with MFA" + (" (requested)" if p.sso else " for a 20+ user tier"), "sso", ["Keycloak"])
        include("rbac", "Role-based access control", "Roles from SSO groups decide which collections each user can search.")
    else:
        exclude("authentik", "SSO (Authentik/Keycloak)", "Single user on localhost." if tier == 1 else "Built-in per-user accounts are enough for this group (features.sso: false).")
    if tier >= 3:
        include("observability", comp["observability_tier3"], "Capacity, latency, queue depth and ingestion monitoring.", "observability")
    else:
        exclude("observability", "Prometheus / Grafana / Loki", "Too heavy for this tier; the Gateway health page covers basics.")
    if p.agents_always_on:
        include("agent-watchdog", "Agent watchdog", "Always-on agents: health checks, automatic restart, alerts; runs only pinned models.")
    if p.connectors and not p.air_gapped:
        include("connectors", f"Scoped read-only connectors: {', '.join(p.connectors)}",
                "Requested. OAuth with minimal read-only scopes, tokens in the vault, per-connector kill switch, 'last accessed' log. (Phase 5)")
    else:
        exclude("connectors", "Live account connectors (email, social, banking)",
                "None requested. Use exported files (.mbox, notes exports) instead of account access.")
    if p.cloud_burst and not p.air_gapped:
        include("cloud-burst", "Cloud burst mode (opt-in per request)",
                "Requested. Every request needs explicit opt-in, PII redaction, a zero-retention provider and an audit entry.")
    else:
        exclude("cloud-burst", "Cloud burst mode", "Off by default. The advisor never turns it on.")
    if tier == 1:
        exclude("gpu-server", "Dedicated GPU server", "One user on their own machine does not need a server.")
    if tier >= 2:
        exclude("desktop-installs", "Per-user desktop installs", "One shared server is simpler to secure, back up and update.")
    return inc, exc


def gpt_size_token(params_b: float) -> str:
    return "general_chat_small" if params_b <= 9 else "general_chat_mid" if params_b <= 40 else "general_chat_large"


def build_tokens(p: Profile, tier: int, plan: dict, enabled: dict, inc: list, custom: dict) -> list[str]:
    """Stable vocabulary describing the result (used by tests and the explainer allowlist)."""
    ids = {c["id"] for c in inc}
    t = {f"tier_{tier}", plan["runtime"]}
    g = plan["general"]
    if g:
        t.add(gpt_size_token(g["total_params_b"]))
    roles = {m["role"]: m for m in plan["specialists"] + plan["aux"]}
    if "embeddings" in roles:
        t.add("embeddings")
    if "reranker" in roles:
        t.add("reranker")
        if roles["reranker"]["memory_gb"] >= 1.0:
            t.add("reranker_large")
    if "stt" in roles:
        t.add("speech_to_text")
    if "code" in roles:
        t.add("code_model")
    if "vision" in enabled:
        t.add("vision")
        v = roles.get("vision")
        if v and v["total_params_b"] <= 9:
            t.add("vision_small")
    if "fast_small" in roles:
        t.add("fast_small_model")
    for cap in ("multilingual", "long_context", "tool_calling"):
        if cap in enabled:
            t.add(cap)
    t.add("conversation_history")
    if "embeddings" in enabled:
        t.add("hybrid_search")
    if "knowledge_recall" in p.jobs and "code" in p.data_types:
        t.add("code_aware_chunking")
    m = {"ocr": "ocr_pipeline", "searxng": "searxng", "authentik": "sso", "rbac": "rbac", "observability": "monitoring",
         "backups": "encrypted_backups", "model-pinning": "model_pinning", "agent-watchdog": "agent_watchdog",
         "accounts": "per_user_accounts", "connectors": "live_connectors", "cloud-burst": "cloud_burst", "vllm": "vllm",
         "sglang": "vllm"}
    t |= {v for k, v in m.items() if k in ids}
    t.add("audit_log")
    if tier >= 2:
        t |= {"collection_acls", "gpu_server"}
    if p.regulated:
        t.add("restricted_tier")
    if p.air_gapped:
        t.add("air_gap_mode")
    for k in ("post_training", "distillation"):
        if custom[k]["status"] == "planned":
            t |= {k, f"customize_track_{k}"}
    if custom["abliteration"]["status"] == "approved":
        t.add("abliteration")
    return sorted(t)


def has_token(tokens: list[str], token: str) -> bool:
    """Supports alternatives such as 'general_chat_small_or_mid' and 'ollama_or_vllm'."""
    if token in tokens:
        return True
    if "_or_" in token:
        head, *rest = token.split("_or_")
        prefix = head.rsplit("_", 1)[0] + "_" if "_" in head else ""
        options = [head] + [(prefix + r) if prefix and not r.startswith(prefix) else r for r in rest]
        return any(o in tokens for o in options)
    return False


# =========================================================================== customization

def customization_track(p: Profile, plan: dict, cfg: AdvisorConfig) -> dict:
    out = {}
    g = plan["general"]
    gm = cfg.model(g["id"]) if g else None
    pt = p.customization["post_training"]
    if not pt.requested:
        out["post_training"] = {"status": "not_requested"}
    elif not pt.justification.strip():
        out["post_training"] = {"status": "blocked", "reason": "Requested without a justification."}
    elif gm and gm["allows_fine_tuning"] == "no":
        out["post_training"] = {"status": "blocked", "reason": f"{gm['name']}'s license does not allow fine-tuning."}
    else:
        out["post_training"] = {
            "status": "planned", "base_model": gm["name"] if gm else None,
            "license_check": gm["allows_fine_tuning"] if gm else "unknown",
            "gate": "RAG first. Start only when the golden-set eval shows RAG alone misses domain terms or behavior.",
            "requires": ["dataset sensitivity review", "versioned LoRA adapters", "before/after eval on the golden set",
                         "runs locally; weights and data never leave"],
            "justification": pt.justification}
    di = p.customization["distillation"]
    if not di.requested:
        out["distillation"] = {"status": "not_requested"}
    elif not di.justification.strip():
        out["distillation"] = {"status": "blocked", "reason": "Requested without a justification."}
    else:
        teachers = [m for m in cfg.models if m["role"] == "general" and license_ok(m, p)
                    and m["allows_distillation"] != "no" and m["quality_rank"] > (gm["quality_rank"] if gm else 0)]
        teacher = max(teachers, key=lambda m: m["quality_rank"]) if teachers else None
        if not teacher:
            out["distillation"] = {"status": "blocked", "reason": "No larger catalog model whose license allows distillation."}
        else:
            out["distillation"] = {
                "status": "planned", "teacher": teacher["name"], "teacher_license": teacher["license"],
                "teacher_distillation_allowed": teacher["allows_distillation"],
                "student": gm["name"] if gm else None,
                "why": "Larger-model quality on the serving hardware / high concurrency.",
                "note": "The teacher can run temporarily on rented or borrowed hardware you control; the student serves users.",
                "requires": ["teacher license permits distillation", "before/after eval", "versioned promotion"],
                "justification": di.justification}
    ab = p.customization["abliteration"]
    if not ab.requested:
        out["abliteration"] = {"status": "not_requested"}
    else:
        why_not = []
        if not ab.justification.strip():
            why_not.append("no recorded justification")
        if not ab.admin_approved:
            why_not.append("no admin approval")
        if p.org_type == "household" or "children" in p.regulated_data:
            why_not.append("never for household or children's-data profiles")
        out["abliteration"] = ({"status": "refused", "reason": "; ".join(why_not)} if why_not else {
            "status": "approved", "justification": ab.justification,
            "guardrails": ["Gateway content filters stay on", "every request audited", "RBAC limits who can use the model",
                           "separate model id, never the default", "documented in the threat model (ADR-007)"]})
    return out


# =========================================================================== why local, blockers

def why_local(p: Profile, plan: dict) -> list[dict]:
    where = "this computer" if p.users == 1 else "your network"
    text = {
        "privacy": f"Prompts, documents and answers never leave {where}. That is a simpler guarantee than any contract a provider can offer"
                   + (", and it matters for the regulated data you hold." if p.regulated else "."),
        "security": "You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.",
        "availability": "No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month"
                        + (", which matters most for your always-on agents." if p.agents_always_on else "."),
        "customization": "Open weights let you train on your own material or distill a faster model, locally, without handing data or weights to a provider.",
    }
    order = [x for x in p.priorities if x in text] + [x for x in ("privacy", "security", "availability") if x not in p.priorities]
    if "customization" in p.priorities and "customization" not in order:
        order.append("customization")
    return [{"benefit": b, "weight": len(order) - i, "text": text[b]} for i, b in enumerate(order)]


def blocker_guidance(p: Profile) -> list[dict]:
    g = {
        "cost": "Start with the honest cost comparison below and the smallest pilot; only buy hardware after the pilot proves value.",
        "expertise": "Use the simplest stack (Ollama + Open WebUI) and follow the numbered setup steps; no command line needed after install.",
        "security_review": "A security review packet (data flows, egress policy, threat model, model provenance and licenses, pinning and update process) is generated next to this file.",
        "where_to_begin": "Follow the two-week pilot plan on the hardware you already have.",
    }
    return [{"blocker": b, "guidance": g[b]} for b in p.blockers]


def pilot_plan(p: Profile) -> list[str]:
    return [
        "Days 1-2: run the advisor, install only the recommended runtime and model, confirm outbound traffic is blocked (make egress-test).",
        "Days 3-4: write 20-30 real questions your team asks (the golden set) and record today's answers and time spent.",
        "Days 5-7: daily use by one or two people; log what worked and what did not.",
        "Days 8-10: if documents matter, ingest one folder and test search with citations against the golden set.",
        "Days 11-12: review the audit log, memory use and speed against the estimates here.",
        "Days 13-14: decide go / adjust / stop, using the golden-set results and the cost comparison.",
    ]


# =========================================================================== top level

def recommend(p: Profile, cfg: AdvisorConfig) -> dict:
    from .tco import tco

    enabled, excluded_caps = derive_capabilities(p, cfg)
    tier = choose_tier(p, cfg)
    caps = cfg.pricing["budget_caps_usd"]
    warnings: list[str] = []
    procurement = None
    upgrades: list[dict] = []
    gbb: list[dict] = []

    if p.hardware.known:
        plan = plan_for_hardware(p, p.hardware, cfg, enabled, tier)
        if not plan["acceptable"]:
            upgrades = [_public(o) for o in evaluate_options(p, cfg, enabled, tier, None)[:3]]
        elif p.budget != "none":
            cur = cfg.model(plan["general"]["id"])["quality_rank"]
            seen, picks = set(), []
            for o in evaluate_options(p, cfg, enabled, tier, caps[p.budget], min_rank=cur + 8):
                if o["general_model"] not in seen:
                    seen.add(o["general_model"])
                    picks.append(_public(o))
            gbb = picks[:3]
    else:
        within = evaluate_options(p, cfg, enabled, tier, caps[p.budget] if p.budget != "none" else None)
        if within and p.budget != "none":
            choice = within[0]
        else:
            allopts = evaluate_options(p, cfg, enabled, tier, None)
            choice = allopts[0] if allopts else None
            if choice and p.budget != "none":
                warnings.append(f"No listed option fits the {p.budget} budget; the cheapest one that does is shown.")
            if choice and p.budget == "none":
                warnings.append("No hardware given and no budget set: showing the cheapest option that fits.")
        if choice:
            plan = choice["plan"]
            procurement = _public(choice)
            seen = {choice["general_model"]}
            for o in within[1:]:
                if o["general_model"] not in seen and o["rank"] >= choice["rank"] + 8:
                    seen.add(o["general_model"])
                    gbb.append(_public(o))
            gbb = gbb[:2]
        else:
            plan = plan_for_hardware(p, Hardware(known=True, platform="cpu", ram_gb=16, source="none"), cfg, enabled, tier)
            warnings.append("No listed workstation fits: this profile has outgrown local workstations "
                            "(Enterprise AI infrastructure, out of scope).")

    inc, exc = choose_components(p, tier, plan, enabled, excluded_caps, cfg)
    custom = customization_track(p, plan, cfg)
    if custom["abliteration"]["status"] == "refused":
        exc.append({"id": "abliteration", "name": "Abliteration (refusal removal)", "reason": f"Requested but refused: {custom['abliteration']['reason']}."})
    excluded_models = []
    for cap, label in (("vision", "Vision-language model"), ("code", "Code model"), ("stt", "Speech-to-text model")):
        if cap not in enabled:
            excluded_models.append({"id": cap, "name": label, "reason": excluded_caps[cap]})
    if tier < 3:
        excluded_models.append({"id": "fast_small", "name": "Separate small routing/summary model",
                                "reason": "The general model handles summaries; saves memory at this tier."})
    for k, label in (("post_training", "Post-training on your data"), ("distillation", "Distillation to a smaller model")):
        if custom[k]["status"] != "planned":
            excluded_models.append({"id": k, "name": label, "reason": custom[k].get("reason", "Not requested.")})

    s = cfg.rules["sizing"]
    model_disk = sum(m["memory"]["weights_gb"] for m in ([plan["general"]] if plan["general"] else []) + plan["specialists"])
    model_disk += sum(a.get("memory_gb", a.get("memory", {}).get("weights_gb", 0)) for a in plan["aux"])
    data_disk = (p.volume_gb + 12 * p.growth_gb_per_month) * s["index_overhead_factor"]
    disk_total = round(model_disk + data_disk + s["base_disk_gb"], 1)
    free = p.hardware.free_disk_gb
    if free is not None and disk_total > free:
        warnings.append(f"Estimated disk need {disk_total} GB (incl. one year of growth) exceeds free disk {free} GB.")

    selected = ([plan["general"]] if plan["general"] else []) + plan["specialists"] + plan["aux"]
    lic = license_notes(selected, p, cfg) if p.licensing_use == "commercial" else []
    for ln in lic:
        if not ln["osi_open_source"]:
            warnings.append(f"{ln['model']} is open-weight but not OSI open source ({ln['license']}); read its terms.")
    if p.connectors and p.air_gapped:
        warnings.append("Connectors were requested but the profile is air-gapped; they stay off.")
    if p.offline_required and "web_research" in enabled:
        warnings.append("Offline operation is required: web research works only while a connection is available.")
    for a in p.assumptions:
        warnings.append(f"Assumption: {a}")

    rec = {
        "advisor_version": "0.2.0",
        "profile_id": p.id, "profile_name": p.org_name or p.id, "description": p.description,
        "skill": p.skill, "latency": p.latency, "tier": tier,
        "tier_name": cfg.rules["tiers"][tier]["name"], "setup_effort": cfg.rules["tiers"][tier]["setup_effort"],
        "capabilities": {"enabled": enabled, "excluded": excluded_caps},
        "plan": plan, "procurement": procurement,
        "market_tier": (procurement or {}).get("market_tier") or ("consumer_ai_workstation" if plan["general"] else "enterprise_ai_infrastructure"),
        "components": {"included": inc, "excluded": exc}, "excluded_models": excluded_models,
        "upgrades_required": bool(p.hardware.known and not plan["acceptable"]),
        "upgrade_options": upgrades, "good_better_best": gbb,
        "disk_estimate_gb": disk_total,
        "licenses": lic, "customization": custom,
        "why_local": why_local(p, plan), "blockers": blocker_guidance(p),
        "pilot_plan": pilot_plan(p) if "where_to_begin" in p.blockers or "cost" in p.blockers else [],
        "generate": (["security_review_packet"] if "security_review" in p.blockers else []) + ["tco_comparison"],
        "privacy": privacy_posture(p, tier, enabled, cfg),
        "cloud_burst": {"enabled": bool(p.cloud_burst and not p.air_gapped)},
        "warnings": warnings + plan["notes"],
        "estimates_note": "All memory, speed, disk and price figures are estimates, not benchmarks.",
    }
    rec["tco"] = tco(p, rec, cfg)
    rec["hardware_cost"] = rec["tco"]["local"]["hardware_note"]
    rec["tokens"] = build_tokens(p, tier, plan, enabled, inc, custom)
    return rec


def privacy_posture(p: Profile, tier: int, enabled: dict, cfg: AdvisorConfig) -> dict:
    if p.air_gapped:
        egress = "None, ever. Models and updates arrive on an offline drive with checksums."
    else:
        egress = ("Deny all outbound by default. Model downloads happen only in a separate `make pull` step." +
                  (" SearXNG may reach search engines (named exception)." if "web_research" in enabled else ""))
    return {
        "egress": egress,
        "encryption_at_rest": "Full-disk encryption required (FileVault / LUKS / BitLocker); encrypted backups.",
        "encryption_in_transit": "localhost only" if tier == 1 else "TLS on the LAN via Caddy's local CA",
        "remote_access": "None by default; if needed, WireGuard/Tailscale only. Never port-forward.",
        "retention_days": p.retention.get("conversations_days", cfg.rules["privacy"]["retention_days_default"]),
        "documents_retention": p.retention.get("documents", "keep_until_deleted"),
        "memory_cards": p.retention.get("memory_cards", "user_managed"),
        "sensitivity_tiers": ["Public", "Personal", "Confidential", "Restricted"],
        "restricted_tier_enforced": p.regulated,
        "acl_enforcement": ("n/a (single user)" if tier == 1 else
                            "Per-user and per-collection ACLs enforced in the database query (Postgres row-level security), not only in the UI."),
        "collections": (p.departments or ["shared", "personal"]) if tier >= 2 else ["personal"],
        "regulated_categories": p.regulated_data,
        "connectors": ", ".join(p.connectors) + " (read-only)" if p.connectors and not p.air_gapped else "none",
        "web_research": "on" if "web_research" in enabled else "off",
        "cloud_burst": "opt-in per request" if p.cloud_burst and not p.air_gapped else "off",
        "compliance_note": ("Regulated data present. These controls support privacy obligations but do not by themselves make "
                            "you compliant with HIPAA, PCI, GDPR or similar; review with your compliance advisor.") if p.regulated else None,
    }
