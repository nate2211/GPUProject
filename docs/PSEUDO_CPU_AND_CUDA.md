# Pseudo CPU lanes and GPU execution

Version 4.0 contains two different lane concepts. The UI names them separately
because they have different technical meaning.

## Native GVM lanes

```text
Python control plane
        |
        `-- gpu_host_runtime.dll
                |
                `-- Direct3D 12 compute shader
                        +-- GVM lane 0
                        +-- GVM lane 1
                        +-- ...
                        `-- GVM lane N
```

Each GVM lane is a real compute-shader invocation. It has sixteen private
32-bit virtual registers and executes the shared GVM instruction stream. Shared
program and data buffers are GPU resources during execution.

The host CPU still performs Windows-only control-plane work: loading the DLL,
creating the D3D12 device, submitting command lists, waiting on the fence, and
copying requested results back. The translated arithmetic/data kernel itself is
executed by the GPU.

## XMRig CUDA projections

```text
PyQt6 workstation
        |
        +-- display projection (GCPU-00 ... GCPU-N)
        |
        `-- XMRig Windows process
                |
                +-- CPU backend -> physical x86 CPU threads
                `-- CUDA backend -> xmrig-cuda.dll -> NVIDIA GPU
```

These rows divide XMRig's total reported CUDA hashrate evenly for visualization.
They are not individually measured threads and do not redirect XMRig's CPU JIT
to the native GVM.

## Why arbitrary executables still cannot move to GPU lanes

A Windows executable uses x86-64 machine instructions and Windows system calls.
A D3D12/CUDA/OpenCL GPU executes a different instruction set and cannot invoke
Windows services directly. Running an application on the GVM therefore requires
translation of its parallel kernels or a purpose-built GPU backend.

This is the same architectural boundary used by production GPU applications:
CPU control flow coordinates GPU command buffers, while data-parallel kernels
run on GPU execution units.

## XMRig backend safety checks

GPU-only and pseudo-CUDA XMRig modes continue to watch startup output. The
manager stops an instance when:

- CUDA is required but the plugin/backend fails.
- XMRig starts a real CPU mining profile despite GPU-only mode.

Hybrid mode explicitly allows physical CPU mining and reports that state.
