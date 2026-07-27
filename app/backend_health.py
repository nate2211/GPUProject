from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendEvent:
    kind: str
    detail: str


def classify_xmrig_line(line: str) -> BackendEvent | None:
    """Classify the small set of startup lines needed for backend safety.

    This intentionally avoids treating the hardware inventory line beginning
    with ``* CPU`` as evidence that CPU mining is active.
    """
    normalized = " ".join(line.lower().split())

    cuda_failure_markers = (
        "cuda disabled",
        "failed to load cuda",
        "unable to load cuda",
        "cuda plugin is not found",
        "failed to load xmrig-cuda",
        "unsupported algorithm" if "cuda" in normalized else "\0never\0",
    )
    if any(marker in normalized for marker in cuda_failure_markers):
        return BackendEvent("cuda_failed", line.strip())

    if "* cuda" in normalized and "enabled" in normalized:
        return BackendEvent("cuda_enabled", line.strip())
    padded = f" {normalized} "
    if " cuda " in padded and ("ready threads" in normalized or "use profile" in normalized):
        return BackendEvent("cuda_ready", line.strip())

    if " cpu " in padded and ("use profile" in normalized or "ready threads" in normalized):
        return BackendEvent("cpu_mining", line.strip())

    return None
