import unittest
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

from quant.backtest.engine import DailyEquity
from quant.data.schema import Bar, PITIndustryObservation
from quant.portfolio.allocator import (
    inverse_volatility_weights,
    mean_variance_weights,
    volatility_target_scale,
)
from quant.portfolio.analytics import build_portfolio_risk_report


class PortfolioAnalyticsTests(unittest.TestCase):
    def test_inverse_volatility_weights_obey_name_cap(self) -> None:
        weights = inverse_volatility_weights({"A": 0.01, "B": 0.02, "C": 0.03}, max_weight=0.5)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertLessEqual(max(weights.values()), 0.5)
        self.assertGreater(weights["A"], weights["C"])

    def test_volatility_target_scale_only_reduces_risk_and_uses_prior_history(self) -> None:
        start = date(2024, 1, 1)
        history = {}
        for symbol, amplitude in (("A", 0.03), ("B", 0.02)):
            bars = []
            close = 100.0
            for index in range(80):
                close *= 1 + amplitude * (1 if index % 2 else -1)
                bars.append(Bar(symbol, start + timedelta(days=index), close, close, close, close, 1000))
            history[symbol] = bars
        scale = volatility_target_scale(history, {"A": 0.5, "B": 0.5})
        self.assertGreaterEqual(scale, 0.0)
        self.assertLess(scale, 1.0)

    def test_risk_report_includes_var_es_correlation_and_explicit_missing_stress_periods(self) -> None:
        start = date(2024, 1, 1)
        curve = []
        bars = []
        equity = 1_000_000.0
        close_a = close_b = 100.0
        for index in range(31):
            day = start + timedelta(days=index)
            if index:
                change = -0.03 if index == 20 else (0.004 if index % 2 else -0.002)
                equity *= 1 + change
                close_a *= 1 + change
                close_b *= 1 + change
            curve.append(DailyEquity(day, equity, 0, equity))
            bars.extend([
                Bar("A", day, close_a, close_a, close_a, close_a, 1000),
                Bar("B", day, close_b, close_b, close_b, close_b, 1000),
            ])
        result = SimpleNamespace(equity_curve=curve, fills=[])
        report = build_portfolio_risk_report(result, bars)
        self.assertEqual(report["status"], "available")
        self.assertIsNotNone(report["historical_var_95"])
        self.assertIsNotNone(report["historical_expected_shortfall_95"])
        self.assertAlmostEqual(report["correlation"]["matrix"]["A"]["B"], 1.0)
        self.assertEqual(report["historical_periods"]["2015-2016"]["status"], "unavailable")
        self.assertLess(report["max_drawdown"], 0)

    def test_mean_variance_optimizer_uses_full_covariance_and_pit_neutral_bands(self) -> None:
        decision_day = date(2024, 6, 28)
        pit = datetime(2024, 6, 27, 16, tzinfo=timezone(timedelta(hours=8)))
        symbols = {"A1": "Tech", "A2": "Tech", "B1": "Banks", "B2": "Banks"}
        observations = [
            PITIndustryObservation(symbol, decision_day, pit, industry, "industry-v1", "first_seen_at")
            for symbol, industry in symbols.items()
        ]
        observations.extend([
            PITIndustryObservation("A3", decision_day, pit, "Tech", "industry-v1", "first_seen_at"),
            PITIndustryObservation("B3", decision_day, pit, "Banks", "industry-v1", "first_seen_at"),
        ])
        daily = {symbol: {} for symbol in ("A1", "A2", "B1", "B2")}
        for index in range(40):
            day = date(2024, 1, 1) + timedelta(days=index)
            common = 0.01 if index % 2 else -0.01
            independent = 0.004 if index % 3 else -0.004
            daily["A1"][day] = common
            daily["A2"][day] = common + independent * 0.1
            daily["B1"][day] = -common + independent
            daily["B2"][day] = -common - independent
        weights = mean_variance_weights(
            {"A1": 0.0010, "A2": 0.0008, "B1": 0.0010, "B2": 0.0008},
            daily,
            industry_observations=observations,
            benchmark_constituent_weights={"A3": 0.5, "B3": 0.5},
            as_of=decision_day,
            max_weight=0.5,
            max_active_industry_deviation=0.05,
            risk_aversion=2.0,
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        self.assertLessEqual(max(weights.values()), 0.5 + 1e-8)
        tech = weights.get("A1", 0.0) + weights.get("A2", 0.0)
        self.assertGreaterEqual(tech, 0.45 - 1e-6)
        self.assertLessEqual(tech, 0.55 + 1e-6)
        self.assertAlmostEqual(tech + weights.get("B1", 0.0) + weights.get("B2", 0.0), 1.0, places=5)

    def test_mean_variance_optimizer_rejects_effective_date_only_industry_labels(self) -> None:
        decision_day = date(2024, 6, 28)
        observations = [
            PITIndustryObservation(
                "A", decision_day, datetime(2024, 6, 1, tzinfo=UTC), "Tech", "industry-v1", "unknown"
            ),
            PITIndustryObservation(
                "B", decision_day, datetime(2024, 6, 1, tzinfo=UTC), "Banks", "industry-v1", "unknown"
            ),
        ]
        daily = {"A": {}, "B": {}}
        for index in range(30):
            day = date(2024, 1, 1) + timedelta(days=index)
            daily["A"][day] = 0.001 if index % 2 else -0.001
            daily["B"][day] = -0.001 if index % 2 else 0.001
        with self.assertRaisesRegex(ValueError, "strict PIT"):
            mean_variance_weights(
                {"A": 0.001, "B": 0.001},
                daily,
                industry_observations=observations,
                benchmark_constituent_weights={"A": 0.5, "B": 0.5},
                as_of=decision_day,
            )


if __name__ == "__main__":
    unittest.main()
