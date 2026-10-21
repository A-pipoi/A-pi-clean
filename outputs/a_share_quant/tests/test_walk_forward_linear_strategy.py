import math
import unittest
from datetime import date, timedelta

from quant.backtest.engine import PortfolioSnapshot
from quant.data.schema import Bar
from quant.strategies.ml import WalkForwardLinearStrategy


class WalkForwardLinearStrategyTests(unittest.TestCase):
    def test_model_fits_only_available_history_and_emits_lot_sized_orders(self) -> None:
        start = date(2024, 1, 1)
        history = {}
        for offset, symbol in enumerate(("600000.SH", "000001.SZ", "600036.SH")):
            bars = []
            close = 10.0 + offset * 3
            for index in range(180):
                daily_return = 0.001 * math.sin(index / 3 + offset) + 0.0004 * math.cos(index / 7) + 0.0002 * (offset - 1)
                close *= 1 + daily_return
                bars.append(Bar(symbol, start + timedelta(days=index), close, close * 1.01, close * .99, close, 100_000))
            history[symbol] = bars
        observed_history = {symbol: bars[:-1] for symbol, bars in history.items()}
        portfolio = PortfolioSnapshot(date(2024, 6, 29), 1_000_000, {}, 0, 1_000_000)
        strategy = WalkForwardLinearStrategy(rebalance_every=1, min_training_samples=60)
        orders = strategy.orders_for_date(date(2024, 6, 29), observed_history, portfolio)
        metadata = strategy.factor_metadata()
        self.assertEqual(metadata["latest_fit"]["status"], "trained")
        self.assertGreaterEqual(metadata["latest_fit"]["training_samples"], 60)
        self.assertTrue(orders)
        self.assertTrue(all(order.side == "BUY" and order.quantity % 100 == 0 for order in orders))

    def test_model_does_not_access_current_or_future_bars(self) -> None:
        start = date(2024, 1, 1)
        bars = []
        close = 20.0
        for index in range(150):
            close *= 1 + 0.002 * math.sin(index / 4) + 0.0003
            bars.append(Bar("600000.SH", start + timedelta(days=index), close, close, close, close, 100_000))
        prefix = {"600000.SH": bars[:140]}
        original = WalkForwardLinearStrategy(rebalance_every=1, min_training_samples=60)
        changed_future = WalkForwardLinearStrategy(rebalance_every=1, min_training_samples=60)
        portfolio = PortfolioSnapshot(date(2024, 5, 20), 1_000_000, {}, 0, 1_000_000)
        first = original.orders_for_date(portfolio.as_of, prefix, portfolio)
        prefix_with_future = {"600000.SH": [*bars[:140], *bars[140:]]}
        second = changed_future.orders_for_date(portfolio.as_of, prefix, portfolio)
        self.assertEqual(first, second)
        self.assertEqual(original.factor_metadata()["latest_fit"], changed_future.factor_metadata()["latest_fit"])
        self.assertEqual(len(prefix_with_future["600000.SH"]), 150)


if __name__ == "__main__":
    unittest.main()
