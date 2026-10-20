"""Network test: nothing can reach the model runtime except through the Gateway."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
RUNTIMES = {"ollama", "vllm", "whisper"}


def nets(svc):
    n = COMPOSE["services"][svc].get("networks", [])
    return set(n if isinstance(n, list) else n.keys())


def test_runtime_networks_are_internal_and_private():
    for r in RUNTIMES:
        assert nets(r) == {"models"}, r
    assert COMPOSE["networks"]["models"]["internal"] is True
    assert COMPOSE["networks"]["backend"]["internal"] is True


def test_only_gateway_and_worker_reach_models():
    # Prometheus (tier 3 monitoring) may scrape runtime /metrics; it never forwards prompts.
    on_models = {s for s in COMPOSE["services"] if "models" in nets(s)} - RUNTIMES
    assert on_models == {"gateway", "ingest-worker", "prometheus"}


def test_open_webui_points_at_gateway_only():
    env = COMPOSE["services"]["open-webui"]["environment"]
    assert env["OPENAI_API_BASE_URL"] == "http://gateway:8080/v1"
    assert env["ENABLE_OLLAMA_API"] == "false"
    assert "OLLAMA_BASE_URL" not in env
    assert not nets("open-webui") & {"models", "edge", "research", "pull"}


def test_only_caddy_publishes_ports():
    published = {s for s, d in COMPOSE["services"].items() if d.get("ports")}
    assert published == {"caddy"}


def test_ingest_worker_mounts_documents_read_only():
    vols = COMPOSE["services"]["ingest-worker"]["volumes"]
    assert any(v.endswith(":/documents:ro") for v in vols)
