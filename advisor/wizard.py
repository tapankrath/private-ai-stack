"""Interactive CLI questionnaire that writes a schema-valid profile.yaml.

Order follows the INTAKE rules: who -> jobs -> data -> sensitivity -> usage (incl. agents)
-> hardware/budget -> licensing -> blockers -> customization. Plain words, no jargon.
Vague answers fall back to the conservative (smaller, more private) choice and are recorded
under `assumptions`. Never asks for passwords, logins, keys or document contents.
"""

from __future__ import annotations

from typing import Callable

from . import hardware as hwmod
from .profile import profile_from_dict

JOBS = [
    ("general_qa", "answer general questions"), ("writing", "help with writing"),
    ("research", "research and summaries"), ("knowledge_recall", "search your own documents"),
    ("conversation_recall", "find past conversations"), ("drafting", "draft emails and documents"),
    ("coding", "coding help (a main use)"), ("data_analysis", "spreadsheets and data"),
    ("scanned_docs", "read scanned documents and receipts"), ("image_understanding", "understand photos or charts"),
    ("transcription", "transcribe meetings and voice notes"), ("translation", "translate"),
    ("agent_tasks", "run multi-step tasks on its own"),
]
DATA = [("text", "notes and text files"), ("pdf", "PDFs"), ("scans", "scanned paper"), ("images", "photos"),
        ("audio", "recordings"), ("code", "source code"), ("spreadsheets", "spreadsheets"),
        ("email_exports", "exported email files (.mbox/.eml)")]
REGULATED = ["health", "financial", "legal", "children", "client_confidential"]
BLOCKERS = [("cost", "cost"), ("expertise", "not enough technical help"), ("security_review", "needs a security review"),
            ("where_to_begin", "not sure where to begin")]
BUDGETS = ["none", "lt_1500", "lt_5000", "lt_10000", "lt_20000", "lt_150000", "gt_150000"]


def _ask(prompt, default, ask):
    ans = ask(f"{prompt} [{default}]: ").strip()
    return ans or str(default)


def _choose(prompt, options, default, ask):
    while True:
        ans = _ask(f"{prompt} ({'/'.join(options)})", default, ask)
        if ans in options:
            return ans
        print(f"  Please choose one of: {', '.join(options)}")


def _multi(prompt, options, default_ids, ask):
    print(prompt)
    for i, (oid, label) in enumerate(options, 1):
        print(f"  {i:2}. {label}")
    ids = [o[0] for o in options]
    default = ",".join(str(ids.index(d) + 1) for d in default_ids)
    while True:
        ans = _ask("  Numbers separated by commas (Enter for default, 0 for none)", default, ask)
        if ans.strip() == "0" or not ans.strip():
            return []
        try:
            return list(dict.fromkeys(ids[int(x) - 1] for x in ans.replace(" ", "").split(",") if x))
        except (ValueError, IndexError):
            print("  Use the numbers shown, e.g. 1,3")


def _int(prompt, default, ask):
    while True:
        try:
            return int(_ask(prompt, default, ask))
        except ValueError:
            print("  Please enter a whole number")


def run_wizard(ask: Callable[[str], str] = input, detect: bool = True) -> dict:
    assumptions: list[str] = []
    print("Right-Size Advisor. Press Enter to accept the [default].\n")
    org_type = _choose("Who is this for", ["individual", "household", "small_business", "smb"], "individual", ask)
    skill = _choose("Who will set it up", ["non_technical", "power_user", "it_admin"], "power_user", ask)
    count = _int("How many people will use it", 1, ask)
    peak = min(_int("Most people using it at the same moment", 1, ask), count)
    jobs = _multi("\nWhat do you need it to do?", JOBS, ["general_qa"], ask) or ["general_qa"]
    types = _multi("\nWhat kinds of files will it read?", DATA, ["text", "pdf"] if "knowledge_recall" in jobs else [], ask)
    volume = float(_ask("Roughly how many GB of files", 1, ask)) if types else 0
    langs = [x.strip() for x in _ask("Languages (comma separated)", "en", ask).split(",") if x.strip()]
    regulated = _multi("\nAny of these kinds of sensitive data?", [(x, x.replace("_", " ")) for x in REGULATED], [], ask)
    air = _choose("Must it work with no internet connection at all", ["no", "yes", "not_sure"], "no", ask)
    if air == "not_sure":
        assumptions.append("Air-gapped requirement unclear: chose air-gapped (the more private option).")
    latency = _choose("How quickly should answers start", ["instant", "few_seconds", "batch_ok"], "few_seconds", ask)
    context = _choose("How much text per question", ["standard", "long", "very_long"], "standard", ask)
    agents = _int("How many AI agents will run tasks on their own (0 if none)", 0, ask)
    always_on = agents > 0 and _choose("Will they run all the time", ["no", "yes"], "no", ask) == "yes"

    detected = hwmod.detect() if detect else {}
    if detected:
        print(f"\nDetected hardware: {detected}")
    override = {}
    if not detected or _choose("Is that right", ["yes", "no"], "yes", ask) == "no":
        if _choose("Do you already have the machine", ["yes", "no"], "yes", ask) == "yes":
            plat = _choose("Machine type", ["apple_silicon", "linux_x86", "windows_x86", "nvidia_dgx_spark", "amd_ryzen_ai_max"], "apple_silicon", ask)
            override = {"platform": plat, "ram_gb": float(_ask("Memory (RAM) in GB", 16, ask))}
            if plat in {"linux_x86", "windows_x86"}:
                gpu = _choose("Graphics card", ["none", "nvidia", "amd"], "none", ask)
                override["gpu"] = gpu
                if gpu != "none":
                    override["vram_gb"] = float(_ask("Graphics card memory (VRAM) in GB", 12, ask))
    budget = _choose("Budget for new hardware", BUDGETS, "none", ask)
    use = "commercial" if org_type in {"small_business", "smb"} else _choose("Personal or business use", ["personal", "commercial"], "personal", ask)
    blockers = _multi("\nWhat is holding you back?", BLOCKERS, ["where_to_begin"], ask)
    post = _choose("Interested in training a model on your own material later", ["no", "yes"], "no", ask) == "yes"

    level = "regulated" if regulated else "moderate"
    raw = {
        "schema_version": 1, "profile_id": f"{org_type.replace('_', '-')}-{count}", "description": "Created by the wizard",
        "org": {"type": org_type, "technical_skill": skill},
        "users": {"count": count, "peak_concurrent": max(1, peak)},
        "jobs": jobs,
        "data": {"types": types, "volume_gb": volume, "languages": langs or ["en"]},
        "sensitivity": {"level": level, "regulated_data": regulated, "air_gapped": air in {"yes", "not_sure"},
                        "data_residency": "local_device" if count == 1 else "local_network"},
        "usage": {"latency": latency, "context_needs": context, "offline_required": air in {"yes", "not_sure"}},
        "hardware": {"autodetect": bool(detected) and not override, "override": override, "budget_usd": budget},
        "features": {"web_research": "research" in jobs and air == "no" and not regulated, "connectors": [],
                     "cloud_burst": False, "sso": org_type in {"small_business", "smb"} and bool(regulated)},
        "workload": {"shape": "prompt_heavy" if context != "standard" or "knowledge_recall" in jobs else "balanced",
                     "agents": {"count": agents, "always_on": always_on}},
        "customization": {"post_training": {"requested": post, "justification": "Requested in wizard; confirm with eval set first." if post else ""},
                          "distillation": {"requested": False, "justification": ""},
                          "abliteration": {"requested": False, "justification": "", "admin_approved": False}},
        "blockers": blockers, "priorities": ["privacy", "security", "availability", "cost"],
        "licensing": {"use": use}, "assumptions": assumptions,
    }
    profile_from_dict(raw, detected or None)  # validate before writing
    return raw
