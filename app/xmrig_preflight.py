from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PreflightResult:
    ok: bool
    return_code: int
    output: str
    reason: str


def resolve_cuda_loader(
    xmrig_path: Path,
    configured_loader: Path | str | None,
) -> Path | None:
    if configured_loader:
        loader = Path(os.path.expandvars(str(configured_loader))).expanduser()
        if not loader.is_absolute():
            loader = xmrig_path.parent / loader
        return loader.resolve()

    default_loader = xmrig_path.parent / "xmrig-cuda.dll"
    return default_loader.resolve() if default_loader.exists() else None


def evaluate_preflight_output(
    return_code: int,
    output: str,
    *,
    requires_cuda: bool,
) -> PreflightResult:
    normalized = output.lower()
    if return_code != 0:
        return PreflightResult(False, return_code, output, f"XMRig dry-run exited with code {return_code}")

    if requires_cuda:
        failure_markers = (
            "cuda disabled",
            "failed to load cuda",
            "unable to load cuda",
            "failed to load xmrig-cuda",
            "cuda plugin is not found",
        )
        for marker in failure_markers:
            if marker in normalized:
                return PreflightResult(False, return_code, output, f"CUDA preflight reported: {marker}")

    return PreflightResult(True, return_code, output, "Configuration dry-run passed")


def run_xmrig_preflight(
    executable: Path,
    arguments: list[str],
    *,
    working_directory: Path,
    requires_cuda: bool,
    timeout_seconds: float = 25.0,
) -> PreflightResult:
    command = [str(executable), *arguments, "--dry-run"]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            cwd=str(working_directory),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            creationflags=creation_flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        return PreflightResult(False, -1, output, "XMRig dry-run timed out")
    except OSError as exc:
        return PreflightResult(False, -1, "", f"Could not execute XMRig dry-run: {exc}")

    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    return evaluate_preflight_output(completed.returncode, output, requires_cuda=requires_cuda)
