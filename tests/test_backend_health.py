from __future__ import annotations

import unittest

from app.backend_health import classify_xmrig_line


class BackendHealthTests(unittest.TestCase):
    def test_timestamped_cuda_ready_is_detected(self) -> None:
        event = classify_xmrig_line(
            "[2026-07-26 21:32:36.110]  cuda     READY threads 8/8"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "cuda_ready")

    def test_timestamped_cpu_profile_is_detected(self) -> None:
        event = classify_xmrig_line(
            "[2026-07-26 21:32:32.685]  cpu      use profile  rx  (24 threads)"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "cpu_mining")

    def test_cpu_inventory_is_not_mining(self) -> None:
        self.assertIsNone(
            classify_xmrig_line(" * CPU AMD Ryzen 9 5900X 12-Core Processor")
        )

    def test_cuda_disabled_is_detected(self) -> None:
        event = classify_xmrig_line(" * CUDA disabled (failed to load CUDA plugin)")
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "cuda_failed")

    def test_dataset_init_is_not_cpu_mining(self) -> None:
        event = classify_xmrig_line(
            "[2026-07-27 23:43:03.700] randomx init dataset algo rx/0 (1 threads)"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "dataset_init")

    def test_cuda_compute_error_is_detected(self) -> None:
        event = classify_xmrig_line("nvidia GPU #0 COMPUTE ERROR")
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "cuda_compute_error")



if __name__ == "__main__":
    unittest.main()
