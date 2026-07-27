from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import psutil
from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from app.backend_health import classify_xmrig_line
from app.hashrate_guard import HashrateGuard
from app.isolation import (
    apply_process_isolation,
    format_cpu_list,
    priority_name,
)
from app.models import CUDA_BACKENDS, InstanceMetrics, InstanceSnapshot, InstanceSpec, InstanceState
from app.settings import WorkstationSettings
from app.xmrig_api import ApiPoller
from app.xmrig_config import (
    choose_free_local_port,
    load_json,
    make_instance_directory,
    patch_xmrig_config,
    write_json,
)
from app.xmrig_launch import build_xmrig_arguments
from app.xmrig_preflight import resolve_cuda_loader, run_xmrig_preflight

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass(slots=True)
class ManagedInstance:
    virtual_pid: int
    name: str
    backend: str
    instance_dir: Path
    config_path: Path
    runtime_exe: Path
    api_port: int
    process: QProcess
    spec: InstanceSpec
    state: InstanceState = InstanceState.CREATED
    host_pid: int = 0
    metrics: InstanceMetrics = field(default_factory=InstanceMetrics)
    api_poller: ApiPoller | None = None
    guard: HashrateGuard | None = None
    guard_status: str = "Not configured"
    isolation_status: str = "Pending"
    exit_code: int | None = None
    last_error: str = ""
    recent_output: list[str] = field(default_factory=list)
    backend_health: str = "Starting"
    saw_cuda_ready: bool = False
    saw_cpu_mining: bool = False
    backend_abort_requested: bool = False


class XmrigManager(QObject):
    instance_changed = pyqtSignal()
    log_line = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, settings: WorkstationSettings) -> None:
        super().__init__()
        self.settings = settings
        self._instances: dict[int, ManagedInstance] = {}
        self._next_virtual_pid = 1000

    @property
    def instances(self) -> Iterable[ManagedInstance]:
        return tuple(self._instances.values())

    def launch(self, spec: InstanceSpec) -> ManagedInstance:
        api_port = choose_free_local_port()
        instance_id = uuid.uuid4().hex[:16]
        instance_dir = make_instance_directory(self.settings.data_path(), spec.name)
        config_path = (instance_dir / "config.json").resolve()

        source = load_json(spec.source_config)
        if spec.backend in CUDA_BACKENDS:
            configured_loader = spec.cuda_loader
            if configured_loader is None:
                cuda_section = source.get("cuda")
                if isinstance(cuda_section, dict):
                    raw_loader = cuda_section.get("loader")
                    if isinstance(raw_loader, str) and raw_loader.strip():
                        configured_loader = Path(raw_loader.strip())
            spec.cuda_loader = resolve_cuda_loader(spec.xmrig_path.resolve(), configured_loader)

        self._validate_spec(spec)
        preflight_warnings = self._backend_preflight_warnings(spec, source)

        patched = patch_xmrig_config(
            source,
            instance_name=spec.name,
            instance_id=instance_id,
            api_port=api_port,
            backend=spec.backend,
            keep_cpu=spec.keep_cpu,
            hard_gpu_only=spec.hard_gpu_only,
            force_dataset_vram=spec.force_dataset_vram,
            cuda_loader=spec.cuda_loader,
            cuda_devices=spec.cuda_devices,
            opencl_devices=spec.opencl_devices,
        )
        if patched.get("log-file"):
            patched["log-file"] = str((instance_dir / "xmrig.log").resolve())
        write_json(config_path, patched)

        runtime_exe = spec.xmrig_path.resolve()
        arguments = self._build_arguments(spec, config_path)
        preflight_output = ""
        if spec.preflight_dry_run:
            preflight = run_xmrig_preflight(
                runtime_exe,
                arguments,
                working_directory=runtime_exe.parent,
                requires_cuda=spec.backend in CUDA_BACKENDS,
            )
            preflight_output = preflight.output
            (instance_dir / "preflight.log").write_text(preflight.output + "\n", encoding="utf-8")
            if not preflight.ok and spec.require_cuda_ready:
                tail = "\n".join(preflight.output.splitlines()[-18:]) or "No dry-run output was captured."
                raise RuntimeError(
                    f"XMRig preflight failed: {preflight.reason}\n\n{tail}\n\n"
                    f"See {instance_dir / 'preflight.log'}"
                )
            if not preflight.ok:
                preflight_warnings.append(preflight.reason)

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(str(runtime_exe.parent))

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("XMRIG_DATA_DIR", str(instance_dir))
        environment.insert("GPU_VIRTUAL_WORKSTATION_INSTANCE", spec.name)
        environment.insert("GPU_VIRTUAL_WORKSTATION_GPU_ONLY", "1" if spec.hard_gpu_only else "0")
        environment.insert("GPU_VIRTUAL_WORKSTATION_PSEUDO_LANES", str(spec.pseudo_lane_count))
        process.setProcessEnvironment(environment)

        virtual_pid = self._next_virtual_pid
        self._next_virtual_pid += 1
        instance = ManagedInstance(
            virtual_pid=virtual_pid,
            name=spec.name,
            backend=spec.backend,
            instance_dir=instance_dir,
            config_path=config_path,
            runtime_exe=runtime_exe,
            api_port=api_port,
            process=process,
            spec=spec,
            state=InstanceState.STARTING,
            backend_health="Preflight passed" if spec.preflight_dry_run else "Starting",
        )
        self._instances[virtual_pid] = instance

        process.readyReadStandardOutput.connect(lambda vpid=virtual_pid: self._read_output(vpid))
        process.started.connect(lambda vpid=virtual_pid: self._on_started(vpid))
        process.finished.connect(
            lambda code, status, vpid=virtual_pid: self._on_finished(vpid, int(code), status)
        )
        process.errorOccurred.connect(
            lambda process_error, vpid=virtual_pid: self._on_process_error(vpid, process_error)
        )

        launch_manifest = {
            "instance": spec.name,
            "source_executable": str(spec.xmrig_path),
            "runtime_executable": str(runtime_exe),
            "config": str(config_path),
            "arguments": arguments,
            "backend": spec.backend,
            "cuda_loader": str(spec.cuda_loader) if spec.cuda_loader else None,
            "pseudo_lane_count": spec.pseudo_lane_count,
            "preflight_dry_run": spec.preflight_dry_run,
            "require_cuda_ready": spec.require_cuda_ready,
            "abort_on_cpu_fallback": spec.abort_on_cpu_fallback,
            "hard_gpu_only": spec.hard_gpu_only,
            "requested_cpu_affinity": spec.cpu_affinity,
            "requested_priority": spec.priority,
            "eco_qos": spec.eco_qos,
            "pin_workstation": spec.pin_workstation,
            "preflight_warnings": preflight_warnings,
            "preflight_output_file": str(instance_dir / "preflight.log") if preflight_output else None,
            "console_log": str(instance_dir / "console.log"),
            "runtime_strategy": "original executable directory with absolute isolated config/data/API paths",
            "pseudo_cpu_note": "Pseudo lanes are CUDA scheduling/display lanes, not x86 XMRig CPU threads.",
        }
        (instance_dir / "launch.json").write_text(
            json.dumps(launch_manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        process.setProgram(str(runtime_exe))
        process.setArguments(arguments)
        process.start()
        for warning in preflight_warnings:
            self.log_line.emit(spec.name, f"[preflight] Warning: {warning}")
        self.log_line.emit(
            spec.name,
            f"[workstation] Starting managed instance: {runtime_exe} {' '.join(arguments)}",
        )
        if spec.backend == "pseudo_cuda":
            self.log_line.emit(
                spec.name,
                f"[pseudo-cpu] Presenting {spec.pseudo_lane_count} logical GPU worker lanes; hashing remains in XMRig CUDA.",
            )
        elif spec.backend == "hybrid_cuda":
            self.log_line.emit(
                spec.name,
                "[hybrid] Real XMRig CPU threads and CUDA are both enabled; CPU hashes remain on the physical CPU.",
            )
        self.instance_changed.emit()
        return instance


    @staticmethod
    def _backend_preflight_warnings(
        spec: InstanceSpec,
        source_config: dict[str, object],
    ) -> list[str]:
        del source_config
        warnings: list[str] = []
        if spec.backend not in CUDA_BACKENDS:
            return warnings

        if spec.cuda_loader is None:
            warnings.append(
                "xmrig-cuda.dll was not found. CUDA and pseudo-CPU modes require the matching XMRig CUDA plugin."
            )
        elif not spec.cuda_loader.exists():
            warnings.append(f"configured CUDA loader was not found: {spec.cuda_loader}")
        return warnings

    @staticmethod
    def _build_arguments(spec: InstanceSpec, config_path: Path) -> list[str]:
        return build_xmrig_arguments(spec, config_path)

    @staticmethod
    def _validate_spec(spec: InstanceSpec) -> None:
        if not spec.name.strip():
            raise ValueError("Instance name is required.")
        if not spec.xmrig_path.is_file():
            raise FileNotFoundError(f"XMRig executable not found: {spec.xmrig_path}")
        if spec.xmrig_path.suffix.lower() != ".exe":
            raise ValueError("Select a Windows xmrig.exe executable.")
        if not spec.source_config.is_file():
            raise FileNotFoundError(f"XMRig config not found: {spec.source_config}")
        if spec.backend in CUDA_BACKENDS and spec.require_cuda_ready:
            if spec.cuda_loader is None or not spec.cuda_loader.is_file():
                raise FileNotFoundError(
                    "CUDA mode requires xmrig-cuda.dll. Select the matching plugin beside xmrig.exe or browse to it."
                )
        if spec.backend == "pseudo_cuda" and not spec.hard_gpu_only:
            raise ValueError("Pseudo CPU on CUDA mode requires hard GPU-only mode.")
        if spec.backend == "hybrid_cuda" and spec.hard_gpu_only:
            raise ValueError("Hybrid CPU + CUDA mode cannot use hard GPU-only mode.")
        if not 1 <= spec.pseudo_lane_count <= 128:
            raise ValueError("Pseudo lane count must be between 1 and 128.")
        if spec.hard_gpu_only and spec.backend not in {"cuda", "pseudo_cuda", "opencl"}:
            raise ValueError("Hard GPU-only mode requires the CUDA or OpenCL backend.")
        if spec.protected_api_url and spec.protected_baseline_hs <= 0:
            raise ValueError("Capture a positive CPU-miner baseline before enabling the guard.")

        if spec.hard_gpu_only:
            forbidden_prefixes = (
                "--threads",
                "-t",
                "--cpu-affinity",
                "--cpu-priority",
                "--cpu-max-threads-hint",
                "--randomx-init",
            )
            for argument in spec.extra_args:
                lowered = argument.lower()
                if any(lowered == prefix or lowered.startswith(prefix + "=") for prefix in forbidden_prefixes):
                    raise ValueError(
                        f"Extra argument {argument!r} conflicts with hard GPU-only isolation."
                    )

    def _read_output(self, virtual_pid: int) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        raw = bytes(instance.process.readAllStandardOutput()).decode(errors="replace")
        clean_lines: list[str] = []
        for line in raw.replace("\r\n", "\n").replace("\r", "\n").splitlines():
            clean = ANSI_ESCAPE.sub("", line).rstrip()
            if clean:
                clean_lines.append(clean)
                instance.recent_output.append(clean)
                del instance.recent_output[:-80]
                self.log_line.emit(instance.name, clean)
                self._inspect_backend_line(instance, clean)
        if clean_lines:
            try:
                with (instance.instance_dir / "console.log").open("a", encoding="utf-8") as handle:
                    handle.write("\n".join(clean_lines) + "\n")
            except OSError as exc:
                self.log_line.emit(instance.name, f"[workstation] Could not write console.log: {exc}")

    def _inspect_backend_line(self, instance: ManagedInstance, line: str) -> None:
        event = classify_xmrig_line(line)
        if event is None:
            return

        if event.kind in {"cuda_enabled", "cuda_ready"}:
            instance.saw_cuda_ready = True
            instance.backend_health = "CUDA ready" if event.kind == "cuda_ready" else "CUDA enabled"
            self.instance_changed.emit()
            return

        if event.kind == "cuda_failed" and instance.spec.backend in CUDA_BACKENDS:
            instance.backend_health = "CUDA failed"
            self.log_line.emit(instance.name, f"[backend] CUDA failure detected: {event.detail}")
            if instance.spec.require_cuda_ready:
                self._abort_backend(instance, "CUDA was required but did not initialize.")
            self.instance_changed.emit()
            return

        if event.kind == "cpu_mining":
            instance.saw_cpu_mining = True
            if instance.spec.backend == "hybrid_cuda":
                instance.backend_health = (
                    "Hybrid CPU + CUDA" if instance.saw_cuda_ready else "CPU active; waiting for CUDA"
                )
            elif instance.spec.hard_gpu_only:
                instance.backend_health = "Unexpected CPU fallback"
                self.log_line.emit(
                    instance.name,
                    "[backend] CPU mining started in a GPU-only instance. This is not a pseudo-GPU CPU thread.",
                )
                if instance.spec.abort_on_cpu_fallback:
                    self._abort_backend(instance, "Unexpected CPU mining fallback was detected.")
            self.instance_changed.emit()

    def _abort_backend(self, instance: ManagedInstance, reason: str) -> None:
        if instance.backend_abort_requested:
            return
        instance.backend_abort_requested = True
        instance.last_error = reason
        self.log_line.emit(instance.name, f"[backend] {reason} Stopping this instance to protect the host CPU miner.")
        QTimer.singleShot(0, lambda vpid=instance.virtual_pid: self.kill(vpid))

    def _on_started(self, virtual_pid: int) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        instance.host_pid = int(instance.process.processId())

        # Scheduling controls are intentionally best-effort. The CPU backend is
        # already disabled in both config.json and the --no-cpu launch flag, so an
        # affinity/EcoQoS failure must not kill an otherwise valid GPU miner.
        process_affinity = [] if instance.spec.backend == "hybrid_cuda" else instance.spec.cpu_affinity
        applied = apply_process_isolation(
            instance.host_pid,
            process_affinity,
            instance.spec.priority,
            eco_qos=instance.spec.eco_qos,
            strict=False,
        )
        instance.isolation_status = ", ".join(applied)

        if instance.spec.pin_workstation:
            workstation_applied = apply_process_isolation(
                os.getpid(),
                instance.spec.cpu_affinity,
                instance.spec.priority,
                very_low_io=False,
                eco_qos=instance.spec.eco_qos,
                strict=False,
            )
            self.log_line.emit(
                instance.name,
                f"[isolation] Workstation controls: {', '.join(workstation_applied)}",
            )

        instance.state = InstanceState.RUNNING
        instance.backend_health = "Waiting for CUDA" if instance.spec.backend in CUDA_BACKENDS else "Running"
        self.log_line.emit(
            instance.name,
            f"[workstation] Running host PID {instance.host_pid}; vPID {virtual_pid}.",
        )
        self.log_line.emit(instance.name, f"[isolation] {instance.isolation_status}")
        if instance.spec.hard_gpu_only:
            self.log_line.emit(
                instance.name,
                "[isolation] CPU backend hard-disabled in config and with --no-cpu; optional Windows scheduling controls may degrade without stopping the miner.",
            )

        poller = ApiPoller(instance.api_port, self.settings.api_poll_seconds)
        poller.summary_received.connect(
            lambda metrics, vpid=virtual_pid: self._on_metrics(vpid, metrics)
        )
        poller.api_status.connect(
            lambda online, message, vpid=virtual_pid: self._on_api_status(vpid, online, message)
        )
        instance.api_poller = poller
        poller.start()

        if instance.spec.protected_api_url:
            self._start_guard(instance)
        else:
            instance.guard_status = "Affinity/priority only"

        self.instance_changed.emit()

    def _start_guard(self, instance: ManagedInstance) -> None:
        guard = HashrateGuard(
            api_url=instance.spec.protected_api_url,
            baseline_hs=instance.spec.protected_baseline_hs,
            max_drop_percent=instance.spec.max_drop_percent,
            consecutive_samples=instance.spec.guard_consecutive_samples,
            interval_seconds=max(3.0, self.settings.api_poll_seconds),
        )
        guard.sample_received.connect(
            lambda current, threshold, count, vpid=instance.virtual_pid: self._on_guard_sample(
                vpid, current, threshold, count
            )
        )
        guard.drop_detected.connect(
            lambda message, vpid=instance.virtual_pid: self._on_guard_drop(vpid, message)
        )
        guard.guard_error.connect(
            lambda message, vpid=instance.virtual_pid: self._on_guard_error(vpid, message)
        )
        instance.guard = guard
        instance.guard_status = "Guard starting"
        guard.start()
        self.log_line.emit(
            instance.name,
            f"[guard] Protecting baseline {instance.spec.protected_baseline_hs:,.1f} H/s; "
            f"allowed drop {instance.spec.max_drop_percent:.1f}%.",
        )

    def _on_guard_sample(self, virtual_pid: int, current: float, threshold: float, count: int) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        instance.guard_status = f"CPU miner {current:,.1f} H/s (floor {threshold:,.1f}); low={count}"
        self.instance_changed.emit()

    def _on_guard_error(self, virtual_pid: int, message: str) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        instance.guard_status = "Guard API unavailable"
        self.log_line.emit(instance.name, f"[guard] {message}")
        self.instance_changed.emit()

    def _on_guard_drop(self, virtual_pid: int, message: str) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        instance.guard_status = "Protection triggered"
        self.log_line.emit(instance.name, f"[guard] {message}")
        try:
            if instance.spec.guard_action == "stop":
                self.stop(virtual_pid)
                self.log_line.emit(instance.name, "[guard] GPU instance stopped to protect CPU hashrate.")
            else:
                self.suspend(virtual_pid)
                self.log_line.emit(instance.name, "[guard] GPU instance suspended to protect CPU hashrate.")
        except Exception as exc:
            self.error.emit(f"{instance.name}: protection action failed: {exc}")
        self.instance_changed.emit()

    def _on_metrics(self, virtual_pid: int, metrics: InstanceMetrics) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        instance.metrics = metrics
        self.instance_changed.emit()

    def _on_api_status(self, virtual_pid: int, online: bool, message: str) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        status = "online" if online else "not ready"
        self.log_line.emit(
            instance.name,
            f"[workstation] XMRig API {status} on 127.0.0.1:{instance.api_port}"
            + (f" ({message})" if message and not online else ""),
        )

    def _stop_instance_threads(self, instance: ManagedInstance) -> None:
        if instance.api_poller is not None:
            instance.api_poller.stop()
            instance.api_poller.wait(1500)
            instance.api_poller = None
        if instance.guard is not None:
            instance.guard.stop()
            instance.guard.wait(1500)
            instance.guard = None

    def _on_finished(
        self,
        virtual_pid: int,
        exit_code: int,
        _exit_status: QProcess.ExitStatus,
    ) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        was_stopping = instance.state == InstanceState.STOPPING
        self._stop_instance_threads(instance)
        instance.exit_code = exit_code
        instance.state = InstanceState.EXITED if exit_code == 0 else InstanceState.FAILED
        self.log_line.emit(instance.name, f"[workstation] Process exited with code {exit_code}.")
        if exit_code != 0 and not was_stopping:
            tail = "\n".join(instance.recent_output[-12:]) or "No console output was captured."
            self.error.emit(
                f"{instance.name}: XMRig exited with code {exit_code}.\n\n"
                f"Last output:\n{tail}\n\n"
                f"Diagnostics: {instance.instance_dir / 'launch.json'}\n"
                f"Full console: {instance.instance_dir / 'console.log'}"
            )
        self.instance_changed.emit()

    def _on_process_error(self, virtual_pid: int, process_error: QProcess.ProcessError) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is None:
            return
        message = instance.process.errorString()
        instance.last_error = message
        if instance.state == InstanceState.STARTING:
            instance.state = InstanceState.FAILED
        self.log_line.emit(
            instance.name,
            f"[workstation] QProcess error {getattr(process_error, 'value', process_error)}: {message}",
        )
        self.error.emit(f"{instance.name}: {message}")
        self.instance_changed.emit()

    def find(self, pid: int) -> ManagedInstance | None:
        if pid in self._instances:
            return self._instances[pid]
        for instance in self._instances.values():
            if instance.host_pid == pid:
                return instance
        return None

    def suspend(self, pid: int) -> None:
        instance = self._require_running(pid)
        psutil.Process(instance.host_pid).suspend()
        instance.state = InstanceState.SUSPENDED
        self.log_line.emit(instance.name, "[workstation] Process suspended.")
        self.instance_changed.emit()

    def resume(self, pid: int) -> None:
        instance = self._require_instance(pid)
        if instance.host_pid <= 0:
            raise RuntimeError("Process has no host PID.")
        psutil.Process(instance.host_pid).resume()
        instance.state = InstanceState.RUNNING
        if (
            instance.spec.protected_api_url
            and (instance.guard is None or not instance.guard.isRunning())
        ):
            instance.guard = None
            self._start_guard(instance)
        self.log_line.emit(instance.name, "[workstation] Process resumed.")
        self.instance_changed.emit()

    def set_priority(self, pid: int, level: str) -> None:
        instance = self._require_running(pid)
        apply_process_isolation(instance.host_pid, [], level, eco_qos=instance.spec.eco_qos)
        instance.spec.priority = level
        self.log_line.emit(instance.name, f"[workstation] Priority set to {level}.")
        self.instance_changed.emit()

    def set_affinity(self, pid: int, cores: list[int]) -> None:
        instance = self._require_running(pid)
        apply_process_isolation(
            instance.host_pid, cores, instance.spec.priority, eco_qos=instance.spec.eco_qos
        )
        instance.spec.cpu_affinity = list(cores)
        self.log_line.emit(
            instance.name,
            f"[workstation] CPU affinity set to {format_cpu_list(cores)}.",
        )
        self.instance_changed.emit()

    def stop(self, pid: int) -> None:
        instance = self._require_instance(pid)
        if instance.process.state() == QProcess.ProcessState.NotRunning:
            return
        instance.state = InstanceState.STOPPING
        self.log_line.emit(instance.name, "[workstation] Requesting graceful stop.")
        instance.process.write(b"q\n")
        instance.process.closeWriteChannel()
        QTimer.singleShot(1500, lambda vpid=instance.virtual_pid: self._terminate_if_running(vpid))
        QTimer.singleShot(4500, lambda vpid=instance.virtual_pid: self._kill_if_running(vpid))
        self.instance_changed.emit()

    def _terminate_if_running(self, virtual_pid: int) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is not None and instance.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit(instance.name, "[workstation] Graceful stop pending; sending terminate.")
            instance.process.terminate()

    def _kill_if_running(self, virtual_pid: int) -> None:
        instance = self._instances.get(virtual_pid)
        if instance is not None and instance.process.state() != QProcess.ProcessState.NotRunning:
            self.log_line.emit(instance.name, "[workstation] Grace period expired; killing process.")
            instance.process.kill()

    def kill(self, pid: int) -> None:
        instance = self._require_instance(pid)
        if instance.process.state() != QProcess.ProcessState.NotRunning:
            instance.state = InstanceState.STOPPING
            instance.process.kill()
            self.log_line.emit(instance.name, "[workstation] Process killed.")
            self.instance_changed.emit()

    def snapshots(self) -> list[InstanceSnapshot]:
        output: list[InstanceSnapshot] = []
        for instance in sorted(self._instances.values(), key=lambda item: item.virtual_pid):
            cpu = 0.0
            memory_mib = 0.0
            affinity = format_cpu_list(instance.spec.cpu_affinity) or "all"
            priority = instance.spec.priority
            if instance.host_pid > 0:
                try:
                    process = psutil.Process(instance.host_pid)
                    cpu = float(process.cpu_percent(interval=None))
                    memory_mib = float(process.memory_info().rss) / (1024 * 1024)
                    affinity = format_cpu_list(process.cpu_affinity()) or "all"
                    priority = priority_name(process)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            output.append(
                InstanceSnapshot(
                    virtual_pid=instance.virtual_pid,
                    host_pid=instance.host_pid,
                    name=instance.name,
                    state=instance.state.value,
                    backend=instance.backend,
                    backend_health=instance.backend_health,
                    pseudo_lanes=instance.spec.pseudo_lane_count if instance.spec.backend == "pseudo_cuda" else 0,
                    gpu_devices=instance.spec.cuda_devices if instance.spec.backend in CUDA_BACKENDS else instance.spec.opencl_devices,
                    cpu_percent=cpu,
                    memory_mib=memory_mib,
                    hashrate_10s=instance.metrics.hashrate_10s,
                    accepted=instance.metrics.shares_good,
                    rejected=instance.metrics.rejected,
                    affinity=affinity,
                    priority=priority,
                    guard_status=instance.guard_status,
                    instance_dir=str(instance.instance_dir),
                )
            )
        return output

    def shutdown_all(self) -> None:
        for instance in tuple(self._instances.values()):
            if instance.guard is not None:
                instance.guard.stop()
            if instance.api_poller is not None:
                instance.api_poller.stop()
            if instance.process.state() != QProcess.ProcessState.NotRunning:
                instance.process.terminate()
        for instance in tuple(self._instances.values()):
            if instance.guard is not None:
                instance.guard.wait(1000)
            if instance.api_poller is not None:
                instance.api_poller.wait(1000)
            if instance.process.state() != QProcess.ProcessState.NotRunning:
                instance.process.waitForFinished(1500)
                if instance.process.state() != QProcess.ProcessState.NotRunning:
                    instance.process.kill()

    def _require_instance(self, pid: int) -> ManagedInstance:
        instance = self.find(pid)
        if instance is None:
            raise KeyError(f"No managed instance has PID {pid}.")
        return instance

    def _require_running(self, pid: int) -> ManagedInstance:
        instance = self._require_instance(pid)
        if instance.host_pid <= 0 or instance.state not in {InstanceState.RUNNING, InstanceState.SUSPENDED}:
            raise RuntimeError(f"Instance {instance.name} is not running.")
        return instance
