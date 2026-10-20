"""Inclusion / exclusion: capabilities appear only when a job, data type or feature flag needs them."""

import itertools

import pytest

from advisor.engine import recommend
from advhelpers import make_profile

HW = [
    {"platform": "apple_silicon", "ram_gb": 8, "chip": "apple_m1"},
    {"platform": "apple_silicon", "ram_gb": 36, "chip": "apple_m3_pro"},
    {"platform": "apple_silicon", "ram_gb": 128, "chip": "apple_m5_max"},
    {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 12, "ram_gb": 32, "gpu_model": "rtx_3060"},
    {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 24, "ram_gb": 64, "gpu_model": "rtx_4090"},
    {"platform": "nvidia_dgx_spark", "ram_gb": 128},
    {"platform": "amd_ryzen_ai_max", "ram_gb": 128},
    {"platform": "linux_x86", "gpu": "none", "ram_gb": 32},
]
CHAT_RESEARCH = [["general_qa"], ["general_qa", "research"], ["writing", "drafting"], ["research", "conversation_recall"]]


def roles(rec):
    p = rec["plan"]
    return {m["role"] for m in ([p["general"]] if p["general"] else []) + p["specialists"] + p["aux"]}


def comps(rec):
    return {c["id"] for c in rec["components"]["included"]}


@pytest.mark.parametrize("hw,jobs", list(itertools.product(HW, CHAT_RESEARCH)))
def test_chat_research_never_gets_vision_stt_or_code(cfg, hw, jobs):
    rec = recommend(make_profile(jobs=jobs, hardware={"override": hw}), cfg)
    assert not {"vision", "stt", "code"} & roles(rec)
    assert not {"vision", "vision_small", "speech_to_text", "code_model", "ocr_pipeline"} & set(rec["tokens"])
    assert not {"whisper", "ocr"} & comps(rec)


def test_scans_get_ocr_not_vision(cfg):
    rec = recommend(make_profile(jobs=["knowledge_recall", "scanned_docs"], data={"types": ["scans", "pdf"]}), cfg)
    assert "ocr" in rec["capabilities"]["enabled"] and "vision" not in rec["capabilities"]["enabled"]
    assert "vision" not in roles(rec)


def test_image_understanding_adds_vision(cfg):
    rec = recommend(make_profile(jobs=["image_understanding"], hardware={"override": {"platform": "apple_silicon", "ram_gb": 36}}), cfg)
    covered = "vision" in roles(rec) or "vision" in rec["plan"]["general"].get("covers", [])
    assert covered and "vision" in rec["tokens"]


def test_vision_capable_general_model_replaces_separate_vlm(cfg):
    rec = recommend(make_profile(jobs=["general_qa", "image_understanding"],
                                 hardware={"override": {"platform": "apple_silicon", "ram_gb": 128, "chip": "apple_m5_max"}}), cfg)
    g = rec["plan"]["general"]
    if "vision" in g["capabilities"]:
        assert "vision" not in {s["role"] for s in rec["plan"]["specialists"]}


def test_audio_without_recall_or_transcription_adds_nothing(cfg):
    rec = recommend(make_profile(jobs=["general_qa"], data={"types": ["audio"]}), cfg)
    assert "stt" not in rec["capabilities"]["enabled"]


def test_transcription_adds_whisper(cfg):
    rec = recommend(make_profile(jobs=["transcription"]), cfg)
    assert "stt" in roles(rec) and "whisper" in comps(rec)


def test_coding_job_adds_code_model(cfg):
    rec = recommend(make_profile(jobs=["coding"], hardware={"override": {"platform": "linux_x86", "gpu": "nvidia", "vram_gb": 24, "gpu_model": "rtx_4090"}}), cfg)
    assert "code" in roles(rec)


def test_web_research_needs_explicit_flag(cfg):
    off = recommend(make_profile(jobs=["research"], features={"web_research": False}), cfg)
    on = recommend(make_profile(jobs=["research"], features={"web_research": True}), cfg)
    assert "searxng" not in comps(off) and "searxng" in comps(on)


def test_air_gapped_blocks_web_research_connectors_and_cloud(cfg):
    rec = recommend(make_profile(features={"web_research": True, "connectors": ["calendar_readonly"], "cloud_burst": True},
                                 sensitivity={"air_gapped": True}), cfg)
    assert not {"searxng", "connectors", "cloud-burst"} & comps(rec)
    assert rec["privacy"]["egress"].startswith("None, ever")
    assert "air_gap_mode" in rec["tokens"]


def test_context_needs_drive_context(cfg):
    big = {"override": {"platform": "apple_silicon", "ram_gb": 128, "chip": "apple_m5_max"}}
    std = recommend(make_profile(usage={"context_needs": "standard"}, hardware=big), cfg)["plan"]["general"]
    vl = recommend(make_profile(usage={"context_needs": "very_long"}, hardware=big), cfg)["plan"]["general"]
    assert std["context_tokens"] == 8192 and vl["context_tokens"] > 32768


def test_non_english_adds_multilingual_embeddings(cfg):
    rec = recommend(make_profile(jobs=["knowledge_recall"], data={"types": ["pdf"], "languages": ["en", "de"]}), cfg)
    assert [a for a in rec["plan"]["aux"] if a["role"] == "embeddings"][0]["id"] == "bge-m3"


def test_cloud_burst_off_unless_requested(cfg, profiles):
    for prof in profiles.values():
        rec = recommend(prof, cfg)
        assert rec["cloud_burst"]["enabled"] is False
        assert "cloud_burst" not in rec["tokens"]


def test_every_exclusion_has_a_reason(cfg, profiles):
    for prof in profiles.values():
        rec = recommend(prof, cfg)
        for e in rec["components"]["excluded"] + rec["excluded_models"]:
            assert e["reason"], e
        assert all(rec["capabilities"]["excluded"].values())
