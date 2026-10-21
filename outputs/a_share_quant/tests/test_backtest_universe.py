import unittest
from datetime import date
from unittest.mock import patch

from quant import api_server
from quant.data.schema import Bar


class BacktestUniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = {
            "ok": True,
            "source": "tushare:index_weight",
            "index_code": "000300.SH",
            "index_name": "沪深300",
            "first_snapshot": "2024-02-01",
            "last_snapshot": "2024-04-01",
            "snapshot_count": 2,
            "data_version": "members-v1",
            "stale": False,
            "snapshots": [
                {"trade_date": "2024-02-01", "members": [{"ts_code": "600000.SH"}]},
                {"trade_date": "2024-04-01", "members": [{"ts_code": "000001.SZ"}]},
            ],
        }

    def _resolve(self, *, listing_metadata=None, delisting_dates=None, history=None):
        listing_dates = {
            "600000.SH": date(2000, 1, 1),
            "000001.SZ": date(2024, 3, 15),
            "601989.SH": date(2009, 12, 16),
        }
        metadata = {
            "data_version": "listing-v1",
            "stale": False,
            "delisting_dates": delisting_dates or {},
        }
        if listing_metadata:
            metadata.update(listing_metadata)
        with (
            patch.object(api_server, "fetch_tushare_index_member_history", return_value=history or self.history),
            patch.object(
                api_server,
                "fetch_tushare_listing_dates",
                return_value=(listing_dates, metadata),
            ),
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
        ):
            return api_server._resolve_backtest_universe(
                {"universe_mode": "pit_index"}, date(2024, 2, 20), date(2024, 5, 20)
            )

    def test_pit_universe_uses_index_history_and_records_data_versions(self) -> None:
        universe = self._resolve()
        self.assertEqual(universe["symbols"], ["000001.SZ", "600000.SH"])
        self.assertTrue(universe["metadata"]["pit_membership"])
        self.assertEqual(universe["metadata"]["member_data_version"], "members-v1")
        self.assertEqual(universe["metadata"]["listing_data_version"], "listing-v1")
        self.assertEqual(universe["pit_data"]["schema_version"], "backtest-historical-universe-v1")

    def test_membership_and_listing_age_are_checked_on_each_bar_date(self) -> None:
        universe = self._resolve()
        bars = [
            Bar("600000.SH", date(2024, 3, 20), 10, 10, 10, 10, 1000),
            Bar("000001.SZ", date(2024, 3, 20), 10, 10, 10, 10, 1000),
            Bar("000001.SZ", date(2024, 4, 15), 10, 10, 10, 10, 1000),
            Bar("000001.SZ", date(2024, 5, 20), 10, 10, 10, 10, 1000),
            Bar("600000.SH", date(2024, 5, 20), 10, 10, 10, 10, 1000),
        ]
        selected = api_server._filter_backtest_universe(bars, universe)
        self.assertEqual([(bar.symbol, bar.date) for bar in selected], [("600000.SH", date(2024, 3, 20)), ("000001.SZ", date(2024, 5, 20))])

    def test_pit_universe_fails_closed_for_stale_listing_dates(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "上市/退市日期缓存过期"):
            self._resolve(listing_metadata={"data_version": "listing-old", "stale": True})

    def test_delisted_before_window_is_not_fetched_but_remains_in_pit_history(self) -> None:
        history = {
            **self.history,
            "snapshots": [
                {"trade_date": "2024-02-01", "members": [{"ts_code": "600000.SH"}, {"ts_code": "601989.SH"}]},
                {"trade_date": "2024-04-01", "members": [{"ts_code": "000001.SZ"}]},
            ],
        }
        universe = self._resolve(
            history=history,
            delisting_dates={"601989.SH": date(2024, 2, 15)},
        )

        self.assertEqual(universe["symbols"], ["000001.SZ", "600000.SH"])
        self.assertEqual(universe["metadata"]["historical_membership_symbol_count"], 3)
        self.assertEqual(universe["metadata"]["delisted_before_window_symbols"], ["601989.SH"])
        self.assertIn(
            "601989.SH",
            {member["ts_code"] for member in universe["pit_data"]["snapshots"][0]["members"]},
        )
        self.assertEqual(universe["pit_data"]["delisting_dates"]["601989.SH"], "2024-02-15")

    def test_bar_after_delisting_is_excluded_even_if_still_in_index_snapshot(self) -> None:
        universe = self._resolve(delisting_dates={"000001.SZ": date(2024, 5, 20)})
        bars = [
            Bar("000001.SZ", date(2024, 5, 20), 10, 10, 10, 10, 1000),
            Bar("000001.SZ", date(2024, 5, 21), 10, 10, 10, 10, 1000),
        ]

        selected = api_server._filter_backtest_universe(bars, universe)

        self.assertEqual([bar.date for bar in selected], [date(2024, 5, 20)])

    def test_only_preregistered_index_can_be_used(self) -> None:
        with self.assertRaisesRegex(ValueError, "固定为 000300.SH"):
            api_server._resolve_backtest_universe(
                {"universe_mode": "pit_index", "universe_index_code": "000905.SH"},
                date(2024, 1, 2),
                date(2024, 9, 1),
            )

    def test_fixed_pool_is_explicitly_marked_non_pit(self) -> None:
        universe = api_server._resolve_backtest_universe(
            {"universe_mode": "fixed", "symbols": ["600036.SH"]}, None, None
        )
        self.assertFalse(universe["metadata"]["pit_membership"])
        self.assertIn("前视选择偏差", universe["metadata"]["note"])


if __name__ == "__main__":
    unittest.main()
