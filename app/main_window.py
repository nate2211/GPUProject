from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gpu_compute_runtime import GpuVirtualMachine
from app.gpu_monitor import GpuMonitor
from app.gvm_program import GvmProgram
from app.isolation import (
    ExternalXmrigProcess,
    assess_isolation,
    discover_xmrig_processes,
    format_cpu_list,
    parse_cpu_list,
    read_xmrig_hashrate,
    recommend_control_cores,
)
from app.models import GpuDevice, InstanceSpec
from app.process_table import ProcessTableModel
from app.pseudo_cpu import build_pseudo_cpu_lanes
from app.settings import WorkstationSettings
from app.xmrig_manager import XmrigManager


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll


class MetricCard(QGroupBox):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        layout = QVBoxLayout(self)
        self.value = QLabel("—")
        self.value.setObjectName("MetricValue")
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        layout.addWidget(self.value)
        layout.addWidget(self.detail)


class MainWindow(QMainWindow):
    def __init__(self, settings: WorkstationSettings) -> None:
        super().__init__()
        self.settings = settings
        self.setWindowTitle("GPU Virtual Workstation — Isolated XMRig Host")

        self.gpu_monitor = GpuMonitor()
        self.gpu_vm = GpuVirtualMachine()
        self.manager = XmrigManager(settings)
        self.process_model = ProcessTableModel()
        self._last_gpus: list[GpuDevice] = []
        self._external_xmrig: list[ExternalXmrigProcess] = []
        self._baseline_hashrate = 0.0

        self._build_ui()
        self._connect_signals()
        self._start_timers()
        self.refresh_gpu()
        self.refresh_processes()
        self._scan_external_xmrig()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(245)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 18, 14, 18)

        title = QLabel("GPU Host")
        title.setObjectName("Title")
        subtitle = muted("Low-impact XMRig control plane")
        self.navigation = QListWidget()
        self.navigation.addItems(
            [
                "Overview",
                "Launch GPU XMRig",
                "CPU Protection",
                "Pseudo CPU Lanes",
                "Task Manager",
                "Virtual Terminal",
                "Settings",
            ]
        )
        self.navigation.setCurrentRow(0)

        side_layout.addWidget(title)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(12)
        side_layout.addWidget(self.navigation, 1)
        side_layout.addWidget(
            muted(
                "The GPU instance remains a Windows process. "
                "Protection uses GPU-only configuration first; priority, affinity, EcoQoS, and the hashrate guard are optional layers."
            )
        )

        self.pages = QStackedWidget()
        self.pages.addWidget(self._create_overview_page())
        self.pages.addWidget(self._create_launcher_page())
        self.pages.addWidget(self._create_protection_page())
        self.pages.addWidget(self._create_pseudo_cpu_page())
        self.pages.addWidget(self._create_task_page())
        self.pages.addWidget(self._create_terminal_page())
        self.pages.addWidget(self._create_settings_page())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 20)
        content_layout.addWidget(self.pages)

        layout.addWidget(sidebar)
        layout.addWidget(content, 1)
        self.setCentralWidget(root)

    def _create_overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(section_title("Isolated GPU workstation overview"))
        layout.addWidget(
            muted(
                "Strict mode disables the child XMRig CPU backend and places its host-control threads "
                "at low priority on selected logical CPUs. A separate API guard can suspend it if your "
                "existing CPU miner loses sustained hashrate."
            )
        )

        cards = QGridLayout()
        self.gpu_count_card = MetricCard("GPU adapters")
        self.running_card = MetricCard("Managed instances")
        self.hashrate_card = MetricCard("GPU-instance hashrate")
        self.shares_card = MetricCard("Accepted / rejected")
        cards.addWidget(self.gpu_count_card, 0, 0)
        cards.addWidget(self.running_card, 0, 1)
        cards.addWidget(self.hashrate_card, 0, 2)
        cards.addWidget(self.shares_card, 0, 3)
        layout.addLayout(cards)

        layout.addWidget(section_title("GPU devices"))
        self.gpu_table = QTableWidget(0, 8)
        self.gpu_table.setHorizontalHeaderLabels(
            ["Index", "Name", "Vendor", "VRAM used", "GPU", "Temp", "Power", "Source"]
        )
        self.gpu_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.gpu_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.gpu_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.gpu_table, 1)
        return page

    def _create_launcher_page(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(section_title("Launch XMRig with CUDA pseudo-CPU lanes"))
        layout.addWidget(
            muted(
                "Pseudo CPU mode presents CUDA workers as logical workstation lanes while XMRig hashes through its real CUDA plugin. "
                "Hybrid mode enables both the physical CPU backend and CUDA. Absolute config/plugin paths and a dry-run preflight prevent silent CPU fallback."
            )
        )

        group = QGroupBox("Instance configuration")
        form = QFormLayout(group)
        self.instance_name = QLineEdit("xmrig-isolated-gpu-1")

        self.xmrig_path = QLineEdit()
        xmrig_row = QHBoxLayout()
        xmrig_row.addWidget(self.xmrig_path, 1)
        browse_xmrig = QPushButton("Browse…")
        browse_xmrig.clicked.connect(self._browse_xmrig)
        xmrig_row.addWidget(browse_xmrig)

        self.config_path = QLineEdit()
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_path, 1)
        browse_config = QPushButton("Browse…")
        browse_config.clicked.connect(self._browse_config)
        config_row.addWidget(browse_config)

        self.backend = QComboBox()
        self.backend.addItem("Pseudo CPU on CUDA — GPU lanes, CPU backend off", "pseudo_cuda")
        self.backend.addItem("Hybrid CPU + CUDA — real CPU threads plus GPU", "hybrid_cuda")
        self.backend.addItem("CUDA GPU only", "cuda")
        self.backend.addItem("OpenCL GPU only", "opencl")
        self.backend.addItem("Use config as-is (advanced)", "existing")
        self.backend.addItem("CPU only", "cpu")
        default_index = self.backend.findData(self.settings.default_backend)
        self.backend.setCurrentIndex(max(0, default_index))

        self.hard_gpu_only = QCheckBox("Hard GPU-only mode: disable CPU mining in config and add --no-cpu")
        self.hard_gpu_only.setChecked(self.settings.default_hard_gpu_only)
        self.keep_cpu = QCheckBox("Allow this second instance to mine on the physical CPU")
        self.keep_cpu.setChecked(False)

        self.cuda_loader = QLineEdit()
        self.cuda_loader.setPlaceholderText("Optional; defaults to xmrig-cuda.dll beside xmrig.exe")
        cuda_loader_row = QHBoxLayout()
        cuda_loader_row.addWidget(self.cuda_loader, 1)
        browse_cuda_loader = QPushButton("Browse…")
        browse_cuda_loader.clicked.connect(self._browse_cuda_loader)
        cuda_loader_row.addWidget(browse_cuda_loader)

        self.cuda_devices = QLineEdit("0")
        self.cuda_devices.setPlaceholderText("Comma-separated CUDA devices, usually 0")
        self.opencl_devices = QLineEdit("0")
        self.opencl_devices.setPlaceholderText("Comma-separated OpenCL devices, usually 0")

        self.cuda_tune_profile = QComboBox()
        self.cuda_tune_profile.addItem("Maximum throughput — BF 0 / BS 0", "max")
        self.cuda_tune_profile.addItem("Fast — BF 2 / BS 0", "fast")
        self.cuda_tune_profile.addItem("Balanced — BF 4 / BS 10", "balanced")
        self.cuda_tune_profile.addItem("Compatibility — BF 6 / BS 25", "compat")
        self.cuda_tune_profile.addItem("Keep existing XMRig profile/autoconfig", "existing")
        tune_index = self.cuda_tune_profile.findData(self.settings.default_cuda_tune_profile)
        self.cuda_tune_profile.setCurrentIndex(max(0, tune_index))

        self.cuda_threads = QSpinBox()
        self.cuda_threads.setRange(1, 1024)
        self.cuda_threads.setValue(32)
        self.cuda_blocks = QSpinBox()
        self.cuda_blocks.setRange(0, 65535)
        self.cuda_blocks.setValue(0)
        self.cuda_blocks.setSpecialValueText("Auto from SM/VRAM")
        self.cuda_memory_reserve = QSpinBox()
        self.cuda_memory_reserve.setRange(256, 16384)
        self.cuda_memory_reserve.setSingleStep(256)
        self.cuda_memory_reserve.setValue(1024)
        self.cuda_memory_reserve.setSuffix(" MiB")

        self.cuda_bfactor = QSpinBox()
        self.cuda_bfactor.setRange(-1, 12)
        self.cuda_bfactor.setValue(-1)
        self.cuda_bfactor.setSpecialValueText("Preset")
        self.force_dataset_vram = QCheckBox("Keep RandomX CUDA dataset in GPU VRAM")
        self.force_dataset_vram.setChecked(True)

        self.cuda_bsleep = QSpinBox()
        self.cuda_bsleep.setRange(-1, 1000)
        self.cuda_bsleep.setValue(-1)
        self.cuda_bsleep.setSpecialValueText("Preset")

        self.randomx_init_threads = QSpinBox()
        self.randomx_init_threads.setRange(1, 64)
        self.randomx_init_threads.setValue(self.settings.default_randomx_init_threads)
        self.native_isolation = QCheckBox("Use native C++ isolation DLL before RandomX dataset initialization")
        self.native_isolation.setChecked(self.settings.default_native_isolation)

        self.pseudo_lanes = QSpinBox()
        self.pseudo_lanes.setRange(1, 128)
        self.pseudo_lanes.setValue(8)
        self.preflight_dry_run = QCheckBox("Run XMRig --dry-run before launch")
        self.preflight_dry_run.setChecked(True)
        self.require_cuda_ready = QCheckBox("Require CUDA plugin/backend to initialize")
        self.require_cuda_ready.setChecked(True)
        self.abort_cpu_fallback = QCheckBox("Stop GPU-only instance if real CPU mining starts")
        self.abort_cpu_fallback.setChecked(True)

        self.extra_args = QLineEdit()
        self.extra_args.setPlaceholderText("Optional non-CPU XMRig arguments")

        form.addRow("Instance name", self.instance_name)
        form.addRow("xmrig.exe", xmrig_row)
        form.addRow("Source config.json", config_row)
        form.addRow("Mining backend", self.backend)
        form.addRow("", self.hard_gpu_only)
        form.addRow("", self.keep_cpu)
        form.addRow("CUDA plugin", cuda_loader_row)
        form.addRow("CUDA devices", self.cuda_devices)
        form.addRow("OpenCL devices", self.opencl_devices)
        form.addRow("CUDA tuning preset", self.cuda_tune_profile)
        form.addRow("CUDA threads", self.cuda_threads)
        form.addRow("CUDA blocks", self.cuda_blocks)
        form.addRow("GPU memory reserve", self.cuda_memory_reserve)
        form.addRow("CUDA bfactor override", self.cuda_bfactor)
        form.addRow("CUDA bsleep override", self.cuda_bsleep)
        form.addRow("RandomX init CPU threads", self.randomx_init_threads)
        form.addRow("", self.native_isolation)
        form.addRow("Pseudo CPU lanes", self.pseudo_lanes)
        form.addRow("", self.preflight_dry_run)
        form.addRow("", self.require_cuda_ready)
        form.addRow("", self.abort_cpu_fallback)
        form.addRow("", self.force_dataset_vram)
        form.addRow("Extra arguments", self.extra_args)

        isolation_group = QGroupBox("Protection profile used at launch")
        isolation_layout = QVBoxLayout(isolation_group)
        self.launch_isolation_summary = QLabel()
        self.launch_isolation_summary.setWordWrap(True)
        edit_protection = QPushButton("Review CPU protection settings")
        edit_protection.clicked.connect(lambda: self.navigation.setCurrentRow(2))
        isolation_layout.addWidget(self.launch_isolation_summary)
        isolation_layout.addWidget(edit_protection, alignment=Qt.AlignmentFlag.AlignLeft)

        self.launch_button = QPushButton("Launch CUDA workstation instance")
        self.launch_button.setObjectName("Primary")
        self.launch_button.clicked.connect(self._launch_xmrig)

        layout.addWidget(group)
        layout.addWidget(isolation_group)
        layout.addWidget(
            muted(
                "On laptops and other shared-power systems, GPU load can still reduce CPU boost clocks through "
                "power or thermal coupling. Process isolation cannot guarantee mathematically zero loss; the "
                "API guard is the measured fallback."
            )
        )
        layout.addWidget(self.launch_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return scroll_page(content)

    def _create_protection_page(self) -> QScrollArea:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(section_title("Protect the existing Windows CPU miner"))
        layout.addWidget(
            muted(
                "Scan for an already-running XMRig process, reserve low-impact CPUs for the workstation, "
                "and optionally capture the CPU miner's HTTP API baseline. No settings on the existing miner "
                "are changed by this application."
            )
        )

        process_group = QGroupBox("Detected external XMRig processes")
        process_layout = QVBoxLayout(process_group)
        scan_row = QHBoxLayout()
        scan = QPushButton("Scan running XMRig")
        scan.clicked.connect(self._scan_external_xmrig)
        scan_row.addWidget(scan)
        scan_row.addStretch(1)
        self.external_table = QTableWidget(0, 8)
        self.external_table.setHorizontalHeaderLabels(
            [
                "PID",
                "Name",
                "CPU",
                "Process affinity",
                "Detected mining cores",
                "Core source",
                "Detected API",
                "Config",
            ]
        )
        self.external_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.external_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.external_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.external_table.horizontalHeader().setStretchLastSection(True)
        self.external_table.itemSelectionChanged.connect(self._external_selection_changed)
        process_layout.addLayout(scan_row)
        process_layout.addWidget(self.external_table)

        core_group = QGroupBox("Control-plane resource isolation")
        core_form = QFormLayout(core_group)
        self.control_cores = QLineEdit(self.settings.default_affinity)
        self.control_cores.setPlaceholderText("Example: 23 or 22-23")
        core_row = QHBoxLayout()
        core_row.addWidget(self.control_cores, 1)
        auto_core = QPushButton("Auto-pick")
        auto_core.clicked.connect(self._auto_pick_core)
        core_row.addWidget(auto_core)

        self.priority = QComboBox()
        self.priority.addItem("Idle — strongest CPU protection", "idle")
        self.priority.addItem("Below normal — better GPU feed", "below")
        self.priority.addItem("Normal", "normal")
        priority_index = self.priority.findData(self.settings.default_priority)
        self.priority.setCurrentIndex(max(0, priority_index))

        self.eco_qos = QCheckBox("Enable Windows EcoQoS for CPU-side control work")
        self.eco_qos.setChecked(self.settings.default_eco_qos)
        self.pin_workstation = QCheckBox("Pin the PyQt workstation to the same control CPUs")
        self.pin_workstation.setChecked(self.settings.default_pin_workstation)
        self.require_isolation = QCheckBox(
            "Block launch only when GPU-only preflight fails (affinity and EcoQoS remain best-effort)"
        )
        self.require_isolation.setChecked(False)

        core_form.addRow("Control CPU indexes", core_row)
        core_form.addRow("Windows priority", self.priority)
        core_form.addRow("", self.eco_qos)
        core_form.addRow("", self.pin_workstation)
        core_form.addRow("", self.require_isolation)

        guard_group = QGroupBox("Measured CPU-hashrate guard")
        guard_form = QFormLayout(guard_group)
        self.protect_hashrate = QCheckBox("Suspend or stop the GPU instance after a sustained CPU-hashrate drop")
        self.protect_hashrate.setChecked(False)
        self.protected_api_url = QLineEdit()
        self.protected_api_url.setPlaceholderText("http://127.0.0.1:18080/2/summary")
        baseline_row = QHBoxLayout()
        capture = QPushButton("Capture current baseline")
        capture.clicked.connect(self._capture_baseline)
        self.baseline_label = QLabel("Not captured")
        baseline_row.addWidget(capture)
        baseline_row.addWidget(self.baseline_label)
        baseline_row.addStretch(1)

        self.max_drop = QDoubleSpinBox()
        self.max_drop.setRange(0.5, 30.0)
        self.max_drop.setDecimals(1)
        self.max_drop.setValue(self.settings.default_guard_drop_percent)
        self.max_drop.setSuffix(" %")
        self.guard_samples = QSpinBox()
        self.guard_samples.setRange(1, 12)
        self.guard_samples.setValue(3)
        self.guard_action = QComboBox()
        self.guard_action.addItem("Suspend GPU instance", "suspend")
        self.guard_action.addItem("Stop GPU instance", "stop")

        guard_form.addRow("", self.protect_hashrate)
        guard_form.addRow("Protected XMRig API", self.protected_api_url)
        guard_form.addRow("Baseline", baseline_row)
        guard_form.addRow("Maximum allowed drop", self.max_drop)
        guard_form.addRow("Consecutive low samples", self.guard_samples)
        guard_form.addRow("Protection action", self.guard_action)

        status_group = QGroupBox("Preflight assessment")
        status_layout = QVBoxLayout(status_group)
        self.isolation_status = QLabel()
        self.isolation_status.setWordWrap(True)
        refresh_assessment = QPushButton("Recalculate assessment")
        refresh_assessment.clicked.connect(self._update_isolation_status)
        status_layout.addWidget(self.isolation_status)
        status_layout.addWidget(refresh_assessment, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(process_group)
        layout.addWidget(core_group)
        layout.addWidget(guard_group)
        layout.addWidget(status_group)
        layout.addWidget(
            muted(
                "The scanner now prefers explicit CPU-mining affinities from XMRig's command line, local config, "
                "or /2/config endpoint. This can identify an unused logical CPU even when Windows reports that the "
                "existing process itself is allowed to run on every CPU. Automatic (-1) mining affinities cannot be "
                "resolved to exact cores, so the guard remains important in that case."
            )
        )
        layout.addStretch(1)
        return scroll_page(content)

    def _create_pseudo_cpu_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(section_title("Native GPU virtual-machine lanes"))
        layout.addWidget(
            muted(
                "The native C++ DLL now creates a persistent Direct3D 12 compute runtime and executes a compact virtual instruction set inside GPU shaders. "
                "This provides real GPU-backed arithmetic lanes for translated numeric workloads. It does not claim that arbitrary x86 Windows executables can run unchanged on a GPU."
            )
        )

        native_group = QGroupBox("C++ DLL / Direct3D 12 compute engine")
        native_layout = QVBoxLayout(native_group)
        native_form = QFormLayout()
        self.gvm_adapter = QSpinBox()
        self.gvm_adapter.setRange(0, 63)
        self.gvm_adapter.setValue(0)
        self.gvm_lanes = QSpinBox()
        self.gvm_lanes.setRange(1, 1_048_576)
        self.gvm_lanes.setValue(4096)
        self.gvm_lanes.setSingleStep(1024)
        self.gvm_steps = QSpinBox()
        self.gvm_steps.setRange(1, 1_048_576)
        self.gvm_steps.setValue(65_536)
        self.gvm_status = QLabel()
        self.gvm_status.setWordWrap(True)
        native_form.addRow("DXGI adapter index", self.gvm_adapter)
        native_form.addRow("GPU virtual lanes", self.gvm_lanes)
        native_form.addRow("Maximum instructions per lane", self.gvm_steps)
        native_layout.addLayout(native_form)
        native_layout.addWidget(self.gvm_status)

        native_buttons = QHBoxLayout()
        initialize = QPushButton("Initialize native GPU engine")
        initialize.setObjectName("Primary")
        initialize.clicked.connect(self._initialize_native_runtime)
        self_test = QPushButton("Run GPU self-test")
        self_test.clicked.connect(self._run_native_self_test)
        demo = QPushButton("Run lane demo")
        demo.clicked.connect(self._run_native_lane_demo)
        run_program = QPushButton("Run .gvm.json program…")
        run_program.clicked.connect(self._browse_and_run_gvm_program)
        shutdown = QPushButton("Shutdown engine")
        shutdown.clicked.connect(self._shutdown_native_runtime)
        native_buttons.addWidget(initialize)
        native_buttons.addWidget(self_test)
        native_buttons.addWidget(demo)
        native_buttons.addWidget(run_program)
        native_buttons.addWidget(shutdown)
        native_buttons.addStretch(1)
        native_layout.addLayout(native_buttons)
        layout.addWidget(native_group)

        layout.addWidget(section_title("XMRig CUDA lane projections"))
        layout.addWidget(
            muted(
                "The table below remains an XMRig telemetry projection. Unlike the native engine above, these rows divide XMRig's total CUDA hashrate for display and are not individually measured execution contexts."
            )
        )
        self.pseudo_table = QTableWidget(0, 7)
        self.pseudo_table.setHorizontalHeaderLabels(
            ["Instance", "Lane", "CUDA device", "State", "Estimated H/s", "VRAM slice", "Meaning"]
        )
        self.pseudo_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pseudo_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pseudo_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.pseudo_table, 1)
        self._refresh_native_runtime_status()
        return page

    def _initialize_native_runtime(self) -> None:
        try:
            self.gpu_vm.start(
                adapter_index=self.gvm_adapter.value(),
                lane_count=self.gvm_lanes.value(),
                max_steps_per_lane=self.gvm_steps.value(),
            )
            snapshot = self.gpu_vm.status()
            self.console.appendPlainText(
                f"[gvm] Native D3D12 runtime started on {snapshot.adapter_name} with "
                f"{snapshot.lane_count:,} GPU lanes."
            )
            self._refresh_native_runtime_status()
        except Exception as exc:
            self._refresh_native_runtime_status(str(exc))
            QMessageBox.critical(self, "Native GPU engine failed", str(exc))

    def _run_native_self_test(self) -> None:
        try:
            report = self.gpu_vm.self_test(
                adapter_index=self.gvm_adapter.value(),
                lane_count=self.gvm_lanes.value(),
            )
            summary = (
                f"passed={report.passed} lanes={report.lane_count:,} "
                f"mismatches={report.mismatches} dispatch={report.elapsed_ms:.3f} ms; "
                f"{report.message}"
            )
            self.console.appendPlainText(f"[gvm] Self-test {summary}")
            QMessageBox.information(self, "GPU self-test", summary)
        except Exception as exc:
            self.console.appendPlainText(f"[gvm] Self-test failed: {exc}")
            QMessageBox.critical(self, "GPU self-test failed", str(exc))
        finally:
            self._refresh_native_runtime_status()

    def _run_native_lane_demo(self) -> None:
        try:
            if not self.gpu_vm.active:
                self._initialize_native_runtime()
            if not self.gpu_vm.active:
                return
            words, elapsed_ms = self.gpu_vm.run_lane_transform(multiplier=3, addend=1)
            sample = ", ".join(str(value) for value in words[:8])
            self.console.appendPlainText(
                f"[gvm] Executed output[lane] = lane * 3 + 1 across {len(words):,} GPU lanes "
                f"in {elapsed_ms:.3f} ms. First outputs: {sample}"
            )
            self._refresh_native_runtime_status()
        except Exception as exc:
            self.console.appendPlainText(f"[gvm] Lane demo failed: {exc}")
            QMessageBox.critical(self, "GPU lane demo failed", str(exc))

    def _browse_and_run_gvm_program(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select GVM program",
            str(Path("examples").resolve()),
            "GPU virtual-machine programs (*.gvm.json *.json);;JSON files (*.json)",
        )
        if path:
            self._run_gvm_program(Path(path))

    def _run_gvm_program(self, path: Path) -> None:
        try:
            program = GvmProgram.load(path)
            self.gvm_lanes.setValue(program.lane_count)
            self.gvm_steps.setValue(program.max_steps_per_lane)
            current = self.gpu_vm.status()
            if (
                not current.active
                or current.adapter_index != self.gvm_adapter.value()
                or current.lane_count != program.lane_count
                or current.max_steps_per_lane != program.max_steps_per_lane
            ):
                self.gpu_vm.start(
                    adapter_index=self.gvm_adapter.value(),
                    lane_count=program.lane_count,
                    max_steps_per_lane=program.max_steps_per_lane,
                )
            output, elapsed_ms = self.gpu_vm.execute(program.instructions, program.data_words)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            result_directory = self.settings.data_path().parent / "gvm-results"
            result_path = result_directory / f"{path.stem}-{stamp}.result.json"
            program.write_result(result_path, output, elapsed_ms)
            preview = ", ".join(str(value) for value in output[: program.preview_words])
            self.console.appendPlainText(
                f"[gvm] Program '{program.name}' completed: lanes={program.lane_count:,}, "
                f"instructions={len(program.instructions)}, words={len(output):,}, "
                f"dispatch={elapsed_ms:.3f} ms. Preview: {preview or '(disabled)'}"
            )
            self.console.appendPlainText(f"[gvm] Result saved to {result_path}")
            self._refresh_native_runtime_status()
        except Exception as exc:
            self.console.appendPlainText(f"[gvm] Program failed: {exc}")
            QMessageBox.critical(self, "GVM program failed", str(exc))

    def _shutdown_native_runtime(self) -> None:
        try:
            self.gpu_vm.stop()
            self.console.appendPlainText("[gvm] Native GPU runtime stopped.")
        except Exception as exc:
            QMessageBox.warning(self, "Native GPU shutdown", str(exc))
        finally:
            self._refresh_native_runtime_status()

    def _refresh_native_runtime_status(self, error: str = "") -> None:
        if not hasattr(self, "gvm_status"):
            return
        if error:
            self.gvm_status.setText(f"Native engine error: {error}")
            return
        if not self.gpu_vm.available:
            self.gvm_status.setText(
                "Native compute ABI unavailable. Build native/bin/gpu_host_runtime.dll with build_native.ps1. "
                "The Python workstation and XMRig controls remain usable without it."
            )
            return
        snapshot = self.gpu_vm.status()
        if not snapshot.active:
            self.gvm_status.setText(
                f"DLL loaded; compute ABI {self.gpu_vm.abi_version_text}. Engine is stopped. "
                "Initialize it to reserve a D3D12 compute queue and GPU virtual lanes."
            )
            return
        self.gvm_status.setText(
            f"Running on {snapshot.adapter_name} (adapter {snapshot.adapter_index}) with "
            f"{snapshot.lane_count:,} lanes; executions={snapshot.executions:,}; "
            f"last program={snapshot.last_instruction_count} instructions / "
            f"{snapshot.last_data_words:,} words / {snapshot.last_elapsed_ms:.3f} ms."
        )

    def _create_task_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(section_title("Virtual Task Manager"))
        layout.addWidget(muted("vPID is the workstation ID; Host PID is the real Windows process."))

        self.process_table = QTableView()
        self.process_table.setModel(self.process_model)
        self.process_table.setAlternatingRowColors(True)
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.process_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.process_table.horizontalHeader().setStretchLastSection(True)
        self.process_table.verticalHeader().setVisible(False)

        buttons = QHBoxLayout()
        for label, callback, object_name in [
            ("Refresh", self.refresh_processes, ""),
            ("Suspend", self._suspend_selected, ""),
            ("Resume", self._resume_selected, ""),
            ("Stop", self._stop_selected, ""),
            ("Kill", self._kill_selected, "Danger"),
        ]:
            button = QPushButton(label)
            if object_name:
                button.setObjectName(object_name)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout.addWidget(self.process_table, 1)
        layout.addLayout(buttons)
        return page

    def _create_terminal_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(section_title("Virtual terminal"))
        layout.addWidget(muted("This console controls managed instances; it is not an unrestricted host shell."))

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(10000)
        self.console.appendPlainText("GPU Virtual Workstation terminal\nType 'help' for commands.\n")

        command_row = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("Enter a workstation command")
        self.command.returnPressed.connect(self._run_terminal_command)
        send = QPushButton("Run")
        send.clicked.connect(self._run_terminal_command)
        command_row.addWidget(self.command, 1)
        command_row.addWidget(send)

        layout.addWidget(self.console, 1)
        layout.addLayout(command_row)
        return page

    def _create_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(section_title("Workstation settings"))

        group = QGroupBox("Low-overhead local settings")
        form = QFormLayout(group)
        self.data_directory = QLineEdit(self.settings.data_directory)
        self.gpu_refresh = QSpinBox()
        self.gpu_refresh.setRange(1000, 60000)
        self.gpu_refresh.setValue(self.settings.gpu_refresh_ms)
        self.gpu_refresh.setSuffix(" ms")
        self.process_refresh = QSpinBox()
        self.process_refresh.setRange(1000, 60000)
        self.process_refresh.setValue(self.settings.process_refresh_ms)
        self.process_refresh.setSuffix(" ms")
        form.addRow("Instance data directory", self.data_directory)
        form.addRow("GPU telemetry refresh", self.gpu_refresh)
        form.addRow("Process table refresh", self.process_refresh)

        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_settings)
        layout.addWidget(group)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _connect_signals(self) -> None:
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.manager.instance_changed.connect(self.refresh_processes)
        self.manager.log_line.connect(self._append_log)
        self.manager.error.connect(lambda message: QMessageBox.warning(self, "XMRig process error", message))
        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.hard_gpu_only.toggled.connect(self._backend_changed)
        self.control_cores.textChanged.connect(self._update_isolation_status)
        self.priority.currentIndexChanged.connect(self._update_isolation_status)
        self.eco_qos.toggled.connect(self._update_isolation_status)
        self.protect_hashrate.toggled.connect(self._update_isolation_status)
        self.protected_api_url.textChanged.connect(self._baseline_invalidated)
        self.max_drop.valueChanged.connect(self._update_isolation_status)
        self._backend_changed()

    def _start_timers(self) -> None:
        self.gpu_timer = QTimer(self)
        self.gpu_timer.timeout.connect(self.refresh_gpu)
        self.gpu_timer.start(self.settings.gpu_refresh_ms)
        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self.refresh_processes)
        self.process_timer.start(self.settings.process_refresh_ms)

    def _browse_xmrig(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select xmrig.exe", "", "XMRig executable (xmrig.exe);;Windows executables (*.exe)"
        )
        if path:
            self.xmrig_path.setText(path)

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select XMRig config", "", "JSON configuration (*.json);;All files (*)"
        )
        if path:
            self.config_path.setText(path)

    def _browse_cuda_loader(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select xmrig-cuda.dll", "", "XMRig CUDA plugin (xmrig-cuda.dll);;DLL files (*.dll)"
        )
        if path:
            self.cuda_loader.setText(path)

    def _backend_changed(self) -> None:
        backend = str(self.backend.currentData())
        uses_cuda = backend in {"pseudo_cuda", "hybrid_cuda", "cuda"}

        if backend == "pseudo_cuda":
            self.hard_gpu_only.setChecked(True)
            self.hard_gpu_only.setEnabled(False)
            self.keep_cpu.setChecked(False)
            self.keep_cpu.setEnabled(False)
        elif backend == "hybrid_cuda":
            self.hard_gpu_only.setChecked(False)
            self.hard_gpu_only.setEnabled(False)
            self.keep_cpu.setChecked(True)
            self.keep_cpu.setEnabled(False)
            normal_index = self.priority.findData("normal")
            if normal_index >= 0 and self.priority.currentData() == "idle":
                self.priority.setCurrentIndex(normal_index)
        else:
            self.hard_gpu_only.setEnabled(backend in {"cuda", "opencl", "existing"})
            strict = self.hard_gpu_only.isChecked()
            self.keep_cpu.setEnabled(not strict and backend in {"cuda", "opencl", "existing"})
            if strict:
                self.keep_cpu.setChecked(False)

        self.cuda_loader.setEnabled(uses_cuda)
        self.cuda_devices.setEnabled(uses_cuda)
        self.cuda_tune_profile.setEnabled(uses_cuda)
        self.cuda_threads.setEnabled(uses_cuda)
        self.cuda_blocks.setEnabled(uses_cuda)
        self.cuda_memory_reserve.setEnabled(uses_cuda)
        self.cuda_bfactor.setEnabled(uses_cuda)
        self.cuda_bsleep.setEnabled(uses_cuda)
        self.randomx_init_threads.setEnabled(backend in {"pseudo_cuda", "cuda", "opencl"})
        self.native_isolation.setEnabled(backend in {"pseudo_cuda", "cuda", "opencl"})
        self.pseudo_lanes.setEnabled(backend == "pseudo_cuda")
        self.preflight_dry_run.setEnabled(uses_cuda)
        self.require_cuda_ready.setEnabled(uses_cuda)
        self.abort_cpu_fallback.setEnabled(backend in {"pseudo_cuda", "cuda", "opencl"})
        self.opencl_devices.setEnabled(backend == "opencl")
        self._update_isolation_status()

    def _scan_external_xmrig(self) -> None:
        excluded = {item.host_pid for item in self.manager.instances if item.host_pid > 0}
        self._external_xmrig = discover_xmrig_processes(excluded)
        self.external_table.setRowCount(len(self._external_xmrig))
        for row, process in enumerate(self._external_xmrig):
            values = [
                str(process.pid),
                process.name,
                f"{process.cpu_percent:.1f}%",
                format_cpu_list(process.affinity) or "unknown/all",
                format_cpu_list(process.mining_cores) or "not explicit",
                process.mining_core_source,
                process.api_url or "Not detected",
                process.config_path or "Not detected",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {5, 6, 7}:
                    item.setToolTip(value)
                self.external_table.setItem(row, column, item)
        self.external_table.resizeColumnsToContents()
        if self._external_xmrig:
            self.external_table.selectRow(0)
        if not self.control_cores.text().strip():
            self._auto_pick_core()
        else:
            self._update_isolation_status()

    def _selected_external_processes(self) -> list[ExternalXmrigProcess]:
        row = self.external_table.currentRow()
        if 0 <= row < len(self._external_xmrig):
            return [self._external_xmrig[row]]
        return list(self._external_xmrig)

    def _external_selection_changed(self) -> None:
        selected = self._selected_external_processes()
        if len(selected) == 1 and selected[0].api_url:
            self.protected_api_url.setText(selected[0].api_url)
        self._baseline_hashrate = 0.0
        self.baseline_label.setText("Not captured")
        self._update_isolation_status()

    def _auto_pick_core(self) -> None:
        cores, shared, message = recommend_control_cores(self._selected_external_processes(), count=1)
        self.control_cores.setText(format_cpu_list(cores))
        prefix = "Warning" if shared else "Recommendation"
        self.console.appendPlainText(f"[isolation] {prefix}: {message}")
        self._update_isolation_status()

    def _baseline_invalidated(self) -> None:
        self._baseline_hashrate = 0.0
        if hasattr(self, "baseline_label"):
            self.baseline_label.setText("Not captured")
        self._update_isolation_status()

    def _capture_baseline(self) -> None:
        try:
            sample = read_xmrig_hashrate(self.protected_api_url.text())
            baseline = sample.preferred_hashrate
            if baseline <= 0:
                raise RuntimeError("The API is online but has not reported a positive 10s/60s hashrate yet.")
            self._baseline_hashrate = baseline
            self.baseline_label.setText(
                f"{baseline:,.1f} H/s (10s={sample.hashrate_10s:,.1f}, 60s={sample.hashrate_60s:,.1f})"
            )
            self.protect_hashrate.setChecked(True)
            self._update_isolation_status()
        except Exception as exc:
            QMessageBox.warning(self, "Baseline capture failed", str(exc))

    def _update_isolation_status(self) -> None:
        if not hasattr(self, "isolation_status"):
            return
        try:
            cores = parse_cpu_list(self.control_cores.text())
            core_error = ""
        except Exception as exc:
            cores = []
            core_error = str(exc)

        guard_ready = (
            self.protect_hashrate.isChecked()
            and bool(self.protected_api_url.text().strip())
            and self._baseline_hashrate > 0
        )
        assessment = assess_isolation(
            backend=str(self.backend.currentData()),
            hard_gpu_only=self.hard_gpu_only.isChecked(),
            selected_cores=cores,
            protected_processes=self._selected_external_processes(),
            guard_ready=guard_ready,
        )
        text = f"[{assessment.level.upper()}] {assessment.title}: {assessment.details}"
        if core_error:
            text = f"[FAIL] Invalid CPU list: {core_error}"
        self.isolation_status.setText(text)
        self.launch_isolation_summary.setText(
            text
            + f"\nPriority: {self.priority.currentData()}; EcoQoS: {'on' if self.eco_qos.isChecked() else 'off'}; control CPUs: {format_cpu_list(cores) or 'not set'}; "
            + (f"guard baseline: {self._baseline_hashrate:,.1f} H/s" if guard_ready else "hashrate guard: not ready")
        )

    def _launch_xmrig(self) -> None:
        try:
            cores = parse_cpu_list(self.control_cores.text())
            protect = self.protect_hashrate.isChecked()
            if protect and self._baseline_hashrate <= 0:
                raise ValueError("Capture the running CPU XMRig baseline before launching with the guard enabled.")

            guard_ready = (
                protect
                and bool(self.protected_api_url.text().strip())
                and self._baseline_hashrate > 0
            )
            assessment = assess_isolation(
                backend=str(self.backend.currentData()),
                hard_gpu_only=self.hard_gpu_only.isChecked(),
                selected_cores=cores,
                protected_processes=self._selected_external_processes(),
                guard_ready=guard_ready,
            )
            if self.require_isolation.isChecked() and assessment.level == "fail":
                raise ValueError(f"GPU-only preflight failed: {assessment.details}")

            extra = shlex.split(self.extra_args.text(), posix=False)
            spec = InstanceSpec(
                name=self.instance_name.text().strip(),
                xmrig_path=Path(self.xmrig_path.text().strip()),
                source_config=Path(self.config_path.text().strip()),
                backend=str(self.backend.currentData()),
                hard_gpu_only=self.hard_gpu_only.isChecked(),
                keep_cpu=self.keep_cpu.isChecked(),
                cuda_loader=Path(self.cuda_loader.text().strip()) if self.cuda_loader.text().strip() else None,
                cuda_devices=self.cuda_devices.text().strip(),
                opencl_devices=self.opencl_devices.text().strip(),
                cuda_bfactor_hint=None if self.cuda_bfactor.value() < 0 else self.cuda_bfactor.value(),
                cuda_bsleep_hint=None if self.cuda_bsleep.value() < 0 else self.cuda_bsleep.value(),
                cuda_tune_profile=str(self.cuda_tune_profile.currentData()),
                cuda_threads=self.cuda_threads.value(),
                cuda_blocks=self.cuda_blocks.value(),
                cuda_memory_reserve_mib=self.cuda_memory_reserve.value(),
                randomx_init_threads=self.randomx_init_threads.value(),
                force_dataset_vram=self.force_dataset_vram.isChecked(),
                native_isolation=self.native_isolation.isChecked(),
                pseudo_lane_count=self.pseudo_lanes.value(),
                preflight_dry_run=self.preflight_dry_run.isChecked(),
                require_cuda_ready=self.require_cuda_ready.isChecked(),
                abort_on_cpu_fallback=self.abort_cpu_fallback.isChecked(),
                cpu_affinity=cores,
                priority=str(self.priority.currentData()),
                eco_qos=self.eco_qos.isChecked(),
                pin_workstation=self.pin_workstation.isChecked(),
                require_isolation=self.require_isolation.isChecked(),
                protected_api_url=self.protected_api_url.text().strip() if protect else "",
                protected_baseline_hs=self._baseline_hashrate if protect else 0.0,
                max_drop_percent=self.max_drop.value(),
                guard_consecutive_samples=self.guard_samples.value(),
                guard_action=str(self.guard_action.currentData()),
                extra_args=extra,
            )
            instance = self.manager.launch(spec)
            self.console.appendPlainText(
                f"[workstation] Created {instance.name} as vPID {instance.virtual_pid}."
            )
            self.navigation.setCurrentRow(4)
        except Exception as exc:
            QMessageBox.critical(self, "Launch failed", str(exc))

    def refresh_gpu(self) -> None:
        self._last_gpus = self.gpu_monitor.sample()
        self.gpu_table.setRowCount(len(self._last_gpus))
        for row, gpu in enumerate(self._last_gpus):
            used = (
                f"{gpu.used_vram_mib:,.0f} / {gpu.total_vram_mib:,.0f} MiB"
                if gpu.dedicated_vram_bytes
                else "Unknown"
            )
            values = [
                str(gpu.index),
                gpu.name,
                gpu.vendor,
                used,
                f"{gpu.utilization_percent:.0f}%",
                f"{gpu.temperature_c:.0f} °C" if gpu.temperature_c is not None else "—",
                f"{gpu.power_w:.1f} W" if gpu.power_w is not None else "—",
                gpu.source,
            ]
            for column, value in enumerate(values):
                self.gpu_table.setItem(row, column, QTableWidgetItem(value))
        self.gpu_table.resizeColumnsToContents()
        self.gpu_count_card.value.setText(str(len(self._last_gpus)))
        self.gpu_count_card.detail.setText(
            ", ".join(gpu.name for gpu in self._last_gpus[:2])
            if self._last_gpus
            else "No NVML/native/nvidia-smi inventory available"
        )
        self._refresh_native_runtime_status()

    def refresh_processes(self) -> None:
        snapshots = self.manager.snapshots()
        self.process_model.set_rows(snapshots)
        self.process_table.resizeColumnsToContents()
        running = sum(1 for row in snapshots if row.state in {"Running", "Suspended", "Starting"})
        total_hashrate = sum(row.hashrate_10s for row in snapshots)
        accepted = sum(row.accepted for row in snapshots)
        rejected = sum(row.rejected for row in snapshots)
        self.running_card.value.setText(str(running))
        self.running_card.detail.setText(f"{len(snapshots)} managed total")
        self.hashrate_card.value.setText(f"{total_hashrate:,.1f}")
        self.hashrate_card.detail.setText("H/s from managed GPU-instance APIs")
        self.shares_card.value.setText(f"{accepted} / {rejected}")
        self.shares_card.detail.setText("accepted / rejected")
        self._refresh_pseudo_lanes(snapshots)

    def _refresh_pseudo_lanes(self, snapshots) -> None:
        lanes = build_pseudo_cpu_lanes(snapshots, self._last_gpus)
        self.pseudo_table.setRowCount(len(lanes))
        for row, lane in enumerate(lanes):
            values = [
                lane.instance_name,
                f"GCPU-{lane.lane_index:02d}",
                lane.device_label,
                lane.state,
                f"{lane.estimated_hashrate:,.1f}",
                f"{lane.vram_slice_mib:,.0f} MiB" if lane.vram_slice_mib else "—",
                lane.note,
            ]
            for column, value in enumerate(values):
                self.pseudo_table.setItem(row, column, QTableWidgetItem(value))
        self.pseudo_table.resizeColumnsToContents()

    def _selected_pid(self) -> int:
        index = self.process_table.currentIndex()
        if not index.isValid():
            raise RuntimeError("Select a process row first.")
        snapshot = self.process_model.snapshot_at(index.row())
        if snapshot is None:
            raise RuntimeError("The selected process is no longer available.")
        return snapshot.virtual_pid

    def _with_selected(self, action) -> None:
        try:
            action(self._selected_pid())
        except Exception as exc:
            QMessageBox.warning(self, "Process action failed", str(exc))

    def _suspend_selected(self) -> None:
        self._with_selected(self.manager.suspend)

    def _resume_selected(self) -> None:
        self._with_selected(self.manager.resume)

    def _stop_selected(self) -> None:
        self._with_selected(self.manager.stop)

    def _kill_selected(self) -> None:
        self._with_selected(self.manager.kill)

    def _append_log(self, instance_name: str, line: str) -> None:
        self.console.appendPlainText(f"[{instance_name}] {line}")

    def _run_terminal_command(self) -> None:
        raw = self.command.text().strip()
        self.command.clear()
        if not raw:
            return
        self.console.appendPlainText(f"GPUHOST:\\> {raw}")
        try:
            parts = shlex.split(raw, posix=False)
            command = parts[0].lower()
            args = parts[1:]
            if command == "help":
                self.console.appendPlainText(
                    "Commands:\n"
                    "  gpu\n  ps\n  lanes\n  gvm status|start [adapter] [lanes]|test [adapter] [lanes]|demo|run <file>|stop\n  external\n  suspend <pid>\n  resume <pid>\n"
                    "  stop <pid>\n  kill <pid>\n"
                    "  priority <pid> idle|below|normal|above|high\n"
                    "  affinity <pid> 20-23\n  clear"
                )
            elif command == "gpu":
                if not self._last_gpus:
                    self.console.appendPlainText("No GPU inventory is available.")
                for gpu in self._last_gpus:
                    self.console.appendPlainText(
                        f"GPU {gpu.index}: {gpu.name}; {gpu.utilization_percent:.0f}% util; "
                        f"{gpu.used_vram_mib:,.0f}/{gpu.total_vram_mib:,.0f} MiB; source={gpu.source}"
                    )
            elif command == "ps":
                rows = self.manager.snapshots()
                if not rows:
                    self.console.appendPlainText("No managed processes.")
                for row in rows:
                    self.console.appendPlainText(
                        f"vPID={row.virtual_pid} host={row.host_pid or '-'} {row.name} "
                        f"state={row.state} backend={row.backend} health={row.backend_health} lanes={row.pseudo_lanes} affinity={row.affinity} "
                        f"priority={row.priority} hashrate={row.hashrate_10s:,.1f} H/s guard={row.guard_status}"
                    )
            elif command == "lanes":
                lanes = build_pseudo_cpu_lanes(self.manager.snapshots(), self._last_gpus)
                if not lanes:
                    self.console.appendPlainText("No pseudo CUDA lanes are active.")
                for lane in lanes:
                    self.console.appendPlainText(
                        f"{lane.instance_name} GCPU-{lane.lane_index:02d} {lane.state} "
                        f"estimated={lane.estimated_hashrate:,.1f} H/s device={lane.device_label}"
                    )
            elif command == "gvm":
                action = args[0].lower() if args else "status"
                if action == "status":
                    self._refresh_native_runtime_status()
                    self.console.appendPlainText(self.gvm_status.text())
                elif action == "start":
                    if len(args) > 1:
                        self.gvm_adapter.setValue(int(args[1]))
                    if len(args) > 2:
                        self.gvm_lanes.setValue(int(args[2]))
                    self._initialize_native_runtime()
                elif action == "test":
                    if len(args) > 1:
                        self.gvm_adapter.setValue(int(args[1]))
                    if len(args) > 2:
                        self.gvm_lanes.setValue(int(args[2]))
                    self._run_native_self_test()
                elif action == "demo":
                    self._run_native_lane_demo()
                elif action == "run":
                    if len(args) != 2:
                        raise ValueError("Usage: gvm run <program.gvm.json>")
                    self._run_gvm_program(Path(args[1]).expanduser().resolve())
                elif action == "stop":
                    self._shutdown_native_runtime()
                else:
                    raise ValueError(
                        "Usage: gvm status|start [adapter] [lanes]|test [adapter] [lanes]|demo|run <file>|stop"
                    )
            elif command == "external":
                self._scan_external_xmrig()
                for process in self._external_xmrig:
                    self.console.appendPlainText(
                        f"PID={process.pid} {process.name} process_affinity={format_cpu_list(process.affinity) or 'unknown'} "
                        f"mining_cores={format_cpu_list(process.mining_cores) or 'not explicit'} "
                        f"source={process.mining_core_source} API={process.api_url or 'not detected'}"
                    )
            elif command == "clear":
                self.console.clear()
            elif command in {"suspend", "resume", "stop", "kill"}:
                if len(args) != 1:
                    raise ValueError(f"Usage: {command} <pid>")
                getattr(self.manager, command)(int(args[0]))
            elif command == "priority":
                if len(args) != 2:
                    raise ValueError("Usage: priority <pid> idle|below|normal|above|high")
                self.manager.set_priority(int(args[0]), args[1].lower())
            elif command == "affinity":
                if len(args) != 2:
                    raise ValueError("Usage: affinity <pid> 20-23")
                self.manager.set_affinity(int(args[0]), parse_cpu_list(args[1]))
            else:
                self.console.appendPlainText("Unknown command. Type 'help'.")
        except Exception as exc:
            self.console.appendPlainText(f"Error: {exc}")

    def _save_settings(self) -> None:
        try:
            self.settings.data_directory = self.data_directory.text().strip()
            self.settings.gpu_refresh_ms = self.gpu_refresh.value()
            self.settings.process_refresh_ms = self.process_refresh.value()
            self.settings.default_backend = str(self.backend.currentData())
            self.settings.default_hard_gpu_only = self.hard_gpu_only.isChecked()
            self.settings.default_priority = str(self.priority.currentData())
            self.settings.default_eco_qos = self.eco_qos.isChecked()
            self.settings.default_affinity = self.control_cores.text().strip()
            self.settings.default_pin_workstation = self.pin_workstation.isChecked()
            self.settings.default_guard_drop_percent = self.max_drop.value()
            self.settings.default_cuda_tune_profile = str(self.cuda_tune_profile.currentData())
            self.settings.default_randomx_init_threads = self.randomx_init_threads.value()
            self.settings.default_native_isolation = self.native_isolation.isChecked()
            self.settings.save()
            self.gpu_timer.setInterval(self.settings.gpu_refresh_ms)
            self.process_timer.setInterval(self.settings.process_refresh_ms)
            QMessageBox.information(self, "Settings", "Settings saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:
        running = [
            item for item in self.manager.instances
            if item.process.state() != QProcess.ProcessState.NotRunning
        ]
        if running:
            answer = QMessageBox.question(
                self,
                "Close workstation",
                "Stop all managed XMRig instances and close the workstation?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.manager.shutdown_all()
        self.gpu_vm.stop()
        self.gpu_monitor.close()
        event.accept()
