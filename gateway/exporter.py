"""Plain-format export: everything a user owns, as Markdown or JSON."""

from __future__ import annotations

import datetime as dt

from . import SENSITIVITY_NAMES


def _d(ts) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def to_markdown(data: dict) -> str:
    out = [f"# Export for {data['user']}", f"Exported {_d(data['exported_at'])}", ""]
    out += ["## Memory cards", ""]
    for c in data["memory_cards"] or []:
        out.append(f"- [{c['status']}] {c['text']} ({SENSITIVITY_NAMES.get(c['sensitivity'], '')})")
    out += ["", "## Conversations", ""]
    for c in data["conversations"]:
        out += [f"### {c['title']}", f"*{_d(c['created_at'])} - {SENSITIVITY_NAMES.get(c['sensitivity'], '')}*", ""]
        if c.get("summary"):
            out += [f"> Summary: {c['summary']['text']}", ""]
        for m in c["messages"]:
            out += [f"**{m['role']}** ({_d(m['created_at'])}{', ' + m['model'] if m.get('model') else ''}):", "", m["content"], ""]
    out += ["## Documents you own", ""]
    for d in data["owned_documents"]:
        out.append(f"- {d['name']}: {d['path']} (sha256 {d['sha256'][:12]}..., {SENSITIVITY_NAMES.get(d['sensitivity'], '')})")
    return "\n".join(out) + "\n"
