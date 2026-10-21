import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from prepare_qlib_data import (
    adjusted_row,
    contiguous_intervals,
    sealed_cutoff,
    unique_rows,
)


class ExportTests(unittest.TestCase):
    def test_adjustment_preserves_raw_price_and_volume_notional(self):
        row = {"open": 10, "high": 12, "low": 9, "close": 11, "vol": 50, "amount": 52}
        values = adjusted_row(row, 4, 20)
        self.assertAlmostEqual(values["close"] / values["factor"], 11)
        self.assertAlmostEqual(values["volume"] * values["factor"], 5000)
        self.assertEqual(values["money"], 52000)
        self.assertAlmostEqual(values["vwap"], values["money"] / values["volume"])

    def test_suspended_row_is_all_nan(self):
        row = {"open": 10, "high": 12, "low": 9, "close": 11, "vol": 0, "amount": 0}
        self.assertTrue(all(math.isnan(v) for v in adjusted_row(row, 4, 20).values()))

    def test_bad_adjustment_rejected(self):
        row = {"open": 10, "high": 12, "low": 9, "close": 11, "vol": 10, "amount": 10}
        for factor in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                adjusted_row(row, factor, 20)

    def test_member_reentry_keeps_separate_intervals(self):
        self.assertEqual(contiguous_intervals([1, 2, 5, 6, 6]), [[1, 2], [5, 6]])

    def test_archived_windows_remain_protected_without_payload_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "CREATE TABLE sealed_tests(sealed_start TEXT, archived_at TEXT)"
                )
                db.executemany(
                    "INSERT INTO sealed_tests VALUES (?,?)",
                    [("2026-07-02", "archived"), ("2026-07-07", None)],
                )
                db.commit()
            self.assertEqual(sealed_cutoff(path), "2026-07-02")

    def test_conflicting_revisions_rejected_and_future_rows_not_selected(self):
        with sqlite3.connect(":memory:") as db:
            db.execute(
                "CREATE TABLE ts_daily(ts_code TEXT,period_date TEXT,payload_json TEXT)"
            )
            db.executemany(
                "INSERT INTO ts_daily VALUES (?,?,?)",
                [
                    ("A", "20260701", '{"trade_date":"20260701","close":1}'),
                    ("A", "20260702", '{"trade_date":"20260702","close":999}'),
                ],
            )
            self.assertEqual(
                list(unique_rows(db, "ts_daily", "A", "2026-07-01", "2026-07-01")),
                ["20260701"],
            )
            db.execute(
                "INSERT INTO ts_daily VALUES (?,?,?)",
                ("A", "20260701", '{"trade_date":"20260701","close":2}'),
            )
            with self.assertRaises(ValueError):
                unique_rows(db, "ts_daily", "A", "2026-07-01", "2026-07-01")


if __name__ == "__main__":
    unittest.main()
