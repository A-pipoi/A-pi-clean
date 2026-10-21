import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from quant.data.fundamentals import fetch_tushare_fundamental_factors
from quant.data.index_members import fetch_tushare_index_members


class _Frame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        return list(self.rows)


class TusharePointInTimeTests(unittest.TestCase):
    def test_financial_factor_uses_latest_announced_record_only(self) -> None:
        frame = _Frame([
            {"ts_code": "000001.SZ", "ann_date": "2024-04-25", "end_date": "2024-03-31", "roe": 3.2, "netprofit_yoy": 5.0, "tr_yoy": 6.0, "debt_to_assets": 92.0},
            {"ts_code": "000001.SZ", "ann_date": "2024-10-28", "end_date": "2024-09-30", "roe": 4.1, "netprofit_yoy": 8.0, "tr_yoy": 9.0, "debt_to_assets": 91.0},
            {"ts_code": "000001.SZ", "ann_date": "2025-04-25", "end_date": "2025-03-31", "roe": 5.9, "netprofit_yoy": 12.0, "tr_yoy": 13.0, "debt_to_assets": 90.0},
        ])
        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.fundamentals._tushare_http_query", return_value=frame
        ):
            rows, metadata = fetch_tushare_fundamental_factors(
                ["000001.SZ"],
                as_of=date(2024, 12, 31),
                token="test-token",
                cache_dir=Path(directory),
            )
        self.assertEqual(metadata["source"], "tushare:fina_indicator_vip")
        self.assertEqual({row["announcement_date"] for row in rows}, {"2024-10-28"})
        roe = next(row for row in rows if row["metric"] == "roe")
        self.assertAlmostEqual(roe["value"], 0.041)
        self.assertEqual(roe["available_at"], "2024-10-28T16:00:00+08:00")

    def test_index_weight_selects_latest_snapshot_not_after_cutoff(self) -> None:
        frame = _Frame([
            {"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "2024-06-28", "weight": 1.1},
            {"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "2024-06-28", "weight": 0.9},
            {"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "2024-09-30", "weight": 1.3},
        ])
        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.index_members._tushare_http_query", return_value=frame
        ):
            result = fetch_tushare_index_members(
                "000300.SH",
                as_of=date(2024, 8, 1),
                token="test-token",
                cache_dir=Path(directory),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "tushare:index_weight")
        self.assertEqual(result["trade_date"], "2024-06-28")
        self.assertEqual([row["ts_code"] for row in result["members"]], ["000001.SZ", "600000.SH"])


if __name__ == "__main__":
    unittest.main()
