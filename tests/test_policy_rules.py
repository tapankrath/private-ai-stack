"""Licenses and triggers, customization gates, runtime rules, honest cost (Appendix A9)."""

import datetime as dt

import pytest

from advisor.engine import choose_runtime, recommend
from advisor.render import render_markdown
from advisor.tco import stale_entries
from advisor import sizing
from advhelpers import make_profile

COMMERCIAL = dict(org={"type": "small_business"}, users={"count": 5, "peak_concurrent": 2}, licensing={"use": "commercial"})
BIG_MAC = {"override": {"platform": "apple_silicon", "ram_gb": 256, "chip": "apple_m5_max"}}


def selected(rec):
    p = rec["plan"]
    return ([p["general"]] if p["general"] else []) + p["specialists"] + p["aux"]


# ------------------------------------------------------------------ licenses

def test_open_weight_vs_osi_fields(cfg):
    for m in cfg.models:
        assert m["open_weight"] is True
        assert m["osi_open_source"] == (m["license"] in {"Apache-2.0", "MIT"}), m["id"]


def test_commercial_never_selects_non_commercial(cfg):
    rec = recommend(make_profile(jobs=["coding", "image_understanding"], hardware=BIG_MAC, **COMMERCIAL), cfg)
    for m in selected(rec):
        assert m["commercial_use"] != "no", m["name"]


def test_blocking_trigger_requires_acknowledgement(cfg):
    # Qwen3.8 Flash-Next has a blocking commercial trigger; force a profile where it would win.
    hw = {"override": {"platform": "apple_silicon", "ram_gb": 256, "chip": "apple_m5_max"}}
    personal = recommend(make_profile(hardware=hw, usage={"latency": "batch_ok"}), cfg)
    commercial = recommend(make_profile(hardware=hw, usage={"latency": "batch_ok"}, **COMMERCIAL), cfg)
    acked = recommend(make_profile(hardware=hw, usage={"latency": "batch_ok"},
                                   **{**COMMERCIAL, "licensing": {"use": "commercial",
                                                                  "acknowledged_triggers": ["qwen-community-commercial-review"]}}), cfg)
    assert personal["plan"]["general"]["id"] == "qwen3.8-flash-next"
    assert commercial["plan"]["general"]["id"] != "qwen3.8-flash-next"
    assert acked["plan"]["general"]["id"] == "qwen3.8-flash-next"
    ln = [x for x in acked["licenses"] if x["model"] == "Qwen3.8 Flash-Next"][0]
    assert any(t["status"] == "acknowledged" for t in ln["triggers"])
    assert "Trigger `qwen-community-commercial-review`" in render_markdown(acked)


def test_triggers_surfaced_for_commercial(cfg):
    hw = {"override": {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 12, "gpu_model": "rtx_3060"}}
    rec = recommend(make_profile(hardware=hw, **COMMERCIAL), cfg)
    for ln in rec["licenses"]:
        if ln["model"].startswith("Llama"):
            assert ln["triggers"]


# ------------------------------------------------------------------ customization

def _ab(**kw):
    item = {"requested": True, "justification": "Refusals block legitimate security research.", "admin_approved": True}
    item.update(kw)
    return {"abliteration": item}


def test_abliteration_off_by_default(cfg, profiles):
    for prof in profiles.values():
        assert "abliteration" not in recommend(prof, cfg)["tokens"]


def test_abliteration_requires_request_approval_and_justification(cfg):
    assert "abliteration" in recommend(make_profile(customization=_ab(), **COMMERCIAL), cfg)["tokens"]
    for bad in (_ab(admin_approved=False), _ab(justification=""), _ab(requested=False)):
        assert "abliteration" not in recommend(make_profile(customization=bad, **COMMERCIAL), cfg)["tokens"]


def test_abliteration_never_for_households_or_children(cfg):
    hh = make_profile(org={"type": "household"}, users={"count": 3, "peak_concurrent": 1}, customization=_ab())
    kids = make_profile(customization=_ab(), sensitivity={"level": "regulated", "regulated_data": ["children"]}, **COMMERCIAL)
    for p in (hh, kids):
        rec = recommend(p, cfg)
        assert "abliteration" not in rec["tokens"]
        assert rec["customization"]["abliteration"]["status"] == "refused"


def test_post_training_needs_justification(cfg):
    yes = recommend(make_profile(customization={"post_training": {"requested": True, "justification": "Domain terms."}}), cfg)
    no = recommend(make_profile(customization={"post_training": {"requested": True, "justification": ""}}), cfg)
    assert "customize_track_post_training" in yes["tokens"] and "post_training" not in no["tokens"]
    assert "RAG first" in yes["customization"]["post_training"]["gate"]


def test_distillation_teacher_license_allows_it(cfg):
    rec = recommend(make_profile(customization={"distillation": {"requested": True, "justification": "Serve many users."}},
                                 **COMMERCIAL), cfg)
    d = rec["customization"]["distillation"]
    assert d["status"] == "planned" and d["teacher_distillation_allowed"] != "no"


# ------------------------------------------------------------------ runtimes

def _hw(cfg, **kw):
    from advisor.profile import Hardware
    return sizing.resolve(Hardware(known=True, **kw), cfg)


def test_runtime_never_tgi(cfg):
    p = make_profile(runtime_preferences={"preferred": "tgi"})
    rt = choose_runtime(p, _hw(cfg, platform="nvidia", vram_gb=24), cfg)
    assert rt["engine"] != "tgi" and any("maintenance" in n for n in rt["notes"])
    for rule in cfg.runtimes["rules"]:
        assert rule["choose"] != "tgi"


def test_tensorfold_only_when_experimental_allowed(cfg):
    hw = _hw(cfg, platform="apple", ram_gb=64)
    assert choose_runtime(make_profile(runtime_preferences={"preferred": "tensorfold"}), hw, cfg)["engine"] != "tensorfold"
    rt = choose_runtime(make_profile(runtime_preferences={"preferred": "tensorfold", "allow_experimental": True}), hw, cfg)
    assert rt["engine"] == "tensorfold" and any("EXPERIMENTAL" in n for n in rt["notes"])


def test_lm_studio_only_when_closed_source_accepted(cfg):
    hw = _hw(cfg, platform="apple", ram_gb=64)
    assert choose_runtime(make_profile(runtime_preferences={"preferred": "lm_studio"}), hw, cfg)["engine"] != "lm_studio"
    rt = choose_runtime(make_profile(runtime_preferences={"preferred": "lm_studio", "accept_closed_source": True}), hw, cfg)
    assert rt["engine"] == "lm_studio" and any("closed source" in n for n in rt["notes"])


def test_runtime_rules(cfg):
    mac = _hw(cfg, platform="apple", ram_gb=64)
    assert choose_runtime(make_profile(org={"technical_skill": "non_technical"}), mac, cfg)["engine"] == "ollama"
    nv = _hw(cfg, platform="nvidia", vram_gb=48)
    many = make_profile(users={"count": 20, "peak_concurrent": 10}, org={"type": "small_business"}, licensing={"use": "commercial"})
    assert choose_runtime(many, nv, cfg)["engine"] == "vllm"
    assert choose_runtime(many, mac, cfg)["engine"] == "ollama"


# ------------------------------------------------------------------ honest cost

def test_every_recommendation_has_why_local_and_cost(cfg, profiles):
    for prof in profiles.values():
        md = render_markdown(recommend(prof, cfg))
        assert "## Why Local (Beyond Price)" in md and "## Cost Comparison (Honest View)" in md


def test_cost_verdict_is_honest(cfg, profiles):
    for prof in profiles.values():
        t = recommend(prof, cfg)["tco"]
        cheapest = min(o["total_usd"] for o in t["options"].values())
        assert t["local_is_cheapest"] == (t["options"]["local"]["total_usd"] == cheapest)
        if not t["local_is_cheapest"]:
            assert "cheaper than local" in t["verdict"] and "not price" in t["verdict"]


def test_three_benefits_always_documented(cfg, profiles):
    for prof in profiles.values():
        benefits = {w["benefit"] for w in recommend(prof, cfg)["why_local"]}
        assert {"privacy", "security", "availability"} <= benefits


def test_priorities_order_why_local(cfg):
    rec = recommend(make_profile(priorities=["availability", "privacy", "security"]), cfg)
    assert rec["why_local"][0]["benefit"] == "availability"


def test_stale_prices_flagged(cfg):
    entries = [("fresh", {"last_verified": "2026-10-01"}), ("old", {"last_verified": "2026-01-01"}), ("never", {"last_verified": None})]
    assert stale_entries(cfg, entries, today=dt.date(2026, 10, 19)) == ["old", "never"]


def test_prices_come_from_pricing_yaml(cfg):
    from advisor import engine, tco
    import inspect
    src = inspect.getsource(engine) + inspect.getsource(tco)
    assert "$4,999" not in src and "6950" not in src
