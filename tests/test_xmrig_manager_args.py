from __future__ import annotations

import unittest
from pathlib import Path

from app.models import InstanceSpec
from app.xmrig_launch import build_xmrig_arguments


class XmrigArgumentTests(unittest.TestCase):
    def test_pseudo_cuda_arguments_are_explicit_and_absolute(self) -> None:
        spec = InstanceSpec(
            name="gpu",
            xmrig_path=Path("xmrig.exe"),
            source_config=Path("config.json"),
            backend="pseudo_cuda",
            hard_gpu_only=True,
            cuda_loader=Path("xmrig-cuda.dll"),
            cuda_devices="0",
            cpu_affinity=[23],
            priority="idle",
        )
        args = build_xmrig_arguments(spec, Path("instance/config.json"))
        self.assertIn("--cuda", args)
        self.assertIn("--no-cpu", args)
        self.assertIn("--cuda-devices=0", args)
        self.assertTrue(any(arg.startswith("--cuda-loader=") for arg in args))
        config_index = args.index("--config") + 1
        self.assertTrue(Path(args[config_index]).is_absolute())
        self.assertFalse(any(arg.startswith("--cpu-affinity") for arg in args))
        self.assertFalse(any(arg.startswith("--cpu-priority") for arg in args))

    def test_hybrid_cuda_keeps_cpu_available(self) -> None:
        spec = InstanceSpec(
            name="hybrid",
            xmrig_path=Path("xmrig.exe"),
            source_config=Path("config.json"),
            backend="hybrid_cuda",
            hard_gpu_only=False,
            keep_cpu=True,
            cuda_loader=Path("xmrig-cuda.dll"),
            cuda_devices="0",
        )
        args = build_xmrig_arguments(spec, Path("instance/config.json"))
        self.assertIn("--cuda", args)
        self.assertNotIn("--no-cpu", args)


if __name__ == "__main__":
    unittest.main()
