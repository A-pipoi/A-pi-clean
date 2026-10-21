import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant import api_server
from quant.data.schema import Bar


class FeatureMaterializationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = [
            Bar("600000.SH", date(2024, 1, 2), 10, 10.2, 9.9, 10.1, 1000),
            Bar("600000.SH", date(2024, 1, 3), 10.1, 10.3, 10, 10.2, 1100),
        ]

    def test_only_pit_universe_can_be_materialized(self) -> None:
        with (
            patch.object(api_server, "TushareDailyAdapter") as adapter,
            self.assertRaisesRegex(ValueError, "仅接受严格 PIT"),
        ):
            api_server._materialize_pit_feature_request({"universe_mode": "fixed"})
        adapter.assert_not_called()

    def test_materialization_uses_real_complete_pit_bars_without_budget_or_orders(
        self,
    ) -> None:
        snapshot = SimpleNamespace(
            version="tushare:pro_bar:raw:test-hash",
            metadata={"data_source": "tushare_live", "latest_trade_date": "2024-01-03"},
        )
        adapter_instance = Mock()
        adapter_instance.daily_bars.return_value = self.bars
        adapter_instance.snapshot.return_value = snapshot
        store = Mock()
        store.status.return_value = {
            "status": "available",
            "rows": 7,
            "max_feature_date": None,
            "warmup": {"completed_shards": 0, "pending_months": []},
        }
        store.get_warmup_shard.return_value = None
        store.materialize.return_value = {
            "status": "available",
            "rows": 7,
            "written": 7,
        }
        universe = {
            "symbols": ["600000.SH"],
            "version": "membership-v1",
            "metadata": {"mode": "pit_historical_index", "pit_membership": True},
            "start_date": date(2023, 9, 4),
            "end_date": date(2024, 1, 3),
        }

        with (
            patch.object(
                api_server, "_resolve_backtest_universe", return_value=universe
            ) as resolve,
            patch.object(
                api_server, "TushareDailyAdapter", return_value=adapter_instance
            ) as adapter,
            patch.object(api_server, "_require_complete_universe") as require_complete,
            patch.object(
                api_server, "_filter_backtest_universe", return_value=self.bars
            ),
            patch.object(
                api_server, "_scoped_dataset_version", return_value="scoped-version"
            ) as version,
            patch.object(
                api_server,
                "_data_probe_status",
                return_value={"latest_trade_date": "2024-01-03"},
            ),
            patch.object(api_server, "FEATURE_STORE", store),
        ):
            result = api_server._materialize_pit_feature_request(
                {
                    "universe_mode": "pit_index",
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-03",
                    "catch_up": False,
                    "resume": False,
                }
            )

        resolve.assert_called()
        self.assertEqual(
            resolve.call_args.args[0]["universe_index_code"],
            api_server.FACTOR_MINING_UNIVERSE_INDEX_CODE,
        )
        adapter.assert_called()
        self.assertFalse(adapter.call_args.kwargs["fetch_limits"])
        self.assertTrue(adapter.call_args.kwargs["force_refresh"])
        require_complete.assert_called()
        version.assert_called_with(snapshot.version, "membership-v1")
        # One January 2024 shard
        store.materialize.assert_called()
        kwargs = store.materialize.call_args.kwargs
        self.assertEqual(kwargs.get("feature_start"), date(2024, 1, 2))
        self.assertEqual(kwargs.get("feature_end"), date(2024, 1, 3))
        self.assertEqual(result["materialization"]["written"], 7)
        self.assertEqual(result["effective_feature_start"], "2024-01-02")
        self.assertFalse(result["research_budget_charged"])
        self.assertEqual(result["orders_created"], 0)
        self.assertEqual(result["source"], "tushare_live")


if __name__ == "__main__":
    unittest.main()
