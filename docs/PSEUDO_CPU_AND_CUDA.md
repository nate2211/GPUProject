# Pseudo CPU and CUDA design

## Execution boundary

```text
PyQt6 virtual workstation
        |
        +-- pseudo lane model (GCPU-00 ... GCPU-N)
        |
        +-- XMRig Windows process
                |
                +-- CPU backend -> physical x86 CPU threads
                |
                `-- CUDA backend -> xmrig-cuda.dll -> NVIDIA GPU
```

The pseudo lane model is a control and visualization abstraction over CUDA. It
cannot redirect XMRig CPU JIT instructions to the GPU.

## Why the distinction matters

XMRig's CPU configuration defines native CPU mining threads and CPU affinity.
CUDA is a separate plugin backend. Turning both on creates a hybrid miner, not a
GPU-emulated CPU.

## Pseudo lane calculations

The UI divides the total 10-second CUDA-instance hashrate evenly across the
configured number of logical lanes. That per-lane value is an estimate for
visualization, not a measurement exported by XMRig.

## Backend safety checks

GPU-only and pseudo-CPU modes watch startup output. The manager stops the
instance when:

- CUDA is required but the plugin/backend fails.
- XMRig starts a real CPU mining profile despite GPU-only mode.

Hybrid mode allows real CPU mining and reports that state explicitly.

## Future custom backend work

A true custom GPU virtual-machine backend would require an XMRig source fork or
separate miner backend implementing RandomX on CUDA. It would still be a CUDA
backend; it would not make the stock XMRig CPU backend run on the GPU.
