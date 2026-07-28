from __future__ import annotations

import json
import re
import socket
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from app.cuda_tuning import CudaRxProfile
from app.models import CUDA_BACKENDS


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "xmrig-instance"


def choose_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_instance_directory(base: Path, name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    resolved_base = base.expanduser().resolve()
    resolved_base.mkdir(parents=True, exist_ok=True)
    path = resolved_base / f"{slugify(name)}-{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _ensure_object(config: dict[str, Any], key: str) -> dict[str, Any]:
    existing = config.get(key)
    if isinstance(existing, dict):
        return existing
    replacement: dict[str, Any] = {}
    config[key] = replacement
    return replacement


def _force_randomx_dataset_to_vram(backend: dict[str, Any]) -> int:
    changed = 0
    for profile_name, profile in backend.items():
        if not str(profile_name).lower().startswith("rx") or not isinstance(profile, list):
            continue
        for entry in profile:
            if isinstance(entry, dict):
                entry["dataset_host"] = False
                changed += 1
    return changed


def _install_cuda_rx_profile(cuda: dict[str, Any], profile: CudaRxProfile) -> None:
    entry = profile.as_xmrig_json()
    # XMRig commonly resolves rx/0 through the generic "rx" profile. Supplying
    # both names also keeps an explicitly selected rx/0 pool from falling back to
    # a stale source profile.
    cuda["rx"] = [deepcopy(entry)]
    cuda["rx/0"] = [deepcopy(entry)]


def patch_xmrig_config(
    source: dict[str, Any],
    *,
    instance_name: str,
    instance_id: str,
    api_port: int,
    backend: str,
    keep_cpu: bool,
    hard_gpu_only: bool = False,
    force_dataset_vram: bool = False,
    cuda_loader: Path | None = None,
    cuda_devices: str = "",
    opencl_devices: str = "",
    randomx_init_threads: int = 1,
    cuda_rx_profile: CudaRxProfile | None = None,
) -> dict[str, Any]:
    del cuda_devices, opencl_devices  # Device selection is applied with documented CLI flags.

    config = deepcopy(source)
    config["autosave"] = False
    config["background"] = False
    config["title"] = instance_name
    config["print-time"] = max(10, int(config.get("print-time", 10) or 10))
    config["health-print-time"] = max(15, int(config.get("health-print-time", 30) or 30))

    api = _ensure_object(config, "api")
    api["id"] = instance_id
    api["worker-id"] = instance_name

    http = _ensure_object(config, "http")
    http.update(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": int(api_port),
            "access-token": None,
            "restricted": True,
        }
    )

    if backend == "existing" and not hard_gpu_only:
        return config

    cpu = _ensure_object(config, "cpu")
    cuda = _ensure_object(config, "cuda")
    opencl = _ensure_object(config, "opencl")

    if backend == "cpu":
        cpu["enabled"] = True
        cuda["enabled"] = False
        opencl["enabled"] = False
    elif backend in CUDA_BACKENDS:
        # pseudo_cuda and cuda are GPU-only; hybrid_cuda deliberately keeps the
        # real CPU backend active in addition to CUDA.
        cpu["enabled"] = bool(backend == "hybrid_cuda" or (keep_cpu and not hard_gpu_only))
        cuda["enabled"] = True
        cuda["nvml"] = True
        if cuda_loader is not None:
            cuda["loader"] = cuda_loader.expanduser().resolve().as_posix()
        if cuda_rx_profile is not None:
            _install_cuda_rx_profile(cuda, cuda_rx_profile)
        opencl["enabled"] = False
    elif backend == "opencl":
        cpu["enabled"] = bool(keep_cpu and not hard_gpu_only)
        cuda["enabled"] = False
        opencl["enabled"] = True
    else:
        raise ValueError(f"Unsupported backend mode: {backend}")

    if hard_gpu_only:
        # CPU hashing is disabled. The remaining CPU work is XMRig's network,
        # CUDA driver control, and RandomX dataset initialization. Limit that
        # startup work to the requested count and the process control affinity.
        cpu.update(
            {
                "enabled": False,
                "huge-pages": False,
                "huge-pages-jit": False,
                "memory-pool": 0,
                "yield": True,
                "priority": 0,
                "max-threads-hint": 1,
            }
        )
        randomx = _ensure_object(config, "randomx")
        randomx.update(
            {
                "init": max(1, int(randomx_init_threads)),
                "rdmsr": False,
                "wrmsr": False,
                "numa": False,
                "1gb-pages": False,
            }
        )
        if force_dataset_vram:
            _force_randomx_dataset_to_vram(cuda if backend in CUDA_BACKENDS else opencl)

    return config


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"XMRig config must contain a JSON object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
