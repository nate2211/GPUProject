# Architecture

```text
PyQt6 MainWindow
  |
  +-- GPU Monitor
  |     +-- NVML
  |     +-- Optional DXGI C++ DLL
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

## Isolation boundaries

Each managed instance receives:

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

## Protection model

Protection has four layers:

1. **Workload separation:** the child receives `--no-cpu` and a GPU backend.
2. **Startup scheduling:** XMRig receives a CPU-affinity mask and idle-priority flag
   in its own launch arguments.
3. **Windows enforcement:** the manager reapplies affinity, priority, low I/O
   priority, and EcoQoS after process creation.
4. **Measured fallback:** the hashrate guard suspends or stops the GPU instance when
   the protected CPU miner remains below its configured floor.

## Core detection

The scanner prefers explicit mining-thread affinity over process affinity:

1. Existing XMRig `--cpu-affinity` bitmask.
2. Explicit affinity entries in its local `cpu` profiles.
3. Explicit affinity entries from `/2/config`.
4. Windows process affinity as a conservative fallback.

Automatic `-1` XMRig thread affinities do not name exact logical CPUs and are not
invented by the scanner.

## Why no zero-impact guarantee exists

A GPU miner still needs host threads and memory traffic. CPU and GPU may also share
power and cooling limits. Affinity prevents direct scheduler overlap but cannot
prevent platform-level thermal or power coupling. The hashrate guard therefore
measures the outcome rather than assuming isolation is perfect.

## Non-blocking isolation policy

GPU-only enforcement is part of the generated XMRig configuration and command line. Windows affinity, priority, I/O priority, EcoQoS, and GUI pinning are scheduling hints. Failure to apply a hint is logged but never terminates XMRig.
