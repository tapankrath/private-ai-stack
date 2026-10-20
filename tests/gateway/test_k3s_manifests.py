"""Static checks of the tier 3 k3s manifests (they cannot be applied in CI here)."""

from pathlib import Path

import yaml

K = Path(__file__).resolve().parents[2] / "deploy" / "k3s"


def docs():
    out = []
    for f in sorted(K.glob("*.yaml")):
        out += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return out


def test_all_resources_listed_and_parse():
    kust = yaml.safe_load((K / "kustomization.yaml").read_text())
    for r in kust["resources"]:
        assert (K / r).exists(), r
    assert docs()


def test_default_deny_and_webui_cannot_reach_runtime():
    pols = {d["metadata"]["name"]: d for d in docs() if d["kind"] == "NetworkPolicy"}
    deny = pols["default-deny-all"]["spec"]
    assert deny["podSelector"] == {} and set(deny["policyTypes"]) == {"Ingress", "Egress"}
    web = pols["webui-to-gateway"]["spec"]["egress"]
    targets = [t for rule in web for t in rule["to"]]
    assert {"podSelector": {"matchLabels": {"app": "gateway"}}} in targets
    assert not any("vllm" in str(t) or "ollama" in str(t) for t in targets)


def test_workloads_hardened():
    for d in docs():
        if d["kind"] != "Deployment":
            continue
        spec = d["spec"]["template"]["spec"]
        assert spec.get("automountServiceAccountToken") is False, d["metadata"]["name"]
        for c in spec["containers"]:
            sc = c.get("securityContext", {})
            assert sc.get("allowPrivilegeEscalation") is False, c["name"]
            assert "ALL" in sc.get("capabilities", {}).get("drop", []), c["name"]


def test_open_webui_points_at_gateway():
    web = next(d for d in docs() if d["kind"] == "Deployment" and d["metadata"]["name"] == "open-webui")
    env = {e["name"]: e.get("value") for e in web["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["OPENAI_API_BASE_URL"] == "http://gateway:8080/v1" and env["ENABLE_OLLAMA_API"] == "false"
