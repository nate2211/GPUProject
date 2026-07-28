from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from app.native_bridge import (
    NativeGpuBridge,
    NativeInstruction,
    NativeRuntimeError,
)


class GpuOpcode(IntEnum):
    NOP = 0
    MOV_IMM = 1
    MOV_LANE = 2
    MOV = 3
    ADD = 4
    SUB = 5
    MUL_LO = 6
    XOR = 7
    AND = 8
    OR = 9
    SHL = 10
    SHR = 11
    LOAD = 12
    STORE = 13
    CMP_LT = 14
    JNZ = 15
    ADD_IMM = 16
    HALT = 255


@dataclass(frozen=True, slots=True)
class GpuInstruction:
    opcode: GpuOpcode
    dst: int = 0
    src_a: int = 0
    src_b: int = 0
    immediate: int = 0

    def validate(self) -> None:
        for name, value in (
            ("dst", self.dst),
            ("src_a", self.src_a),
            ("src_b", self.src_b),
        ):
            if not 0 <= int(value) < 16:
                raise ValueError(f"{name} must address virtual register 0 through 15.")
        if not 0 <= int(self.immediate) <= 0xFFFFFFFF:
            raise ValueError("immediate must fit in an unsigned 32-bit word.")

    def to_native(self) -> NativeInstruction:
        self.validate()
        return NativeInstruction(
            opcode=int(self.opcode),
            dst=int(self.dst),
            src_a=int(self.src_a),
            src_b=int(self.src_b),
            immediate=int(self.immediate),
        )


@dataclass(frozen=True, slots=True)
class GpuRuntimeSnapshot:
    active: bool
    adapter_index: int = 0
    adapter_name: str = ""
    lane_count: int = 0
    max_steps_per_lane: int = 0
    executions: int = 0
    last_instruction_count: int = 0
    last_data_words: int = 0
    last_elapsed_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class GpuSelfTestReport:
    passed: bool
    lane_count: int
    mismatches: int
    elapsed_ms: float
    message: str


class GpuVirtualMachine:
    """Persistent Direct3D 12 runtime for GPU-native pseudo CPU lanes.

    It executes the project's compact virtual ISA in a compute shader. This is
    real GPU execution, but it is deliberately not an x86 emulator: arbitrary
    Windows executables must use a GPU-aware backend or be translated into this
    virtual ISA before their numeric kernels can run here.
    """

    def __init__(self, bridge: NativeGpuBridge | None = None) -> None:
        self.bridge = bridge or NativeGpuBridge()
        self._handle = None

    @property
    def available(self) -> bool:
        return self.bridge.compute_available

    @property
    def active(self) -> bool:
        return self._handle is not None and bool(self._handle.value)

    @property
    def abi_version_text(self) -> str:
        version = self.bridge.abi_version
        if version <= 0:
            return "unavailable"
        return f"{(version >> 16) & 0xFFFF}.{version & 0xFFFF}"

    def start(self, adapter_index: int, lane_count: int, max_steps_per_lane: int = 65536) -> None:
        if self.active:
            self.stop()
        if not 1 <= int(lane_count) <= 1_048_576:
            raise ValueError("GPU lane count must be between 1 and 1,048,576.")
        if not 1 <= int(max_steps_per_lane) <= 1_048_576:
            raise ValueError("Maximum steps must be between 1 and 1,048,576.")
        if not self.bridge.adapter_supports_compute(int(adapter_index)):
            raise NativeRuntimeError(
                f"DXGI adapter {adapter_index} does not expose a Direct3D 12 compute device."
            )
        self._handle = self.bridge.create_runtime(
            adapter_index=int(adapter_index),
            lane_count=int(lane_count),
            max_steps_per_lane=int(max_steps_per_lane),
        )

    def stop(self) -> None:
        if not self.active:
            self._handle = None
            return
        self.bridge.destroy_runtime(self._handle)
        self._handle = None

    def status(self) -> GpuRuntimeSnapshot:
        if not self.active:
            return GpuRuntimeSnapshot(active=False)
        native = self.bridge.runtime_status(self._handle)
        return GpuRuntimeSnapshot(
            active=bool(native.active),
            adapter_index=int(native.adapter_index),
            adapter_name=str(native.adapter_name),
            lane_count=int(native.lane_count),
            max_steps_per_lane=int(native.max_steps_per_lane),
            executions=int(native.executions),
            last_instruction_count=int(native.last_instruction_count),
            last_data_words=int(native.last_data_words),
            last_elapsed_ms=float(native.last_elapsed_ms),
        )

    def execute(
        self,
        instructions: list[GpuInstruction],
        words: list[int],
    ) -> tuple[list[int], float]:
        if not self.active:
            raise NativeRuntimeError("Start the native GPU virtual-machine runtime first.")
        if len(instructions) > 4096:
            raise ValueError("GPU virtual-ISA programs are limited to 4,096 instructions.")
        native_program = [instruction.to_native() for instruction in instructions]
        return self.bridge.execute(self._handle, native_program, words)

    def self_test(self, adapter_index: int, lane_count: int) -> GpuSelfTestReport:
        native = self.bridge.self_test(adapter_index, lane_count)
        return GpuSelfTestReport(
            passed=bool(native.passed),
            lane_count=int(native.lane_count),
            mismatches=int(native.mismatches),
            elapsed_ms=float(native.elapsed_ms),
            message=str(native.message),
        )

    def run_lane_transform(self, multiplier: int = 3, addend: int = 1) -> tuple[list[int], float]:
        """Run output[lane] = lane * multiplier + addend on the active GPU lanes."""
        snapshot = self.status()
        if not snapshot.active:
            raise NativeRuntimeError("Start the native GPU virtual-machine runtime first.")
        program = [
            GpuInstruction(GpuOpcode.MOV_LANE, dst=0),
            GpuInstruction(GpuOpcode.MOV_IMM, dst=1, immediate=multiplier & 0xFFFFFFFF),
            GpuInstruction(GpuOpcode.MUL_LO, dst=2, src_a=0, src_b=1),
            GpuInstruction(GpuOpcode.ADD_IMM, dst=2, src_a=2, immediate=addend & 0xFFFFFFFF),
            GpuInstruction(GpuOpcode.STORE, src_a=0, src_b=2),
            GpuInstruction(GpuOpcode.HALT),
        ]
        return self.execute(program, [0] * snapshot.lane_count)

    def __enter__(self) -> "GpuVirtualMachine":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()
