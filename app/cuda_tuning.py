from __future__ import annotations

import csv
import shutil
import subprocess
from dataclasses import dataclass
from io import StringIO


@dataclass(frozen=True, slots=True)
class NvidiaGpuInfo:
    index: int
    name: str
    total_memory_mib: int
    free_memory_mib: int
    multiprocessors: int


@dataclass(frozen=True, slots=True)
class CudaRxProfile:
    index: int
    threads: int
    blocks: int
    bfactor: int
    bsleep: int
    affinity: int
    dataset_host: bool = False

    @property
    def intensity(self) -> int:
        return self.threads * self.blocks

    @property
    def memory_mib(self) -> int:
        # RandomX uses one 2 MiB scratchpad per active CUDA hash lane.
        return self.intensity * 2

    def as_xmrig_json(self) -> dict[str, int | bool]:
        return {
            "index": self.index,
            "threads": self.threads,
            "blocks": self.blocks,
            "bfactor": self.bfactor,
            "bsleep": self.bsleep,
            "affinity": self.affinity,
            "dataset_host": self.dataset_host,
        }


def parse_cuda_device_indexes(value: str) -> list[int]:
    indexes: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            index = int(token, 10)
        except ValueError as exc:
            raise ValueError(f"Invalid CUDA device index: {token!r}") from exc
        if index < 0:
            raise ValueError("CUDA device indexes cannot be negative.")
        indexes.append(index)
    return indexes or [0]


def _to_int(value: str) -> int:
    cleaned = value.strip().split()[0]
    return int(cleaned)


def _query_nvidia_smi(fields: str, timeout_seconds: float) -> list[list[str]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    command = [
        executable,
        f"--query-gpu={fields}",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return [row for row in csv.reader(StringIO(completed.stdout)) if row]


def query_nvidia_gpus(timeout_seconds: float = 4.0) -> list[NvidiaGpuInfo]:
    rows = _query_nvidia_smi(
        "index,name,memory.total,memory.free,multiprocessor_count", timeout_seconds
    )
    has_multiprocessors = bool(rows)
    if not rows:
        rows = _query_nvidia_smi("index,name,memory.total,memory.free", timeout_seconds)

    output: list[NvidiaGpuInfo] = []
    for row in rows:
        if len(row) < 4:
            continue
        try:
            output.append(
                NvidiaGpuInfo(
                    index=_to_int(row[0]),
                    name=row[1].strip(),
                    total_memory_mib=_to_int(row[2]),
                    free_memory_mib=_to_int(row[3]),
                    multiprocessors=_to_int(row[4]) if has_multiprocessors and len(row) > 4 else 0,
                )
            )
        except (ValueError, IndexError):
            continue
    return output


def select_gpu_info(infos: list[NvidiaGpuInfo], index: int) -> NvidiaGpuInfo | None:
    for info in infos:
        if info.index == index:
            return info
    return None


def tune_cuda_rx_profile(
    *,
    device_index: int,
    gpu: NvidiaGpuInfo | None,
    preset: str,
    threads: int,
    blocks_override: int,
    memory_reserve_mib: int,
    affinity: int,
    bfactor_override: int | None = None,
    bsleep_override: int | None = None,
) -> CudaRxProfile | None:
    normalized = preset.strip().lower()
    if normalized in {"existing", "auto", "off"}:
        return None

    if threads <= 0:
        raise ValueError("CUDA threads must be positive.")
    if blocks_override < 0:
        raise ValueError("CUDA blocks override cannot be negative.")
    if memory_reserve_mib < 256:
        raise ValueError("Reserve at least 256 MiB of GPU memory.")

    defaults = {
        "max": (0, 0, 2.0),
        "fast": (2, 0, 1.75),
        "balanced": (4, 10, 1.60),
        "compat": (6, 25, 1.50),
    }
    if normalized not in defaults:
        raise ValueError(f"Unsupported CUDA tuning preset: {preset}")
    default_bfactor, default_bsleep, sm_multiplier = defaults[normalized]
    bfactor = default_bfactor if bfactor_override is None else int(bfactor_override)
    bsleep = default_bsleep if bsleep_override is None else int(bsleep_override)

    if blocks_override > 0:
        blocks = blocks_override
    elif gpu is not None:
        bytes_per_block_mib = threads * 2
        available_mib = max(0, gpu.free_memory_mib - memory_reserve_mib)
        memory_cap = max(1, available_mib // bytes_per_block_mib)
        if gpu.multiprocessors > 0:
            target_blocks = max(1, round(gpu.multiprocessors * sm_multiplier))
        else:
            memory_fraction = {
                "max": 0.95,
                "fast": 0.85,
                "balanced": 0.75,
                "compat": 0.65,
            }[normalized]
            target_blocks = max(1, round(memory_cap * memory_fraction))
        blocks = max(1, min(target_blocks, memory_cap))
    else:
        # Conservative fallback when nvidia-smi is unavailable.
        blocks = 64 if normalized == "max" else 56

    return CudaRxProfile(
        index=int(device_index),
        threads=int(threads),
        blocks=int(blocks),
        bfactor=max(0, min(12, int(bfactor))),
        bsleep=max(0, int(bsleep)),
        affinity=int(affinity),
        dataset_host=False,
    )
