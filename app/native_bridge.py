from __future__ import annotations

import ctypes
import os
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_double,
    c_int,
    c_uint32,
    c_uint64,
    c_void_p,
    c_wchar,
)
from pathlib import Path
from typing import Iterable

from app.models import GpuDevice


class AdapterInfo(Structure):
    _fields_ = [
        ("name", c_wchar * 128),
        ("dedicated_video_memory", c_uint64),
        ("vendor_id", c_uint32),
        ("device_id", c_uint32),
        ("subsystem_id", c_uint32),
        ("revision", c_uint32),
    ]


class NativeInstruction(Structure):
    _fields_ = [
        ("opcode", c_uint32),
        ("dst", c_uint32),
        ("src_a", c_uint32),
        ("src_b", c_uint32),
        ("immediate", c_uint32),
    ]


class NativeRuntimeConfig(Structure):
    _fields_ = [
        ("adapter_index", c_uint32),
        ("lane_count", c_uint32),
        ("max_steps_per_lane", c_uint32),
        ("flags", c_uint32),
    ]


class NativeRuntimeStatus(Structure):
    _fields_ = [
        ("active", c_uint32),
        ("adapter_index", c_uint32),
        ("lane_count", c_uint32),
        ("max_steps_per_lane", c_uint32),
        ("executions", c_uint64),
        ("last_instruction_count", c_uint32),
        ("last_data_words", c_uint32),
        ("last_elapsed_ms", c_double),
        ("adapter_name", c_wchar * 128),
    ]


class NativeSelfTestResult(Structure):
    _fields_ = [
        ("passed", c_uint32),
        ("lane_count", c_uint32),
        ("mismatches", c_uint32),
        ("elapsed_ms", c_double),
        ("message", c_wchar * 256),
    ]


VENDOR_NAMES = {
    0x10DE: "NVIDIA",
    0x1002: "AMD",
    0x8086: "Intel",
}


class NativeRuntimeError(RuntimeError):
    """Raised when the optional native GPU runtime reports an error."""


class NativeGpuBridge:
    """ctypes binding for gpu_host_runtime.dll.

    The adapter inventory functions remain usable with the original v3 DLL.
    The Direct3D 12 GPU virtual-machine functions are detected separately so a
    stale DLL cannot be mistaken for a compute-capable runtime.
    """

    REQUIRED_COMPUTE_ABI = 0x00040000

    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        candidates = [
            project_root / "native/bin/gpu_host_runtime.dll",
            project_root / "gpu_host_runtime.dll",
        ]
        self._dll: object | None = None
        self.loaded_path: Path | None = None
        self._compute_bound = False

        win_dll = getattr(ctypes, "WinDLL", None)
        if os.name != "nt" or win_dll is None:
            return

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                dll = win_dll(str(candidate))
                self._bind_inventory(dll)
                self._dll = dll
                self.loaded_path = candidate
                self._compute_bound = self._bind_compute(dll)
                break
            except (AttributeError, OSError, TypeError):
                continue

    @staticmethod
    def _bind_inventory(dll: object) -> None:
        dll.ghr_get_adapter_count.argtypes = []
        dll.ghr_get_adapter_count.restype = c_int
        dll.ghr_get_adapter_info.argtypes = [c_int, POINTER(AdapterInfo)]
        dll.ghr_get_adapter_info.restype = c_int

    @classmethod
    def _bind_compute(cls, dll: object) -> bool:
        required = [
            "ghr_get_runtime_abi_version",
            "ghr_get_last_error",
            "ghr_adapter_supports_compute",
            "ghr_runtime_create",
            "ghr_runtime_destroy",
            "ghr_runtime_get_status",
            "ghr_runtime_execute",
            "ghr_runtime_self_test",
        ]
        if not all(hasattr(dll, name) for name in required):
            return False

        dll.ghr_get_runtime_abi_version.argtypes = []
        dll.ghr_get_runtime_abi_version.restype = c_uint32
        if int(dll.ghr_get_runtime_abi_version()) < cls.REQUIRED_COMPUTE_ABI:
            return False

        dll.ghr_get_last_error.argtypes = [POINTER(c_wchar), c_int]
        dll.ghr_get_last_error.restype = c_int
        dll.ghr_adapter_supports_compute.argtypes = [c_int]
        dll.ghr_adapter_supports_compute.restype = c_int
        dll.ghr_runtime_create.argtypes = [POINTER(NativeRuntimeConfig), POINTER(c_void_p)]
        dll.ghr_runtime_create.restype = c_int
        dll.ghr_runtime_destroy.argtypes = [c_void_p]
        dll.ghr_runtime_destroy.restype = c_int
        dll.ghr_runtime_get_status.argtypes = [c_void_p, POINTER(NativeRuntimeStatus)]
        dll.ghr_runtime_get_status.restype = c_int
        dll.ghr_runtime_execute.argtypes = [
            c_void_p,
            POINTER(NativeInstruction),
            c_uint32,
            POINTER(c_uint32),
            c_uint32,
            POINTER(c_double),
        ]
        dll.ghr_runtime_execute.restype = c_int
        dll.ghr_runtime_self_test.argtypes = [c_int, c_uint32, POINTER(NativeSelfTestResult)]
        dll.ghr_runtime_self_test.restype = c_int
        return True

    @property
    def available(self) -> bool:
        return self._dll is not None

    @property
    def compute_available(self) -> bool:
        return self._dll is not None and self._compute_bound

    @property
    def abi_version(self) -> int:
        if not self.compute_available:
            return 0
        return int(self._dll.ghr_get_runtime_abi_version())

    def last_error(self) -> str:
        if not self.compute_available:
            return "Native D3D12 compute ABI is unavailable. Rebuild gpu_host_runtime.dll."
        buffer = (c_wchar * 1024)()
        self._dll.ghr_get_last_error(buffer, len(buffer))
        text = str(buffer.value).strip()
        return text or "The native GPU runtime returned an unspecified error."

    def _check(self, result: int, operation: str) -> None:
        if int(result) == 0:
            return
        raise NativeRuntimeError(f"{operation} failed ({int(result)}): {self.last_error()}")

    def enumerate_adapters(self) -> list[GpuDevice]:
        if self._dll is None:
            return []

        count = max(0, int(self._dll.ghr_get_adapter_count()))
        devices: list[GpuDevice] = []
        for index in range(count):
            info = AdapterInfo()
            result = int(self._dll.ghr_get_adapter_info(index, byref(info)))
            if result != 0:
                continue
            devices.append(
                GpuDevice(
                    index=index,
                    name=str(info.name),
                    vendor=VENDOR_NAMES.get(int(info.vendor_id), hex(int(info.vendor_id))),
                    dedicated_vram_bytes=int(info.dedicated_video_memory),
                    source="Native DXGI/D3D12 DLL",
                )
            )
        return devices

    def adapter_supports_compute(self, index: int) -> bool:
        if not self.compute_available:
            return False
        result = int(self._dll.ghr_adapter_supports_compute(int(index)))
        if result < 0:
            raise NativeRuntimeError(
                f"D3D12 capability check failed ({result}): {self.last_error()}"
            )
        return result == 1

    def create_runtime(
        self,
        adapter_index: int,
        lane_count: int,
        max_steps_per_lane: int,
    ) -> c_void_p:
        if not self.compute_available:
            raise NativeRuntimeError(self.last_error())
        config = NativeRuntimeConfig(
            adapter_index=max(0, int(adapter_index)),
            lane_count=int(lane_count),
            max_steps_per_lane=int(max_steps_per_lane),
            flags=0,
        )
        handle = c_void_p()
        self._check(
            int(self._dll.ghr_runtime_create(byref(config), byref(handle))),
            "Create GPU virtual-machine runtime",
        )
        if not handle.value:
            raise NativeRuntimeError("The native runtime returned a null handle.")
        return handle

    def destroy_runtime(self, handle: c_void_p | None) -> None:
        if not self.compute_available or handle is None or not handle.value:
            return
        self._check(
            int(self._dll.ghr_runtime_destroy(handle)),
            "Destroy GPU virtual-machine runtime",
        )
        handle.value = None

    def runtime_status(self, handle: c_void_p) -> NativeRuntimeStatus:
        if not self.compute_available or handle is None or not handle.value:
            raise NativeRuntimeError("The GPU virtual-machine runtime is not active.")
        status = NativeRuntimeStatus()
        self._check(
            int(self._dll.ghr_runtime_get_status(handle, byref(status))),
            "Read GPU virtual-machine status",
        )
        return status

    def execute(
        self,
        handle: c_void_p,
        instructions: Iterable[NativeInstruction],
        words: list[int],
    ) -> tuple[list[int], float]:
        instruction_list = list(instructions)
        if not instruction_list:
            raise ValueError("At least one GPU instruction is required.")
        if not words:
            raise ValueError("At least one 32-bit data word is required.")

        instruction_array_type = NativeInstruction * len(instruction_list)
        instruction_array = instruction_array_type(*instruction_list)
        word_array_type = c_uint32 * len(words)
        word_array = word_array_type(*(int(value) & 0xFFFFFFFF for value in words))
        elapsed_ms = c_double(0.0)
        self._check(
            int(
                self._dll.ghr_runtime_execute(
                    handle,
                    instruction_array,
                    len(instruction_list),
                    word_array,
                    len(words),
                    byref(elapsed_ms),
                )
            ),
            "Execute GPU virtual-ISA program",
        )
        return [int(value) for value in word_array], float(elapsed_ms.value)

    def self_test(self, adapter_index: int, lane_count: int) -> NativeSelfTestResult:
        if not self.compute_available:
            raise NativeRuntimeError(self.last_error())
        output = NativeSelfTestResult()
        self._check(
            int(
                self._dll.ghr_runtime_self_test(
                    int(adapter_index),
                    int(lane_count),
                    byref(output),
                )
            ),
            "Run GPU virtual-machine self-test",
        )
        return output
