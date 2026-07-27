# GPU Virtual Workstation

GPU Virtual Workstation is a Windows-oriented PyQt6 control shell that displays
GPU hardware as a virtual host and launches one or more isolated `xmrig.exe`
instances.

## v2.2 launch-failure fix

Version 2.2 changes Windows scheduling isolation from a launch requirement into a best-effort optimization. If CPU affinity, Idle priority, EcoQoS, I/O priority, or PyQt workstation pinning cannot be applied, the warning is written to the virtual terminal and `launch.json`, but XMRig is allowed to continue.

The CPU miner remains disabled through two independent controls:

- `"cpu": {"enabled": false}` in the instance-local config.
- XMRig's documented `--no-cpu` command-line flag.

The launch command is intentionally minimal for compatibility. The selected `xmrig.exe` is run from its original directory so relative `xmrig-cuda.dll`, OpenCL loader, and companion-DLL paths continue to work. Only the config, data directory, log, API port, and process are isolated. Each instance directory contains `launch.json` so the exact executable, config, arguments, and requested scheduling controls can be inspected after a failure.

## What it really does

Each XMRig instance is a normal Windows child process with:

- Its own working/data directory.
- Its own copied `config.json`.
- Its own local XMRig HTTP API port.
- Captured console output.
- Start, suspend, resume, stop, kill, and priority controls.
- CPU, memory, hashrate, accepted-share, and rejected-share telemetry.
- NVIDIA GPU telemetry through NVML when available.
- A virtual PID in the workstation UI, in addition to the real host PID.

The application does **not** make Windows or arbitrary `.exe` programs execute
inside GPU cores. Device selections are passed through XMRig's documented
`--cuda-devices` or `--opencl-devices` command-line options. Windows process execution still occurs on the CPU. XMRig may
use its supported CPU, CUDA, or OpenCL backend according to the selected mode
and the XMRig build/plugins you provide.

## Safety and authorization

Run mining software only on systems you own or are explicitly authorized to
use. This project deliberately contains no persistence, stealth launch,
credential collection, remote deployment, or automatic mining behavior.

## Requirements

- Windows 10 or Windows 11.
- Python 3.11 or 3.12.
- Your own trusted copy of `xmrig.exe`.
- A valid XMRig JSON configuration.
- Optional: NVIDIA driver/NVML for NVIDIA telemetry.
- Optional: `xmrig-cuda.dll` for an XMRig build and algorithm that support it.
- Optional: Visual Studio Build Tools and CMake for the native DXGI inventory DLL.

XMRig is not bundled with this project.

## Setup

PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_venv.ps1
.\.venv\Scripts\python.exe main.py
```

Or from Command Prompt after setup:

```bat
run_windows.bat
```

## First launch

1. Open **Launch XMRig**.
2. Select your trusted `xmrig.exe`.
3. Select an existing valid XMRig `config.json`.
4. Give the virtual instance a name.
5. Choose **CUDA GPU only** or **OpenCL GPU only**.
6. Keep **Hard GPU-only mode** enabled.
7. Leave CPU affinity blank for the first compatibility launch; add it later if desired.
8. Press **Launch isolated GPU instance**.

A new instance directory is created under:

```text
data\instances\<instance-name>-<timestamp>\
```

The copied configuration is patched with a unique localhost HTTP API port.
Your original config is never overwritten.

## Separate-instance behavior

The app sets `XMRIG_DATA_DIR` to the instance directory and starts XMRig with:

```text
xmrig.exe --config <instance-dir>\config.json
```

XMRig's preferred configuration method is JSON. Its local HTTP API is used for
summary telemetry when available. The API is bound to `127.0.0.1` and uses a
random free local port.

## Virtual terminal

The internal console supports:

```text
help
gpu
ps
suspend <virtual-or-host-pid>
resume <virtual-or-host-pid>
stop <virtual-or-host-pid>
kill <virtual-or-host-pid>
priority <virtual-or-host-pid> idle|below|normal|above|high
clear
```

It intentionally does not expose an unrestricted host shell.

## Native C++ DLL

The application runs without the native DLL. The optional DLL uses DXGI to
enumerate Windows display adapters and dedicated video-memory capacity.

Build it in an x64 Visual Studio developer shell:

```powershell
.\build_native.ps1
```

The output is copied to:

```text
native\bin\gpu_host_runtime.dll
```

## Notes about GPU mining

- Enabling CUDA requires a compatible `xmrig-cuda.dll`.
- Enabling OpenCL requires a compatible OpenCL runtime and supported algorithm.
- Not every XMRig algorithm/backend combination is supported.
- RandomX is CPU-oriented; selecting a GPU backend does not turn the GPU into
  Windows CPU cores.
- The application reports what XMRig actually starts. Backend/plugin errors
  remain visible in the captured instance log.

## Official references

- XMRig configuration: https://xmrig.com/docs/miner/config
- XMRig command-line options: https://xmrig.com/docs/miner/command-line-options
- XMRig HTTP API: https://xmrig.com/docs/miner/api
- XMRig CUDA configuration: https://xmrig.com/docs/miner/config/cuda
- XMRig OpenCL configuration: https://xmrig.com/docs/miner/config/opencl
- Qt process documentation: https://doc.qt.io/qt-6/qprocess.html
