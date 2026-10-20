"""Load the YAML configuration that drives the advisor (rules, catalog, hardware reference)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"


def load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class AdvisorConfig:
    rules: dict
    catalog: dict
    hardware: dict
    runtimes: dict
    pricing: dict

    @property
    def models(self) -> list[dict]:
        return self.catalog["models"]

    def model(self, model_id: str) -> dict:
        for m in self.models:
            if m["id"] == model_id:
                return m
        raise KeyError(model_id)


def load_config(config_dir: Path | None = None) -> AdvisorConfig:
    d = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    cfg = AdvisorConfig(
        rules=load_yaml(d / "rules.yaml"),
        catalog=load_yaml(d / "model_catalog.yaml"),
        hardware=load_yaml(d / "hardware_reference.yaml"),
        runtimes=load_yaml(d / "runtimes.yaml"),
        pricing=load_yaml(d / "pricing.yaml"),
    )
    _validate_catalog(cfg.catalog)
    return cfg


_LLM_ROLES = {"general", "code", "vision", "fast_small"}
_AUX_ROLES = {"embeddings", "reranker", "stt"}


def _validate_catalog(catalog: dict) -> None:
    seen = set()
    for m in catalog["models"]:
        mid = m.get("id")
        if not mid or mid in seen:
            raise ValueError(f"catalog: missing or duplicate id: {mid!r}")
        seen.add(mid)
        if m.get("commercial_use") not in {"yes", "no", "conditional"}:
            raise ValueError(f"catalog {mid}: commercial_use must be yes/no/conditional")
        role = m.get("role")
        if role in _LLM_ROLES:
            for key in ("total_params_b", "active_params_b", "architecture", "quantizations", "quality_rank",
                        "open_weight", "osi_open_source", "allows_fine_tuning", "allows_distillation"):
                if key not in m:
                    raise ValueError(f"catalog {mid}: LLM entry needs {key}")
            if m["architecture"] not in {"dense", "moe"}:
                raise ValueError(f"catalog {mid}: architecture must be dense or moe")
            if m["architecture"] == "dense" and m["active_params_b"] != m["total_params_b"]:
                raise ValueError(f"catalog {mid}: dense models have active == total parameters")
            if "attention" not in m and "kv_bytes_per_token_est" not in m:
                raise ValueError(f"catalog {mid}: needs attention fields or kv_bytes_per_token_est")
            for q in m["quantizations"]:
                if q not in catalog["quantizations"]:
                    raise ValueError(f"catalog {mid}: unknown quantization {q}")
        elif role in _AUX_ROLES:
            if "memory_gb" not in m:
                raise ValueError(f"catalog {mid}: auxiliary entry needs memory_gb")
        else:
            raise ValueError(f"catalog {mid}: unknown role {role!r}")
