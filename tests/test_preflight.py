from __future__ import annotations

import unittest

from app.xmrig_preflight import evaluate_preflight_output


class PreflightTests(unittest.TestCase):
    def test_cuda_failure_is_rejected(self) -> None:
        result = evaluate_preflight_output(
            0,
            "* CUDA disabled (failed to load CUDA plugin)",
            requires_cuda=True,
        )
        self.assertFalse(result.ok)

    def test_clean_dry_run_passes(self) -> None:
        result = evaluate_preflight_output(
            0,
            "configuration OK",
            requires_cuda=True,
        )
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
