"""Appendix A9: every starter profile validates and meets its `expected:` block."""

import pytest
import yaml

from advisor.engine import has_token, recommend
from advisor.profile import ProfileError, profile_from_dict, validate_raw
from advhelpers import PROFILE_FILES, ROOT, raw_profiles

IDS = [p.stem for p in PROFILE_FILES]


def test_template_and_all_profiles_validate():
    validate_raw(yaml.safe_load((ROOT / "profiles" / "_template.yaml").read_text()))
    for raw in raw_profiles().values():
        validate_raw(raw)


def test_five_starter_profiles_present():
    assert set(IDS) == {"solo-researcher-16gb", "household-photos-docs", "law-accounting-office-10",
                        "smb-50-sales-ops", "software-team-8"}


@pytest.fixture(scope="module")
def results(cfg):
    out = {}
    for name, raw in raw_profiles().items():
        out[name] = (raw["expected"], recommend(profile_from_dict(raw), cfg))
    return out


@pytest.mark.parametrize("name", IDS)
def test_tier_and_runtime(results, name):
    exp, rec = results[name]
    assert rec["tier"] == exp["tier"]
    assert has_token(rec["tokens"], exp["runtime"]), (exp["runtime"], rec["plan"]["runtime"])


@pytest.mark.parametrize("name", IDS)
def test_must_include(results, name):
    exp, rec = results[name]
    missing = [t for t in exp.get("must_include", []) + exp.get("must_include_extra", []) if not has_token(rec["tokens"], t)]
    assert not missing, f"{name} missing {missing}; tokens={rec['tokens']}"


@pytest.mark.parametrize("name", IDS)
def test_must_exclude(results, name):
    exp, rec = results[name]
    present = [t for t in exp.get("must_exclude", []) if has_token(rec["tokens"], t)]
    assert not present, f"{name} must not include {present}"


@pytest.mark.parametrize("name", IDS)
def test_must_generate(results, name):
    exp, rec = results[name]
    for item in exp.get("must_generate", []):
        assert item in rec["generate"], item


@pytest.mark.parametrize("name", IDS)
def test_memory_use_within_limit(results, name):
    exp, rec = results[name]
    assert rec["plan"]["memory_use_pct"] <= exp["max_memory_use_pct"]


@pytest.mark.parametrize("name", IDS)
def test_commercial_license_filter(results, name, cfg):
    exp, rec = results[name]
    if exp.get("license_filter") != "commercial_allowed":
        return
    p = rec["plan"]
    for m in ([p["general"]] if p["general"] else []) + p["specialists"] + p["aux"]:
        assert cfg.model(m["id"])["commercial_use"] != "no", m["name"]
    assert rec["licenses"], "commercial profiles list licenses in the recommendation"


def test_expected_block_is_ignored_by_engine(cfg):
    raw = raw_profiles()["solo-researcher-16gb"]
    a = recommend(profile_from_dict(raw), cfg)
    raw2 = dict(raw, expected={"tier": 3, "must_include": ["vision"]})
    b = recommend(profile_from_dict(raw2), cfg)
    assert a["tokens"] == b["tokens"]


def test_schema_rejects_bad_enums():
    raw = raw_profiles()["solo-researcher-16gb"]
    for path, value in ((("jobs",), ["telepathy"]), (("org", "type"), "alien"), (("hardware", "budget_usd"), "lt_999"),
                        (("workload", "shape"), "sideways"), (("blockers",), ["boredom"])):
        bad = yaml.safe_load(yaml.safe_dump(raw))
        node = bad
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        with pytest.raises(ProfileError):
            validate_raw(bad)


def test_peak_cannot_exceed_users():
    raw = raw_profiles()["household-photos-docs"]
    raw["users"]["peak_concurrent"] = 9
    with pytest.raises(ProfileError):
        profile_from_dict(raw)
