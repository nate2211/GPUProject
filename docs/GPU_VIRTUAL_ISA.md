# GPU Virtual ISA (GVM ABI 4.0)

The native DLL exposes a compact, deterministic virtual instruction set that is
interpreted by a Direct3D 12 compute shader. Every dispatched shader thread is a
logical GPU lane. All lanes execute the same instruction stream and have sixteen
private 32-bit registers.

This is a GPU-native compute VM, not an x86 emulator. It is designed for numeric,
bitwise, hashing, transformation, filtering, and simulation kernels that can be
translated into data-parallel work.

## Execution model

- One shared instruction stream, up to 4,096 instructions.
- 1 to 1,048,576 logical lanes per runtime.
- Sixteen unsigned 32-bit registers per lane.
- One shared read/write 32-bit data buffer in GPU VRAM.
- A configurable maximum-step guard prevents infinite shader loops.
- The CPU creates the D3D12 queue and submits command buffers; instruction
  execution and data mutation occur in the GPU compute dispatch.

## Opcodes

| Value | Name | Behavior |
|---:|---|---|
| 0 | `NOP` | No operation. |
| 1 | `MOV_IMM` | `r[dst] = immediate`. |
| 2 | `MOV_LANE` | `r[dst] = lane_index`. |
| 3 | `MOV` | `r[dst] = r[src_a]`. |
| 4 | `ADD` | Unsigned 32-bit addition. |
| 5 | `SUB` | Unsigned 32-bit subtraction. |
| 6 | `MUL_LO` | Low 32 bits of multiplication. |
| 7 | `XOR` | Bitwise XOR. |
| 8 | `AND` | Bitwise AND. |
| 9 | `OR` | Bitwise OR. |
| 10 | `SHL` | Left shift by `r[src_b] & 31`. |
| 11 | `SHR` | Logical right shift by `r[src_b] & 31`. |
| 12 | `LOAD` | Read `data[(r[src_a] + immediate) % words]`. |
| 13 | `STORE` | Write `r[src_b]` to `data[(r[src_a] + immediate) % words]`. |
| 14 | `CMP_LT` | Write 1 when `r[src_a] < r[src_b]`, otherwise 0. |
| 15 | `JNZ` | Jump to `immediate` when `r[src_a] != 0`. |
| 16 | `ADD_IMM` | `r[dst] = r[src_a] + immediate`. |
| 255 | `HALT` | Stop the current lane. |

## Python example

```python
from app.gpu_compute_runtime import GpuVirtualMachine

with GpuVirtualMachine() as machine:
    machine.start(adapter_index=0, lane_count=4096)
    output, elapsed_ms = machine.run_lane_transform(multiplier=7, addend=11)
    print(output[:8], elapsed_ms)
```

The example computes `output[lane] = lane * 7 + 11` in the D3D12 compute
shader and copies the result back after the GPU fence completes.

## Porting full applications

A normal Windows executable contains x86-64 machine code, system calls, pointer-
rich control flow, and dependencies that a GPU cannot execute unchanged. To use
the GPU runtime, isolate the application's parallel kernels and either:

1. Translate them into the GVM instruction stream.
2. Implement them as native Direct3D 12, CUDA, OpenCL, or Vulkan compute kernels.
3. Add an application-specific C API to `gpu_host_runtime.dll` and call it from
   the Python control plane.

The window manager, filesystem, network stack, process loader, and UI continue
to run on the host CPU. That small control-plane requirement cannot be removed
on standard Windows hardware.
