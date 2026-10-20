"""Phase 6: dataset + sensitivity review, license gates, distillation data, LoRA plan, registry, abliteration gate."""

import json

import httpx
import pytest
import yaml

from gateway import customize as CU
from gateway.eval import no_worse
from gateway.ingest import Ingestor
from gwhelpers import H, chat, make


@pytest.fixture
def ds(tmp_path):
    client, rt, store, s = make()
    store.upsert_user("owner", "owner", "admin", [])
    docs = store.get_collection(store.create_collection("docs", "owner", 1))
    fin = store.get_collection(store.create_collection("finance", "owner", 3))
    ing = Ingestor(store, client.app.state.embedder, s)
    ing.ingest_bytes(docs, "product.md", b"# Widgets\nOur Widget Pro supports 12 languages and ships with a 3-year warranty in all regions.")
    ing.ingest_bytes(docs, "contacts.md", b"# Support\nEscalations go to jane.doe@example.com, who handles enterprise support tickets.")
    ing.ingest_bytes(fin, "pay.md", b"# Payroll\nPayroll runs on the 25th from the operating account for all staff members.")
    return client, rt, store, s, tmp_path


def sign(d, digest=None, approved=True):
    rev = json.loads((d / "review.json").read_text())
    (d / "SIGNOFF.yaml").write_text(yaml.safe_dump({"reviewer": "Pat Lee", "date": "2026-10-20", "approved": approved,
                                                     "dataset_sha256": digest or rev["dataset_sha256"]}))


def test_dataset_excludes_restricted_and_pii(ds):
    client, rt, store, s, tmp = ds
    rev = CU.build_dataset(store, ["docs", "finance"], tmp / "set")
    data = (tmp / "set" / "dataset.jsonl").read_text()
    assert "Widget Pro" in data and "Payroll" not in data and "jane.doe" not in data
    assert rev["excluded_restricted"] == 1 and rev["excluded_pii"] == 1
    assert "Sign-off" in (tmp / "set" / "REVIEW.md").read_text()


def test_training_needs_signoff_matching_dataset(ds):
    client, rt, store, s, tmp = ds
    d = tmp / "set"
    CU.build_dataset(store, ["docs"], d)
    with pytest.raises(CU.CustomizeError, match="SIGNOFF"):
        CU.train_lora("Qwen/Qwen3-8B", d, tmp / "out", license_ack=False, dry_run=True)
    sign(d, approved=False)
    with pytest.raises(CU.CustomizeError, match="approved"):
        CU.train_lora("Qwen/Qwen3-8B", d, tmp / "out", license_ack=False, dry_run=True)
    sign(d)
    (d / "dataset.jsonl").write_text((d / "dataset.jsonl").read_text() + '{"text": "smuggled after review"}\n')
    with pytest.raises(CU.CustomizeError, match="changed"):
        CU.train_lora("Qwen/Qwen3-8B", d, tmp / "out", license_ack=False, dry_run=True)


def test_lora_license_gate_and_plan(ds):
    client, rt, store, s, tmp = ds
    d = tmp / "set"
    CU.build_dataset(store, ["docs"], d)
    sign(d)
    with pytest.raises(CU.CustomizeError, match="does not allow"):
        CU.train_lora("Qwen/Qwen2.5-VL-3B-Instruct", d, tmp / "o", license_ack=True, dry_run=True)   # research license
    with pytest.raises(CU.CustomizeError, match="conditions"):
        CU.train_lora("meta-llama/Llama-3.1-8B-Instruct", d, tmp / "o", license_ack=False, dry_run=True)
    plan = CU.train_lora("Qwen/Qwen3-8B", d, tmp / "o", license_ack=False, dry_run=True)
    assert plan["status"] == "planned" and plan["config"]["reviewed_by"] == "Pat Lee"
    assert json.loads((tmp / "o" / "train_config.json").read_text())["r"] == 16


def test_distillation_teacher_license_and_generation(ds):
    client, rt, store, s, tmp = ds
    d = tmp / "set"
    CU.build_dataset(store, ["docs"], d)
    sign(d)
    with pytest.raises(CU.CustomizeError, match="does not allow"):
        CU.distill_data(d, "codestral:22b", "http://rt/v1", 5, license_ack=True)

    def teacher(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"question": "How long is the warranty?", "answer": "3 years."}'}}]})
    out = CU.distill_data(d, "qwen3:32b", "http://rt/v1", 5, license_ack=False, transport=httpx.MockTransport(teacher))
    pair = json.loads(out.read_text().splitlines()[0])
    assert pair["messages"][1]["content"] == "3 years." and pair["teacher"] == "qwen3:32b"


def test_register_and_promote_with_eval(ds):
    client, rt, store, s, tmp = ds
    (tmp / "adapter").mkdir()
    (tmp / "adapter" / "adapter_model.safetensors").write_bytes(b"weights")
    mid = store.register_model("widgets-lora", CU.dir_digest(tmp / "adapter"), "candidate", "Apache-2.0", "", "adapter")
    assert store.get_model(mid)["status"] == "candidate"
    ok, _ = no_worse({"recall_at_k": 0.9, "citation_accuracy": 0.95}, {"recall_at_k": 0.85, "citation_accuracy": 0.95})
    assert ok
    store.set_model_status(mid, "active")
    assert store.active_model("widgets-lora")["id"] == mid


def test_promote_cli_refuses_regression(ds, tmp_path, monkeypatch, capsys):
    client, rt, store, s, tmp = ds
    from gateway import cli
    monkeypatch.setattr(cli, "_store", lambda _s: store)
    mid = store.register_model("cand", "sha256:x", "candidate", None, "", "base")
    (tmp / "c.json").write_text(json.dumps({"recall_at_k": 0.6}))
    (tmp / "b.json").write_text(json.dumps({"recall_at_k": 0.8}))
    rc = cli.main(["models", "promote", "--name", mid, "--candidate-eval", str(tmp / "c.json"), "--current-eval", str(tmp / "b.json")])
    assert rc == 1 and "NOT promoted" in capsys.readouterr().out
    assert store.get_model(mid)["status"] == "candidate"


def _profile(**cust):
    return {"org": {"type": "small_business"}, "sensitivity": {"regulated_data": []},
            "customization": {"abliteration": {"requested": True, "admin_approved": True, "justification": "security research", **cust}}}


def test_abliteration_gate():
    assert CU.abliteration_check(_profile(), "", "alex")[0]
    assert not CU.abliteration_check(_profile(admin_approved=False), "x", "alex")[0]
    assert not CU.abliteration_check(_profile(requested=False), "x", "alex")[0]
    assert not CU.abliteration_check(_profile(justification=""), "", "alex")[0]
    hh = _profile()
    hh["org"]["type"] = "household"
    assert not CU.abliteration_check(hh, "x", "alex")[0]
    kids = _profile()
    kids["sensitivity"]["regulated_data"] = ["children"]
    assert not CU.abliteration_check(kids, "x", "alex")[0]


def test_abliterated_model_limited_to_admins_and_groups():
    client, rt, store, s = make({"auth_mode": "proxy_header", "abliterated_groups": ("security",)})
    store.register_model("sec-research-model", "sha256:abc", "candidate", None, "abliterated", "abliterated")
    assert chat(client, "hi", model="sec-research-model", headers=H("bob@firm.test")).status_code == 403
    store.upsert_user("eve@firm.test", "eve", "member", ["security"])
    assert chat(client, "hi", model="sec-research-model", headers=H("eve@firm.test"), chat_id="e").status_code == 200
    assert store.list_audit(action="abliterated_model_refused")
