# CUDA performance and CPU-miner isolation

## What the original log means

`--no-cpu` correctly disables XMRig's CPU hashing backend. These lines are not CPU mining:

```text
cpu      use argon2 implementation AVX2
randomx  init dataset algo rx/0 (24 threads)
randomx  allocated 2336 MB (2080+256)
```

They are one-time RandomX dataset construction and control-plane setup required before the CUDA worker starts. Actual CPU mining would show a CPU profile and CPU `READY threads` line. Version 4.1 treats those cases separately.

## Protected startup

For hard GPU-only launches, the generated configuration now includes:

```json
{
  "randomx": {
    "init": 1,
    "rdmsr": false,
    "wrmsr": false,
    "numa": false,
    "1gb-pages": false
  },
  "cpu": {
    "enabled": false,
    "priority": 0,
    "max-threads-hint": 1,
    "huge-pages": false,
    "huge-pages-jit": false,
    "memory-pool": 0,
    "yield": true
  }
}
```

The process is also pinned to a reserved logical CPU. The CUDA RX profile's `affinity` field uses the same CPU. This prevents the 24-thread dataset initialization burst in the supplied log. The tradeoff is a slower one-time dataset build.

## Native isolation DLL

`process_isolation_runtime.dll` is loaded by Python when available. Immediately after XMRig is created, it:

1. Briefly suspends the child's threads.
2. Applies the selected process affinity.
3. Applies Idle, Below Normal, Normal, Above Normal, or High priority.
4. Optionally enables Windows EcoQoS.
5. Attaches a Job Object carrying persistent affinity and priority limits.
6. Resumes the child.

If the DLL cannot load or Windows rejects the Job Object, the existing psutil isolation path remains active and the warning is written to the instance log.

## CUDA presets

| Preset | bfactor | bsleep | Target |
|---|---:|---:|---|
| Maximum | 0 | 0 | Highest GPU throughput; least desktop responsiveness |
| Fast | 2 | 0 | Near-maximum throughput with smaller kernels |
| Balanced | 4 | 10 | Moderate responsiveness and power |
| Compatibility | 6 | 25 | Similar to the conservative profile in the supplied log |
| Existing | unchanged | unchanged | Preserve source config or XMRig autoconfiguration |

For an RTX 3070 Ti reporting 7,107 MiB free VRAM and 48 SMs, the default 1,024 MiB reserve produces approximately:

```text
threads=32
blocks=95
intensity=3040
scratchpad memory≈6080 MiB
bfactor=0
bsleep=0
dataset_host=false
```

The supplied XMRig autoconfiguration used 2,432 intensity and about 4,864 MiB. The new profile exposes more concurrent RandomX lanes, but the exact hashrate improvement depends on driver, clocks, thermals, power limit, and CUDA plugin stability.

## Stability fallback

When `GPU #0 COMPUTE ERROR` appears:

1. Reduce CUDA blocks by 8-16.
2. Change Maximum to Fast.
3. Change Fast to Compatibility if errors continue.
4. Keep at least 1,024 MiB of VRAM free for Windows and display workloads.

Do not increase blocks after VRAM is nearly full. A compute error can reduce accepted work to zero even if the displayed intensity is higher.

## Protecting the host CPU miner

The strongest setup is:

- Run the host CPU XMRig with explicit CPU-thread affinities, leaving one logical CPU unused.
- Scan the host miner in the Protection page.
- Auto-pick the unused control CPU.
- Keep the GPU instance at Idle priority.
- Enable the native isolation DLL.
- Capture the CPU miner API baseline and enable the sustained-drop guard.

Scheduler isolation cannot prevent shared PSU, motherboard, memory-controller, or thermal limits. The measured API guard is the final protection layer when GPU load reduces CPU boost clocks.
