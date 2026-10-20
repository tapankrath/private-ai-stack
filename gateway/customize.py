"""Phase 6: the optional Customize track (ADR-006, ADR-007, ADR-008).

  gateway customize dataset        build a training set from chosen collections + a sensitivity review
  gateway customize distill-data   have a (license-compatible) teacher model write Q&A pairs from it
  gateway customize train-lora     LoRA post-training plan / run (needs a signed review + license check)
  gateway customize register       register an adapter or distilled model as a CANDIDATE
  gateway customize abliteration-check   apply the ADR-007 gate; never automatic

Promotion of any candidate goes through `gateway models promote` (golden-set eval, no regression).
Everything runs locally; data and weights never go to a provider.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import yaml

from . import SENSITIVITY_NAMES

REDACTED = re.compile(r"\[REDACTED:[a-z_]+\]")


class CustomizeError(RuntimeError):
    pass


# ------------------------------------------------------------------ license checks (catalog)
def catalog_entry(name: str) -> dict | None:
    """Find a catalog model by id, name, Ollama tag or HF repo. None if the advisor catalog is unavailable."""
    try:
        from advisor.config import load_config
    except ImportError:
        return None
    for m in load_config().models:
        rt = m.get("runtimes", {})
        if name in {m["id"], m["name"], rt.get("ollama"), rt.get("hf"), rt.get("vllm")}:
            return m
    return None


def license_allows(name: str, field: str, ack: bool) -> tuple[bool, str]:
    m = catalog_entry(name)
    if m is None:
        return (ack, f"{name} is not in the catalog; " + ("license acknowledged by operator" if ack
                else "pass --license-ack after checking its license yourself"))
    v = m.get(field)
    if v == "no":
        return False, f"{m['name']} ({m['license']}) does not allow {field.replace('allows_', '').replace('_', '-')}"
    if v == "conditional" and not ack:
        return False, f"{m['name']} ({m['license']}) allows it only under conditions; read them and pass --license-ack"
    return True, f"{m['name']} ({m['license']}): {field} = {v}"


# ------------------------------------------------------------------ dataset + sensitivity review
def build_dataset(store, collections: list[str], out: Path, include_pii: bool = False, reviewer_note: str = "") -> dict:
    out.mkdir(parents=True, exist_ok=True)
    stats = {"chunks_included": 0, "excluded_restricted": 0, "excluded_pii": 0, "by_sensitivity": {}, "pii_tags_excluded": {},
             "sources": set()}
    lines = []
    with store.tx() as x:                    # maintenance path: explicit collection list chosen by an admin
        cols = []
        for name in collections:
            c = x.one("SELECT id, name FROM collections WHERE name=? OR id=?", (name, name))
            if not c:
                raise CustomizeError(f"no collection {name!r}")
            cols.append(c)
        for c in cols:
            rows = x.all("SELECT ch.text, ch.heading_path, ch.sensitivity, ch.pii_json, d.path FROM chunks ch "
                         "JOIN documents d ON d.id=ch.document_id WHERE ch.collection_id=? AND d.deleted=0 ORDER BY d.path, ch.ord",
                         (c["id"],))
            for r in rows:
                pii = json.loads(r["pii_json"] or "[]")
                if r["sensitivity"] >= 3:
                    stats["excluded_restricted"] += 1
                    continue
                if pii and not include_pii:
                    stats["excluded_pii"] += 1
                    for t in pii:
                        stats["pii_tags_excluded"][t] = stats["pii_tags_excluded"].get(t, 0) + 1
                    continue
                text = REDACTED.sub("", r["text"]).strip()
                if len(text) < 40:
                    continue
                lines.append(json.dumps({"text": (r["heading_path"] + "\n" if r["heading_path"] else "") + text,
                                         "source": f"{c['name']}/{r['path']}"}))
                lab = SENSITIVITY_NAMES[r["sensitivity"]]
                stats["by_sensitivity"][lab] = stats["by_sensitivity"].get(lab, 0) + 1
                stats["chunks_included"] += 1
                stats["sources"].add(f"{c['name']}/{r['path']}")
    data = "\n".join(lines) + ("\n" if lines else "")
    (out / "dataset.jsonl").write_text(data, encoding="utf-8")
    digest = hashlib.sha256(data.encode()).hexdigest()
    review = {"created": dt.datetime.now().isoformat(timespec="seconds"), "collections": collections,
              "dataset_sha256": digest, "include_pii": include_pii, **{k: v for k, v in stats.items() if k != "sources"},
              "sources": sorted(stats["sources"]), "note": reviewer_note}
    (out / "review.json").write_text(json.dumps(review, indent=2))
    md = [f"# Dataset sensitivity review", "", f"Dataset SHA-256: `{digest}`", f"Collections: {', '.join(collections)}",
          f"Chunks included: {stats['chunks_included']} {stats['by_sensitivity']}",
          f"Excluded Restricted chunks: {stats['excluded_restricted']} (always excluded)",
          f"Excluded chunks with personal data: {stats['excluded_pii']} {stats['pii_tags_excluded']}"
          + ("" if not include_pii else " (PII INCLUDED by request)"), "",
          "## Sources", ""] + [f"- {s}" for s in sorted(stats["sources"])] + [
          "", "## Sign-off", "",
          "Read dataset.jsonl. If it is acceptable to bake into model weights (they cannot be selectively deleted later),",
          "create SIGNOFF.yaml next to this file:", "", "```yaml", "reviewer: Full Name", "date: YYYY-MM-DD",
          f"dataset_sha256: {digest}", "approved: true", "```"]
    (out / "REVIEW.md").write_text("\n".join(md) + "\n")
    return review


def check_signoff(dataset_dir: Path) -> dict:
    review = json.loads((dataset_dir / "review.json").read_text())
    so_path = dataset_dir / "SIGNOFF.yaml"
    if not so_path.exists():
        raise CustomizeError("No SIGNOFF.yaml: a person must review REVIEW.md and sign off first.")
    so = yaml.safe_load(so_path.read_text()) or {}
    data = (dataset_dir / "dataset.jsonl").read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if not so.get("approved") or not so.get("reviewer"):
        raise CustomizeError("SIGNOFF.yaml must have approved: true and a reviewer.")
    if so.get("dataset_sha256") != review["dataset_sha256"] or actual != review["dataset_sha256"]:
        raise CustomizeError("Dataset changed after review (SHA-256 mismatch). Rebuild and re-review.")
    return so


# ------------------------------------------------------------------ distillation data
def distill_data(dataset_dir: Path, teacher: str, runtime_url: str, n: int, license_ack: bool,
                 transport=None, runtime_key: str | None = None) -> Path:
    ok, why = license_allows(teacher, "allows_distillation", license_ack)
    if not ok:
        raise CustomizeError(why)
    check_signoff(dataset_dir)
    import httpx

    client = httpx.Client(base_url=runtime_url.rstrip("/"), timeout=600, transport=transport,
                          headers={"Authorization": f"Bearer {runtime_key}"} if runtime_key else {})
    rows = [json.loads(line) for line in (dataset_dir / "dataset.jsonl").read_text().splitlines() if line.strip()][:n]
    out = dataset_dir / "distill.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            prompt = ("From the PASSAGE, write one question a colleague would ask and a correct, complete answer that uses only "
                      "the passage. Reply as JSON: {\"question\": ..., \"answer\": ...}\n\nPASSAGE:\n" + r["text"][:6000])
            resp = client.post("/chat/completions", json={"model": teacher, "stream": False, "temperature": 0.3,
                                                          "messages": [{"role": "user", "content": prompt}]})
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            try:
                qa = json.loads(m.group(0)) if m else None
            except ValueError:
                qa = None
            if qa and qa.get("question") and qa.get("answer"):
                fh.write(json.dumps({"messages": [{"role": "user", "content": qa["question"]},
                                                  {"role": "assistant", "content": qa["answer"]}],
                                     "source": r["source"], "teacher": teacher}) + "\n")
    (dataset_dir / "distill.meta.json").write_text(json.dumps({"teacher": teacher, "license": why, "pairs_from": len(rows)}))
    return out


# ------------------------------------------------------------------ LoRA training
LORA_DEFAULTS = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05, "target_modules": "all-linear", "epochs": 2,
                 "learning_rate": 2e-4, "max_seq_len": 2048, "batch_size": 4, "grad_accum": 4}


def train_lora(base: str, dataset_dir: Path, out: Path, license_ack: bool, dry_run: bool = False, **over) -> dict:
    ok, why = license_allows(base, "allows_fine_tuning", license_ack)
    if not ok:
        raise CustomizeError(why)
    so = check_signoff(dataset_dir)
    review = json.loads((dataset_dir / "review.json").read_text())
    data_file = dataset_dir / ("distill.jsonl" if (dataset_dir / "distill.jsonl").exists() else "dataset.jsonl")
    cfg = {**LORA_DEFAULTS, **over, "base_model": base, "dataset": str(data_file), "dataset_sha256": review["dataset_sha256"],
           "reviewed_by": so["reviewer"], "license": why, "created": dt.datetime.now().isoformat(timespec="seconds")}
    out.mkdir(parents=True, exist_ok=True)
    (out / "train_config.json").write_text(json.dumps(cfg, indent=2))
    if dry_run:
        return {"status": "planned", "config": cfg}
    try:
        import datasets  # noqa: F401
        import peft  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        raise CustomizeError("Training needs: pip install torch transformers peft datasets accelerate "
                             "(GPU machine). The plan is in train_config.json.")
    return _run_lora(cfg, out)          # pragma: no cover - needs a GPU and model weights


def _run_lora(cfg: dict, out: Path) -> dict:  # pragma: no cover
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

    tok = AutoTokenizer.from_pretrained(cfg["base_model"], local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], torch_dtype=torch.bfloat16, local_files_only=True)
    model = get_peft_model(model, LoraConfig(r=cfg["r"], lora_alpha=cfg["lora_alpha"], lora_dropout=cfg["lora_dropout"],
                                             target_modules=cfg["target_modules"], task_type="CAUSAL_LM"))
    ds = load_dataset("json", data_files=cfg["dataset"])["train"]

    def fmt(ex):
        text = tok.apply_chat_template(ex["messages"], tokenize=False) if "messages" in ex else ex["text"]
        return tok(text, truncation=True, max_length=cfg["max_seq_len"])
    ds = ds.map(fmt, remove_columns=ds.column_names)
    args = TrainingArguments(output_dir=str(out), num_train_epochs=cfg["epochs"], learning_rate=cfg["learning_rate"],
                             per_device_train_batch_size=cfg["batch_size"], gradient_accumulation_steps=cfg["grad_accum"],
                             bf16=True, logging_steps=10, save_strategy="epoch", report_to=[])
    Trainer(model=model, args=args, train_dataset=ds).train()
    model.save_pretrained(out / "adapter")
    return {"status": "trained", "adapter": str(out / "adapter")}


def dir_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(f.relative_to(path).as_posix().encode())
        h.update(f.read_bytes())
    return "sha256:" + h.hexdigest()


# ------------------------------------------------------------------ abliteration gate (ADR-007)
def abliteration_check(profile_raw: dict, justification: str, admin: str) -> tuple[bool, list[str]]:
    org = profile_raw.get("org", {}).get("type")
    item = (profile_raw.get("customization") or {}).get("abliteration") or {}
    regulated = (profile_raw.get("sensitivity") or {}).get("regulated_data") or []
    problems = []
    if not item.get("requested"):
        problems.append("profile does not request abliteration")
    if not item.get("admin_approved"):
        problems.append("profile has no admin approval")
    if not (justification or item.get("justification", "")).strip():
        problems.append("no justification recorded")
    if not admin.strip():
        problems.append("approving admin not named")
    if org == "household":
        problems.append("never for household profiles")
    if "children" in regulated:
        problems.append("never where children's data is present")
    return (not problems), problems


# ------------------------------------------------------------------ CLI
def add_cli(sub):
    p = sub.add_parser("customize", help="optional customize track (post-training, distillation)")
    cs = p.add_subparsers(dest="ccmd", required=True)
    d = cs.add_parser("dataset")
    d.add_argument("--collections", required=True, help="comma-separated collection names")
    d.add_argument("--out", required=True)
    d.add_argument("--include-pii", action="store_true")
    d.set_defaults(fn=_cli_dataset)
    t = cs.add_parser("distill-data")
    t.add_argument("--dataset", required=True)
    t.add_argument("--teacher", required=True)
    t.add_argument("--n", type=int, default=200)
    t.add_argument("--license-ack", action="store_true")
    t.set_defaults(fn=_cli_distill)
    tr = cs.add_parser("train-lora")
    tr.add_argument("--base", required=True, help="HF repo id or catalog name of the base model (local weights)")
    tr.add_argument("--dataset", required=True)
    tr.add_argument("--out", required=True)
    tr.add_argument("--license-ack", action="store_true")
    tr.add_argument("--dry-run", action="store_true")
    tr.set_defaults(fn=_cli_train)
    r = cs.add_parser("register")
    r.add_argument("--name", required=True)
    r.add_argument("--path", required=True)
    r.add_argument("--kind", choices=["adapter", "distilled"], required=True)
    r.add_argument("--license")
    r.set_defaults(fn=_cli_register)
    a = cs.add_parser("abliteration-check")
    a.add_argument("--profile", required=True)
    a.add_argument("--model-name", required=True)
    a.add_argument("--justification", default="")
    a.add_argument("--admin", required=True)
    a.add_argument("--digest")
    a.set_defaults(fn=_cli_ablit)


def _store(s):
    from .store import open_store

    return open_store(s.database_url)


def _cli_dataset(args, s):
    rev = build_dataset(_store(s), [c.strip() for c in args.collections.split(",") if c.strip()], Path(args.out), args.include_pii)
    print(f"{rev['chunks_included']} chunks; review {args.out}/REVIEW.md and sign off before training.")
    return 0


def _cli_distill(args, s):
    out = distill_data(Path(args.dataset), args.teacher, s.runtime_url, args.n, args.license_ack, runtime_key=s.runtime_key)
    print(f"wrote {out}")
    return 0


def _cli_train(args, s):
    res = train_lora(args.base, Path(args.dataset), Path(args.out), args.license_ack, args.dry_run)
    print(json.dumps(res, indent=2, default=str))
    return 0


def _cli_register(args, s):
    store = _store(s)
    digest = dir_digest(Path(args.path))
    mid = store.register_model(args.name, digest, "candidate", args.license, f"path={args.path}", args.kind)
    store.audit("admin-cli", "model_register", mid, {"name": args.name, "kind": args.kind, "digest": digest})
    print(f"registered candidate {mid} ({digest}). Promote with: gateway models promote --name {args.name} ...")
    return 0


def _cli_ablit(args, s):
    raw = yaml.safe_load(Path(args.profile).read_text())
    ok, problems = abliteration_check(raw, args.justification, args.admin)
    store = _store(s)
    store.audit(args.admin, "abliteration_check", args.model_name, {"ok": ok, "problems": problems,
                                                                     "justification": args.justification})
    if not ok:
        print("REFUSED (ADR-007): " + "; ".join(problems))
        return 1
    mid = store.register_model(args.model_name, args.digest, "candidate", None,
                               "abliterated; gateway filters, audit and RBAC stay on; never the default model", "abliterated")
    print(f"registered abliterated candidate {mid}; only admins and GATEWAY_ABLITERATED_GROUPS may use it.")
    return 0
