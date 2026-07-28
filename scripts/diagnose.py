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
    parser.add_argument("--gvm-self-test", action="store_true", help="Run a real D3D12 GVM dispatch")
    parser.add_argument("--adapter", type=int, default=0, help="DXGI adapter for the GVM test")
    parser.add_argument("--lanes", type=int, default=4096, help="GVM self-test lane count")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    print("GPU Virtual Workstation 4.1 diagnostics")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.platform()}")
    print(f"PyQt6: {check_module('PyQt6')}")
    print(f"psutil: {check_module('psutil')}")
    print(f"pynvml: {check_module('pynvml')}")
    print(f"nvidia-smi: {shutil.which('nvidia-smi') or 'not found'}")
    try:
        from app.cuda_tuning import query_nvidia_gpus, tune_cuda_rx_profile

        for gpu in query_nvidia_gpus():
            profile = tune_cuda_rx_profile(
                device_index=gpu.index,
                gpu=gpu,
                preset="max",
                threads=32,
                blocks_override=0,
                memory_reserve_mib=1024,
                affinity=-1,
            )
            print(
                f"CUDA tune preview [{gpu.index}] {gpu.name}: free={gpu.free_memory_mib} MiB, "
                f"SMs={gpu.multiprocessors or 'unknown'}, profile={profile.as_xmrig_json() if profile else 'existing'}"
            )
    except Exception as exc:
        print(f"CUDA tuning preview unavailable: {exc}")

    native_dll = project_root / "native/bin/gpu_host_runtime.dll"
    isolation_dll = project_root / "native/bin/process_isolation_runtime.dll"
    print(f"Native GPU runtime DLL: {native_dll} exists={native_dll.exists()}")
    print(f"Native isolation DLL: {isolation_dll} exists={isolation_dll.exists()}")
    try:
        from app.native_isolation_bridge import NativeProcessIsolationBridge

        isolation = NativeProcessIsolationBridge()
        print(
            f"Native process isolation loaded={isolation.available}; "
            f"path={isolation.loaded_path or 'not loaded'}"
        )
    except Exception as exc:
        print(f"Native process isolation diagnostics unavailable: {exc}")
    try:
        from app.gpu_compute_runtime import GpuVirtualMachine

        machine = GpuVirtualMachine()
        print(
            f"Native inventory loaded={machine.bridge.available}; "
            f"compute ABI loaded={machine.available}; ABI={machine.abi_version_text}"
        )
        adapters = machine.bridge.enumerate_adapters()
        print(f"DXGI adapters: {len(adapters)}")
        for adapter in adapters:
            try:
                compute = machine.bridge.adapter_supports_compute(adapter.index)
            except Exception as exc:
                compute = f"error ({exc})"
            print(
                f"  [{adapter.index}] {adapter.name}; vendor={adapter.vendor}; "
                f"VRAM={adapter.total_vram_mib:,.0f} MiB; D3D12 compute={compute}"
            )
        if args.gvm_self_test:
            report = machine.self_test(args.adapter, args.lanes)
            print(
                f"GVM self-test: passed={report.passed}; lanes={report.lane_count}; "
                f"mismatches={report.mismatches}; elapsed={report.elapsed_ms:.3f} ms"
            )
            print(f"  {report.message}")
    except Exception as exc:
        print(f"Native GVM diagnostics unavailable: {exc}")

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
