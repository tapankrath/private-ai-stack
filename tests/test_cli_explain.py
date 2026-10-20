"""CLI, wizard, and the local-LLM EXPLAIN / INTAKE guards (fake model on 127.0.0.1)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

from advisor.cli import main
from advisor.engine import recommend
from advisor.explain import EgressRefused, assert_loopback, check_explanation, explain, run_intake
from advisor.profile import profile_from_dict
from advisor.render import render_markdown
from advisor.wizard import run_wizard
from advhelpers import ROOT, raw_profiles


def test_cli_run_writes_files_diff_and_packet(tmp_path, capsys):
    prof = ROOT / "profiles" / "law-accounting-office-10.yaml"
    assert main(["run", "-p", str(prof), "-o", str(tmp_path), "--no-detect"]) == 0
    for name in ("recommendation.md", "recommendation.json", ".env", "docker-compose.override.yml",
                 "config/policy.yaml", "models-ollama.txt", "SECURITY_REVIEW_PACKET.md"):
        assert (tmp_path / name).exists(), name
    packet = (tmp_path / "SECURITY_REVIEW_PACKET.md").read_text()
    assert "{{" not in packet and "Gemma" in packet or "Qwen" in packet
    assert main(["run", "-p", str(prof), "-o", str(tmp_path), "--no-detect"]) == 0
    assert "No changes since last run" in capsys.readouterr().out


def test_packet_only_when_security_review_blocker(tmp_path):
    prof = ROOT / "profiles" / "solo-researcher-16gb.yaml"
    main(["run", "-p", str(prof), "-o", str(tmp_path), "-q", "--no-detect"])
    assert not (tmp_path / "SECURITY_REVIEW_PACKET.md").exists()


def test_cli_rejects_bad_profile(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    raw = raw_profiles()["solo-researcher-16gb"]
    raw["jobs"] = ["telepathy"]
    bad.write_text(yaml.safe_dump(raw))
    assert main(["run", "-p", str(bad), "-o", str(tmp_path / "o"), "--no-detect"]) == 2
    assert "telepathy" in capsys.readouterr().err


def test_env_has_no_secrets(cfg, profiles):
    from advisor.render import render_env
    for prof in profiles.values():
        for line in render_env(recommend(prof, cfg)).splitlines():
            if line and not line.startswith("#"):
                key = line.split("=", 1)[0].upper()
                assert not any(w in key for w in ("PASSWORD", "SECRET", "TOKEN", "KEY")), line


def test_wizard_produces_valid_profile():
    answers = iter(["small_business", "it_admin", "10", "4", "1,4,5", "2", "50", "en", "3", "not_sure",
                    "few_seconds", "long", "0", "yes", "linux_x86", "64", "nvidia", "24", "lt_5000", "3", "no"])
    raw = run_wizard(ask=lambda _: next(answers), detect=False)
    p = profile_from_dict(raw)
    assert p.air_gapped and p.assumptions and p.regulated_data == ["legal"]
    assert p.hardware.platform == "nvidia" and p.licensing_use == "commercial"


# ------------------------------------------------------------------ fake local model

class _Fake(BaseHTTPRequestHandler):
    replies: list = []

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        reply = type(self).replies.pop(0) if type(self).replies else "ok"
        body = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_model():
    servers = []

    def start(*replies):
        h = type("H", (_Fake,), {"replies": list(replies)})
        srv = HTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}/v1"

    yield start
    for s in servers:
        s.shutdown()


def test_loopback_only():
    assert_loopback("http://127.0.0.1:11434/v1")
    assert_loopback("http://localhost:11434/v1")
    for url in ("https://api.example.com/v1", "http://192.168.1.10:11434/v1", "http://10.0.0.5/v1"):
        with pytest.raises(EgressRefused):
            assert_loopback(url)


@pytest.mark.parametrize("name", sorted(raw_profiles()))
def test_faithful_explanation_accepted(cfg, profiles, fake_model, name):
    rec = recommend(profiles[name], cfg)
    good = render_markdown(rec)   # the deterministic text is by construction faithful
    text, used, problems = explain(rec, cfg, fake_model(good), "fake")
    assert used, problems


@pytest.mark.parametrize("bad,why", [
    ("I also recommend adding Qwen2.5-VL 7B Instruct in case you need photos later.", "names models"),
    ("Running local is cheaper than any subscription.", "cheaper"),
    ("You could also try abliteration later.", "abliteration"),
])
def test_unfaithful_explanation_rejected(cfg, profiles, fake_model, bad, why):
    rec = recommend(profiles["solo-researcher-16gb"], cfg)
    text = render_markdown(rec).replace("## In One Sentence\n", "## In One Sentence\n" + bad + "\n")
    used_text, used, problems = explain(rec, cfg, fake_model(text), "fake")
    assert not used and any(why in p for p in problems)
    assert used_text == render_markdown(rec)


def test_missing_sections_rejected(cfg, profiles):
    rec = recommend(profiles["solo-researcher-16gb"], cfg)
    assert any("missing sections" in p for p in check_explanation("# Your Private AI Setup\nShort.", rec, cfg))


def test_excluded_capability_outside_dont_need_rejected(cfg, profiles):
    rec = recommend(profiles["solo-researcher-16gb"], cfg)
    text = render_markdown(rec).replace("## What You Need\n", "## What You Need\nA coding model would help.\n")
    assert any("excluded capability 'code'" in p for p in check_explanation(text, rec, cfg))


def test_intake_returns_valid_profile(fake_model):
    raw = raw_profiles()["solo-researcher-16gb"]
    final = "```yaml\n" + yaml.safe_dump(raw) + "```"
    url = fake_model("Who will use it? (a) just me (b) family (c) a team", "```yaml\nnot: valid\n```", final)
    answers = iter(["just me"])
    out = run_intake(url, "fake", ask=lambda _: next(answers), say=lambda _: None)
    assert out["profile_id"] == "solo-researcher-16gb"


def test_intake_refuses_remote():
    with pytest.raises(EgressRefused):
        run_intake("https://example.com/v1", "m", ask=lambda _: "", say=lambda _: None)
