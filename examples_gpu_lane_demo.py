from __future__ import annotations

from app.gpu_compute_runtime import GpuVirtualMachine


def main() -> None:
    with GpuVirtualMachine() as machine:
        machine.start(adapter_index=0, lane_count=4096, max_steps_per_lane=4096)
        output, elapsed_ms = machine.run_lane_transform(multiplier=7, addend=11)
        print(f"Executed {len(output):,} GPU lanes in {elapsed_ms:.3f} ms")
        print("First eight outputs:", output[:8])


if __name__ == "__main__":
    main()
