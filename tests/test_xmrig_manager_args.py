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

    def test_explicit_profile_keeps_bfactor_out_of_cli(self) -> None:
        spec = InstanceSpec(
            name="gpu",
            xmrig_path=Path("xmrig.exe"),
            source_config=Path("config.json"),
            backend="cuda",
            hard_gpu_only=True,
            cuda_loader=Path("xmrig-cuda.dll"),
            cuda_tune_profile="max",
            cuda_bfactor_hint=0,
            cuda_bsleep_hint=0,
        )
        args = build_xmrig_arguments(spec, Path("instance/config.json"))
        self.assertFalse(any(arg.startswith("--cuda-bfactor-hint") for arg in args))
        self.assertFalse(any(arg.startswith("--cuda-bsleep-hint") for arg in args))

    def test_existing_profile_uses_cli_hints(self) -> None:
        spec = InstanceSpec(
            name="gpu",
            xmrig_path=Path("xmrig.exe"),
            source_config=Path("config.json"),
            backend="cuda",
            hard_gpu_only=True,
            cuda_loader=Path("xmrig-cuda.dll"),
            cuda_tune_profile="existing",
            cuda_bfactor_hint=2,
            cuda_bsleep_hint=5,
        )
        args = build_xmrig_arguments(spec, Path("instance/config.json"))
        self.assertIn("--cuda-bfactor-hint=2", args)
        self.assertIn("--cuda-bsleep-hint=5", args)



if __name__ == "__main__":
    unittest.main()
