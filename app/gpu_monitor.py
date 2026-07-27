from __future__ import annotations

import subprocess
from typing import Any

from app.models import GpuDevice
from app.native_bridge import NativeGpuBridge


class GpuMonitor:
    def __init__(self) -> None:
        self._nvml: Any = None
        self._nvml_ready = False
        self._native = NativeGpuBridge()
        self._init_nvml()

    def _init_nvml(self) -> None:
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._nvml_ready = True
        except Exception:
            self._nvml = None
            self._nvml_ready = False

    def close(self) -> None:
        if self._nvml_ready and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        self._nvml_ready = False

    def sample(self) -> list[GpuDevice]:
        devices = self._sample_nvml()
        if devices:
            return devices

        devices = self._native.enumerate_adapters()
        if devices:
            return devices

        return self._sample_nvidia_smi()

    def _sample_nvml(self) -> list[GpuDevice]:
        if not self._nvml_ready or self._nvml is None:
            return []

        pynvml = self._nvml
        devices: list[GpuDevice] = []
        try:
            count = pynvml.nvmlDeviceGetCount()
            for index in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode(errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)

                temperature = None
                power = None
                try:
                    temperature = float(
                        pynvml.nvmlDeviceGetTemperature(
                            handle,
                            pynvml.NVML_TEMPERATURE_GPU,
                        )
                    )
                except Exception:
                    pass
                try:
                    power = float(pynvml.nvmlDeviceGetPowerUsage(handle)) / 1000.0
                except Exception:
                    pass

                devices.append(
                    GpuDevice(
                        index=index,
                        name=name,
                        vendor="NVIDIA",
                        dedicated_vram_bytes=int(memory.total),
                        used_vram_bytes=int(memory.used),
                        utilization_percent=float(utilization.gpu),
                        temperature_c=temperature,
                        power_w=power,
                        source="NVML",
                    )
                )
        except Exception:
            return []
        return devices

    @staticmethod
    def _sample_nvidia_smi() -> list[GpuDevice]:
        command = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return []

        devices: list[GpuDevice] = []
        for line in completed.stdout.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) < 7:
                continue
            try:
                devices.append(
                    GpuDevice(
                        index=int(fields[0]),
                        name=fields[1],
                        vendor="NVIDIA",
                        dedicated_vram_bytes=int(float(fields[2])) * 1024 * 1024,
                        used_vram_bytes=int(float(fields[3])) * 1024 * 1024,
                        utilization_percent=float(fields[4]),
                        temperature_c=float(fields[5]),
                        power_w=float(fields[6]),
                        source="nvidia-smi",
                    )
                )
            except ValueError:
                continue
        return devices
