# Changelog

## 2.2.0 — Non-blocking isolation repair

- Stops treating affinity, priority, EcoQoS, I/O priority, or workstation pinning failures as fatal.
- Keeps XMRig running and reports degraded isolation as warnings.
- Defaults workstation pinning and EcoQoS off for maximum Windows compatibility.
- Defaults the preflight launch gate off.
- Removes nonessential startup flags and process-affinity flags from the XMRig command line.
- Runs XMRig from its original directory so relative CUDA/OpenCL plugin and DLL paths remain valid.
- Keeps CPU mining disabled in the copied JSON config and with the documented `--no-cpu` flag.
- Writes an instance-local `launch.json` diagnostic manifest.

## 2.1.0 — Isolated GPU XMRig host

- Stages a separate instance-local XMRig runtime with companion DLLs and plugin/kernel folders.
- Forces GPU-only launch with `--no-cpu`.
- Adds startup-time XMRig CPU-affinity mask and idle-priority flags.
- Applies Windows affinity, Idle/Below Normal priority, very-low I/O priority, and optional EcoQoS after process creation.
- Detects explicit CPU-mining cores from an existing XMRig command line, local config, or `/2/config` API.
- Adds a strict launch gate that requires non-overlapping CPU control affinity or a configured hashrate guard.
- Adds a stable 60-second CPU-hashrate guard that suspends or stops the GPU instance after a sustained drop.
- Restarts the guard after a suspended instance is resumed.
- Isolates config, log, data directory, HTTP API port, and runtime files for each launch.
- Lowers workstation and telemetry polling frequency by default.
- Expands diagnostics and unit coverage.
