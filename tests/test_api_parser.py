from __future__ import annotations

import unittest

from app.xmrig_api_data import parse_summary


class ApiParserTests(unittest.TestCase):
    def test_parse_summary(self) -> None:
        metrics = parse_summary(
            {
                "uptime": 42,
                "hashrate": {"total": [100.5, 90.0, 80.0]},
                "results": {"shares_good": 7, "shares_total": 8},
                "connection": {"pool": "127.0.0.1:3333"},
            }
        )
        self.assertEqual(metrics.hashrate_10s, 100.5)
        self.assertEqual(metrics.shares_good, 7)
        self.assertEqual(metrics.rejected, 1)
        self.assertEqual(metrics.uptime_seconds, 42)


if __name__ == "__main__":
    unittest.main()
