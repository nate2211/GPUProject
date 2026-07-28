from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.native_isolation_bridge import NativeProcessIsolationBridge  # noqa: E402


def main() -> int:
    if os.name != "nt":
        print("Native process isolation self-test requires Windows.")
        return 2

    bridge = NativeProcessIsolationBridge()
    if not bridge.available:
        print("process_isolation_runtime.dll did not load.")
        return 3

    eligible = psutil.Process().cpu_affinity()
    if not eligible:
        print("No eligible logical CPU was reported.")
        return 4
    core = eligible[-1]

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=creation_flags,
    )
    try:
        time.sleep(0.1)
        message = bridge.apply(
            child.pid,
            [core],
            "idle",
            eco_qos=False,
            suspend_during_apply=True,
        )
        process = psutil.Process(child.pid)
        actual = process.cpu_affinity()
        if actual != [core]:
            print(f"Affinity mismatch: requested [{core}], received {actual}")
            return 5
        print(f"Native isolation self-test passed for PID {child.pid} on CPU {core}: {message}")
        return 0
    finally:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
        bridge.release(child.pid)


if __name__ == "__main__":
    raise SystemExit(main())
