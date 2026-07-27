from __future__ import annotations

import unittest

from app.isolation import (
    ExternalXmrigProcess,
    assess_isolation,
    cpu_list_to_mask,
    cpu_mask_to_list,
    extract_cpu_mining_cores,
    format_cpu_list,
    normalize_summary_url,
    parse_cpu_list,
)


class IsolationTests(unittest.TestCase):
    def test_parse_and_format_cpu_list(self) -> None:
        self.assertEqual(parse_cpu_list("0,2-4", cpu_count=8), [0, 2, 3, 4])
        self.assertEqual(format_cpu_list([0, 2, 3, 4]), "0,2-4")

    def test_mask_round_trip(self) -> None:
        cores = [0, 2, 5, 23]
        self.assertEqual(cpu_mask_to_list(hex(cpu_list_to_mask(cores))), cores)

    def test_extract_explicit_xmrig_cpu_profiles(self) -> None:
        config = {
            "cpu": {
                "enabled": True,
                "max-threads-hint": 75,
                "rx": [0, 2, 4, -1],
                "cn": [
                    {"intensity": 1, "affinity": 6},
                    {"intensity": 1, "affinity": 8},
                ],
            }
        }
        self.assertEqual(extract_cpu_mining_cores(config), [0, 2, 4, 6, 8])

    def test_invalid_cpu_index(self) -> None:
        with self.assertRaises(ValueError):
            parse_cpu_list("8", cpu_count=8)

    def test_normalize_api_url(self) -> None:
        self.assertEqual(
            normalize_summary_url("127.0.0.1:18080"),
            "http://127.0.0.1:18080/2/summary",
        )

    def test_non_overlapping_assessment_uses_mining_cores(self) -> None:
        protected = ExternalXmrigProcess(
            pid=10,
            name="xmrig.exe",
            executable="xmrig.exe",
            command_line="xmrig.exe",
            cpu_percent=0.0,
            affinity=[0, 1, 2, 3, 4, 5],
            mining_cores=[0, 1, 2, 3],
            mining_core_source="local config profiles",
            api_url="",
            config_path="",
        )
        result = assess_isolation(
            backend="cuda",
            hard_gpu_only=True,
            selected_cores=[4],
            protected_processes=[protected],
            guard_ready=True,
        )
        self.assertEqual(result.level, "pass")


if __name__ == "__main__":
    unittest.main()
