from __future__ import annotations

from pathlib import Path

from app.models import CUDA_BACKENDS, InstanceSpec


def build_xmrig_arguments(spec: InstanceSpec, config_path: Path) -> list[str]:
    """Build an explicit XMRig command line with absolute runtime paths."""
    resolved_config = config_path.expanduser().resolve()
    arguments = ["--config", str(resolved_config), "--no-color"]

    if spec.hard_gpu_only:
        arguments.append("--no-cpu")

    if spec.backend in CUDA_BACKENDS:
        arguments.append("--cuda")
        if spec.cuda_loader is not None:
            arguments.append(f"--cuda-loader={spec.cuda_loader.expanduser().resolve()}")
        if spec.cuda_devices.strip():
            arguments.append(f"--cuda-devices={spec.cuda_devices.strip()}")
        # Preset/explicit RX profiles carry BF/BS in config.json. CLI hints are
        # only useful when the user deliberately keeps XMRig autoconfiguration.
        if spec.cuda_tune_profile == "existing":
            if spec.cuda_bfactor_hint is not None:
                arguments.append(f"--cuda-bfactor-hint={int(spec.cuda_bfactor_hint)}")
            if spec.cuda_bsleep_hint is not None:
                arguments.append(f"--cuda-bsleep-hint={int(spec.cuda_bsleep_hint)}")
    elif spec.backend == "opencl":
        arguments.append("--opencl")
        if spec.opencl_devices.strip():
            arguments.append(f"--opencl-devices={spec.opencl_devices.strip()}")

    arguments.extend(spec.extra_args)
    return arguments
