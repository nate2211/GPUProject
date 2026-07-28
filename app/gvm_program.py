from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.gpu_compute_runtime import GpuInstruction, GpuOpcode


_OPCODE_BY_NAME = {opcode.name: opcode for opcode in GpuOpcode}


@dataclass(frozen=True, slots=True)
class GvmProgram:
    name: str
    instructions: list[GpuInstruction]
    data_words: list[int]
    lane_count: int
    max_steps_per_lane: int
    preview_words: int = 16

    @classmethod
    def load(cls, path: Path) -> "GvmProgram":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("A GVM program must be a JSON object.")
        schema = str(payload.get("schema", ""))
        if schema != "gvm-program-v1":
            raise ValueError("Unsupported GVM schema. Expected 'gvm-program-v1'.")

        name = str(payload.get("name") or path.stem)
        runtime = payload.get("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("runtime must be a JSON object.")
        lane_count = int(runtime.get("lanes", 4096))
        max_steps = int(runtime.get("max_steps_per_lane", 65536))
        if not 1 <= lane_count <= 1_048_576:
            raise ValueError("runtime.lanes must be between 1 and 1,048,576.")
        if not 1 <= max_steps <= 1_048_576:
            raise ValueError("runtime.max_steps_per_lane must be between 1 and 1,048,576.")

        instructions_payload = payload.get("instructions")
        if not isinstance(instructions_payload, list) or not instructions_payload:
            raise ValueError("instructions must be a non-empty JSON array.")
        if len(instructions_payload) > 4096:
            raise ValueError("A GVM program cannot exceed 4,096 instructions.")
        instructions = [cls._parse_instruction(item, index) for index, item in enumerate(instructions_payload)]

        data_words = cls._parse_data(payload.get("data"), lane_count)
        preview_words = int(payload.get("preview_words", 16))
        preview_words = max(0, min(preview_words, 256))
        return cls(
            name=name,
            instructions=instructions,
            data_words=data_words,
            lane_count=lane_count,
            max_steps_per_lane=max_steps,
            preview_words=preview_words,
        )

    @staticmethod
    def _parse_instruction(payload: Any, index: int) -> GpuInstruction:
        if not isinstance(payload, dict):
            raise ValueError(f"Instruction {index} must be a JSON object.")
        raw_opcode = payload.get("op", payload.get("opcode"))
        if isinstance(raw_opcode, str):
            key = raw_opcode.strip().upper()
            if key not in _OPCODE_BY_NAME:
                raise ValueError(f"Instruction {index} uses unknown opcode '{raw_opcode}'.")
            opcode = _OPCODE_BY_NAME[key]
        else:
            try:
                opcode = GpuOpcode(int(raw_opcode))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Instruction {index} has an invalid opcode.") from exc

        instruction = GpuInstruction(
            opcode=opcode,
            dst=int(payload.get("dst", 0)),
            src_a=int(payload.get("src_a", 0)),
            src_b=int(payload.get("src_b", 0)),
            immediate=int(payload.get("immediate", 0)),
        )
        try:
            instruction.validate()
        except ValueError as exc:
            raise ValueError(f"Instruction {index}: {exc}") from exc
        return instruction

    @staticmethod
    def _parse_data(payload: Any, lane_count: int) -> list[int]:
        if isinstance(payload, list):
            if not payload:
                raise ValueError("data must contain at least one word.")
            return [int(value) & 0xFFFFFFFF for value in payload]
        if not isinstance(payload, dict):
            raise ValueError("data must be an array or an object with words/fill.")
        word_count = int(payload.get("words", lane_count))
        fill = int(payload.get("fill", 0)) & 0xFFFFFFFF
        if not 1 <= word_count <= 1_048_576:
            raise ValueError("data.words must be between 1 and 1,048,576.")
        return [fill] * word_count

    def result_payload(self, output_words: list[int], elapsed_ms: float) -> dict[str, Any]:
        return {
            "schema": "gvm-result-v1",
            "program": self.name,
            "lane_count": self.lane_count,
            "instruction_count": len(self.instructions),
            "data_word_count": len(output_words),
            "elapsed_ms": elapsed_ms,
            "output": output_words,
        }

    def write_result(self, path: Path, output_words: list[int], elapsed_ms: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.result_payload(output_words, elapsed_ms), indent=2) + "\n",
            encoding="utf-8",
        )
