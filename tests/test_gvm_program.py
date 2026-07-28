from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.gpu_compute_runtime import GpuOpcode
from app.gvm_program import GvmProgram


class GvmProgramTests(unittest.TestCase):
    def test_loads_program(self) -> None:
        payload = {
            "schema": "gvm-program-v1",
            "name": "test",
            "runtime": {"lanes": 4, "max_steps_per_lane": 32},
            "data": {"words": 4, "fill": 9},
            "instructions": [
                {"op": "MOV_LANE", "dst": 0},
                {"op": "HALT"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.gvm.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            program = GvmProgram.load(path)
        self.assertEqual(program.lane_count, 4)
        self.assertEqual(program.data_words, [9, 9, 9, 9])
        self.assertEqual(program.instructions[0].opcode, GpuOpcode.MOV_LANE)

    def test_rejects_unknown_opcode(self) -> None:
        payload = {
            "schema": "gvm-program-v1",
            "instructions": [{"op": "NOT_REAL"}],
            "data": [0],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.gvm.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown opcode"):
                GvmProgram.load(path)


if __name__ == "__main__":
    unittest.main()
