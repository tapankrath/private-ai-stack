"""The browser Advisor (site/advisor-engine.js) must agree with the Python engine on every starter profile."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from advisor.config import load_config
from advisor.engine import recommend
from advisor.profile import load_profile

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

RUNNER = """
require(process.argv[1] + "/site/advisor-data.js"); require(process.argv[1] + "/site/advisor-engine.js");
const E = AdvisorEngine.Engine(ADVISOR_DATA), out = {};
for (const id in ADVISOR_DATA.profiles) out[id] = E.recommend(ADVISOR_DATA.profiles[id]);
process.stdout.write(JSON.stringify(out));
"""


def _summary(r):
    plan = r["plan"]
    g = plan["general"] or {}
    return {
        "tier": r["tier"], "runtime": plan["runtime"], "general": g.get("id"), "quant": g.get("quant"),
        "ctx": g.get("context_tokens"), "kv": g.get("kv_cache_type"), "tps": g.get("tokens_per_sec_est"),
        "mem_used": plan["memory_used_gb"], "ttft": plan["ttft_s_est"],
        "specialists": [(m["id"], m["quant"], m.get("resident")) for m in plan["specialists"]],
        "aux": sorted(m["id"] for m in plan["aux"]),
        "procurement": (r["procurement"] or {}).get("id"),
        "gbb": [o["id"] for o in r["good_better_best"]], "upgrades": [o["id"] for o in r["upgrade_options"]],
        "included": [c["id"] for c in r["components"]["included"]],
        "excluded": [c["id"] for c in r["components"]["excluded"]],
        "enabled": sorted(r["capabilities"]["enabled"]), "tokens": r["tokens"], "disk": r["disk_estimate_gb"],
        "tco": {k: v["total_usd"] for k, v in r["tco"]["options"].items()}, "cheapest": r["tco"]["cheapest"],
        "custom": {k: v["status"] for k, v in r["customization"].items()},
        "warnings": sorted(r["warnings"]),
    }


def test_data_file_is_current():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build-site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "site" / "advisor-data.js").read_text() == mod.build(), "run: python scripts/build-site.py"


def test_guide_page_is_current():
    import importlib.util
    pytest.importorskip("markdown_it")
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build-site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "site" / "guide.html").read_text() == mod.build_guide(), "run: python scripts/build-site.py"


def test_js_matches_python():
    out = subprocess.run(["node", "-e", RUNNER, str(ROOT)], capture_output=True, text=True, check=True).stdout
    js = json.loads(out)
    cfg = load_config()
    profiles = [p for p in sorted((ROOT / "profiles").glob("*.yaml")) if not p.name.startswith("_")]
    assert len(js) == len(profiles) >= 5
    for path in profiles:
        p = load_profile(path)
        py = json.loads(json.dumps(recommend(p, cfg), default=str))
        assert _summary(js[p.id]) == _summary(py), p.id
