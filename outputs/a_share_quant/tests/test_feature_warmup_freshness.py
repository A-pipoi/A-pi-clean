"""Warm-up freshness uses coverage tip, not lagging-symbol min."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from quant.api.feature_materialization import coverage_latest_trade_date


class CoverageLatestTests(unittest.TestCase):
    def test_prefers_max_of_per_symbol_dates_over_lagging_min(self):
        metadata = {
            "latest_trade_date": "2026-09-30",  # adapter min
            "latest_trade_dates": {
                "600000.SH": "2026-10-14",
                "000001.SZ": "2026-09-30",
            },
        }
        self.assertEqual(coverage_latest_trade_date(metadata), "2026-10-14")

    def test_falls_back_to_bars_and_single_field(self):
        bars = [SimpleNamespace(date=date(2026, 10, 13))]
        self.assertEqual(
            coverage_latest_trade_date({"latest_trade_date": "2026-10-10"}, bars),
            "2026-10-13",
        )


if __name__ == "__main__":
    unittest.main()
