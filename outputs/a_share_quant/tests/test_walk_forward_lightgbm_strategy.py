import importlib.util
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from quant.backtest.engine import PortfolioSnapshot
from quant.data.schema import Bar
from quant.features.store import FEATURE_NAMES, FeatureStore
from quant.strategies.lightgbm import WalkForwardLightGBMStrategy


class _DeterministicFakeDataset:
    def __init__(self, features, label, **kwargs):
        self.features = features
        self.label = label


class _DeterministicFakeBooster:
    def predict(self, features, **kwargs):
        return [float(row[0]) for row in features]

    def feature_importance(self, importance_type):
        return [1.0] * len(FEATURE_NAMES)


class WalkForwardLightGBMStrategyTests(unittest.TestCase):
    def test_fit_uses_materialized_prior_features_and_lot_sized_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FeatureStore(Path(directory) / "features.sqlite3")
            start = date(2024, 1, 1)
            bars = []
            for symbol_index in range(4):
                symbol = f"60000{symbol_index}.SH"
                for day_index in range(170):
                    close = 10 + symbol_index * 2 + day_index * (0.01 + symbol_index * 0.001)
                    bars.append(Bar(
                        symbol, start + timedelta(days=day_index), close, close + 0.2, close - 0.2,
                        close, 1_000_000,
                    ))
            store.materialize(bars, "dataset-v1")
            trade_day = start + timedelta(days=160)
            history = {
                symbol: [bar for bar in bars if bar.symbol == symbol and bar.date < trade_day]
                for symbol in sorted({bar.symbol for bar in bars})
            }
            strategy = WalkForwardLightGBMStrategy(
                store, "dataset-v1", top_n=2, rebalance_every=1, min_training_samples=120
            )
            fake_module = ModuleType("lightgbm")
            fake_module.Dataset = _DeterministicFakeDataset
            fake_module.train = lambda _params, _dataset, num_boost_round: _DeterministicFakeBooster()
            with patch.dict("sys.modules", {"lightgbm": fake_module}):
                orders = strategy.orders_for_date(
                    trade_day,
                    history,
                    PortfolioSnapshot(trade_day, 1_000_000, {}, 0, 1_000_000),
                )
            self.assertEqual(strategy.factor_metadata()["latest_fit"]["status"], "trained")
            self.assertGreater(strategy.factor_metadata()["latest_fit"]["training_samples"], 120)
            self.assertTrue(orders)
            self.assertTrue(all(order.side == "BUY" and order.quantity % 100 == 0 for order in orders))

    def test_requires_feature_store_and_data_version(self):
        with self.assertRaisesRegex(ValueError, "点时 FeatureStore"):
            from quant.strategies.registry import build_strategy
            build_strategy("lightgbm", rebalance_every=5)

    @unittest.skipUnless(importlib.util.find_spec("lightgbm"), "optional LightGBM extra is not installed")
    def test_00_real_lightgbm_cpu_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FeatureStore(Path(directory) / "features.sqlite3")
            start = date(2024, 1, 1)
            bars = []
            for symbol_index in range(4):
                symbol = f"00000{symbol_index}.SZ"
                for day_index in range(170):
                    # Deterministic non-linear perturbations keep the sample
                    # non-constant while remaining synthetic and offline.
                    close = 12 + symbol_index + 0.02 * day_index + ((day_index % 7) - 3) * 0.015
                    bars.append(Bar(
                        symbol, start + timedelta(days=day_index), close, close + 0.15,
                        close - 0.15, close, 1_000_000,
                    ))
            store.materialize(bars, "real-lgbm-smoke-v1")
            trade_day = start + timedelta(days=160)
            history = {
                symbol: [bar for bar in bars if bar.symbol == symbol and bar.date < trade_day]
                for symbol in sorted({bar.symbol for bar in bars})
            }
            strategy = WalkForwardLightGBMStrategy(
                store, "real-lgbm-smoke-v1", top_n=2, rebalance_every=1,
                min_training_samples=120,
            )
            orders = strategy.orders_for_date(
                trade_day,
                history,
                PortfolioSnapshot(trade_day, 1_000_000, {}, 0, 1_000_000),
            )
            self.assertEqual(strategy.factor_metadata()["latest_fit"]["status"], "trained")
            self.assertTrue(all(order.quantity % 100 == 0 for order in orders))


if __name__ == "__main__":
    unittest.main()
