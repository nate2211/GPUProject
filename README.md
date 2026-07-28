# GPU Virtual Workstation 4.1

GPU Virtual Workstation is a Windows PyQt6 control plane for two related GPU
execution paths:

1. Isolated GPU-backed XMRig instances using supported CUDA or OpenCL backends.
2. A native C++ Direct3D 12 GPU virtual machine that executes a compact virtual
   instruction set across configurable pseudo CPU lanes.

## What changed in 4.1

Version 4.1 adds a protected high-throughput CUDA launch path for RandomX:

- Generates an explicit CUDA `rx`/`rx/0` profile instead of relying only on conservative autoconfiguration.
- Provides Maximum, Fast, Balanced, Compatibility, and Existing/autoconfig presets.
- The Maximum preset uses `bfactor=0` and `bsleep=0`, with blocks sized from NVIDIA SM count and free VRAM while retaining a configurable memory reserve.
- Pins the CUDA plugin control thread to the selected control CPU and sets `dataset_host=false`.
- Limits RandomX dataset initialization to one reserved CPU thread by default. This protects a separate CPU miner but makes the one-time startup initialization slower.
- Adds `process_isolation_runtime.dll`, which briefly suspends the new XMRig child, applies affinity/priority/EcoQoS, attaches a persistent Windows Job Object, then resumes it before dataset initialization normally begins.
- Automatically chooses a low-impact logical CPU when no control affinity is supplied.
- Distinguishes RandomX dataset initialization from actual CPU mining in backend health logs.
- Detects CUDA compute errors and recommends reducing blocks or selecting a safer preset.

See [docs/CUDA_PERFORMANCE_AND_ISOLATION.md](docs/CUDA_PERFORMANCE_AND_ISOLATION.md).

## What changed in 4.0

The old pseudo CPU page only divided XMRig's total hashrate into display rows.
Version 4.0 retains those telemetry projections but adds a real native compute
engine in `gpu_host_runtime.dll`:

- Persistent Direct3D 12 device and compute queue.
- Runtime-compiled compute shader implementing the GVM 4.0 virtual ISA.
- Up to 1,048,576 logical GPU lanes.
- Sixteen 32-bit virtual registers per lane.
- Shared GPU data memory with `LOAD` and `STORE` instructions.
- Arithmetic, bitwise, comparison, bounded branching, and halt instructions.
- GPU fence synchronization, deterministic readback, runtime status, and a
  built-in self-test.
- ABI version checks so an older inventory-only DLL cannot be treated as the
  new compute engine.

The **Pseudo CPU Lanes** page now lets you initialize the native engine, choose
a DXGI adapter and lane count, run a real GPU self-test, run a lane arithmetic
demo, inspect execution telemetry, and shut the engine down cleanly.

## Important execution boundary

A GPU is not binary-compatible with an x86-64 CPU. A normal Windows `.exe`
cannot be moved unchanged into GPU cores because it contains x86 instructions,
system calls, pointer-heavy control flow, and operating-system dependencies.

Version 4.0 provides the closest technically valid architecture:

```text
Windows/Python control plane on CPU
        |
        +-- process, filesystem, networking, UI, command submission
        |
        `-- gpu_host_runtime.dll
                |
                +-- D3D12 compute queue
                +-- GVM instruction/data buffers in GPU resources
                `-- thousands of GPU shader lanes executing translated kernels
```

For supported GVM programs, the arithmetic and data mutation are executed by
the GPU compute shader. A small CPU control plane is still required to create
the device, submit the command list, and interact with Windows. The project does
not silently fall back to pretending host CPU threads are GPU cores.

To accelerate a full application, isolate its parallel workload and translate
that workload into the GVM instruction set or implement a native Direct3D 12,
CUDA, OpenCL, or Vulkan kernel. The UI, filesystem, networking, and other Windows
services remain CPU-hosted.

See [docs/GPU_VIRTUAL_ISA.md](docs/GPU_VIRTUAL_ISA.md) for the instruction set.

## Existing XMRig host features

Each managed XMRig instance remains a normal Windows child process with:

- Its own instance directory and copied `config.json`.
- Its own localhost XMRig HTTP API port.
- Captured console output and an instance-local `launch.json` manifest.
- Start, suspend, resume, stop, kill, priority, and affinity controls.
- CPU, memory, hashrate, accepted-share, and rejected-share telemetry.
- NVIDIA telemetry through NVML when available.
- A workstation virtual PID in addition to the real Windows PID.
- GPU-only launch enforcement through config patching and `--no-cpu`.
- Detection and optional abort when real CPU mining unexpectedly starts.
- Optional protection of a separate CPU miner through affinity and a measured
  hashrate guard.
- Explicit RandomX CUDA tuning with VRAM-aware blocks and BF/BS presets.
- One-thread protected RandomX dataset initialization by default.
- Native process affinity/priority/Job Object isolation through a second C++ DLL.

XMRig's CUDA/OpenCL backend and the native GVM runtime are separate systems.
The GVM does not reinterpret XMRig's CPU JIT. XMRig must still use a backend it
natively supports.

## Safety and authorization

Run mining or compute software only on systems you own or are explicitly
authorized to use. This project contains no persistence, stealth launch,
credential collection, remote deployment, process injection, or automatic
mining behavior.

## Requirements

- Windows 10 or Windows 11.
- A Direct3D 12-capable GPU and current graphics driver for the native GVM.
- Python 3.11 or 3.12.
- PyQt6, psutil, and optional NVML support from `requirements.txt`.
- Visual Studio 2022 Build Tools with **Desktop development with C++**.
- A Windows 10/11 SDK and CMake to build `gpu_host_runtime.dll`.
- Optional: your own trusted `xmrig.exe`, valid XMRig config, and matching
  `xmrig-cuda.dll` or OpenCL runtime.

XMRig is not bundled with this project.

## Setup

PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_venv.ps1
.\build_native.ps1
.\.venv\Scripts\python.exe main.py
```

The native build script creates:

```text
native\bin\gpu_host_runtime.dll
native\bin\process_isolation_runtime.dll
```

It then runs a real D3D12 self-test by default. To build without the post-build
test:

```powershell
.\build_native.ps1 -SkipSelfTest
```

To test a different DXGI adapter or lane count:

```powershell
.\build_native.ps1 -SelfTestAdapter 1 -SelfTestLanes 8192
```

## Native GPU engine quick start

1. Build the DLL with `build_native.ps1`.
2. Launch `main.py`.
3. Open **Pseudo CPU Lanes**.
4. Select the DXGI adapter index.
5. Choose a lane count, such as 4,096 or 16,384.
6. Press **Run GPU self-test**.
7. Press **Initialize native GPU engine**.
8. Press **Run lane demo**, or load `examples\lane_affine.gvm.json` with **Run .gvm.json program…**.

The demo executes this translated kernel on the GPU:

```text
output[lane] = lane * 3 + 1
```

The terminal reports the first outputs and measured dispatch/fence duration.

A standalone test is also included:

```powershell
.\.venv\Scripts\python.exe scripts\native_selftest.py --adapter 0 --lanes 4096
```

A Python API example is available in `examples_gpu_lane_demo.py`. The reusable JSON program format is demonstrated by `examples/lane_affine.gvm.json`; completed output is written under `data/gvm-results`.

## Virtual terminal

```text
help
gpu
ps
lanes
gvm status
gvm start [adapter] [lanes]
gvm test [adapter] [lanes]
gvm demo
gvm run <program.gvm.json>
gvm stop
external
suspend <virtual-or-host-pid>
resume <virtual-or-host-pid>
stop <virtual-or-host-pid>
kill <virtual-or-host-pid>
priority <virtual-or-host-pid> idle|below|normal|above|high
affinity <virtual-or-host-pid> 20-23
clear
```

The terminal intentionally does not expose an unrestricted host shell.

## Native DLL API

The main exported functions are:

- `ghr_get_adapter_count`
- `ghr_get_adapter_info`
- `ghr_adapter_supports_compute`
- `ghr_runtime_create`
- `ghr_runtime_get_status`
- `ghr_runtime_execute`
- `ghr_runtime_self_test`
- `ghr_runtime_destroy`
- `ghr_get_last_error`

The API uses fixed-width structs suitable for Python `ctypes` and other FFI
callers. Runtime calls are serialized per handle, and every dispatch waits on a
D3D12 fence before readback.

## Testing

Cross-platform Python tests do not require the Windows DLL:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

The native D3D12 path must be built and exercised on Windows with a compatible
GPU. `scripts/native_selftest.py` validates real shader execution and output.

## Notes about GPU mining

- Enabling CUDA requires a compatible `xmrig-cuda.dll`.
- Enabling OpenCL requires a compatible OpenCL runtime and supported algorithm.
- Not every XMRig algorithm/backend combination is supported.
- RandomX is CPU-oriented; a GPU backend does not turn stock XMRig CPU threads
  into GPU threads. On an RTX 3070 Ti, the GPU RandomX rate can remain far below
  a Ryzen 9 5900X CPU rate even after tuning.
- `randomx.init=1` protects the host CPU miner during startup, but dataset creation
  takes longer than the 24-thread initialization shown in the original log.
- Maximum throughput can increase heat and board power. Thermal or PSU coupling
  can still reduce CPU boost clocks even with perfect scheduler isolation.
- The application reports what XMRig actually starts and keeps plugin/backend
  failures visible in the captured instance log.
