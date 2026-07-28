from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class InstanceState(str, Enum):
    CREATED = "Created"
    STARTING = "Starting"
    RUNNING = "Running"
    SUSPENDED = "Suspended"
    STOPPING = "Stopping"
    EXITED = "Exited"
    FAILED = "Failed"


CUDA_BACKENDS = {"cuda", "pseudo_cuda", "hybrid_cuda"}
GPU_ONLY_BACKENDS = {"cuda", "pseudo_cuda", "opencl"}


@dataclass(slots=True)
class GpuDevice:
    index: int
    name: str
    vendor: str = "Unknown"
    dedicated_vram_bytes: int = 0
    used_vram_bytes: int = 0
    utilization_percent: float = 0.0
    temperature_c: float | None = None
    power_w: float | None = None
    source: str = "Fallback"

    @property
    def total_vram_mib(self) -> float:
        return self.dedicated_vram_bytes / (1024 * 1024)

    @property
    def used_vram_mib(self) -> float:
        return self.used_vram_bytes / (1024 * 1024)


@dataclass(slots=True)
class InstanceSpec:
    name: str
    xmrig_path: Path
    source_config: Path
    backend: str = "pseudo_cuda"
    hard_gpu_only: bool = True
    keep_cpu: bool = False
    cuda_loader: Path | None = None
    cuda_devices: str = "0"
    opencl_devices: str = ""
    cuda_bfactor_hint: int | None = None
    cuda_bsleep_hint: int | None = None
    cuda_tune_profile: str = "max"
    cuda_threads: int = 32
    cuda_blocks: int = 0
    cuda_memory_reserve_mib: int = 1024
    randomx_init_threads: int = 1
    force_dataset_vram: bool = True
    native_isolation: bool = True
    pseudo_lane_count: int = 8
    preflight_dry_run: bool = True
    require_cuda_ready: bool = True
    abort_on_cpu_fallback: bool = True
    cpu_affinity: list[int] = field(default_factory=list)
    priority: str = "idle"
    eco_qos: bool = True
    pin_workstation: bool = True
    require_isolation: bool = True
    protected_api_url: str = ""
    protected_baseline_hs: float = 0.0
    max_drop_percent: float = 4.0
    guard_consecutive_samples: int = 3
    guard_action: str = "suspend"
    extra_args: list[str] = field(default_factory=list)

    @property
    def uses_cuda(self) -> bool:
        return self.backend in CUDA_BACKENDS

    @property
    def is_pseudo_cpu(self) -> bool:
        return self.backend == "pseudo_cuda"

    @property
    def is_hybrid(self) -> bool:
        return self.backend == "hybrid_cuda"


@dataclass(slots=True)
class InstanceMetrics:
    hashrate_10s: float = 0.0
    hashrate_60s: float = 0.0
    hashrate_15m: float = 0.0
    shares_good: int = 0
    shares_total: int = 0
    rejected: int = 0
    uptime_seconds: int = 0
    connection: str = ""
    backend_summary: str = ""


@dataclass(slots=True)
class InstanceSnapshot:
    virtual_pid: int
    host_pid: int
    name: str
    state: str
    backend: str
    backend_health: str
    pseudo_lanes: int
    gpu_devices: str
    cpu_percent: float
    memory_mib: float
    hashrate_10s: float
    accepted: int
    rejected: int
    affinity: str
    priority: str
    guard_status: str
    instance_dir: str


def nested_get(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
