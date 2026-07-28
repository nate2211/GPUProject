from __future__ import annotations

import unittest

from app.gpu_compute_runtime import GpuInstruction, GpuOpcode, GpuVirtualMachine
from app.native_bridge import NativeInstruction


class FakeBridge:
    compute_available = True
    abi_version = 0x00040000

    def adapter_supports_compute(self, index: int) -> bool:
        return index == 0

    def create_runtime(self, adapter_index: int, lane_count: int, max_steps_per_lane: int):
        class Handle:
            value = 123

        self.created = (adapter_index, lane_count, max_steps_per_lane)
        return Handle()

    def destroy_runtime(self, handle) -> None:
        handle.value = None

    def runtime_status(self, _handle):
        class Status:
            active = 1
            adapter_index = 0
            lane_count = 4
            max_steps_per_lane = 256
            executions = 0
            last_instruction_count = 0
            last_data_words = 0
            last_elapsed_ms = 0.0
            adapter_name = "Fake GPU"

        return Status()

    def execute(self, _handle, instructions: list[NativeInstruction], words: list[int]):
        self.instructions = instructions
        return [1, 4, 7, 10], 0.25


class GpuComputeRuntimeTests(unittest.TestCase):
    def test_instruction_validates_registers(self) -> None:
        with self.assertRaises(ValueError):
            GpuInstruction(GpuOpcode.ADD, dst=16).validate()

    def test_native_instruction_layout_is_stable(self) -> None:
        instruction = GpuInstruction(
            GpuOpcode.ADD_IMM,
            dst=2,
            src_a=1,
            immediate=5,
        ).to_native()
        self.assertEqual(int(instruction.opcode), int(GpuOpcode.ADD_IMM))
        self.assertEqual(int(instruction.dst), 2)
        self.assertEqual(int(instruction.src_a), 1)
        self.assertEqual(int(instruction.immediate), 5)

    def test_lane_demo_builds_gpu_program(self) -> None:
        bridge = FakeBridge()
        machine = GpuVirtualMachine(bridge=bridge)
        machine.start(adapter_index=0, lane_count=4, max_steps_per_lane=256)
        words, elapsed = machine.run_lane_transform(3, 1)
        self.assertEqual(words, [1, 4, 7, 10])
        self.assertEqual(elapsed, 0.25)
        self.assertEqual(len(bridge.instructions), 6)
        self.assertEqual(int(bridge.instructions[-1].opcode), int(GpuOpcode.HALT))


if __name__ == "__main__":
    unittest.main()
