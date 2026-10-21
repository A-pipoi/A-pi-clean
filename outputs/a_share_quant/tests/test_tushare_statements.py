import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from quant.data.statements import fetch_tushare_statements


class _Frame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        return list(self.rows)


class TushareStatementsTests(unittest.TestCase):
    def test_three_statements_use_announcement_date_point_in_time(self) -> None:
        calls = []

        def query(_token, api_name, _params, *, fields=""):
            calls.append((api_name, fields))
            return _Frame([
                {"ts_code": "000001.SZ", "ann_date": "2024-04-25", "end_date": "2024-03-31", "report_type": "1", "total_revenue": 100},
                {"ts_code": "000001.SZ", "ann_date": "2025-04-25", "end_date": "2025-03-31", "report_type": "1", "total_revenue": 200},
            ])

        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.statements._tushare_http_query", side_effect=query
        ):
            grouped, metadata = fetch_tushare_statements(
                ["000001.SZ"],
                as_of=date(2024, 12, 31),
                token="test-token",
                cache_dir=Path(directory),
            )

        self.assertEqual(set(metadata["statement_types"]), {"income", "balancesheet", "cashflow"})
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(fields == "" for _, fields in calls))
        for statement in ("income", "balancesheet", "cashflow"):
            row = grouped["000001.SZ"][statement]
            self.assertIsNotNone(row)
            self.assertEqual(row["announcement_date"], "2024-04-25")
            self.assertEqual(row["report_date"], "2024-03-31")
            self.assertEqual(row["source"], f"Tushare:{statement if statement != 'balancesheet' else 'balancesheet'}")

    def test_missing_announcement_date_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.statements._tushare_http_query",
            return_value=_Frame([{"ts_code": "600000.SH", "end_date": "2024-03-31", "total_assets": 10}]),
        ):
            grouped, metadata = fetch_tushare_statements(
                ["600000.SH"],
                as_of=date(2024, 12, 31),
                token="test-token",
                cache_dir=Path(directory),
            )

        self.assertIsNone(grouped["600000.SH"]["income"])
        self.assertEqual(metadata["missing_symbols"], ["600000.SH"])


if __name__ == "__main__":
    unittest.main()
