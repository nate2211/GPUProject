from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path


def check_module(name: str) -> str:
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "installed")
        return str(version)
    except Exception as exc:
        return f"unavailable ({exc})"


def main() -> int:
    parser = argparse.ArgumentParser(description="GPU Virtual Workstation diagnostics")
    parser.add_argument("--xmrig", type=Path, help="Optional path to xmrig.exe")
    parser.add_argument("--cuda-loader", type=Path, help="Optional path to xmrig-cuda.dll")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    print("GPU Virtual Workstation 3.0 diagnostics")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"PyQt6: {check_module('PyQt6')}")
    print(f"psutil: {check_module('psutil')}")
    print(f"pynvml: {check_module('pynvml')}")
    print(f"nvidia-smi: {shutil.which('nvidia-smi') or 'not found'}")

    native_dll = project_root / "native/bin/gpu_host_runtime.dll"
    print(f"Native inventory DLL: {native_dll} exists={native_dll.exists()}")

    if args.xmrig:
        xmrig = args.xmrig.expanduser().resolve()
        print(f"XMRig: {xmrig} exists={xmrig.is_file()}")
        loader = args.cuda_loader.expanduser().resolve() if args.cuda_loader else xmrig.parent / "xmrig-cuda.dll"
        print(f"CUDA plugin: {loader} exists={loader.is_file()}")
        print(f"Suggested command check: \"{xmrig}\" --version")
    else:
        print("XMRig: not supplied; use --xmrig C:\\path\\to\\xmrig.exe")

    try:
        from app.isolation import (
            discover_xmrig_processes,
            format_cpu_list,
            query_windows_cpu_sets,
            recommend_control_cores,
        )

        cpu_sets = query_windows_cpu_sets()
        if cpu_sets:
            efficiency = sorted({info.efficiency_class for info in cpu_sets.values()})
            print(f"Windows CPU-set metadata: {len(cpu_sets)} logical CPUs; efficiency classes={efficiency}")
        else:
            print("Windows CPU-set metadata: unavailable or not running on Windows")

        processes = discover_xmrig_processes()
        print(f"External XMRig processes: {len(processes)}")
        for process in processes:
            print(
                f"  PID {process.pid}: process affinity={format_cpu_list(process.affinity) or 'unknown'}; "
                f"mining cores={format_cpu_list(process.mining_cores) or 'not explicit'}; "
                f"source={process.mining_core_source}; API={process.api_url or 'not detected'}"
            )

        cores, shared, reason = recommend_control_cores(processes, count=1)
        print(
            f"Recommended control CPU: {format_cpu_list(cores) or 'none'}; "
            f"overlap={'yes' if shared else 'no'}; {reason}"
        )
    except Exception as exc:
        print(f"Isolation diagnostics unavailable: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
