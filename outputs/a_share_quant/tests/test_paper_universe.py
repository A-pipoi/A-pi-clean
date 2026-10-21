import unittest
from datetime import date
from unittest.mock import patch

from quant.data.paper_universe import resolve_paper_universe


class PaperUniverseTests(unittest.TestCase):
    def test_resolver_returns_only_asof_index_members_and_version(self) -> None:
        snapshot = {
            "ok": True,
            "source": "tushare:index_weight",
            "index_code": "000300.SH",
            "index_name": "沪深300",
            "as_of": "2026-09-24",
            "trade_date": "2026-09-23",
            "count": 2,
            "data_version": "snapshot-hash",
            "members": [{"ts_code": "600000.SH"}, {"ts_code": "000001.SZ"}],
        }
        with patch(
            "quant.data.paper_universe.fetch_tushare_index_members",
            return_value=snapshot,
        ):
            resolved = resolve_paper_universe(
                "000300.SH", as_of=date(2026, 9, 24), token="test"
            )
        self.assertEqual(resolved["symbols"], ["000001.SZ", "600000.SH"])
        self.assertEqual(resolved["membership_date"], "2026-09-23")
        self.assertEqual(resolved["data_version"], "snapshot-hash")

    def test_resolver_fails_closed_on_future_snapshot(self) -> None:
        with (
            patch(
                "quant.data.paper_universe.fetch_tushare_index_members",
                return_value={
                    "trade_date": "2026-09-25",
                    "members": [{"ts_code": "600000.SH"}],
                },
            ),
            self.assertRaisesRegex(RuntimeError, "晚于信号日期"),
        ):
            resolve_paper_universe("000300.SH", as_of=date(2026, 9, 24), token="test")


if __name__ == "__main__":
    unittest.main()
