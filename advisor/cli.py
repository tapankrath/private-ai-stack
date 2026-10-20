"""`advise` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import __version__
from . import hardware as hwmod
from .config import REPO_ROOT, load_config
from .diff import diff_recommendations
from .engine import recommend
from .profile import ProfileError, load_raw, profile_from_dict
from .render import render_markdown, write_outputs

PACKET_TEMPLATE = REPO_ROOT / "docs" / "SECURITY_REVIEW_PACKET.md"


def _load(profile_path: str, detect: bool | None = None):
    raw = load_raw(profile_path)
    autodetect = raw.get("hardware", {}).get("autodetect", False) if detect is None else detect
    detected = hwmod.detect() if autodetect else None
    return raw, profile_from_dict(raw, detected)


def _cmd_run(args) -> int:
    cfg = load_config(args.config_dir)
    try:
        raw, profile = _load(args.profile, False if args.no_detect else None)
    except (ProfileError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 2
    rec = recommend(profile, cfg)
    out = Path(args.out)
    prev = out / "recommendation.json"
    diff = diff_recommendations(json.loads(prev.read_text()), rec) if prev.exists() else None
    template = PACKET_TEMPLATE.read_text(encoding="utf-8") if PACKET_TEMPLATE.exists() else None
    paths = write_outputs(rec, out, diff, template)
    (out / "profile.resolved.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    if args.quiet:
        return 0
    md = render_markdown(rec, diff)
    print(md.split("## Why Local")[0])
    if diff is not None and not diff:
        print("No changes since last run.")
    print("Wrote:\n  " + "\n  ".join(str(p) for p in paths))
    return 0


def _cmd_detect(args) -> int:
    print(yaml.safe_dump({"hardware": {"autodetect": True, "override": hwmod.detect()}}, sort_keys=False))
    return 0


def _cmd_wizard(args) -> int:
    from .wizard import run_wizard

    raw = run_wizard()
    Path(args.output).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(f"\nSaved {args.output}. Next: advise run --profile {args.output}")
    return 0


def _cmd_intake(args) -> int:
    from .explain import EgressRefused, run_intake

    try:
        raw = run_intake(args.base_url, args.model, detected=hwmod.detect())
    except EgressRefused as exc:
        print(exc, file=sys.stderr)
        return 3
    Path(args.output).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    print(f"\nSaved {args.output}. Next: advise run --profile {args.output}")
    return 0


def _cmd_explain(args) -> int:
    from .explain import EgressRefused, explain

    cfg = load_config(args.config_dir)
    out = Path(args.out)
    rec = json.loads((out / "recommendation.json").read_text())
    raw_path = out / "profile.resolved.yaml"
    raw = yaml.safe_load(raw_path.read_text()) if raw_path.exists() else {}
    try:
        text, used, problems = explain(rec, cfg, args.base_url, args.model, raw)
    except EgressRefused as exc:
        print(exc, file=sys.stderr)
        return 3
    (out / "recommendation.llm.md").write_text(text, encoding="utf-8")
    if not used:
        print("Local-model explanation not used (" + "; ".join(problems) + "). Showing the deterministic version.\n", file=sys.stderr)
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="advise", description="Right-Size Advisor for a private local AI assistant")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config-dir", default=None)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="recommend a setup for a profile and write config files")
    r.add_argument("--profile", "-p", required=True)
    r.add_argument("--out", "-o", default="generated")
    r.add_argument("--no-detect", action="store_true", help="ignore hardware.autodetect")
    r.add_argument("--quiet", "-q", action="store_true")
    r.set_defaults(func=_cmd_run)

    sub.add_parser("detect", help="print detected hardware (local only)").set_defaults(func=_cmd_detect)

    w = sub.add_parser("wizard", help="answer questions in the terminal -> profile.yaml")
    w.add_argument("--output", default="profile.yaml")
    w.set_defaults(func=_cmd_wizard)

    for name, fn, helptext in (("intake", _cmd_intake, "plain-language interview with a LOCAL model -> profile.yaml"),
                               ("explain", _cmd_explain, "plain-language explanation from a LOCAL model")):
        x = sub.add_parser(name, help=helptext)
        x.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
        x.add_argument("--model", default="qwen3:8b")
        if name == "intake":
            x.add_argument("--output", default="profile.yaml")
        else:
            x.add_argument("--out", "-o", default="generated")
        x.set_defaults(func=fn)

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
