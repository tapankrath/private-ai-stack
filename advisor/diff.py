"""Compare a new recommendation with the previous one in the same output folder."""

from __future__ import annotations


def _summary(rec: dict) -> dict:
    p = rec["plan"]
    g = p.get("general") or {}
    return {
        "tier": rec["tier"],
        "runtime": p["runtime"],
        "general model": f"{g.get('name')} {g.get('quant')}" if g else None,
        "context": g.get("context_tokens"),
        "specialists": sorted(f"{s['name']} {s['quant']}" for s in p["specialists"]),
        "auxiliary models": sorted(a["name"] for a in p["aux"]),
        "components": sorted(c["id"] for c in rec["components"]["included"]),
        "capabilities": sorted(rec["capabilities"]["enabled"]),
        "usable memory GB": p["usable_memory_gb"],
        "hardware": (rec.get("procurement") or {}).get("name") or p["hardware"]["hw_key"],
        "3-year local cost": rec.get("tco", {}).get("local", {}).get("total_usd"),
    }


def diff_recommendations(old: dict, new: dict) -> list[str]:
    a, b = _summary(old), _summary(new)
    out = []
    for key in b:
        if a.get(key) == b[key]:
            continue
        if isinstance(b[key], list):
            added = sorted(set(b[key]) - set(a.get(key) or []))
            removed = sorted(set(a.get(key) or []) - set(b[key]))
            if added:
                out.append(f"{key}: added {', '.join(added)}")
            if removed:
                out.append(f"{key}: removed {', '.join(removed)}")
        else:
            out.append(f"{key}: {a.get(key)} -> {b[key]}")
    return out
