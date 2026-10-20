"""Local hardware detection. Reads only local system information; makes no network calls."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess


def _run(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None


def _ram_gb() -> float | None:
    try:
        import psutil  # optional

        return psutil.virtual_memory().total / 1e9
    except ImportError:
        pass
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024 / 1e9
    if hasattr(os, "sysconf"):
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
        except (ValueError, OSError):
            pass
    return None


def normalize_apple_chip(brand: str) -> str | None:
    """'Apple M2 Pro' -> 'apple_m2_pro'."""
    m = re.search(r"Apple\s+(M\d)(?:\s+(Pro|Max|Ultra))?", brand or "", re.I)
    if not m:
        return None
    key = "apple_" + m.group(1).lower()
    if m.group(2):
        key += "_" + m.group(2).lower()
    return key


def normalize_nvidia_gpu(name: str) -> str | None:
    """'NVIDIA GeForce RTX 4090' -> 'rtx_4090'; 'NVIDIA L40S' -> 'l40s'."""
    n = (name or "").lower().replace("nvidia", "").replace("geforce", "").strip()
    rules = [
        (r"rtx\s*6000\s*ada", "rtx_6000_ada"),
        (r"rtx\s*a(\d{4})", r"rtx_a\1"),
        (r"rtx\s*(\d{4})\s*ti", r"rtx_\1_ti"),
        (r"rtx\s*(\d{4})", r"rtx_\1"),
        (r"\bl40s\b", "l40s"),
        (r"a100.*80", "a100_80gb"),
        (r"h100.*pcie", "h100_pcie"),
    ]
    for pat, rep in rules:
        m = re.search(pat, n)
        if m:
            return m.expand(rep) if "\\" in rep else rep
    return None


def detect() -> dict:
    """Return a dict shaped like profile `hardware.override` (best effort, local only)."""
    hw: dict = {}
    ram = _ram_gb()
    if ram:
        hw["ram_gb"] = round(ram, 1)
    try:
        hw["free_disk_gb"] = round(shutil.disk_usage(os.path.expanduser("~")).free / 1e9, 1)
    except OSError:
        pass
    system = platform.system()
    x86 = {"Linux": "linux_x86", "Windows": "windows_x86", "Darwin": "mac_intel"}.get(system, "linux_x86")
    if platform.machine().lower() in {"aarch64", "arm64"} and system == "Linux":
        x86 = "linux_arm"

    if system == "Darwin" and platform.machine() == "arm64":
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or ""
        hw.update(platform="apple_silicon", gpu="integrated", vram_gb=0)
        chip = normalize_apple_chip(brand)
        if chip:
            hw["chip"] = chip
        return hw

    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if smi:
        names, total_mib = [], 0.0
        for line in smi.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2:
                names.append(parts[0])
                try:
                    total_mib += float(parts[1])
                except ValueError:  # unified-memory GPUs report "[N/A]"
                    pass
        if names and "gb10" in names[0].lower():   # DGX Spark-class unified memory
            hw.update(platform="nvidia_dgx_spark", gpu="nvidia")
            return hw
        if names:
            hw.update(platform=x86, gpu="nvidia", gpu_count=len(names),
                      vram_gb=round(total_mib * 1.048576 / 1000, 1))
            model = normalize_nvidia_gpu(names[0])
            if model:
                hw["gpu_model"] = model
            return hw

    hw.update(platform=x86, gpu="none", vram_gb=0)
    return hw
