"""Token Savings Coach hand-off (site/handoff.js) and the shared price list (site/ai-prices.js)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(src: str) -> str:
    return subprocess.check_output(["node", "-e", src], cwd=ROOT, text=True)


@needs_node
def test_handoff_round_trip_keeps_only_known_fields():
    out = _node("""
const h = require('./site/handoff.js');
const code = h.encode({monthly_usd: 340, users: 5, jobs: ['writing', 'not_a_job'], regulated: ['legal'], evil: '<script>'});
const back = h.decode('Here is my code: ' + code + ' thanks');
process.stdout.write(JSON.stringify({code, back}));
""")
    r = json.loads(out)
    assert r["code"].startswith("TSC1.")
    assert r["back"] == {"v": 1, "monthly_usd": 340, "users": 5, "jobs": ["writing"], "regulated": ["legal"]}


@needs_node
@pytest.mark.parametrize("bad", ["", "TSC1.abc", "TSC1." + "e30"])  # garbage, cut off, {} (no monthly cost)
def test_handoff_rejects_bad_codes(bad):
    out = _node(f"const h=require('./site/handoff.js');try{{h.decode({json.dumps(bad)});process.stdout.write('accepted')}}catch(e){{process.stdout.write('rejected')}}")
    assert out == "rejected"


@needs_node
def test_local_suggestion_triggers():
    out = _node("""
const h = require('./site/handoff.js');
process.stdout.write(JSON.stringify([h.localWorthALook({monthly_usd: 20}), h.localWorthALook({monthly_usd: 20, regulated: ['health']}),
  h.localWorthALook({monthly_usd: 20, agents: 1}), h.localWorthALook({monthly_usd: 250})]));
""")
    assert json.loads(out) == [None, "private", "agents", "spend"]


def test_shared_prices_match_pricing_yaml():
    """site/ai-prices.js must be rebuilt after pricing.yaml changes, and mid-tier must equal the Advisor's frontier price."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build-site.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert (ROOT / "site" / "ai-prices.js").read_text() == mod.build_prices(), "run: python scripts/build-site.py"
    pr = yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text())
    fr = pr["api"]["frontier"]["usd_per_million"]
    assert (pr["api_tiers"]["mid"]["input"], pr["api_tiers"]["mid"]["output"]) == (fr["input"], fr["output"])
    t = pr["api_tiers"]
    assert t["small"]["input"] < t["mid"]["input"] < t["top"]["input"]
