from __future__ import annotations

import unittest

from app.cuda_tuning import NvidiaGpuInfo, parse_cuda_device_indexes, tune_cuda_rx_profile
from app.native_isolation_bridge import affinity_mask


class CudaTuningTests(unittest.TestCase):
    def test_device_indexes(self) -> None:
        self.assertEqual(parse_cuda_device_indexes("0, 2"), [0, 2])
        self.assertEqual(parse_cuda_device_indexes(""), [0])

    def test_rtx_3070_ti_max_profile_uses_vram_guard(self) -> None:
        gpu = NvidiaGpuInfo(
            index=0,
            name="NVIDIA GeForce RTX 3070 Ti",
            total_memory_mib=8192,
            free_memory_mib=7107,
            multiprocessors=48,
        )
        profile = tune_cuda_rx_profile(
            device_index=0,
            gpu=gpu,
            preset="max",
            threads=32,
            blocks_override=0,
            memory_reserve_mib=1024,
            affinity=23,
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.threads, 32)
        self.assertEqual(profile.blocks, 95)
        self.assertEqual(profile.bfactor, 0)
        self.assertEqual(profile.bsleep, 0)
        self.assertEqual(profile.affinity, 23)
        self.assertFalse(profile.dataset_host)
        self.assertLessEqual(profile.memory_mib, 7107 - 1024)

    def test_existing_profile_does_not_force_override(self) -> None:
        profile = tune_cuda_rx_profile(
            device_index=0,
            gpu=None,
            preset="existing",
            threads=32,
            blocks_override=0,
            memory_reserve_mib=1024,
            affinity=-1,
        )
        self.assertIsNone(profile)

    def test_native_affinity_mask(self) -> None:
        self.assertEqual(affinity_mask([0, 3, 3]), 0b1001)


if __name__ == "__main__":
    unittest.main()
