"""`gateway` command line: serve, ingest, eval, models, retention, export, delete."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .settings import Settings


def _store(s: Settings):
    from .store import open_store

    return open_store(s.database_url)


def _embedder(s: Settings):
    from .embed import HashEmbedder, HttpEmbedder

    return HashEmbedder() if s.embedder == "hash" else HttpEmbedder(s.embed_url or s.runtime_url, s.embed_model, s.runtime_key)


def cmd_serve(args, s):
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(s), host=args.host, port=args.port, log_level="warning")


def cmd_ingest(args, s):
    from .ingest import Ingestor, load_collections, sync_collections

    store = _store(s)
    ing = Ingestor(store, _embedder(s), s)
    while True:
        for col, path in sync_collections(store, load_collections(args.config)):
            t0 = time.time()
            res = ing.scan_folder(col, path)
            store.audit("ingest-worker", "ingest_scan", col["id"], {**res, "seconds": round(time.time() - t0, 2)})
            print(f"{col['name']}: {res}", flush=True)
        if not args.watch:
            return 0
        time.sleep(args.interval)


def cmd_eval(args, s):
    import httpx

    from .eval import load_golden, run_eval

    qs, h = load_golden(args.golden)
    client = httpx.Client(base_url=args.url, timeout=600)
    res = run_eval(client, args.model, qs, k=args.k, headers={"Authorization": f"Bearer {s.gateway_key}"})
    store = _store(s)
    rid = store.save_eval(args.model, {k: v for k, v in res.items() if k != "rows"}, h)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=2))
    print(f"eval run id: {rid}")
    return 0


def cmd_models(args, s):
    from .eval import no_worse

    store = _store(s)
    if args.action == "list":
        for m in store.list_models():
            print(f"{m['status']:9} {m['kind']:8} {m['name']:40} {m['digest'] or '-':20} {m['id']}")
    elif args.action == "register":
        mid = store.register_model(args.name, args.digest, "candidate", args.license, args.notes or "", args.kind)
        store.audit("admin-cli", "model_register", mid, {"name": args.name, "digest": args.digest})
        print(mid)
    elif args.action == "promote":
        cand = store.get_model(args.name)
        if not cand:
            print("unknown model", file=sys.stderr)
            return 2
        cur = store.active_model() if cand["kind"] == "base" else None
        cand_eval = json.loads(Path(args.candidate_eval).read_text())
        cur_eval = json.loads(Path(args.current_eval).read_text()) if args.current_eval else None
        ok, why = no_worse(cand_eval, cur_eval)
        store.audit("admin-cli", "model_promote_attempt", cand["id"], {"ok": ok, "why": why,
                                                                       "from": cur["digest"] if cur else None, "to": cand["digest"]})
        if not ok:
            print("NOT promoted: " + "; ".join(why))
            return 1
        store.set_model_status(cand["id"], "active")
        print("promoted: " + "; ".join(why))
    return 0


def cmd_retention(args, s):
    store = _store(s)
    res = store.apply_retention(args.conversation_days)
    store.audit("admin-cli", "retention_run", "", res)
    print(res)
    return 0


def cmd_export(args, s):
    from .exporter import to_markdown
    from .store import UserCtx

    store = _store(s)
    data = store.export_user(UserCtx(args.user, unlocked=True))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.user}.json").write_text(json.dumps(data, indent=2, default=str))
    (out / f"{args.user}.md").write_text(to_markdown(data))
    store.audit("admin-cli", "export", args.user, {"conversations": len(data["conversations"])})
    print(f"wrote {out}/{args.user}.json and .md")
    return 0


def cmd_delete(args, s):
    from .store import UserCtx

    store = _store(s)
    if args.confirm != "yes":
        print("Refusing: add --confirm yes. Deletion is permanent (rows, vectors, keyword index); "
              "backups drop it on their next rotation.", file=sys.stderr)
        return 2
    if args.collection:
        col = store.get_collection(args.collection)
        n = store.delete_collection(col["id"]) if col else 0
        store.audit("admin-cli", "delete_collection", args.collection, {"documents": n})
        print(f"deleted collection {args.collection} ({n} documents)")
    if args.user:
        res = store.delete_user_data(UserCtx(args.user, unlocked=True))
        store.audit("admin-cli", "delete_user_data", args.user, res)
        print(f"deleted data of {args.user}: {res}")
    return 0


def cmd_token(args, s):
    store = _store(s)
    if not store.get_user(args.user):
        store.upsert_user(args.user, args.user, "admin" if args.admin else "member", [])
    raw = store.create_token(args.user, args.label, args.days)
    store.audit("admin-cli", "token_create", args.user, {"label": args.label, "days": args.days})
    print(raw)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("ingest", help="scan the folders in collections.yaml")
    p.add_argument("--config", default="config/collections.yaml")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("eval", help="run the golden set through a running Gateway")
    p.add_argument("--golden", required=True)
    p.add_argument("--model", required=True, help='e.g. "qwen3:8b + Recall"')
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("-k", type=int, default=5)
    p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("models", help="model registry and promotion (ADR-008)")
    p.add_argument("action", choices=["list", "register", "promote"])
    p.add_argument("--name")
    p.add_argument("--digest")
    p.add_argument("--license")
    p.add_argument("--notes")
    p.add_argument("--kind", default="base", choices=["base", "adapter", "distilled", "abliterated"])
    p.add_argument("--candidate-eval")
    p.add_argument("--current-eval")
    p.set_defaults(fn=cmd_models)
    p = sub.add_parser("retention")
    p.add_argument("--conversation-days", type=int, default=None)
    p.set_defaults(fn=cmd_retention)
    p = sub.add_parser("export")
    p.add_argument("--user", required=True)
    p.add_argument("--out", default="exports")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("delete")
    p.add_argument("--user")
    p.add_argument("--collection")
    p.add_argument("--confirm", default="no")
    p.set_defaults(fn=cmd_delete)
    p = sub.add_parser("token", help="create a personal access token for the web UI")
    p.add_argument("--user", required=True)
    p.add_argument("--label", default="web ui")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--admin", action="store_true", help="only when creating the user")
    p.set_defaults(fn=cmd_token)
    from .customize import add_cli as add_customize

    add_customize(sub)
    args = ap.parse_args(argv)
    return args.fn(args, Settings.from_env()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
