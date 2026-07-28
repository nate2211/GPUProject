from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class WorkstationSettings:
    data_directory: str = "data/instances"
    gpu_refresh_ms: int = 5000
    process_refresh_ms: int = 4000
    api_poll_seconds: float = 5.0
    default_backend: str = "pseudo_cuda"
    default_hard_gpu_only: bool = True
    default_priority: str = "idle"
    default_eco_qos: bool = False
    default_affinity: str = ""
    default_pin_workstation: bool = False
    default_guard_drop_percent: float = 4.0
    default_cuda_tune_profile: str = "max"
    default_randomx_init_threads: int = 1
    default_native_isolation: bool = True
    config_path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "WorkstationSettings":
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            settings = cls(config_path=path)
            settings.save()
            return settings

        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = {
            "data_directory",
            "gpu_refresh_ms",
            "process_refresh_ms",
            "api_poll_seconds",
            "default_backend",
            "default_hard_gpu_only",
            "default_priority",
            "default_eco_qos",
            "default_affinity",
            "default_pin_workstation",
            "default_guard_drop_percent",
            "default_cuda_tune_profile",
            "default_randomx_init_threads",
            "default_native_isolation",
        }
        filtered = {key: value for key, value in data.items() if key in allowed}
        settings = cls(**filtered)
        settings.config_path = path
        return settings

    def save(self) -> None:
        if self.config_path is None:
            raise ValueError("No settings path has been assigned.")

        payload = asdict(self)
        payload.pop("config_path", None)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def data_path(self) -> Path:
        path = Path(self.data_directory).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
