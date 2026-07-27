from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psutil


@dataclass(slots=True)
class ExternalXmrigProcess:
    pid: int
    name: str
    executable: str
    command_line: str
    cpu_percent: float
    affinity: list[int]
    mining_cores: list[int]
    mining_core_source: str
    api_url: str
    config_path: str


@dataclass(slots=True)
class CpuSetInfo:
    logical_index: int
    group: int
    core_index: int
    efficiency_class: int
    parked: bool


@dataclass(slots=True)
class HashrateSample:
    hashrate_10s: float
    hashrate_60s: float
    hashrate_15m: float
    accepted: int
    rejected: int

    @property
    def guard_hashrate(self) -> float:
        # Match the stable baseline metric first to avoid false protection triggers
        # from a single noisy 10-second sample.
        if self.hashrate_60s > 0:
            return self.hashrate_60s
        if self.hashrate_10s > 0:
            return self.hashrate_10s
        return self.hashrate_15m

    @property
    def preferred_hashrate(self) -> float:
        if self.hashrate_60s > 0:
            return self.hashrate_60s
        if self.hashrate_10s > 0:
            return self.hashrate_10s
        return self.hashrate_15m


@dataclass(slots=True)
class IsolationAssessment:
    level: str
    title: str
    details: str
    overlapping_cores: list[int]


_PRIORITY_MAP = {
    "idle": getattr(psutil, "IDLE_PRIORITY_CLASS", None),
    "below": getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", None),
    "normal": getattr(psutil, "NORMAL_PRIORITY_CLASS", None),
    "above": getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", None),
    "high": getattr(psutil, "HIGH_PRIORITY_CLASS", None),
}



def query_windows_cpu_sets() -> dict[int, CpuSetInfo]:
    """Return logical CPU metadata from GetSystemCpuSetInformation on Windows 10+."""
    if os.name != "nt":
        return {}
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel32.GetSystemCpuSetInformation
    except (AttributeError, OSError):
        return {}

    function.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    function.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p

    required = ctypes.c_ulong(0)
    function(None, 0, ctypes.byref(required), kernel32.GetCurrentProcess(), 0)
    if required.value <= 0:
        return {}

    buffer = ctypes.create_string_buffer(required.value)
    returned = ctypes.c_ulong(required.value)
    if not function(buffer, required.value, ctypes.byref(returned), kernel32.GetCurrentProcess(), 0):
        return {}

    raw = buffer.raw[: returned.value]
    output: dict[int, CpuSetInfo] = {}
    offset = 0
    while offset + 8 <= len(raw):
        size, info_type = struct.unpack_from("<II", raw, offset)
        if size < 8 or offset + size > len(raw):
            break
        if info_type == 0 and size >= 32:  # CpuSetInformation
            group = struct.unpack_from("<H", raw, offset + 12)[0]
            logical = raw[offset + 14]
            core_index = raw[offset + 15]
            efficiency = raw[offset + 18]
            flags = raw[offset + 19]
            global_index = int(group) * 64 + int(logical)
            output[global_index] = CpuSetInfo(
                logical_index=global_index,
                group=int(group),
                core_index=int(core_index),
                efficiency_class=int(efficiency),
                parked=bool(flags & 0x01),
            )
        offset += size
    return output


def set_process_eco_qos(pid: int, enabled: bool = True) -> None:
    """Enable or disable Windows process execution-speed power throttling (EcoQoS)."""
    if os.name != "nt":
        return

    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [
            ("Version", ctypes.c_ulong),
            ("ControlMask", ctypes.c_ulong),
            ("StateMask", ctypes.c_ulong),
        ]

    PROCESS_SET_INFORMATION = 0x0200
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_POWER_THROTTLING = 4
    EXECUTION_SPEED = 0x1

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.SetProcessInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong
    ]
    kernel32.SetProcessInformation.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(
        PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed for EcoQoS")
    try:
        state = PowerThrottlingState(
            Version=1,
            ControlMask=EXECUTION_SPEED,
            StateMask=EXECUTION_SPEED if enabled else 0,
        )
        if not kernel32.SetProcessInformation(
            handle, PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)
        ):
            raise OSError(ctypes.get_last_error(), "SetProcessInformation(EcoQoS) failed")
    finally:
        kernel32.CloseHandle(handle)

def parse_cpu_list(text: str, cpu_count: int | None = None) -> list[int]:
    value = text.strip()
    if not value:
        return []

    maximum = (cpu_count if cpu_count is not None else (psutil.cpu_count() or 1)) - 1
    cores: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            pieces = token.split("-", 1)
            if len(pieces) != 2:
                raise ValueError(f"Invalid CPU range: {token}")
            start = int(pieces[0].strip())
            end = int(pieces[1].strip())
            if start > end:
                raise ValueError(f"CPU range must increase: {token}")
            cores.update(range(start, end + 1))
        else:
            cores.add(int(token))

    if not cores:
        return []
    if min(cores) < 0 or max(cores) > maximum:
        raise ValueError(f"CPU indexes must be between 0 and {maximum}.")
    return sorted(cores)


def format_cpu_list(cores: Iterable[int]) -> str:
    values = sorted(set(int(core) for core in cores))
    if not values:
        return ""

    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)




def cpu_list_to_mask(cores: Iterable[int]) -> int:
    mask = 0
    for core in sorted(set(int(value) for value in cores)):
        if core < 0:
            raise ValueError("CPU indexes cannot be negative.")
        mask |= 1 << core
    return mask


def cpu_mask_to_list(value: str) -> list[int]:
    text = value.strip()
    if not text:
        return []
    try:
        mask = int(text, 0)
    except ValueError as exc:
        raise ValueError(f"Invalid CPU affinity mask: {value}") from exc
    if mask < 0:
        raise ValueError("CPU affinity mask cannot be negative.")
    return [index for index in range(mask.bit_length()) if mask & (1 << index)]


def extract_cpu_mining_cores(config: dict[str, object]) -> list[int]:
    """Collect explicit XMRig CPU-thread affinities from CPU profiles.

    Automatic affinity (-1) is intentionally ignored because it does not identify
    a specific logical CPU that can safely be reserved for the GPU control plane.
    """
    cpu = config.get("cpu")
    if not isinstance(cpu, dict):
        return []

    found: set[int] = set()

    def visit_profile(value: object) -> None:
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, int):
                    if entry >= 0:
                        found.add(entry)
                elif isinstance(entry, dict):
                    affinity = entry.get("affinity")
                    if isinstance(affinity, int) and affinity >= 0:
                        found.add(affinity)
                    elif isinstance(affinity, list):
                        for item in affinity:
                            if isinstance(item, int) and item >= 0:
                                found.add(item)
        elif isinstance(value, dict):
            affinity = value.get("affinity")
            if isinstance(affinity, int) and affinity >= 0:
                found.add(affinity)
            elif isinstance(affinity, list):
                for item in affinity:
                    if isinstance(item, int) and item >= 0:
                        found.add(item)

    metadata_keys = {
        "enabled", "huge-pages", "huge-pages-jit", "hw-aes", "priority",
        "memory-pool", "yield", "asm", "argon2-impl", "astrobwt-max-size",
        "astrobwt-avx2", "cn-lite", "cn-heavy", "max-threads-hint",
    }
    for key, value in cpu.items():
        if str(key) in metadata_keys:
            continue
        visit_profile(value)
    return sorted(found)


def _endpoint_from_summary(api_url: str, endpoint: str) -> str:
    summary = normalize_summary_url(api_url)
    if not summary:
        return ""
    base = summary.rsplit("/2/summary", 1)[0]
    return base + endpoint


def _read_json_endpoint(api_url: str, endpoint: str, timeout: float = 1.0) -> dict[str, object]:
    url = _endpoint_from_summary(api_url, endpoint)
    if not url:
        return {}
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "GPUVirtualWorkstation/2.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def detect_cpu_mining_cores(
    arguments: list[str],
    config_path: Path | None,
    api_url: str,
) -> tuple[list[int], str]:
    affinity_mask = _option_value(arguments, ("--cpu-affinity",))
    if affinity_mask:
        try:
            cores = cpu_mask_to_list(affinity_mask)
            if cores:
                return cores, "command-line mask"
        except ValueError:
            pass

    if config_path and config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                cores = extract_cpu_mining_cores(payload)
                if cores:
                    return cores, "local config profiles"
        except (OSError, json.JSONDecodeError):
            pass

    if api_url:
        payload = _read_json_endpoint(api_url, "/2/config")
        cores = extract_cpu_mining_cores(payload)
        if cores:
            return cores, "XMRig /2/config"

    return [], "not explicitly detectable"


def normalize_summary_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "http://" + url
    if not url.endswith("/2/summary"):
        url += "/2/summary"
    return url


def read_xmrig_hashrate(api_url: str, timeout: float = 1.2) -> HashrateSample:
    url = normalize_summary_url(api_url)
    if not url:
        raise ValueError("An XMRig API URL is required.")

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "GPUVirtualWorkstation/2.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read XMRig API {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("XMRig API returned an unexpected response.")

    totals = payload.get("hashrate", {}).get("total", [])
    if not isinstance(totals, list):
        totals = []

    def rate(index: int) -> float:
        if index >= len(totals) or totals[index] is None:
            return 0.0
        try:
            return float(totals[index])
        except (TypeError, ValueError):
            return 0.0

    results = payload.get("results", {})
    if not isinstance(results, dict):
        results = {}
    accepted = int(results.get("shares_good", 0) or 0)
    total = int(results.get("shares_total", 0) or 0)
    return HashrateSample(
        hashrate_10s=rate(0),
        hashrate_60s=rate(1),
        hashrate_15m=rate(2),
        accepted=accepted,
        rejected=max(0, total - accepted),
    )


def _option_value(arguments: list[str], names: tuple[str, ...]) -> str:
    for index, argument in enumerate(arguments):
        lowered = argument.lower()
        for name in names:
            if lowered == name and index + 1 < len(arguments):
                return arguments[index + 1]
            prefix = name + "="
            if lowered.startswith(prefix):
                return argument[len(prefix):]
    return ""


def _resolve_config_path(arguments: list[str], cwd: str) -> Path | None:
    value = _option_value(arguments, ("--config", "-c"))
    if not value:
        return None
    path = Path(value.strip('"'))
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    try:
        return path.resolve()
    except OSError:
        return path


def _detect_api_url(arguments: list[str], cwd: str) -> tuple[str, str]:
    host = _option_value(arguments, ("--http-host",)) or "127.0.0.1"
    port = _option_value(arguments, ("--http-port",))
    config_path = _resolve_config_path(arguments, cwd)

    if not port and config_path and config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            http = config.get("http", {}) if isinstance(config, dict) else {}
            if isinstance(http, dict) and http.get("enabled"):
                port = str(http.get("port", "") or "")
                host = str(http.get("host", host) or host)
        except (OSError, json.JSONDecodeError):
            pass

    if host in {"0.0.0.0", "::", "*"}:
        host = "127.0.0.1"
    api_url = f"http://{host}:{port}/2/summary" if port else ""
    return api_url, str(config_path or "")


def discover_xmrig_processes(exclude_pids: set[int] | None = None) -> list[ExternalXmrigProcess]:
    excluded = exclude_pids or set()
    output: list[ExternalXmrigProcess] = []

    for process in psutil.process_iter(["pid", "name", "exe", "cmdline", "cwd"]):
        try:
            pid = int(process.info["pid"])
            if pid in excluded or pid == os.getpid():
                continue
            name = str(process.info.get("name") or "")
            exe = str(process.info.get("exe") or "")
            arguments = [str(item) for item in (process.info.get("cmdline") or [])]
            executable_name = Path(exe).name.lower() if exe else ""
            command_name = Path(arguments[0]).name.lower() if arguments else ""
            if "xmrig" not in name.lower() and "xmrig" not in executable_name and "xmrig" not in command_name:
                continue

            try:
                affinity = list(process.cpu_affinity())
            except (AttributeError, psutil.AccessDenied, psutil.NoSuchProcess):
                affinity = []
            try:
                cpu_percent = float(process.cpu_percent(interval=None))
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cpu_percent = 0.0

            cwd = str(process.info.get("cwd") or "")
            api_url, config_path = _detect_api_url(arguments, cwd)
            resolved_config = Path(config_path) if config_path else None
            mining_cores, mining_source = detect_cpu_mining_cores(
                arguments, resolved_config, api_url
            )
            command_line = subprocess.list2cmdline(arguments) if arguments else exe
            output.append(
                ExternalXmrigProcess(
                    pid=pid,
                    name=name or Path(exe).name or "xmrig",
                    executable=exe,
                    command_line=command_line,
                    cpu_percent=cpu_percent,
                    affinity=affinity,
                    mining_cores=mining_cores,
                    mining_core_source=mining_source,
                    api_url=api_url,
                    config_path=config_path,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
            continue

    return sorted(output, key=lambda item: item.pid)


def recommend_control_cores(
    protected_processes: Iterable[ExternalXmrigProcess],
    count: int = 1,
) -> tuple[list[int], bool, str]:
    count = max(1, int(count))
    try:
        eligible = list(psutil.Process().cpu_affinity())
    except (AttributeError, psutil.Error):
        eligible = list(range(psutil.cpu_count() or 1))

    protected: set[int] = set()
    used_explicit_profiles = False
    for process in protected_processes:
        if process.mining_cores:
            protected.update(process.mining_cores)
            used_explicit_profiles = True
        else:
            protected.update(process.affinity)

    cpu_sets = query_windows_cpu_sets()
    loads = psutil.cpu_percent(interval=0.12, percpu=True)

    def rank(core: int) -> tuple[int, float, int]:
        info = cpu_sets.get(core)
        efficiency = info.efficiency_class if info is not None else 0
        load = loads[core] if core < len(loads) else 100.0
        # Lower EfficiencyClass values are more power-efficient on heterogeneous systems.
        return efficiency, load, -core

    free = sorted((core for core in eligible if core not in protected), key=rank)
    topology_note = ""
    if used_explicit_profiles:
        topology_note += " Explicit XMRig mining-thread affinities were used instead of the broader process affinity."
    if cpu_sets:
        topology_note += " Windows CPU-set efficiency classes were used to prefer efficient cores."

    if free:
        selected = free[:count]
        return (
            selected,
            False,
            "Selected CPU indexes outside the detected XMRig process affinity." + topology_note,
        )

    ranked = sorted(eligible, key=rank)
    selected = ranked[:count]
    return (
        selected,
        True,
        "No process-level free CPU was detected. The least-impact CPU index was selected, but it overlaps the existing miner affinity."
        + topology_note,
    )

def priority_name(process: psutil.Process) -> str:
    try:
        current = process.nice()
    except (psutil.Error, OSError):
        return "unknown"
    for name, value in _PRIORITY_MAP.items():
        if value is not None and current == value:
            return name
    return str(current)


def apply_process_isolation(
    pid: int,
    cores: list[int],
    priority: str,
    *,
    very_low_io: bool = True,
    eco_qos: bool = False,
    strict: bool = False,
) -> list[str]:
    """Apply Windows scheduling hints without making process startup depend on them.

    CPU affinity, priority, EcoQoS, and I/O priority are optimizations. A failure in
    any one of them is reported as a warning and the process is left running unless
    strict=True is explicitly requested by a caller.
    """
    messages: list[str] = []
    errors: list[str] = []

    try:
        process = psutil.Process(pid)
    except (psutil.Error, OSError) as exc:
        if strict:
            raise RuntimeError(f"Could not open PID {pid}: {exc}") from exc
        return [f"warning: process scheduling controls unavailable ({exc})"]

    if cores:
        try:
            try:
                eligible = set(process.cpu_affinity())
            except (AttributeError, psutil.Error, OSError):
                eligible = set()
            requested = sorted(set(int(core) for core in cores))
            usable = [core for core in requested if not eligible or core in eligible]
            if not usable:
                raise ValueError(
                    f"requested CPUs {format_cpu_list(requested)} are outside the process CPU set"
                )
            process.cpu_affinity(usable)
            messages.append(f"CPU affinity={format_cpu_list(usable)}")
            skipped = sorted(set(requested) - set(usable))
            if skipped:
                errors.append(f"ignored unavailable CPUs {format_cpu_list(skipped)}")
        except (AttributeError, psutil.Error, OSError, ValueError) as exc:
            errors.append(f"CPU affinity unchanged ({exc})")

    priority_value = _PRIORITY_MAP.get(priority)
    if priority_value is None:
        if os.name == "nt":
            errors.append(f"unsupported Windows priority {priority!r}")
    else:
        try:
            process.nice(priority_value)
            messages.append(f"priority={priority}")
        except (psutil.Error, OSError) as exc:
            errors.append(f"priority unchanged ({exc})")

    if eco_qos:
        try:
            set_process_eco_qos(pid, True)
            messages.append("EcoQoS=enabled")
        except (OSError, AttributeError) as exc:
            errors.append(f"EcoQoS unavailable ({exc})")

    if very_low_io and os.name == "nt" and hasattr(psutil, "IOPRIO_VERYLOW"):
        try:
            process.ionice(psutil.IOPRIO_VERYLOW)
            messages.append("I/O priority=very low")
        except (psutil.AccessDenied, psutil.Error, OSError) as exc:
            errors.append(f"I/O priority unchanged ({exc})")

    if errors:
        messages.extend(f"warning: {error}" for error in errors)
        if strict:
            raise RuntimeError("; ".join(errors))

    return messages or ["Windows scheduling hints left at system defaults"]


def assess_isolation(
    *,
    backend: str,
    hard_gpu_only: bool,
    selected_cores: list[int],
    protected_processes: Iterable[ExternalXmrigProcess],
    guard_ready: bool,
) -> IsolationAssessment:
    if backend == "hybrid_cuda":
        return IsolationAssessment(
            level="warn",
            title="Hybrid mode uses the physical CPU",
            details=(
                "Hybrid CPU + CUDA intentionally enables real XMRig CPU threads as well as CUDA. "
                "It cannot preserve another full-load CPU miner's hashrate without partitioning CPU cores."
            ),
            overlapping_cores=[],
        )
    if backend not in {"cuda", "pseudo_cuda", "opencl"}:
        return IsolationAssessment(
            level="fail",
            title="Not GPU-only",
            details="Select Pseudo CPU on CUDA, CUDA, or OpenCL before using strict CPU protection.",
            overlapping_cores=[],
        )
    if not hard_gpu_only:
        return IsolationAssessment(
            level="fail",
            title="CPU backend can start",
            details="Enable hard GPU-only mode so the child receives --no-cpu.",
            overlapping_cores=[],
        )
    if not selected_cores:
        return IsolationAssessment(
            level="warn",
            title="No control-core affinity",
            details="The GPU miner host threads may run on the same CPUs as the existing CPU miner.",
            overlapping_cores=[],
        )

    protected_cores: set[int] = set()
    for process in protected_processes:
        protected_cores.update(process.mining_cores or process.affinity)
    overlap = sorted(set(selected_cores) & protected_cores)

    if overlap:
        detail = (
            f"Control cores {format_cpu_list(overlap)} overlap a detected XMRig process affinity. "
            "Idle priority reduces contention, but zero impact cannot be guaranteed."
        )
        if guard_ready:
            detail += " The hashrate guard is ready to suspend the GPU instance after a sustained drop."
        return IsolationAssessment("warn", "Shared CPU affinity", detail, overlap)

    detail = "GPU-only mode, low priority, and non-overlapping process affinity are configured."
    if guard_ready:
        detail += " The external CPU-miner hashrate guard is also ready."
    else:
        detail += " Configure the CPU miner API guard for measured protection."
    return IsolationAssessment("pass", "Strong process isolation", detail, [])
