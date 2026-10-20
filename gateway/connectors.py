"""Read-only connectors (Phase 5).

Default: none. Exported or locally synced files are preferred over account access, so the
built-in connectors read LOCAL copies kept up to date by tools you control on the host
(e.g. vdirsyncer for calendars, mbsync/offlineimap for mail). The Gateway itself has no
internet route and never holds account passwords.

Every connector:
  * is read-only and scoped to one collection,
  * has a kill switch (disable) checked before every access,
  * records `last_accessed` and an audit entry on every sync,
  * refuses configuration that contains credentials (tokens live in files referenced by path).
"""

from __future__ import annotations

import datetime as dt
import mailbox
import re
from pathlib import Path

CRED_KEYS = re.compile(r"pass|secret|token|key|credential", re.I)


class ConnectorError(ValueError):
    pass


def validate_config(ctype: str, config: dict) -> None:
    if ctype not in CONNECTORS:
        raise ConnectorError(f"unknown connector type {ctype!r}; available: {sorted(CONNECTORS)}")
    for k in config:
        if CRED_KEYS.search(k) and not k.endswith("_file"):
            raise ConnectorError(f"config key {k!r} looks like a credential; store it in a file and use '{k}_file'")
    for need in CONNECTORS[ctype].required:
        if need not in config:
            raise ConnectorError(f"{ctype} needs '{need}'")


class ICSCalendar:
    """Reads a local .ics file (e.g. synced read-only by vdirsyncer)."""

    required = ("path", "collection")

    def items(self, config: dict) -> list[tuple[str, bytes]]:
        text = Path(config["path"]).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\r?\n[ \t]", "", text)              # unfold continuation lines
        out = []
        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
            fields = {}
            for line in block.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fields[k.split(";")[0].upper()] = v.replace("\\n", "\n").replace("\\,", ",")
            start = fields.get("DTSTART", "")
            try:
                when = dt.datetime.strptime(start[:8], "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                when = start
            uid = re.sub(r"[^A-Za-z0-9_.-]", "_", fields.get("UID", start))[:80]
            md = (f"# {fields.get('SUMMARY', '(no title)')}\nDate: {when}\nLocation: {fields.get('LOCATION', '')}\n\n"
                  f"{fields.get('DESCRIPTION', '')}")
            out.append((f"calendar/{when}-{uid}.md", md.encode()))
        return out


class MaildirReadOnly:
    """Reads a local Maildir (e.g. synced read-only by mbsync). Never marks, moves or deletes mail."""

    required = ("path", "collection")

    def items(self, config: dict) -> list[tuple[str, bytes]]:
        box = mailbox.Maildir(config["path"], factory=None, create=False)
        out = []
        for key in list(box.keys())[: int(config.get("max_messages", 5000))]:
            with box.get_file(key) as fh:            # read-only file access
                out.append((f"mail/{key}.eml", fh.read()))
        return out


CONNECTORS = {"ics_calendar": ICSCalendar(), "maildir": MaildirReadOnly()}


def sync(store, embedder, settings, conn: dict) -> dict:
    """Pull items from one connector into its collection. Honors the kill switch."""
    from .ingest import Ingestor

    if not conn["enabled"]:
        raise ConnectorError("connector is disabled (kill switch)")
    if settings.air_gapped and conn["type"] not in CONNECTORS:
        raise ConnectorError("air-gapped")
    impl = CONNECTORS[conn["type"]]
    col = store.get_collection(conn["config"]["collection"])
    if not col:
        raise ConnectorError("target collection does not exist")
    ing = Ingestor(store, embedder, settings)
    counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for path, data in impl.items(conn["config"]):
        counts[ing.ingest_bytes(col, f"{conn['id']}/{path}", data)["status"]] += 1
    store.touch_connector(conn["id"])
    return counts
