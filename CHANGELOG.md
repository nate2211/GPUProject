# Changelog

## 4.1.0 — Protected CUDA throughput and native process isolation

- Adds VRAM/SM-aware RandomX CUDA profile generation with Maximum, Fast, Balanced, Compatibility, and Existing presets.
- Defaults the Maximum profile to `bfactor=0`, `bsleep=0`, 32 CUDA threads, and auto-sized blocks with a 1,024 MiB VRAM reserve.
- Adds a one-thread protected RandomX dataset initialization path and idle CPU initialization priority.
- Adds `process_isolation_runtime.dll` with suspended-apply affinity, priority, EcoQoS, and a persistent Windows Job Object.
- Auto-selects a low-impact CPU when a GPU-only launch has no explicit control affinity.
- Pins explicit CUDA RX profiles to the reserved control CPU and forces `dataset_host=false`.
- Separates dataset initialization messages from CPU mining fallback detection.
- Adds CUDA compute-error detection and tuning diagnostics.
- Expands unit coverage to 34 tests.

## 4.0.0 — Native Direct3D 12 GPU virtual machine

- Replaces display-only pseudo CPU claims with a real C++ Direct3D 12 compute runtime.
- Adds the GVM 4.0 virtual ISA with sixteen registers per lane, shared GPU data memory, arithmetic, bitwise operations, comparisons, bounded branching, and halt.
- Adds persistent device, compute queue, pipeline, descriptor heap, command allocator, command list, fence, and runtime telemetry.
- Adds a deterministic native GPU self-test and post-build validation script.
- Adds Python ctypes bindings with ABI detection, error propagation, lifecycle management, program validation, and a lane-transform demo.
- Adds GUI controls and terminal commands for native engine start, status, test, demo, and shutdown.
- Keeps XMRig CUDA lane projections separate from measured GVM execution lanes.
- Documents why arbitrary x86 Windows programs cannot execute unchanged on GPU hardware and how to port parallel kernels correctly.

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
