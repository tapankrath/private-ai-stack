import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor.config import load_config  # noqa: E402
from advisor.profile import profile_from_dict  # noqa: E402

PROFILE_FILES = sorted(p for p in (ROOT / "profiles").glob("*.yaml") if not p.name.startswith("_"))
TEMPLATE = yaml.safe_load((ROOT / "profiles" / "_template.yaml").read_text())


def raw_profiles() -> dict:
    return {p.stem: yaml.safe_load(p.read_text()) for p in PROFILE_FILES}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and v:
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def make_raw(**over) -> dict:
    """Template profile with deep-merged overrides (hardware defaults to a 16 GB M3 Mac)."""
    base = _merge(TEMPLATE, {"hardware": {"autodetect": False,
                                          "override": {"platform": "apple_silicon", "ram_gb": 16, "chip": "apple_m3", "free_disk_gb": 500}}})
    return _merge(base, over)


def make_profile(**over):
    return profile_from_dict(make_raw(**over))
