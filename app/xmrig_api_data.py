from __future__ import annotations

from typing import Any

from app.models import InstanceMetrics, nested_get


def parse_summary(data: dict[str, Any]) -> InstanceMetrics:
    totals = nested_get(data, ("hashrate", "total"), [])
    if not isinstance(totals, list):
        totals = []

    def rate(index: int) -> float:
        if index >= len(totals) or totals[index] is None:
            return 0.0
        try:
            return float(totals[index])
        except (TypeError, ValueError):
            return 0.0

    shares_good = int(nested_get(data, ("results", "shares_good"), 0) or 0)
    shares_total = int(nested_get(data, ("results", "shares_total"), 0) or 0)
    rejected = max(0, shares_total - shares_good)

    connection = nested_get(data, ("connection", "pool"), "") or ""
    uptime = int(data.get("uptime", 0) or 0)
    backends = data.get("active", False)
    backend_summary = str(backends) if backends not in (None, "") else ""

    return InstanceMetrics(
        hashrate_10s=rate(0),
        hashrate_60s=rate(1),
        hashrate_15m=rate(2),
        shares_good=shares_good,
        shares_total=shares_total,
        rejected=rejected,
        uptime_seconds=uptime,
        connection=str(connection),
        backend_summary=backend_summary,
    )
