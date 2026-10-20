"""Generated Compose files must start on hardware other than NVIDIA: CPU-only and AMD hosts."""
import copy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from advisor.config import load_config
from advisor.engine import recommend
from advisor.profile import profile_from_dict
from advisor.render import render_env, render_override

ROOT = Path(__file__).resolve().parent.parent
BASE = yaml.safe_load((ROOT / "profiles" / "solo-researcher-16gb.yaml").read_text())
BASE.pop("expected")


def rec_for(platform, gpu, vram=0):
    raw = copy.deepcopy(BASE)
    raw["hardware"]["override"] = {"platform": platform, "gpu": gpu, "ram_gb": 64, "vram_gb": vram}
    return recommend(profile_from_dict(raw), load_config())


@pytest.mark.parametrize("platform,gpu,vram,amd", [
    ("linux_x86", "none", 0, False), ("linux_x86", "amd", 24, True), ("amd_ryzen_ai_max", "integrated", 0, True)])
def test_no_nvidia_reservation_off_nvidia(platform, gpu, vram, amd):
    rec = rec_for(platform, gpu, vram)
    ov = render_override(rec)
    assert "deploy: !reset null" in ov
    assert ("/dev/kfd" in ov) is amd
    assert ("OLLAMA_IMAGE=ollama/ollama:rocm" in render_env(rec)) is amd


def test_nvidia_keeps_reservation():
    ov = render_override(rec_for("linux_x86", "nvidia", 24))
    assert "!reset" not in ov


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_compose_accepts_cpu_override(tmp_path):
    rec = rec_for("linux_x86", "none")
    (tmp_path / ".env").write_text(render_env(rec))
    (tmp_path / "ov.yml").write_text(render_override(rec))
    out = subprocess.run(["docker", "compose", "--env-file", str(tmp_path / ".env"), "-f", str(ROOT / "docker-compose.yml"),
                          "-f", str(tmp_path / "ov.yml"), "--profile", "ollama", "config"],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert "deploy" not in yaml.safe_load(out.stdout)["services"]["ollama"]


def _compose(rec, tmp_path, *profiles):
    (tmp_path / ".env").write_text(render_env(rec))
    (tmp_path / "ov.yml").write_text(render_override(rec))
    args = ["docker", "compose", "--env-file", str(tmp_path / ".env"), "-f", str(ROOT / "docker-compose.yml"), "-f", str(tmp_path / "ov.yml")]
    for p in profiles:
        args += ["--profile", p]
    out = subprocess.run(args + ["config"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)["services"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_mac_gateway_has_no_outside_route(tmp_path):
    rec = recommend(profile_from_dict(copy.deepcopy(BASE)), load_config())     # 16 GB Mac, native Ollama
    assert rec["plan"]["runtime_mode"] == "native"
    svc = _compose(rec, tmp_path, "ui")
    assert set(svc["gateway"]["networks"]) <= {"backend", "models"}
    assert set(svc["ollama-relay"]["networks"]) == {"models", "hostlink"}
    from advisor.render import render_env_gateway
    assert "GATEWAY_RUNTIME_URL=http://ollama-relay:11434/v1" in render_env_gateway(rec)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_sso_publishes_sign_in_port(tmp_path):
    raw = yaml.safe_load((ROOT / "profiles" / "law-accounting-office-10.yaml").read_text())
    raw.pop("expected")
    rec = recommend(profile_from_dict(raw), load_config())
    svc = _compose(rec, tmp_path, "ui")
    targets = {int(p["target"]) for p in svc["caddy"]["ports"]}
    assert targets == {8443, 9443}


# ------------------------------------------------------------------ running cost per machine

def _local(override, profile="software-team-8"):
    raw = yaml.safe_load((ROOT / "profiles" / f"{profile}.yaml").read_text())
    raw.pop("expected")
    raw["hardware"]["override"] = override
    return recommend(profile_from_dict(raw), load_config())["tco"]["local"]


def test_power_depends_on_the_machine():
    small = _local({"platform": "linux_x86", "gpu": "nvidia", "ram_gb": 32, "vram_gb": 12, "gpu_model": "rtx_3060"})
    big = _local({"platform": "linux_x86", "gpu": "nvidia", "ram_gb": 64, "vram_gb": 24, "gpu_model": "rtx_3090"})
    two = _local({"platform": "linux_x86", "gpu": "nvidia", "ram_gb": 64, "vram_gb": 48, "gpu_count": 2, "gpu_model": "rtx_3090"})
    assert small["breakdown"]["power"] < big["breakdown"]["power"] < two["breakdown"]["power"]


def test_purchase_price_counts_when_buying():
    ov = {"platform": "apple_silicon", "gpu": "integrated", "ram_gb": 48, "vram_gb": 0, "chip": "apple_m4_pro"}
    owned = _local(dict(ov))
    bought = _local(dict(ov, purchase_price_usd=2050))
    assert owned["breakdown"]["hardware"] == 0 and bought["breakdown"]["hardware"] == 2050
    assert bought["breakdown"]["power"] == 2 * owned["breakdown"]["power"]    # a bought machine is dedicated
    assert "to buy this machine" in bought["hardware_note"]


def test_reference_machines_are_valid_profiles():
    from advisor.profile import validate_raw
    cfg = load_config()
    for m in cfg.pricing["reference_machines"]:
        raw = copy.deepcopy(BASE)
        raw["hardware"]["override"] = dict(m["override"], purchase_price_usd=sum(m["price_usd"]) / 2)
        validate_raw(raw)
        assert m["price_usd"][0] <= m["price_usd"][1]
