import tempfile
import unittest
from datetime import date, timedelta

from quant.backtest.engine import EventDrivenBacktester, PortfolioSnapshot
from quant.data.schema import Bar
from quant.data.universe import HistoricalUniverse
from quant.research.ledger import ResearchLedger
from quant.validation import run_sealed_test


class _NoOpStrategy:
    def orders_for_date(self, trading_date, history, portfolio: PortfolioSnapshot):
        return []


def _bars(days: int = 80) -> list[Bar]:
    start = date(2024, 1, 2)
    rows: list[Bar] = []
    for offset in range(days):
        current = start + timedelta(days=offset)
        rows.append(
            Bar(
                symbol="000001.SZ",
                date=current,
                open=10.0,
                high=10.5 + offset * 0.01,
                low=9.5,
                close=10.0 + offset * 0.01,
                volume=100_000,
            )
        )
    return rows


class SealedValidationTests(unittest.TestCase):
    def test_engine_reports_only_the_held_out_window(self) -> None:
        bars = _bars()
        result = EventDrivenBacktester().run(
            bars,
            _NoOpStrategy(),
            evaluation_start=date(2024, 2, 1),
            evaluation_end=date(2024, 2, 10),
        )
        self.assertEqual(result.equity_curve[0].date, date(2024, 2, 1))
        self.assertEqual(result.equity_curve[-1].date, date(2024, 2, 10))
        self.assertTrue(all(point.date >= date(2024, 2, 1) for point in result.equity_curve))

    def test_sealed_report_requires_a_reserved_window_and_warmup(self) -> None:
        report = run_sealed_test(
            _bars(),
            _NoOpStrategy,
            sealed_start=date(2024, 2, 1),
            sealed_end=date(2024, 2, 20),
            minimum_observations=10,
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.sealed_start, date(2024, 2, 1))
        self.assertGreaterEqual(report.observations, 10)

    def test_pit_sealed_report_uses_and_audits_constituent_changes(self) -> None:
        start = date(2024, 1, 2)
        days = [start + timedelta(days=offset) for offset in range(80)]
        bars = [
            Bar(
                symbol="600000.SH" if offset < 40 else "000001.SZ",
                date=day,
                open=10.0,
                high=20.0,
                low=1.0,
                close=10.0 + offset * 0.01,
                volume=100_000,
            )
            for offset, day in enumerate(days)
        ]
        universe = HistoricalUniverse(
            {
                days[0]: {"600000.SH"},
                days[40]: {"000001.SZ"},
            }
        )

        report = run_sealed_test(
            bars,
            _NoOpStrategy,
            sealed_start=days[20],
            sealed_end=days[79],
            minimum_observations=20,
            pit_universe=universe,
            pit_index_code="000300.SH",
        )

        self.assertTrue(report.passed)
        checks = {item["name"]: item["status"] for item in report.checks}
        self.assertEqual(checks["历史成分 PIT 覆盖"], "通过")
        self.assertEqual(checks["PIT 历史成分确有变更"], "通过")
        self.assertEqual(report.pit_universe["index_code"], "000300.SH")
        self.assertEqual(report.pit_universe["sealed_membership_changes"][0]["trade_date"], days[40].isoformat())

    def test_pit_seal_fails_if_bars_ignore_constituent_change(self) -> None:
        start = date(2024, 1, 2)
        days = [start + timedelta(days=offset) for offset in range(80)]
        bars = [
            Bar("600000.SH", day, 10, 10.5, 9.5, 10, 100_000)
            for day in days
        ]
        universe = HistoricalUniverse(
            {
                days[0]: {"600000.SH"},
                days[40]: {"000001.SZ"},
            }
        )

        report = run_sealed_test(
            bars,
            _NoOpStrategy,
            sealed_start=days[20],
            sealed_end=days[79],
            minimum_observations=20,
            pit_universe=universe,
            pit_index_code="000300.SH",
        )

        self.assertFalse(report.passed)
        self.assertIn("非成分股日线", next(item["evidence"] for item in report.checks if item["name"] == "历史成分 PIT 覆盖"))

    def test_sealed_snapshot_and_result_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3")
            first = ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:sealed:test",
                data_version="tushare:test",
                sealed_start="2024-02-01",
                sealed_end="2024-03-01",
                dataset_hash="hash-a",
                bars=[{"symbol": "000001.SZ", "date": "2024-02-01", "close": 10}],
                metadata={"source": "tushare"},
                parameters={"sealed_days": 20},
            )
            second = ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:sealed:test",
                data_version="tushare:changed",
                sealed_start="2024-01-01",
                sealed_end="2024-02-01",
                dataset_hash="hash-b",
                bars=[],
                metadata={},
                parameters={},
            )
            self.assertEqual(first["id"], second["id"])
            evaluated = ledger.record_sealed_result(
                int(first["id"]),
                status="passed",
                result={"passed": True, "sharpe": 0.5},
            )
            repeated = ledger.record_sealed_result(
                int(first["id"]),
                status="needs_review",
                result={"passed": False},
            )
            self.assertEqual(evaluated["result"], repeated["result"])
            self.assertEqual(repeated["status"], "passed")

    def test_pit_gate_lookup_does_not_match_a_fixed_pool_seal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3")
            ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:sealed:fixed",
                data_version="tushare:fixed",
                sealed_start="2024-02-01",
                sealed_end="2024-03-01",
                dataset_hash="fixed-hash",
                bars=[],
                metadata={"research_universe": {"mode": "fixed", "pit_membership": False}},
                parameters={"sealed_acceptance_protocol": "pit_historical_index_v1"},
            )
            ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:sealed:pit",
                data_version="tushare:pit",
                sealed_start="2024-02-01",
                sealed_end="2024-03-01",
                dataset_hash="pit-hash",
                bars=[],
                pit_data={"index_code": "000300.SH", "snapshots": [
                    {"trade_date": "2024-02-29", "members": [{"ts_code": "000001.SZ"}]}
                ]},
                metadata={"research_universe": {"mode": "pit_historical_index", "pit_membership": True, "index_code": "000300.SH"}},
                parameters={"sealed_acceptance_protocol": "pit_historical_index_v1"},
            )

            matched = ledger.latest_sealed_test_for_universe(
                "baseline", index_code="000300.SH", protocol="pit_historical_index_v1"
            )

            self.assertIsNotNone(matched)
            self.assertEqual(matched["dataset_hash"], "pit-hash")
            self.assertNotIn("pit_data", matched)
            complete = ledger.latest_sealed_test_for_universe(
                "baseline", index_code="000300.SH",
                protocol="pit_historical_index_v1", include_bars=True,
            )
            self.assertEqual(complete["pit_data"]["snapshots"][0]["trade_date"], "2024-02-29")


if __name__ == "__main__":
    unittest.main()
