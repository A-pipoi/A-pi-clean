import tempfile
import unittest
from datetime import UTC, date, datetime
from unittest.mock import patch

from quant.backtest import CostModel
from quant.execution import PaperStore
from tests.helpers import bar


class PaperStoreTests(unittest.TestCase):
    def test_order_id_uses_beijing_time_not_host_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            utc_now = datetime(2026, 9, 28, 8, 6, tzinfo=UTC)
            with patch("quant.execution.store.datetime") as datetime_mock:
                datetime_mock.now.side_effect = lambda tz=None: utc_now.astimezone(tz) if tz else utc_now.replace(tzinfo=None)
                order = store.create_order("000001.SZ", "BUY", 100)
            store.close()

            self.assertTrue(order["order_id"].startswith("AP-20260928-160600-"))

    def test_position_quote_date_comes_from_persisted_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3", initial_cash=100_000)
            store.create_order("000001.SZ", "BUY", 100)
            store.execute_order(
                store.state()["orders"][0]["order_id"],
                bar("000001.SZ", 0, volume=10_000),
                CostModel(max_participation=0.10),
            )
            store.record_quotes([bar("000001.SZ", 1, close=10.5)])
            self.assertEqual(store.state()["positions"][0]["quote_date"], "2024-01-03")
            self.assertIsNone(store.state({"000001.SZ": 11.0})["positions"][0]["quote_date"])
            store.close()

    def test_persists_orders_positions_and_enforces_t_plus_one(self) -> None:
        no_cost = CostModel(
            commission_rate=0,
            min_commission=0,
            stamp_tax_rate=0,
            transfer_fee_rate=0,
            fixed_slippage_bps=0,
            impact_bps=0,
            max_participation=0.10,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/paper.sqlite3"
            store = PaperStore(path, initial_cash=100_000)
            buy = store.create_order("000001.SZ", "BUY", 100)
            filled_buy = store.execute_order(buy["order_id"], bar("000001.SZ", 0, volume=1_000), no_cost)
            self.assertEqual(filled_buy["status"], "FILLED")

            same_day_sell = store.create_order("000001.SZ", "SELL", 100)
            rejected = store.execute_order(same_day_sell["order_id"], bar("000001.SZ", 0, volume=1_000), no_cost)
            self.assertEqual(rejected["status"], "REJECTED")
            self.assertEqual(rejected["reject_reason"], "t_plus_one_or_no_position")

            next_day_sell = store.create_order("000001.SZ", "SELL", 100)
            filled_sell = store.execute_order(next_day_sell["order_id"], bar("000001.SZ", 1, volume=1_000), no_cost)
            self.assertEqual(filled_sell["status"], "FILLED")

            reopened = PaperStore(path, initial_cash=100_000)
            state = reopened.state({"000001.SZ": 10.0})
            self.assertEqual(state["positions"], [])
            self.assertEqual(len(state["orders"]), 3)
            reopened.close()
            store.close()

    def test_close_generated_order_is_eligible_only_on_a_later_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            signal_day = date(2026, 9, 24)
            next_day = date(2026, 9, 25)
            order = store.create_order("000001.SZ", "BUY", 100, signal_date=signal_day)
            self.assertEqual(order["signal_date"], signal_day.isoformat())
            self.assertEqual(store.pending_strategy_orders(signal_day), [])
            eligible = store.pending_strategy_orders(next_day)
            self.assertEqual([item["order_id"] for item in eligible], [order["order_id"]])
            store.close()

    def test_cycle_marker_is_persistent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/paper.sqlite3"
            store = PaperStore(path)
            cycle_date = date(2026, 1, 5)
            self.assertFalse(store.has_cycle(cycle_date))
            store.record_cycle(
                cycle_date,
                strategy="cross_sectional_momentum",
                generated_orders=2,
                executed_orders=2,
            )
            self.assertTrue(store.has_cycle(cycle_date))
            store.close()

            reopened = PaperStore(path)
            self.assertTrue(reopened.has_cycle(cycle_date))
            reopened.reset(1_000_000)
            self.assertFalse(reopened.has_cycle(cycle_date))
            reopened.close()

    def test_rebalance_anchor_survives_restart_and_legacy_cycles_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/paper.sqlite3"
            store = PaperStore(path)
            cycle_date = date(2026, 9, 28)
            store.record_cycle(
                cycle_date,
                strategy="baseline",
                generated_orders=0,
                executed_orders=0,
            )
            self.assertEqual(store.last_rebalance_date("baseline", 5), cycle_date)

            next_rebalance = date(2026, 10, 12)
            store.set_last_rebalance_date("baseline", 5, next_rebalance)
            store.close()

            reopened = PaperStore(path)
            self.assertEqual(reopened.last_rebalance_date("baseline", 5), next_rebalance)
            reopened.reset()
            self.assertIsNone(reopened.last_rebalance_date("baseline", 5))
            reopened.close()

    def test_cycle_keeps_factor_snapshot_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            cycle_date = date(2026, 1, 6)
            definitions = [{"key": "builtin_momentum_20", "window": 20, "metric": "momentum"}]
            self.assertTrue(
                store.claim_cycle(
                    cycle_date,
                    strategy="baseline",
                    factor_pipeline_version="factor-pipeline-v1",
                    factor_keys=["builtin_momentum_20"],
                    factor_definitions=definitions,
                )
            )
            store.record_cycle(
                cycle_date,
                strategy="baseline",
                generated_orders=1,
                executed_orders=1,
                factor_pipeline_version="factor-pipeline-v2",
                factor_keys=["changed_after_claim"],
                factor_definitions=[{"key": "changed_after_claim"}],
            )
            cycle = store.recent_cycles(1)[0]
            self.assertEqual(cycle["factor_pipeline_version"], "factor-pipeline-v1")
            self.assertEqual(cycle["factor_keys"], ["builtin_momentum_20"])
            self.assertEqual(cycle["factor_definitions"], definitions)
            store.close()


if __name__ == "__main__":
    unittest.main()
