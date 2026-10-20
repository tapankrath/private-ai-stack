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


RECALL_JOBS = {"knowledge_recall", "conversation_recall"}


def tokens_per_query(p: Profile, cfg: AdvisorConfig) -> dict:
    """Input tokens a hosted API bills per question: the question itself, the chat history that is
    resent with it, and the passages Recall adds. `low` counts the question only."""
    u = cfg.pricing["usage_assumptions"]
    base = u["input_tokens_per_query"][p.context_needs]
    history = u["history_tokens_per_query"]
    recall = u["recall_tokens_per_query"] if RECALL_JOBS & set(p.jobs) else 0
    return {"base": base, "history": history, "recall": recall, "typical": base + history + recall, "low": base}


def token_volume(p: Profile, cfg: AdvisorConfig) -> dict:
    u = cfg.pricing["usage_assumptions"]
    tq = tokens_per_query(p, cfg)
    queries = p.users * p.queries_per_user_per_day * u["working_days_per_month"] * MONTHS
    human_in = queries * tq["typical"]
    human_in_low = queries * tq["low"]
    human_out = queries * u["output_tokens_per_query"]
    days = 30 * MONTHS if p.agents_always_on else u["working_days_per_month"] * MONTHS
    agent_in = p.agents_count * u["agent_tokens_per_day"]["input"] * days
    agent_out = p.agents_count * u["agent_tokens_per_day"]["output"] * days
    return {"queries": queries, "human_in": human_in, "human_in_low": human_in_low, "human_out": human_out,
            "agent_in": agent_in, "agent_out": agent_out, "per_query": tq}


def _api_cost(price: dict, tin: float, tout: float, fee: float = 0.0) -> float:
    return (tin / 1e6 * price["input"] + tout / 1e6 * price["output"]) * (1 + fee)


def _tokens_assumption(v: dict, pr: dict) -> str:
    q = v["per_query"]
    parts = f"{q['base']:,} question and instructions + {q['history']:,} chat history resent"
    if q["recall"]:
        parts += f" + {q['recall']:,} Recall passages"
    return (f"hosted APIs bill ~{q['typical']:,} input tokens per question ({parts}) and "
            f"{pr['usage_assumptions']['output_tokens_per_query']} output; the low figure counts the question only, at the cheapest provider")


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
    tout = v["human_out"] + v["agent_out"]
    tin, tin_low = v["human_in"] + v["agent_in"], v["human_in_low"] + v["agent_in"]
    om, fr = pr["api"]["open_model"], pr["api"]["frontier"]
    om_fee, fr_fee = om.get("router_fee_fraction", 0), fr.get("router_fee_fraction", 0)
    api_open = _api_cost(om["usd_per_million"], tin, tout, om_fee)
    api_open_low = _api_cost(om.get("low_usd_per_million", om["usd_per_million"]), tin_low, tout, om_fee)
    api_frontier = _api_cost(fr["usd_per_million"], tin, tout, fr_fee)
    api_frontier_low = _api_cost(fr["usd_per_million"], tin_low, tout, fr_fee)
    sub = pr["subscription"]
    per_user = sub["heavy_user_usd_per_month"] if p.queries_per_user_per_day >= sub["heavy_user_queries_per_day"] else sub["usd_per_user_per_month"]
    sub_total = p.users * per_user * MONTHS
    sub_agents = 0.0
    if p.agents_count and not sub["covers_agents"]:
        sub_agents = _api_cost(om["usd_per_million"], v["agent_in"], v["agent_out"], om_fee)
    sub_total += sub_agents

    options = {
        "local": {"label": "Local (this recommendation)", "total_usd": round(local_total), "low_usd": round(local_total),
                  "breakdown": {"hardware": round(hw_cost), "power": round(power), "admin_time": round(admin), "networking": network},
                  "hardware_note": hw_note, "data_goes_to": None},
        "api_open": {"label": om["label"], "total_usd": round(api_open), "low_usd": round(api_open_low),
                     "fee_note": f"includes the router's {om_fee:.1%} fee" if om_fee else None, "data_goes_to": om.get("data_goes_to")},
        "api_frontier": {"label": fr["label"], "total_usd": round(api_frontier), "low_usd": round(api_frontier_low),
                         "fee_note": f"includes a {fr_fee:.1%} fee" if fr_fee else None, "data_goes_to": fr.get("data_goes_to")},
        "subscription": {"label": sub["label"], "total_usd": round(sub_total), "low_usd": round(sub_total),
                         "note": (f"includes ~${round(sub_agents):,} of API usage for agents (subscriptions rarely cover them)" if sub_agents else None),
                         "data_goes_to": sub.get("data_goes_to")},
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
        goes = options[cheapest_key]["data_goes_to"] or "the provider"
        verdict += (f" The saving has a privacy cost: every question, the chat history sent with it"
                    + (" and the document passages Recall finds" if v["per_query"]["recall"] else "")
                    + f" would go to {goes}.")
        if p.air_gapped or p.regulated:
            verdict += (" For this profile that is not just a preference: its "
                        + ("air-gap requirement rules hosted options out." if p.air_gapped else "regulated data makes it a compliance question."))
    stale = stale_entries(cfg, used_entries, today)
    return {
        "months": MONTHS, "options": options, "local": options["local"], "cheapest": cheapest_key,
        "local_is_cheapest": local_cheapest, "verdict": verdict, "token_volume": v,
        "stale_or_unverified_prices": stale,
        "assumptions": [
            f"{p.users} user(s) x {p.queries_per_user_per_day:g} queries/day x {pr['usage_assumptions']['working_days_per_month']} days/month",
            _tokens_assumption(v, pr),
            f"{p.agents_count} agent(s){' always on' if p.agents_always_on else ''}",
            f"admin time at ${rate}/hour{' (your own time is not priced)' if rate == 0 else ''}; electricity ${run['electricity_usd_per_kwh']}/kWh",
            "High-memory machines may hold resale value while memory prices are high; resale is not counted.",
        ],
    }
