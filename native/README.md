# Native GPU Host Runtime DLL

`gpu_host_runtime.dll` now provides two layers:

1. DXGI adapter inventory used by the workstation GPU monitor.
2. A persistent Direct3D 12 compute runtime implementing the GVM 4.0 virtual ISA.

The compute runtime creates a D3D12 device, compute queue, root signature,
pipeline state, shader-visible descriptors, fence, and completion event. A
shared virtual instruction stream is dispatched across configurable logical GPU
lanes. Program and data buffers live in GPU resources during execution and are
copied back only after the completion fence signals.

## Build

Open an x64 Visual Studio 2022 developer PowerShell and run:

```powershell
.\build_native.ps1
```

Required Windows libraries are supplied by the Windows SDK:

- `d3d12.lib`
- `d3dcompiler.lib`
- `dxgi.lib`

The script copies the release DLL to `native\bin\gpu_host_runtime.dll` and runs
a real GPU self-test unless `-SkipSelfTest` is supplied.

## ABI safety

The Python layer checks `ghr_get_runtime_abi_version()` and only enables compute
features for ABI 4.0 or newer. An older inventory-only DLL can still enumerate
adapters but cannot be mistaken for the GPU virtual-machine runtime.

## Limits

This DLL does not inject into processes or reinterpret x86 instructions. It is a
GPU compute service for translated, data-parallel kernels. Windows process
creation and operating-system services remain CPU-hosted.

## Native process isolation runtime

The same CMake build also creates `process_isolation_runtime.dll`. It applies
XMRig control-plane affinity and priority while the child is briefly suspended,
optionally enables EcoQoS, and keeps the process in a Windows Job Object so the
limits apply to future XMRig/CUDA control threads. The Python manager falls back
to psutil when the DLL is missing or Windows rejects Job Object assignment.

This helper does not inject code into XMRig and does not alter `xmrig.exe` or
`xmrig-cuda.dll`.
