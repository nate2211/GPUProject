from __future__ import annotations

from dataclasses import dataclass

from app.models import GpuDevice, InstanceSnapshot


@dataclass(frozen=True, slots=True)
class PseudoCpuLane:
    instance_name: str
    lane_index: int
    device_label: str
    state: str
    estimated_hashrate: float
    vram_slice_mib: float
    note: str


def build_pseudo_cpu_lanes(
    snapshots: list[InstanceSnapshot],
    devices: list[GpuDevice],
) -> list[PseudoCpuLane]:
    """Build display-only pseudo CPU lanes backed by CUDA instances.

    Lanes are a scheduling/visualization abstraction. They do not claim that
    XMRig's x86 CPU backend has moved to the GPU. Reported total hashrate is
    divided evenly only to make the virtual topology readable.
    """
    device_by_index = {device.index: device for device in devices}
    output: list[PseudoCpuLane] = []

    for snapshot in snapshots:
        if snapshot.backend != "pseudo_cuda" or snapshot.pseudo_lanes <= 0:
            continue
        lane_count = max(1, snapshot.pseudo_lanes)
        selected_index = 0
        try:
            selected_index = int((snapshot.gpu_devices or "0").split(",", 1)[0].strip())
        except ValueError:
            selected_index = 0
        device = device_by_index.get(selected_index)
        device_label = device.name if device is not None else "Selected CUDA device"
        vram_slice = (
            device.total_vram_mib / lane_count
            if device is not None and device.total_vram_mib > 0
            else 0.0
        )
        lane_rate = snapshot.hashrate_10s / lane_count if snapshot.hashrate_10s > 0 else 0.0
        state = "Running" if snapshot.state == "Running" else snapshot.state
        if "failed" in snapshot.backend_health.lower():
            state = "Backend failed"

        for lane_index in range(lane_count):
            output.append(
                PseudoCpuLane(
                    instance_name=snapshot.name,
                    lane_index=lane_index,
                    device_label=device_label,
                    state=state,
                    estimated_hashrate=lane_rate,
                    vram_slice_mib=vram_slice,
                    note="Logical CUDA lane; not an x86/XMRig CPU thread",
                )
            )
    return output
