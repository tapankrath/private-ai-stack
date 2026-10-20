"""Memory fit (TOTAL params), speed (ACTIVE params), agents, workload shape, procurement."""

import pytest

from advisor import sizing
from advisor.engine import evaluate_options, derive_capabilities, recommend
from advisor.profile import Hardware
from advhelpers import make_profile


def test_kv_bytes_formula(cfg):
    assert sizing.kv_bytes_per_token(cfg.model("llama3.1-8b")) == 2 * 32 * 8 * 128 * 2


def test_memory_uses_total_params_speed_uses_active(cfg):
    moe = cfg.model("qwen3.6-35b-a3b")
    dense = cfg.model("gemma4-31b")
    assert moe["architecture"] == "moe" and moe["total_params_b"] > 10 * moe["active_params_b"]
    # memory: MoE needs at least as much as a ~31B dense model
    assert sizing.weights_gb(moe, "Q4_K_M", cfg) >= sizing.weights_gb(dense, "Q4_K_M", cfg)
    # speed: MoE is much faster on the same machine
    assert sizing.tokens_per_sec(moe, "Q4_K_M", 500, cfg) > 5 * sizing.tokens_per_sec(dense, "Q4_K_M", 500, cfg)
    assert sizing.prefill_tokens_per_sec(moe, 100, cfg) > 5 * sizing.prefill_tokens_per_sec(dense, 100, cfg)


def test_dense_models_have_equal_total_and_active(cfg):
    for m in cfg.models:
        if m.get("architecture") == "dense":
            assert m["total_params_b"] == m["active_params_b"], m["id"]


def test_footprint_sum(cfg):
    fp = sizing.llm_footprint(cfg.model("qwen3-8b"), "Q5_K_M", 8192, 1, "f16", cfg)
    assert fp["total_gb"] == pytest.approx(fp["weights_gb"] + fp["kv_cache_gb"] + fp["overhead_gb"], abs=0.02)


def test_unified_memory_usable(cfg):
    assert sizing.usable_memory(Hardware(platform="apple", ram_gb=16), cfg).gb == pytest.approx(16 * 0.67)
    assert sizing.usable_memory(Hardware(platform="nvidia_unified", ram_gb=128), cfg).gb == pytest.approx(128 * 0.85)
    assert sizing.usable_memory(Hardware(platform="nvidia", vram_gb=24, ram_gb=128), cfg).gb == 24


@pytest.mark.parametrize("hw", [
    {"platform": "apple_silicon", "ram_gb": 16, "chip": "apple_m2"},
    {"platform": "apple_silicon", "ram_gb": 48, "chip": "apple_m4_pro"},
    {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 12, "gpu_model": "rtx_3060"},
    {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 24, "gpu_model": "rtx_4090"},
    {"platform": "nvidia_dgx_spark", "ram_gb": 128},
])
@pytest.mark.parametrize("jobs", [["general_qa"], ["research", "knowledge_recall"], ["coding", "knowledge_recall"]])
def test_headroom_always_kept(cfg, hw, jobs):
    rec = recommend(make_profile(jobs=jobs, data={"types": ["pdf"]}, hardware={"override": hw}), cfg)
    assert rec["plan"]["memory_use_pct"] <= 80.0 + 1e-6


def test_bigger_machine_never_gets_a_worse_model(cfg):
    ranks = []
    for ram in (8, 16, 24, 36, 48, 64, 128):
        rec = recommend(make_profile(hardware={"override": {"platform": "apple_silicon", "ram_gb": ram, "chip": "apple_m4_max"}}), cfg)
        g = rec["plan"]["general"]
        ranks.append(cfg.model(g["id"])["quality_rank"] if g else -1)
    assert ranks == sorted(ranks)


def test_agents_add_sessions_and_pinning(cfg):
    hw = {"override": {"platform": "nvidia_dgx_spark", "ram_gb": 128}}
    base = recommend(make_profile(hardware=hw), cfg)
    agents = recommend(make_profile(hardware=hw, workload={"agents": {"count": 3, "always_on": True}}), cfg)
    assert agents["plan"]["general"]["sessions"] > base["plan"]["general"]["sessions"]
    assert {"agent_watchdog", "model_pinning"} <= set(agents["tokens"])
    assert "agent_watchdog" not in base["tokens"]
    assert any(w["benefit"] == "availability" and "agents" in w["text"] for w in agents["why_local"])


def test_moe_preferred_when_responsiveness_matters(cfg):
    hw = {"override": {"platform": "apple_silicon", "ram_gb": 128, "chip": "apple_m5_max"}}
    fast = recommend(make_profile(usage={"latency": "instant"}, hardware=hw), cfg)["plan"]["general"]
    assert fast["architecture"] == "moe"


def test_procurement_mode_when_no_hardware(cfg):
    rec = recommend(make_profile(org={"type": "small_business"}, users={"count": 10, "peak_concurrent": 4},
                                 hardware={"override": {}, "budget_usd": "lt_5000"}, licensing={"use": "commercial"}), cfg)
    assert rec["procurement"] and rec["procurement"]["price_usd"][1] <= 5000
    assert rec["plan"]["acceptable"]


def test_prompt_heavy_prefers_compute_generation_heavy_prefers_bandwidth(cfg):
    common = dict(org={"type": "small_business"}, users={"count": 5, "peak_concurrent": 2},
                  hardware={"override": {}, "budget_usd": "lt_10000"}, licensing={"use": "commercial"})
    ph = make_profile(workload={"shape": "prompt_heavy"}, **common)
    gh = make_profile(workload={"shape": "generation_heavy"}, **common)
    en, _ = derive_capabilities(ph, cfg)
    top_ph = evaluate_options(ph, cfg, en, 2, 10000)[0]
    top_gh = evaluate_options(gh, cfg, en, 2, 10000)[0]
    assert top_ph["plan"]["hardware"]["compute_class"] == "high" or top_ph["option"]["platform"] in {"nvidia", "nvidia_unified"}
    assert (top_gh["plan"]["bandwidth_gbps"] or 0) >= 400


def test_too_small_hardware_gets_upgrade_options(cfg):
    rec = recommend(make_profile(org={"type": "small_business"}, users={"count": 10, "peak_concurrent": 4},
                                 licensing={"use": "commercial"},
                                 hardware={"override": {"platform": "linux_x86", "gpu": "none", "ram_gb": 8}}), cfg)
    assert rec["upgrades_required"] and 1 <= len(rec["upgrade_options"]) <= 3


def test_no_gpu_server_for_solo_light_user(cfg):
    rec = recommend(make_profile(hardware={"budget_usd": "lt_20000"}), cfg)
    assert rec["tier"] == 1 and "gpu_server" not in rec["tokens"]
    assert rec["tco"]["local"]["breakdown"]["hardware"] == 0
