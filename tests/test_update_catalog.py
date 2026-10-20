"""update-catalog: refuses without --allow-egress; verifies licenses with an injected fetcher."""

import importlib.machinery
import importlib.util

from advhelpers import ROOT

loader = importlib.machinery.SourceFileLoader("update_catalog", str(ROOT / "scripts" / "update-catalog"))
spec = importlib.util.spec_from_loader("update_catalog", loader)
uc = importlib.util.module_from_spec(spec)
loader.exec_module(uc)


def test_refuses_without_flag(capsys):
    assert uc.main([]) == 2
    assert "--allow-egress" in capsys.readouterr().err


def test_detects_license_mismatch(cfg):
    cards = {
        "Qwen/Qwen3-8B": {"cardData": {"license": "apache-2.0"}, "sha": "abc123"},
        "microsoft/phi-4": {"tags": ["license:other"], "sha": "def456"},   # catalog says MIT
    }
    models = [cfg.model("qwen3-8b"), cfg.model("phi4-14b"), cfg.model("whisper-small")]
    models[2] = {**models[2], "runtimes": {}}
    res = uc.verify(models, fetch=lambda repo: cards[repo])
    assert res["qwen3-8b"]["status"] == "ok"
    assert res["qwen3-8b"]["pin_revision"] == "abc123"
    assert "MISMATCH" in res["phi4-14b"]["status"]
    assert res["whisper-small"]["status"] == "skipped"
