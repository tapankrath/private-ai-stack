"""Memory and speed estimates. Every number produced here is an ESTIMATE.

Memory uses TOTAL parameters. Decode speed uses ACTIVE parameters (MoE vs dense) and memory
bandwidth. Prefill (reading the prompt) uses ACTIVE parameters and compute (TFLOPS).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import AdvisorConfig
from .profile import Hardware


@dataclass
class UsableMemory:
    gb: float
    basis: str


def resolve(hw: Hardware, cfg: AdvisorConfig) -> Hardware:
    """Fill hw_key, bandwidth and TFLOPS from the reference table (conservative defaults)."""
    ref = cfg.hardware
    key = hw.hw_key
    if not key or (key not in ref["bandwidth_gbps"] and not hw.bandwidth_gbps):
        d = ref["defaults_by_platform"].get(hw.platform)
        if isinstance(d, list):
            per_gpu = hw.vram_gb / max(hw.gpu_count, 1)
            key = next(x["hw_key"] for x in d if per_gpu <= x["max_vram_gb"])
        elif d:
            key = d["hw_key"]
    bw = hw.bandwidth_gbps or ref["bandwidth_gbps"].get(key)
    tf = hw.tflops_fp16 or ref["tflops_fp16"].get(key)
    return replace(hw, hw_key=key, bandwidth_gbps=bw, tflops_fp16=tf)


def usable_memory(hw: Hardware, cfg: AdvisorConfig) -> UsableMemory:
    f = cfg.hardware["unified_usable_fraction"]
    if hw.usable_gb is not None:
        return UsableMemory(float(hw.usable_gb), f"{hw.usable_gb:g} GB usable for models (option spec, estimate)")
    if hw.platform == "apple":
        frac = f["apple_up_to_36gb"] if hw.ram_gb <= 36 else f["apple_above_36gb"]
        return UsableMemory(round(hw.ram_gb * frac, 2),
                            f"{int(frac * 100)}% of {hw.ram_gb:g} GB unified memory (macOS GPU limit, default)")
    if hw.platform in {"nvidia_unified", "amd_unified"}:
        frac = f[hw.platform]
        return UsableMemory(round(hw.ram_gb * frac, 2), f"{int(frac * 100)}% of {hw.ram_gb:g} GB unified memory (OS headroom kept)")
    if hw.platform in {"nvidia", "amd"}:
        n = f"{hw.gpu_count} x GPU" if hw.gpu_count > 1 else "GPU"
        return UsableMemory(float(hw.vram_gb), f"{hw.vram_gb:g} GB VRAM ({n}); models kept fully on GPU")
    reserve = cfg.rules["sizing"]["cpu_only_os_reserve_gb"]
    return UsableMemory(max(hw.ram_gb - reserve, 0.0), f"{hw.ram_gb:g} GB RAM minus {reserve} GB for the OS (CPU-only, slow)")


def compute_class(hw: Hardware, cfg: AdvisorConfig) -> str:
    c = cfg.hardware["compute_class"]
    tf = hw.tflops_fp16 or 0
    return "high" if tf >= c["high"] else "medium" if tf >= c["medium"] else "low"


def kv_bytes_per_token(model: dict) -> int:
    a = model.get("attention")
    if a:
        return 2 * a["kv_layers"] * a["kv_heads"] * a["head_dim"] * 2  # K+V, fp16
    return int(model["kv_bytes_per_token_est"])


def weights_gb(model: dict, quant: str, cfg: AdvisorConfig) -> float:
    return model["total_params_b"] * cfg.catalog["quantizations"][quant] / 8


def kv_cache_gb(model: dict, context: int, sessions: int, kv_mult: float, cfg: AdvisorConfig) -> float:
    fill = 1.0 if sessions <= 1 else cfg.rules["sizing"]["multi_session_context_fill"]
    return kv_bytes_per_token(model) * context * sessions * fill * kv_mult / 1e9


def llm_footprint(model: dict, quant: str, context: int, sessions: int, kv_type: str, cfg: AdvisorConfig) -> dict:
    kv_mult = cfg.rules["sizing"]["kv_cache_types"][kv_type]
    w = weights_gb(model, quant, cfg)
    kv = kv_cache_gb(model, context, sessions, kv_mult, cfg)
    overhead = cfg.catalog["runtime_overhead_gb"]
    return {"weights_gb": round(w, 2), "kv_cache_gb": round(kv, 2), "overhead_gb": overhead,
            "total_gb": round(w + kv + overhead, 2)}


def tokens_per_sec(model: dict, quant: str, bw: float | None, cfg: AdvisorConfig) -> float | None:
    """Single-stream DECODE estimate: bandwidth / bytes read per token (active params)."""
    if not bw:
        return None
    gb_per_token = model["active_params_b"] * cfg.catalog["quantizations"][quant] / 8
    return round(bw * cfg.rules["sizing"]["tokens_per_sec"]["efficiency"] / gb_per_token, 1)


def prefill_tokens_per_sec(model: dict, tflops: float | None, cfg: AdvisorConfig) -> float | None:
    """PREFILL estimate: compute / (2 * active params) FLOPs per prompt token."""
    if not tflops:
        return None
    eff = cfg.rules["sizing"]["prefill_efficiency"]
    return round(tflops * 1e12 * eff / (2 * model["active_params_b"] * 1e9), 0)
