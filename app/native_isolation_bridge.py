from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, c_int, c_uint32, c_uint64, c_wchar
from pathlib import Path
from typing import Iterable


class NativeIsolationError(RuntimeError):
    pass


_PRIORITY_LEVELS = {
    "idle": 0,
    "below": 1,
    "normal": 2,
    "above": 3,
    "high": 4,
}


def affinity_mask(cores: Iterable[int]) -> int:
    mask = 0
    for core in sorted(set(int(value) for value in cores)):
        if not 0 <= core < 64:
            raise ValueError("The native isolation DLL currently supports CPU indexes 0-63.")
        mask |= 1 << core
    return mask


class NativeProcessIsolationBridge:
    REQUIRED_ABI = 0x00040100

    def __init__(self) -> None:
        self._dll: object | None = None
        self.loaded_path: Path | None = None
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            return
        root = Path(__file__).resolve().parents[1]
        candidates = [
            root / "native/bin/process_isolation_runtime.dll",
            root / "process_isolation_runtime.dll",
        ]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                dll = ctypes.WinDLL(str(candidate))
                dll.pir_get_abi_version.argtypes = []
                dll.pir_get_abi_version.restype = c_uint32
                if int(dll.pir_get_abi_version()) < self.REQUIRED_ABI:
                    continue
                dll.pir_apply_isolation.argtypes = [
                    c_uint32,
                    c_uint64,
                    c_int,
                    c_int,
                    c_int,
                    POINTER(c_wchar),
                    c_uint32,
                ]
                dll.pir_apply_isolation.restype = c_int
                dll.pir_release_process.argtypes = [c_uint32]
                dll.pir_release_process.restype = c_int
                dll.pir_get_last_error.argtypes = [POINTER(c_wchar), c_uint32]
                dll.pir_get_last_error.restype = c_int
                self._dll = dll
                self.loaded_path = candidate
                break
            except (AttributeError, OSError, TypeError):
                continue

    @property
    def available(self) -> bool:
        return self._dll is not None

    def last_error(self) -> str:
        if self._dll is None:
            return "process_isolation_runtime.dll is not available"
        buffer = (c_wchar * 1024)()
        self._dll.pir_get_last_error(buffer, len(buffer))
        return str(buffer.value).strip() or "native isolation failed"

    def apply(
        self,
        pid: int,
        cores: Iterable[int],
        priority: str,
        *,
        eco_qos: bool,
        suspend_during_apply: bool = True,
    ) -> str:
        if self._dll is None:
            raise NativeIsolationError("process_isolation_runtime.dll is not available")
        level = _PRIORITY_LEVELS.get(priority)
        if level is None:
            raise ValueError(f"Unsupported priority: {priority}")
        message = (c_wchar * 2048)()
        result = int(
            self._dll.pir_apply_isolation(
                int(pid),
                affinity_mask(cores),
                level,
                1 if eco_qos else 0,
                1 if suspend_during_apply else 0,
                message,
                len(message),
            )
        )
        if result != 0:
            raise NativeIsolationError(f"native isolation failed ({result}): {self.last_error()}")
        return str(message.value).strip()

    def release(self, pid: int) -> None:
        if self._dll is not None and pid > 0:
            self._dll.pir_release_process(int(pid))
