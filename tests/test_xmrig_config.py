from __future__ import annotations

import unittest

from app.cuda_tuning import CudaRxProfile
from app.xmrig_config import patch_xmrig_config, slugify


class XmrigConfigTests(unittest.TestCase):
    def test_slugify(self) -> None:
        self.assertEqual(slugify("My GPU / Miner"), "My-GPU-Miner")

    def test_existing_mode_preserves_backends_without_strict_mode(self) -> None:
        source = {
            "cpu": {"enabled": False},
            "cuda": {"enabled": True},
            "opencl": {"enabled": False},
        }
        patched = patch_xmrig_config(
            source,
            instance_name="test",
            instance_id="abc",
            api_port=12345,
            backend="existing",
            keep_cpu=True,
            hard_gpu_only=False,
        )
        self.assertFalse(patched["cpu"]["enabled"])
        self.assertTrue(patched["cuda"]["enabled"])
        self.assertEqual(patched["http"]["port"], 12345)
        self.assertEqual(patched["http"]["host"], "127.0.0.1")

    def test_cuda_strict_mode_disables_cpu_side_setup(self) -> None:
        patched = patch_xmrig_config(
            {"randomx": {"rdmsr": True, "wrmsr": True}, "cuda": {"rx/0": [{"index": 0, "dataset_host": True}]}},
            instance_name="cuda-test",
            instance_id="abc",
            api_port=18080,
            backend="cuda",
            keep_cpu=True,
            hard_gpu_only=True,
            force_dataset_vram=True,
            cuda_devices="0",
        )
        self.assertFalse(patched["cpu"]["enabled"])
        self.assertFalse(patched["cpu"]["huge-pages"])
        self.assertEqual(patched["cpu"]["memory-pool"], 0)
        self.assertTrue(patched["cuda"]["enabled"])
        self.assertFalse(patched["opencl"]["enabled"])
        self.assertFalse(patched["randomx"]["rdmsr"])
        self.assertFalse(patched["randomx"]["wrmsr"])
        self.assertFalse(patched["randomx"]["numa"])
        self.assertFalse(patched["cuda"]["rx/0"][0]["dataset_host"])

    def test_pseudo_cuda_disables_real_cpu_backend(self) -> None:
        patched = patch_xmrig_config(
            {},
            instance_name="pseudo",
            instance_id="abc",
            api_port=18081,
            backend="pseudo_cuda",
            keep_cpu=False,
            hard_gpu_only=True,
        )
        self.assertFalse(patched["cpu"]["enabled"])
        self.assertTrue(patched["cuda"]["enabled"])

    def test_hybrid_cuda_enables_real_cpu_and_cuda(self) -> None:
        patched = patch_xmrig_config(
            {},
            instance_name="hybrid",
            instance_id="abc",
            api_port=18082,
            backend="hybrid_cuda",
            keep_cpu=True,
            hard_gpu_only=False,
        )
        self.assertTrue(patched["cpu"]["enabled"])
        self.assertTrue(patched["cuda"]["enabled"])

    def test_explicit_cuda_profile_and_protected_dataset_init(self) -> None:
        profile = CudaRxProfile(
            index=0,
            threads=32,
            blocks=95,
            bfactor=0,
            bsleep=0,
            affinity=23,
        )
        patched = patch_xmrig_config(
            {},
            instance_name="max-gpu",
            instance_id="abc",
            api_port=18083,
            backend="pseudo_cuda",
            keep_cpu=False,
            hard_gpu_only=True,
            randomx_init_threads=1,
            cuda_rx_profile=profile,
            force_dataset_vram=True,
        )
        self.assertEqual(patched["randomx"]["init"], 1)
        self.assertEqual(patched["cpu"]["priority"], 0)
        self.assertEqual(patched["cuda"]["rx"][0]["blocks"], 95)
        self.assertEqual(patched["cuda"]["rx/0"][0]["bfactor"], 0)
        self.assertFalse(patched["cuda"]["rx"][0]["dataset_host"])



if __name__ == "__main__":
    unittest.main()
