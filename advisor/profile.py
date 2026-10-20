"""Profiles: load, validate against advisor/schema/profile.schema.yaml, normalize for the engine."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).parent / "schema" / "profile.schema.yaml"
_SCHEMA = None


class ProfileError(ValueError):
    pass


def schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


@dataclass
class Hardware:
    known: bool = False              # False -> "procurement mode": recommend what to buy
    platform: str = "cpu"            # apple | nvidia | amd | cpu | nvidia_unified | amd_unified
    hw_key: str | None = None        # bandwidth/compute lookup key (apple_m4_pro, rtx_4090, ...)
    ram_gb: float = 0.0
    vram_gb: float = 0.0
    gpu_count: int = 1
    bandwidth_gbps: float | None = None
    tflops_fp16: float | None = None
    free_disk_gb: float | None = None
    usable_gb: float | None = None   # set for procurement options (memory available to models)
    source: str = "profile"          # profile | detected | procurement option id


@dataclass
class Item:
    requested: bool = False
    justification: str = ""
    admin_approved: bool = False


@dataclass
class Profile:
    id: str
    description: str
    org_type: str
    org_name: str
    skill: str
    departments: list[str]
    users: int
    peak_concurrent: int
    jobs: list[str]
    data_types: list[str]
    volume_gb: float
    growth_gb_per_month: float
    languages: list[str]
    sensitivity_level: str
    regulated_data: list[str]
    air_gapped: bool
    data_residency: str
    queries_per_user_per_day: float
    latency: str
    offline_required: bool
    context_needs: str
    hardware: Hardware
    budget: str
    deployment_preference: str
    autodetect: bool
    web_research: bool
    connectors: list[str]
    cloud_burst: bool
    sso: bool
    retention: dict
    shape: str
    agents_count: int
    agents_always_on: bool
    customization: dict[str, Item]
    blockers: list[str]
    priorities: list[str]
    licensing_use: str
    licensing_scale: dict
    acknowledged_triggers: list[str]
    runtime_prefs: dict
    assumptions: list[str]
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def regulated(self) -> bool:
        return bool(self.regulated_data) or self.sensitivity_level == "regulated"

    @property
    def is_org(self) -> bool:
        return self.org_type in {"small_business", "smb"}

    @property
    def sessions(self) -> int:
        """Concurrent sessions to size for: human peak + agent sessions."""
        return self.peak_concurrent + self.agents_count


PLATFORM_MAP = {"nvidia_dgx_spark": "nvidia_unified", "amd_ryzen_ai_max": "amd_unified", "apple_silicon": "apple"}


def normalize_hardware(override: dict, source: str = "profile") -> Hardware:
    if not override or "platform" not in override:
        return Hardware(known=False, source="procurement")
    plat = override["platform"]
    gpu = override.get("gpu", "none")
    if plat in PLATFORM_MAP:
        p = PLATFORM_MAP[plat]
    elif gpu == "nvidia":
        p = "nvidia"
    elif gpu == "amd":
        p = "amd"
    else:
        p = "cpu"
    if p in {"nvidia", "amd"} and not override.get("vram_gb"):
        raise ProfileError("hardware.override.vram_gb is required when gpu is nvidia or amd")
    if "ram_gb" not in override and p in {"apple", "cpu", "nvidia_unified", "amd_unified"}:
        raise ProfileError("hardware.override.ram_gb is required for this platform")
    return Hardware(
        known=True, platform=p,
        hw_key=override.get("gpu_model") if p in {"nvidia", "amd"} else override.get("chip"),
        ram_gb=float(override.get("ram_gb", 0)), vram_gb=float(override.get("vram_gb", 0)),
        gpu_count=int(override.get("gpu_count", 1)), bandwidth_gbps=override.get("bandwidth_gbps"),
        tflops_fp16=override.get("tflops_fp16"), free_disk_gb=override.get("free_disk_gb"), source=source,
    )


def validate_raw(raw: dict) -> None:
    v = jsonschema.Draft202012Validator(schema())
    errs = sorted(v.iter_errors(raw), key=lambda e: list(e.path))
    if errs:
        lines = [f"{'.'.join(str(p) for p in e.path) or '(top)'}: {e.message}" for e in errs]
        raise ProfileError("Invalid profile:\n  - " + "\n  - ".join(lines))
    if raw["users"]["peak_concurrent"] > raw["users"]["count"]:
        raise ProfileError("Invalid profile:\n  - users.peak_concurrent cannot exceed users.count")


def profile_from_dict(raw: dict[str, Any], hardware_override: dict | None = None) -> Profile:
    """hardware_override: detected hardware merged UNDER the profile's explicit override."""
    raw = copy.deepcopy(raw)
    validate_raw(raw)
    hw_raw = raw.get("hardware", {})
    override = dict(hw_raw.get("override") or {})
    source = "profile"
    if hardware_override:
        override = {**hardware_override, **override}
        source = "detected" if not hw_raw.get("override") else "detected+profile"
    data = raw.get("data", {})
    sens = raw["sensitivity"]
    usage = raw.get("usage", {})
    feats = raw.get("features", {})
    wl = raw.get("workload", {})
    agents = wl.get("agents", {})
    cust = raw.get("customization", {})
    lic = raw["licensing"]
    return Profile(
        id=raw["profile_id"], description=raw.get("description", ""),
        org_type=raw["org"]["type"], org_name=raw["org"].get("name", ""),
        skill=raw["org"]["technical_skill"], departments=raw["org"].get("departments", []),
        users=raw["users"]["count"], peak_concurrent=raw["users"]["peak_concurrent"],
        jobs=list(raw["jobs"]),
        data_types=data.get("types", []), volume_gb=float(data.get("volume_gb", 0)),
        growth_gb_per_month=float(data.get("growth_gb_per_month", 0)), languages=data.get("languages", ["en"]),
        sensitivity_level=sens["level"], regulated_data=sens.get("regulated_data", []),
        air_gapped=bool(sens.get("air_gapped", False)), data_residency=sens.get("data_residency", "local_device"),
        queries_per_user_per_day=float(usage.get("queries_per_user_per_day", 20)),
        latency=usage.get("latency", "few_seconds"), offline_required=bool(usage.get("offline_required", False)),
        context_needs=usage.get("context_needs", "standard"),
        hardware=normalize_hardware(override, source),
        budget=hw_raw.get("budget_usd", "none"), deployment_preference=hw_raw.get("deployment_preference", "same_machine"),
        autodetect=bool(hw_raw.get("autodetect", False)),
        web_research=bool(feats.get("web_research", False)), connectors=feats.get("connectors", []),
        cloud_burst=bool(feats.get("cloud_burst", False)), sso=bool(feats.get("sso", False)),
        retention=raw.get("retention", {}),
        shape=wl.get("shape", "balanced"), agents_count=int(agents.get("count", 0)),
        agents_always_on=bool(agents.get("always_on", False)),
        customization={k: Item(**(cust.get(k) or {})) for k in ("post_training", "distillation", "abliteration")},
        blockers=raw.get("blockers", []), priorities=raw.get("priorities", []),
        licensing_use=lic["use"], licensing_scale=lic.get("scale", {}),
        acknowledged_triggers=lic.get("acknowledged_triggers", []),
        runtime_prefs=raw.get("runtime_preferences", {}), assumptions=raw.get("assumptions", []),
        raw=raw,
    )


def load_raw(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_profile(path: str | Path, hardware_override: dict | None = None) -> Profile:
    return profile_from_dict(load_raw(path), hardware_override)
