# Architecture

```text
PyQt6 MainWindow
  |
  +-- Native GPU Virtual Machine
  |     +-- ctypes ABI guard
  |     +-- gpu_host_runtime.dll
  |     +-- DXGI adapter selection
  |     +-- D3D12 device + compute queue
  |     +-- GVM shader pipeline
  |     +-- GPU program/data resources
  |     `-- fence, readback, status, self-test
  |
  +-- GPU Monitor
  |     +-- NVML
  |     +-- Native DXGI C++ DLL
  |     `-- nvidia-smi fallback
  |
  +-- Existing-XMRig scanner
  |     +-- Windows process discovery
  |     +-- Command-line affinity-mask parser
  |     +-- Local config CPU-profile parser
  |     +-- /2/config profile parser
  |     `-- Windows CPU-set efficiency metadata
  |
  +-- XMRig Manager
  |     +-- Runtime staging
  |     +-- Config and log isolation
  |     +-- GPU-only argument builder
  |     +-- QProcess lifecycle and output
  |     +-- Windows affinity/priority/I/O/EcoQoS policy
  |     +-- Managed XMRig API poller
  |     `-- Protected CPU-XMRig hashrate guard
  |
  +-- Process table model
  `-- Restricted virtual terminal
```

## Native GPU virtual-machine boundary

The C++ DLL owns a persistent Direct3D 12 device, compute queue, allocator,
command list, root signature, compute pipeline, descriptor heap, fence, and
completion event. Each execution call:

1. Validates the GVM program and 32-bit data buffer.
2. Uploads the program and data to D3D12 resources.
3. Transitions the program to shader-resource state and data to UAV state.
4. Dispatches one shader lane per configured logical GVM lane.
5. Inserts a UAV barrier and transitions data to copy-source state.
6. Copies results to a readback resource.
7. Signals and waits for a fence before returning data to Python.

Calls are serialized per runtime handle. A configurable maximum-step count
bounds shader loops.

## Why the host CPU remains present

Windows process loading, UI event dispatch, filesystem access, network sockets,
and D3D12 command submission are host operations. The project minimizes this
control plane but does not mislabel it as GPU execution. Only translated GVM or
other native GPU kernels execute on the GPU.

## XMRig isolation boundaries

Each managed XMRig instance receives:

- A unique workstation vPID.
- A real Windows PID.
- A unique directory.
- An instance-local runtime containing XMRig and companion files.
- A copied and patched config.
- A unique log path.
- A unique loopback HTTP API port.
- A unique `XMRIG_DATA_DIR`.
- A configured Windows CPU affinity and priority class.

The already-running CPU miner is read but never injected into, patched in memory,
or changed by the application.

## XMRig protection model

Protection has four layers:

1. **Workload separation:** the child receives `--no-cpu` and a GPU backend.
2. **Startup scheduling:** XMRig receives a low-impact control-plane policy.
3. **Windows enforcement:** the manager reapplies affinity, priority, low I/O
   priority, and optional EcoQoS after process creation.
4. **Measured fallback:** the hashrate guard suspends or stops the GPU instance
   when the protected CPU miner remains below its configured floor.

## Core detection

The scanner prefers explicit mining-thread affinity over process affinity:

1. Existing XMRig `--cpu-affinity` bitmask.
2. Explicit affinity entries in its local `cpu` profiles.
3. Explicit affinity entries from `/2/config`.
4. Windows process affinity as a conservative fallback.

Automatic `-1` XMRig thread affinities do not name exact logical CPUs and are not
invented by the scanner.
