from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.gpu_compute_runtime import GpuVirtualMachine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate gpu_host_runtime.dll through a real D3D12 GPU dispatch."
    )
    parser.add_argument("--adapter", type=int, default=0, help="DXGI adapter index")
    parser.add_argument("--lanes", type=int, default=4096, help="GPU virtual lane count")
    args = parser.parse_args()

    machine = GpuVirtualMachine()
    if not machine.available:
        print(
            "Native compute ABI unavailable. Build native/bin/gpu_host_runtime.dll first.",
            file=sys.stderr,
        )
        return 2

    try:
        report = machine.self_test(args.adapter, args.lanes)
        print(
            f"passed={report.passed} adapter={args.adapter} lanes={report.lane_count} "
            f"mismatches={report.mismatches} elapsed_ms={report.elapsed_ms:.3f}"
        )
        print(report.message)
        return 0 if report.passed else 1
    except Exception as exc:
        print(f"Self-test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
