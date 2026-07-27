from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_int, c_uint32, c_uint64, c_wchar
from pathlib import Path

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


VENDOR_NAMES = {
    0x10DE: "NVIDIA",
    0x1002: "AMD",
    0x8086: "Intel",
}


class NativeGpuBridge:
    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        candidates = [
            project_root / "native/bin/gpu_host_runtime.dll",
            project_root / "gpu_host_runtime.dll",
        ]
        self._dll = None
        self.loaded_path: Path | None = None

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                dll = ctypes.WinDLL(str(candidate))
                dll.ghr_get_adapter_count.argtypes = []
                dll.ghr_get_adapter_count.restype = c_int
                dll.ghr_get_adapter_info.argtypes = [c_int, POINTER(AdapterInfo)]
                dll.ghr_get_adapter_info.restype = c_int
                self._dll = dll
                self.loaded_path = candidate
                break
            except OSError:
                continue

    @property
    def available(self) -> bool:
        return self._dll is not None

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
                    source="Native DXGI DLL",
                )
            )
        return devices
