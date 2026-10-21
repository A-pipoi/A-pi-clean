import unittest
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch

from quant.api.research_config import sealed_scope
from quant.api.serialization import bar_to_payload, bars_from_payload
from quant.backtest import CostModel, EventDrivenBacktester, Order
from quant.data.adapters import _apply_tushare_signal_adjustment, _TushareRows
from quant.data.adjustments import total_return
from quant.data.schema import Bar, CorporateAction
from quant.factors.expression import build_factor_panel
from quant.factors.store import FactorDefinition, calculate_factor
from tests.helpers import bar
from tests.test_backtest_rules import OneShotStrategy, no_costs


class CostAndAdjustmentTests(unittest.TestCase):
    def test_signal_adjustment_preserves_raw_execution_bars_and_bridges_split(self) -> None:
        first = Bar("000001.SZ", date(2024, 1, 2), 99, 101, 98, 100, 10000, limit_up=110)
        split_day = Bar("000001.SZ", date(2024, 1, 3), 49.5, 50.5, 49, 50, 20000, limit_up=55)
        rows = _TushareRows([
            {"trade_date": "20240102", "adj_factor": 1.0},
            {"trade_date": "20240103", "adj_factor": 2.0},
        ])
        with patch("quant.data.adapters._tushare_http_query", return_value=rows):
            adjusted, _ = _apply_tushare_signal_adjustment([first, split_day], "token")

        self.assertEqual((adjusted[1].open, adjusted[1].close, adjusted[1].limit_up), (49.5, 50, 55))
        self.assertEqual((adjusted[0].signal_close, adjusted[1].signal_close), (100, 100))
        self.assertEqual(adjusted[1].signal_high, 101)
        execution = EventDrivenBacktester(100_000, no_costs()).run(
            adjusted,
            OneShotStrategy({split_day.date: [Order("000001.SZ", "BUY", 100)]}),
        )
        self.assertEqual(execution.fills[0].price, split_day.open)
        self.assertEqual(
            calculate_factor(FactorDefinition("m", "m", "momentum", 1, True, ""), adjusted),
            0,
        )
        panel = build_factor_panel(adjusted)
        self.assertEqual(panel.fields["close"][0][0], 100)
        self.assertEqual(panel.fields["returns"][1][0], 0)
        self.assertEqual(bar_to_payload(first), bar_to_payload(replace(first)))
        self.assertEqual(bars_from_payload([bar_to_payload(adjusted[1])])[0], adjusted[1])
        self.assertEqual(sealed_scope(["000001.SZ"], ""), sealed_scope(["000001.SZ"], "", signal_adjust="raw"))
        self.assertNotEqual(sealed_scope(["000001.SZ"], ""), sealed_scope(["000001.SZ"], "", signal_adjust="adj"))
        self.assertEqual(sealed_scope(["000001.SZ"], ""), sealed_scope(["000001.SZ"], "", corporate_actions="off"))
        self.assertNotEqual(sealed_scope(["000001.SZ"], ""), sealed_scope(["000001.SZ"], "", corporate_actions="on"))

    def test_signal_adjustment_fails_closed_on_missing_factor_date(self) -> None:
        bars = [
            Bar("000001.SZ", date(2024, 1, 2) + timedelta(days=index), 10, 11, 9, 10, 1000)
            for index in range(2)
        ]
        with patch(
            "quant.data.adapters._tushare_http_query",
            return_value=_TushareRows([{"trade_date": "20240102", "adj_factor": 1.0}]),
        ), self.assertRaisesRegex(RuntimeError, "coverage incomplete"):
            _apply_tushare_signal_adjustment(bars, "token")

    def test_raw_bars_keep_existing_factor_behavior_without_signal_fields(self) -> None:
        bars = [
            Bar("A", date(2024, 1, 2) + timedelta(days=index), 10, max(11, close), 9, close, 1000)
            for index, close in enumerate((10, 12))
        ]
        definition = FactorDefinition("m", "m", "momentum", 1, True, "")
        self.assertIsNone(bars[0].signal_close)
        factor_value = calculate_factor(definition, bars)
        self.assertIsNotNone(factor_value)
        self.assertAlmostEqual(factor_value, 0.2)
        self.assertEqual(build_factor_panel(bars).fields["close"][1][0], 12)

    def test_dividend_total_return_matches_manual_calculation(self) -> None:
        action = CorporateAction("A", bar("A", 1).date, cash_dividend=1.0)
        self.assertAlmostEqual(total_return(10.0, 10.0, action), 10.0 / 9.0 - 1.0)

    def test_cost_difference_equals_recorded_cost(self) -> None:
        bars = [bar("A", 0, open_=10), bar("A", 1, open_=11, close=11)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 10)], bars[1].date: [Order("A", "SELL", 10)]})
        free = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        costly = EventDrivenBacktester(100_000, CostModel(max_participation=0.10)).run(bars, strategy)
        self.assertAlmostEqual(free.final_equity - costly.final_equity, costly.total_cost, places=8)
