"""3-year total cost of ownership: local vs hosted API vs flat-rate subscription.

Honest by design: local AI is not presented as the cheap option. When an API or a
subscription costs less, the result says so plainly and then lists the profile's
non-price reasons for running locally. All figures are ESTIMATES from config/pricing.yaml.
"""

from __future__ import annotations

import datetime as dt

from .config import AdvisorConfig
from .profile import Profile

MONTHS = 36
DEFAULT_POWER = {"apple": (10, 90), "nvidia": (70, 420), "nvidia_unified": (30, 240), "amd_unified": (15, 160),
                 "amd": (60, 350), "cpu": (40, 150)}


def _stale(entry: dict, cfg: AdvisorConfig, today: dt.date) -> bool:
    lv = entry.get("last_verified")
    if not lv:
        return True
    d = lv if isinstance(lv, dt.date) else dt.date.fromisoformat(str(lv))
    return (today - d).days > cfg.pricing["stale_after_days"]


def stale_entries(cfg: AdvisorConfig, used: list[tuple[str, dict]], today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    return [name for name, e in used if _stale(e, cfg, today)]


def token_volume(p: Profile, cfg: AdvisorConfig) -> dict:
    u = cfg.pricing["usage_assumptions"]
    queries = p.users * p.queries_per_user_per_day * u["working_days_per_month"] * MONTHS
    human_in = queries * u["input_tokens_per_query"][p.context_needs]
    human_out = queries * u["output_tokens_per_query"]
    days = 30 * MONTHS if p.agents_always_on else u["working_days_per_month"] * MONTHS
    agent_in = p.agents_count * u["agent_tokens_per_day"]["input"] * days
    agent_out = p.agents_count * u["agent_tokens_per_day"]["output"] * days
    return {"queries": queries, "human_in": human_in, "human_out": human_out, "agent_in": agent_in, "agent_out": agent_out}


def _api_cost(price: dict, tin: float, tout: float) -> float:
    return tin / 1e6 * price["input"] + tout / 1e6 * price["output"]


def tco(p: Profile, rec: dict, cfg: AdvisorConfig, today: dt.date | None = None) -> dict:
    pr = cfg.pricing
    run = pr["running"]
    tier = rec["tier"]
    plan = rec["plan"]
    used_entries: list[tuple[str, dict]] = [("running costs", run), ("open-model API price", pr["api"]["open_model"]),
                                            ("frontier API price", pr["api"]["frontier"]), ("subscription price", pr["subscription"])]

    # ---------------- local
    proc = rec.get("procurement")
    if proc:
        opt = next(o for o in pr["hardware_options"] if o["id"] == proc["id"])
        hw_cost = sum(opt["price_usd"]) / 2
        idle, load = opt["power_watts"]["idle"], opt["power_watts"]["load"]
        share = 1.0
        hw_note = f"${opt['price_usd'][0]:,}-${opt['price_usd'][1]:,} for {opt['name']} (estimate)"
        used_entries.append((f"price of {opt['name']}", opt))
    elif rec.get("upgrades_required") and rec.get("upgrade_options"):
        o = rec["upgrade_options"][0]
        opt = next(x for x in pr["hardware_options"] if x["id"] == o["id"])
        hw_cost = sum(opt["price_usd"]) / 2
        idle, load = opt["power_watts"]["idle"], opt["power_watts"]["load"]
        share = 1.0
        hw_note = f"${opt['price_usd'][0]:,}-${opt['price_usd'][1]:,} (cheapest upgrade that fits, estimate)"
        used_entries.append((f"price of {opt['name']}", opt))
    else:
        hw_cost = 0.0
        idle, load = DEFAULT_POWER.get(plan["hardware"]["platform"], (40, 150))
        share = run["existing_machine_power_share"]
        hw_note = "$0 new hardware (existing machine fits)"
    hours = run["active_hours_per_day"]
    kwh = (load * hours + idle * (24 - hours)) / 1000 * 365 * 3 * share
    power = kwh * run["electricity_usd_per_kwh"]
    rate = run["admin_rate_usd_per_hour"][p.org_type]
    admin = run["admin_hours_per_month"][tier] * rate * MONTHS
    network = run["networking_one_time_usd"][tier]
    local_total = hw_cost + power + admin + network

    # ---------------- hosted
    v = token_volume(p, cfg)
    tin, tout = v["human_in"] + v["agent_in"], v["human_out"] + v["agent_out"]
    api_open = _api_cost(pr["api"]["open_model"]["usd_per_million"], tin, tout)
    api_frontier = _api_cost(pr["api"]["frontier"]["usd_per_million"], tin, tout)
    sub = pr["subscription"]
    per_user = sub["heavy_user_usd_per_month"] if p.queries_per_user_per_day >= sub["heavy_user_queries_per_day"] else sub["usd_per_user_per_month"]
    sub_total = p.users * per_user * MONTHS
    sub_agents = 0.0
    if p.agents_count and not sub["covers_agents"]:
        sub_agents = _api_cost(pr["api"]["open_model"]["usd_per_million"], v["agent_in"], v["agent_out"])
    sub_total += sub_agents

    options = {
        "local": {"label": "Local (this recommendation)", "total_usd": round(local_total),
                  "breakdown": {"hardware": round(hw_cost), "power": round(power), "admin_time": round(admin), "networking": network},
                  "hardware_note": hw_note},
        "api_open": {"label": pr["api"]["open_model"]["label"], "total_usd": round(api_open)},
        "api_frontier": {"label": pr["api"]["frontier"]["label"], "total_usd": round(api_frontier)},
        "subscription": {"label": sub["label"], "total_usd": round(sub_total),
                         "note": (f"includes ~${round(sub_agents):,} of API usage for agents (subscriptions rarely cover them)" if sub_agents else None)},
    }
    cheapest_key = min(options, key=lambda k: options[k]["total_usd"])
    local_cheapest = cheapest_key == "local"
    reasons = [w["benefit"] for w in rec.get("why_local", []) if w["benefit"] != "cost"]
    if local_cheapest:
        verdict = "On these estimates local is the cheapest of the options over three years."
    else:
        diff = options["local"]["total_usd"] - options[cheapest_key]["total_usd"]
        short = {"api_open": "a hosted open-weight model API", "api_frontier": "a frontier model API",
                 "subscription": "a flat-rate subscription"}[cheapest_key]
        verdict = (f"On these estimates, {short} would be cheaper than local by about "
                   f"${diff:,} over three years. Local is recommended here for {', '.join(reasons[:3]) or 'non-price reasons'}, not price.")
        if p.air_gapped or p.regulated:
            verdict += (" Note that the hosted options would send prompts and documents off-site, which this profile's "
                        + ("air-gap requirement rules out." if p.air_gapped else "regulated data makes a compliance question."))
    stale = stale_entries(cfg, used_entries, today)
    return {
        "months": MONTHS, "options": options, "local": options["local"], "cheapest": cheapest_key,
        "local_is_cheapest": local_cheapest, "verdict": verdict, "token_volume": v,
        "stale_or_unverified_prices": stale,
        "assumptions": [
            f"{p.users} user(s) x {p.queries_per_user_per_day:g} queries/day x {pr['usage_assumptions']['working_days_per_month']} days/month",
            f"{pr['usage_assumptions']['input_tokens_per_query'][p.context_needs]:,} input + {pr['usage_assumptions']['output_tokens_per_query']} output tokens per query",
            f"{p.agents_count} agent(s){' always on' if p.agents_always_on else ''}",
            f"admin time at ${rate}/hour{' (your own time is not priced)' if rate == 0 else ''}; electricity ${run['electricity_usd_per_kwh']}/kWh",
            "High-memory machines may hold resale value while memory prices are high; resale is not counted.",
        ],
    }
