import unittest
from datetime import date, timedelta
from tempfile import TemporaryDirectory

from quant.data.schema import Bar
from quant.execution import PaperBroker, PaperOrderStatus
from quant.factors.analysis import neutralize_by_group, quantile_returns, rank_ic
from quant.factors.store import FactorRegistryStore, calculate_factor


class PaperAndFactorTests(unittest.TestCase):
    def test_paper_order_lifecycle_supports_partial_fill(self) -> None:
        broker = PaperBroker()
        broker.submit("o1", "A", "BUY", 100)
        self.assertEqual(broker.acknowledge_fill("o1", 40).status, PaperOrderStatus.PARTIALLY_FILLED)
        self.assertEqual(broker.acknowledge_fill("o1", 60).status, PaperOrderStatus.FILLED)

    def test_factor_analysis(self) -> None:
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0}
        returns = {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04, "E": 0.05}
        self.assertAlmostEqual(rank_ic(values, returns), 1.0)
        self.assertEqual(len(quantile_returns(values, returns, 5)), 5)
        self.assertAlmostEqual(neutralize_by_group(values, {"A": "x", "B": "x"})["A"], -0.5)

    def test_persistent_factor_registry_and_live_calculation(self) -> None:
        with TemporaryDirectory() as directory:
            store = FactorRegistryStore(f"{directory}/factors.sqlite3")
            definitions = store.definitions()
            self.assertEqual(
                [item.key for item in definitions],
                [
                    "builtin_fundamental_debt_to_assets",
                    "builtin_fundamental_netprofit_yoy",
                    "builtin_fundamental_roe",
                    "builtin_fundamental_tr_yoy",
                    "builtin_momentum_20",
                    "builtin_volatility_20",
                ],
            )
            reversal = store.register("10日反转", "reversal", 10)
            self.assertEqual(reversal.factor_type, "reversal")
            self.assertEqual(len(FactorRegistryStore(f"{directory}/factors.sqlite3").definitions()), 7)
            history = [
                Bar(
                    symbol="000001.SH",
                    date=date(2024, 1, 1) + timedelta(days=index),
                    open=10 + index,
                    high=10 + index,
                    low=10 + index,
                    close=10 + index,
                    volume=1_000,
                )
                for index in range(11)
            ]
            self.assertAlmostEqual(calculate_factor(reversal, history), -1.0)
