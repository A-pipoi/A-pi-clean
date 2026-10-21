import unittest
from datetime import date

from quant.backtest.engine import BacktestResult, DailyEquity
from quant.data.schema import Bar
from quant.portfolio.brinson import build_strict_pit_brinson_report


class StrictPITBrinsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.day0 = date(2024, 1, 2)
        self.day1 = date(2024, 1, 3)
        self.result = BacktestResult(
            initial_cash=1000,
            final_cash=0,
            final_equity=1060,
            fills=[],
            rejected=[],
            equity_curve=[
                DailyEquity(self.day0, 0, 1000, 1000, {"A.SH": 0.6, "B.SH": 0.4}),
                DailyEquity(self.day1, 0, 1060, 1060, {"A.SH": 0.62, "B.SH": 0.38}),
            ],
        )
        self.bars = [
            Bar("A.SH", self.day0, 100, 100, 100, 100, 1000),
            Bar("A.SH", self.day1, 110, 110, 110, 110, 1000),
            Bar("B.SH", self.day0, 100, 100, 100, 100, 1000),
            Bar("B.SH", self.day1, 100, 100, 100, 100, 1000),
        ]
        self.industry = [
            {"symbol": "A.SH", "industry": "科技", "valid_from": "2020-01-01",
             "available_at": "2024-01-01T17:00:00+08:00", "source_version": "industry-1"},
            {"symbol": "B.SH", "industry": "银行", "valid_from": "2020-01-01",
             "available_at": "2024-01-01T17:00:00+08:00", "source_version": "industry-1"},
        ]
        self.weights = [
            {"symbol": "A.SH", "as_of": "2024-01-02", "weight": 0.5,
             "available_at": "2024-01-01T17:00:00+08:00", "source_version": "weights-1"},
            {"symbol": "B.SH", "as_of": "2024-01-02", "weight": 0.5,
             "available_at": "2024-01-01T17:00:00+08:00", "source_version": "weights-1"},
        ]

    def test_brinson_fachler_components_reconcile_to_active_return(self) -> None:
        report = build_strict_pit_brinson_report(
            self.result, self.bars,
            industry_records=self.industry,
            benchmark_weight_records=self.weights,
            benchmark_code="TEST",
            source_version="panel-v1",
        )
        self.assertEqual(report["status"], "available")
        self.assertAlmostEqual(report["linked"]["portfolio_return"], 0.06)
        self.assertAlmostEqual(report["linked"]["benchmark_return"], 0.05)
        self.assertAlmostEqual(report["linked"]["active_return"], 0.01)
        self.assertAlmostEqual(report["linked"]["allocation"], 0.01)
        self.assertAlmostEqual(report["linked"]["reconciliation_error"], 0.0, places=12)

    def test_missing_industry_availability_fails_closed(self) -> None:
        invalid = [{key: value for key, value in self.industry[0].items() if key != "available_at"}, self.industry[1]]
        report = build_strict_pit_brinson_report(
            self.result, self.bars,
            industry_records=invalid,
            benchmark_weight_records=self.weights,
            benchmark_code="TEST",
        )
        self.assertEqual(report["status"], "unavailable")
        self.assertIsNone(report["attribution"])

    def test_benchmark_weights_published_after_open_are_not_used(self) -> None:
        invalid = [{**row, "available_at": "2024-01-02T10:00:00+08:00"} for row in self.weights]
        report = build_strict_pit_brinson_report(
            self.result, self.bars,
            industry_records=self.industry,
            benchmark_weight_records=invalid,
            benchmark_code="TEST",
        )
        self.assertEqual(report["status"], "unavailable")
        self.assertIn("基准权重", report["reason"])


if __name__ == "__main__":
    unittest.main()
