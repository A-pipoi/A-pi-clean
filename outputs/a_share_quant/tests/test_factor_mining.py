from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant.data.fundamentals import _history_rows
from quant.data.pit import (
    FutureInformationError,
    materialize_pit_feature_observations,
    materialize_pit_industry_observations,
)
from quant.data.pit_sources import _daily_basic_rows, _industry_rows
from quant.data.schema import Bar, FeatureObservation, PITIndustryObservation
from quant.factors.analysis import evaluate_factor_panel
from quant.factors.expression import (
    build_factor_panel,
    compile_expression,
    evaluate_expression,
)
from quant.factors.store import FactorRegistryStore
from quant.mining import run_factor_mining
from quant.mining.batch import aggregate_factor_mining_runs
from quant.research.ledger import ResearchLedger
from quant.validation.statistics import benjamini_hochberg, factor_ic_significance


def _bars(days: int = 110, symbols: tuple[str, ...] = ("A", "B", "C", "D", "E")) -> list[Bar]:
    start = date(2024, 1, 2)
    rows: list[Bar] = []
    for offset in range(days):
        for rank, symbol in enumerate(symbols):
            close = 10.0 + rank + offset * (0.015 + rank * 0.003) + (offset % 7) * 0.01
            rows.append(Bar(
                symbol=symbol,
                date=start + timedelta(days=offset),
                open=close - 0.05,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=100_000 + rank * 1000 + offset,
                industry=f"sector-{rank % 2}",
            ))
    return rows


class FactorExpressionTests(unittest.TestCase):
    def test_dsl_rolls_only_backward_and_requires_full_window(self) -> None:
        bars = [
            Bar("A", date(2024, 1, 1) + timedelta(days=index), 10 + index, 11 + index, 9 + index, 10 + index, 100)
            for index in range(5)
        ]
        panel = build_factor_panel(bars, data_version="csv:test")
        compiled = compile_expression("ts_delta(close, 2) + shift(close, 1)")
        result = evaluate_expression(compiled, panel)
        self.assertEqual(compiled.min_history, 3)
        self.assertIsNone(result.values[0][0])
        self.assertIsNone(result.values[1][0])
        self.assertEqual(result.values[2][0], 13.0)
        self.assertEqual(result.values[3][0], 14.0)

    def test_unknown_future_and_unbound_financial_fields_fail_closed(self) -> None:
        for expression in ("close.shift(-1)", "future_close", "shift(close, -1)", "pe / pb"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                compile_expression(expression)

    def test_financial_value_must_be_a_point_in_time_observation(self) -> None:
        bars = _bars(4, ("A", "B", "C"))
        day = bars[0].date
        observations = [
            FeatureObservation(symbol, day, datetime.combine(day, datetime.min.time(), UTC), "roe", value, "pit:v1")
            for symbol, value in zip(("A", "B", "C"), (0.1, 0.2, 0.3))
        ]
        panel = build_factor_panel(bars, observations=observations, data_version="pit:v1")
        result = evaluate_expression("cs_rank(roe)", panel)
        self.assertEqual(result.values[0], [0.0, 0.5, 1.0])
        future = FeatureObservation("A", day, datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC), "roe", 0.2, "pit:v1")
        with self.assertRaises(FutureInformationError):
            build_factor_panel(bars, observations=[future])

    def test_materializer_applies_financial_and_close_data_from_next_session(self) -> None:
        sessions = [date(2024, 1, day) for day in (2, 3, 4, 5)]
        records = [
            {
                "symbol": "A", "metric": "roe", "value": 0.1,
                "report_date": "2023-09-30", "announcement_date": "2024-01-02",
                "available_at": "2024-01-02T16:00:00+08:00", "source_version": "r1",
            },
            {
                "symbol": "A", "metric": "roe", "value": 0.2,
                "report_date": "2023-12-31", "announcement_date": "2024-01-03",
                "available_at": "2024-01-03T16:00:00+08:00", "source_version": "r2",
            },
            {
                "symbol": "A", "metric": "market_cap", "value": 1000,
                "trade_date": "20240102", "available_at": "2024-01-02T17:00:00+08:00", "source_version": "m1",
            },
        ]
        observations = materialize_pit_feature_observations(records, sessions)
        roe = {item.as_of: item.value for item in observations if item.name == "roe"}
        market_cap = {item.as_of: item.value for item in observations if item.name == "market_cap"}
        self.assertEqual(roe, {sessions[1]: 0.1, sessions[2]: 0.2, sessions[3]: 0.2})
        self.assertEqual(market_cap, {sessions[1]: 1000.0, sessions[2]: 1000.0, sessions[3]: 1000.0})

    def test_tushare_pit_adapters_keep_announcement_and_effective_date_provenance(self) -> None:
        financial_frame = SimpleNamespace(empty=False, to_dict=lambda orient: [{
            "ts_code": "000001.SZ", "ann_date": "20240102", "end_date": "20230930",
            "roe": 12.5, "netprofit_yoy": 8.0, "tr_yoy": 4.0, "debt_to_assets": 55.0,
            "update_flag": "1",
        }])
        financial = _history_rows(financial_frame, "000001.SZ")
        roe = next(item for item in financial if item["metric"] == "roe")
        self.assertEqual(roe["value"], 0.125)
        self.assertEqual(roe["announcement_date"], "2024-01-02")
        self.assertEqual(roe["report_date"], "2023-09-30")
        self.assertEqual(roe["update_flag"], "1")

        daily_frame = SimpleNamespace(empty=False, to_dict=lambda orient: [{
            "ts_code": "000001.SZ", "trade_date": "20240102", "total_mv": 12345,
            "circ_mv": 6789, "pe_ttm": 6.2, "pb": 0.8,
        }])
        daily = _daily_basic_rows(daily_frame, "000001.SZ")
        market_cap = next(item for item in daily if item["metric"] == "market_cap")
        self.assertEqual(market_cap["value"], 12345.0)
        self.assertEqual(market_cap["unit"], "10k_CNY")
        self.assertEqual(market_cap["available_at"], "2024-01-02T17:00:00+08:00")

        industry_frame = SimpleNamespace(empty=False, to_dict=lambda orient: [{
            "ts_code": "000001.SZ", "l1_name": "银行", "in_date": "20200101", "out_date": "",
        }])
        industry = _industry_rows(industry_frame, "000001.SZ")
        self.assertEqual(industry[0]["availability_basis"], "effective_dates_only_unverified")
        self.assertNotIn("available_at", industry[0])

    def test_industry_effective_dates_without_public_availability_are_not_materialized(self) -> None:
        sessions = [date(2024, 1, day) for day in (2, 3, 4)]
        effective_only = [{
            "symbol": "A", "industry": "银行", "in_date": "20230101", "out_date": "",
        }]
        self.assertEqual(materialize_pit_industry_observations(effective_only, sessions), [])
        published = [{
            **effective_only[0], "available_at": "2024-01-02T16:00:00+08:00",
        }]
        result = materialize_pit_industry_observations(published, sessions)
        self.assertEqual([item.as_of for item in result], sessions[1:])

    def test_bar_current_industry_is_ignored_and_neutralizers_require_verified_pit(self) -> None:
        symbols = ("A", "B", "C", "D")
        bars = _bars(3, symbols)
        day = bars[0].date
        caps = (10.0, 20.0, 40.0, 80.0)
        observations = []
        for symbol, cap in zip(symbols, caps):
            log_cap = __import__("math").log(cap)
            available_at = datetime(2024, 1, 1, 16, tzinfo=timezone(timedelta(hours=8)))
            observations.extend([
                FeatureObservation(symbol, day, available_at, "market_cap", cap, "size:v1"),
                FeatureObservation(symbol, day, available_at, "roe", 3 + 2 * log_cap, "fin:v1"),
            ])
        with self.assertRaisesRegex(ValueError, "行业中性化"):
            compile_expression("cs_industry_neutralize(close)")
        industry = [
            PITIndustryObservation(symbol, day, datetime(2024, 1, 1, 16, tzinfo=timezone(timedelta(hours=8))), group, "ind:v1", "provider_available_at")
            for symbol, group in zip(symbols, ("bank", "bank", "tech", "tech"))
        ]
        panel = build_factor_panel(
            bars,
            observations=observations,
            industry_observations=industry,
            pit_quality={"size": "verified", "industry": "verified"},
        )
        self.assertEqual(panel.industries[0], ("bank", "bank", "tech", "tech"))
        self.assertEqual(panel.industries[1], (None, None, None, None))
        size_compiled = compile_expression(
            "cs_size_neutralize(roe)",
            registered_pit_fields=("roe", "market_cap"),
            pit_quality={"size": "verified"},
        )
        self.assertIn("market_cap", size_compiled.dependencies)
        industry_compiled = compile_expression(
            "cs_industry_neutralize(close)",
            pit_quality={"industry": "verified"},
        )
        self.assertIn("industry", industry_compiled.dependencies)
        size_residuals = evaluate_expression("cs_size_neutralize(roe)", panel).values[0]
        self.assertTrue(all(abs(float(value)) < 1e-10 for value in size_residuals))
        industry_residuals = evaluate_expression("cs_industry_neutralize(close)", panel).values[0]
        self.assertAlmostEqual(sum(industry_residuals[:2]), 0.0)
        self.assertAlmostEqual(sum(industry_residuals[2:]), 0.0)

    def test_alpha_style_template_expressions_compile(self) -> None:
        from quant.data.extension_features import FIELD_NAMES
        from quant.factors.expression import EXPRESSION_TEMPLATES

        for template in EXPRESSION_TEMPLATES:
            with self.subTest(template=template["name"]):
                if template.get('availability'):
                    with self.assertRaises(ValueError):
                        compile_expression(template['expression'])
                    compile_expression(template['expression'], registered_pit_fields=FIELD_NAMES)
                else:
                    compile_expression(template["expression"])

    def test_prompt_required_lookahead_probes_fail_closed(self) -> None:
        bars = _bars(4, ("A", "B", "C"))
        day = bars[0].date
        for expression in ("shift(close, -1)", "close * adj_factor"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                compile_expression(expression)

        unannounced = FeatureObservation(
            "A", day,
            datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC),
            "roe", 0.25, "late-report",
        )
        with self.assertRaises(FutureInformationError):
            build_factor_panel(bars, observations=[unannounced])

        adjustment_factor = FeatureObservation(
            "A", day,
            datetime.combine(day, datetime.min.time(), timezone(timedelta(hours=8))),
            "adj_factor", 9.0, "wrong-adjustment-vintage",
        )
        with self.assertRaisesRegex(ValueError, "未注册的 PIT 财务字段"):
            build_factor_panel(bars, observations=[adjustment_factor])


class FactorEvaluationReportTests(unittest.TestCase):
    def test_report_has_pit_size_slices_annual_drift_and_locked_industry_slice(self) -> None:
        symbols = tuple(f"S{index:02d}" for index in range(12))
        sessions = (
            date(2023, 12, 28), date(2023, 12, 29),
            date(2024, 1, 2), date(2024, 1, 3),
        )
        bars: list[Bar] = []
        observations: list[FeatureObservation] = []
        values: list[list[float | None]] = []
        for row, day in enumerate(sessions):
            year_offset = 0.0 if day.year == 2023 else 20.0
            values.append([float(index) + year_offset + row * 0.1 for index in range(len(symbols))])
            for index, symbol in enumerate(symbols):
                close = 100.0 + index * 2.0 + row * (index + 1)
                bars.append(Bar(symbol, day, close - 0.1, close + 0.2, close - 0.2, close, 100_000))
                observations.append(FeatureObservation(
                    symbol, day,
                    datetime.combine(day - timedelta(days=1), datetime.min.time(), timezone(timedelta(hours=8))),
                    "market_cap", float((index + 1) * 10_000), "daily-basic-pit",
                ))
        panel = build_factor_panel(
            bars, observations=observations, data_version="csv:pit",
            pit_quality={"size": "verified"},
        )
        report = evaluate_factor_panel(panel, values, horizons=(1,), quantiles=10)
        horizon = report["horizons"]["1"]
        self.assertEqual(horizon["environment_slices"]["market_cap"]["status"], "available")
        self.assertEqual(
            set(horizon["environment_slices"]["market_cap"]["groups"]),
            {"small", "mid", "large"},
        )
        industry = horizon["environment_slices"]["industry"]
        self.assertEqual(industry["status"], "unavailable")
        self.assertIn("严格 PIT 门禁", industry["reason"])
        unverified_panel = build_factor_panel(
            bars, observations=observations, data_version="csv:pit-unverified",
            pit_quality={"size": "insufficient_coverage"},
        )
        unverified_report = evaluate_factor_panel(unverified_panel, values, horizons=(1,))
        self.assertEqual(
            unverified_report["horizons"]["1"]["environment_slices"]["market_cap"]["status"],
            "unavailable",
        )
        annual = report["annual_distribution_drift"]["years"]
        self.assertEqual(annual["2023"]["compared_to"], None)
        self.assertEqual(annual["2024"]["compared_to"], "2023")
        self.assertGreater(annual["2024"]["drift_vs_previous_year"]["p50"], 0)


class FactorMiningTests(unittest.TestCase):
    def test_sealed_acceptance_batch_aggregates_all_runs_and_unique_expressions(self) -> None:
        batch = aggregate_factor_mining_runs([
            {
                "run_id": "run-1", "n_tested": 80,
                "result": {"candidates": [{"expression": "cs_rank(close)", "factor_key": "expr-a", "fitness": 0.1}]},
            },
            {
                "run_id": "run-2", "n_tested": 10,
                "result": {"candidates": [
                    {"expression": "cs_rank(close)", "factor_key": "expr-b", "fitness": 0.2},
                    {"expression": "cs_rank(volume)", "factor_key": "expr-c", "fitness": 0.05},
                ]},
            },
        ])
        self.assertEqual(batch["n_tested"], 90)
        self.assertEqual(len(batch["candidates"]), 2)
        strongest = next(item for item in batch["candidates"] if item["expression"] == "cs_rank(close)")
        self.assertEqual(strongest["fitness"], 0.2)
        self.assertEqual(strongest["factor_keys"], ["expr-a", "expr-b"])
        self.assertEqual(strongest["source_run_ids"], ["run-1", "run-2"])

    def test_ten_thousand_trials_remain_in_denominator_when_only_five_survive(self) -> None:
        survivors = [
            {"expression": f"cs_rank(close) + {index}", "factor_key": f"expr-{index}", "fitness": 0.1 - index * 0.01}
            for index in range(5)
        ]
        batch = aggregate_factor_mining_runs([{
            "run_id": "large-search",
            "n_tested": 10_000,
            "result": {"candidates": survivors},
        }])
        self.assertEqual(batch["n_tested"], 10_000)
        self.assertEqual(len(batch["candidates"]), 5)

    def test_gp_is_reproducible_and_counts_every_individual(self) -> None:
        data = _bars()
        end = data[-1].date - timedelta(days=8)
        options = {"train_end": end, "seed": 41, "generations": 3, "population_size": 8, "top_n": 5, "data_version": "csv:snapshot"}
        first = run_factor_mining(data, **options)
        second = run_factor_mining(data, **options)
        self.assertEqual(first.n_tested, 24)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.training_end, end.isoformat())

    def test_post_training_bars_cannot_change_gp_output(self) -> None:
        data = _bars()
        end = data[-1].date - timedelta(days=8)
        training = [bar for bar in data if bar.date <= end]
        future = [
            Bar(bar.symbol, bar.date, bar.open * 10, bar.high * 10, bar.low * 10, bar.close * 10, bar.volume)
            for bar in data if bar.date > end
        ]
        options = {"train_end": end, "seed": 19, "generations": 2, "population_size": 6, "top_n": 4, "data_version": "csv:fixed"}
        self.assertEqual(run_factor_mining(training, **options).as_dict(), run_factor_mining(training + future, **options).as_dict())

    def test_csv_adapter_data_can_feed_the_expression_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.csv"
            rows = _bars(5, ("A", "B", "C"))
            path.write_text(
                "date,symbol,open,high,low,close,volume\n" + "\n".join(
                    f"{row.date},{row.symbol},{row.open},{row.high},{row.low},{row.close},{row.volume}" for row in rows
                ),
                encoding="utf-8",
            )
            from quant.data.adapters import CsvDailyAdapter

            loaded = CsvDailyAdapter(path).daily_bars()
            result = evaluate_expression("cs_rank(close)", build_factor_panel(loaded))
            self.assertEqual(result.values[-1], [0.0, 0.5, 1.0])

    def test_csv_adapter_runs_mining_and_evaluation_without_tushare(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.csv"
            rows = _bars(90, ("A", "B", "C", "D", "E"))
            path.write_text(
                "date,symbol,open,high,low,close,volume\n" + "\n".join(
                    f"{row.date},{row.symbol},{row.open},{row.high},{row.low},{row.close},{row.volume}" for row in rows
                ),
                encoding="utf-8",
            )
            from quant.data.adapters import CsvDailyAdapter

            loaded = CsvDailyAdapter(path).daily_bars()
            train_end = loaded[-1].date - timedelta(days=10)
            mined = run_factor_mining(
                loaded, train_end=train_end, seed=17, generations=1,
                population_size=4, top_n=3, data_version="csv:two-stage",
            )
            training_bars = [bar for bar in loaded if bar.date <= train_end]
            panel = build_factor_panel(training_bars, data_version="csv:two-stage")
            candidate = mined.candidates[0]
            evaluated = evaluate_expression(str(candidate["expression"]), panel)
            report = evaluate_factor_panel(
                panel, evaluated.values, start=min(bar.date for bar in training_bars),
                end=train_end, n_tested=mined.n_tested, horizons=(1, 5),
            )
            self.assertEqual(mined.n_tested, 4)
            self.assertEqual(report["n_tested"], 4)
            self.assertGreater(report["coverage"], 0)

    def test_sealed_snapshot_persists_pit_data_immutably(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(Path(directory) / "research.sqlite3")
            pit_data = {"schema_version": "pit-panel-v1", "daily_basic_records": [{"symbol": "A", "value": 5}]}
            snapshot = ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:pit-v2",
                data_version="tushare:test",
                sealed_start="2024-01-02",
                sealed_end="2024-01-05",
                dataset_hash="hash-v1",
                bars=[{"symbol": "A", "date": "2024-01-02"}],
                pit_data=pit_data,
                metadata={"pit_quality": {"industry": "unverified"}},
                parameters={},
            )
            repeated = ledger.create_sealed_test(
                strategy_version="baseline",
                factor_pipeline_version="pipeline:pit-v2",
                data_version="tushare:changed",
                sealed_start="2024-01-02",
                sealed_end="2024-01-05",
                dataset_hash="hash-v2",
                bars=[],
                pit_data={"daily_basic_records": []},
                metadata={},
                parameters={},
            )
            self.assertEqual(snapshot["id"], repeated["id"])
            self.assertEqual(repeated["pit_data"], pit_data)

    def test_tushare_effective_only_industry_stays_locked_while_size_coverage_is_measured(self) -> None:
        from quant.api_server import _factor_mining_pit_quality

        bars = _bars(3, ("A", "B"))
        pit_data = {
            "daily_basic_records": [
                {
                    "symbol": symbol, "metric": "market_cap", "value": 100.0,
                    "trade_date": "2024-01-02", "report_date": "2024-01-02",
                    "available_at": "2024-01-02T17:00:00+08:00", "source_version": f"{symbol}-v1",
                }
                for symbol in ("A", "B")
            ],
            "industry_records": [
                {"symbol": symbol, "industry": "银行", "in_date": "2020-01-01", "out_date": ""}
                for symbol in ("A", "B")
            ],
            "source_metadata": {"industry": {"availability_quality": "effective_dates_only_unverified"}},
        }
        quality = _factor_mining_pit_quality(pit_data, bars)
        self.assertEqual(quality["size"], "verified")
        self.assertEqual(quality["industry"], "unverified")
        self.assertFalse(quality["industry_neutralization_enabled"])


class FactorMiningGovernanceTests(unittest.TestCase):
    def test_pit_dependent_mined_factor_cannot_promote_before_strategy_runtime_wiring(self) -> None:
        from quant import api_server

        factor = {
            "key": "pit-factor",
            "status": "accepted",
            "expression": "cs_rank(roe)",
            "data_version": "sealed:pit-v2",
            "code_version": "test",
            "sealed_test_id": 17,
            "metadata": {"training": {"dependencies": ["roe"]}},
        }
        with patch.object(api_server, "FACTOR_STORE") as factor_store, \
             patch.object(factor_store, "expression_library", return_value=[factor]), \
             patch.object(api_server.FACTOR_STORE, "promote_expression") as promote:
            with self.assertRaisesRegex(PermissionError, "尚未接入策略与纸面运行时"):
                api_server._promote_mined_factor("pit-factor")
            promote.assert_not_called()

    def test_api_sealed_acceptance_uses_all_same_window_trials_and_unique_candidates(self) -> None:
        from quant import api_server
        from quant.mining.portfolio_metrics import PortfolioPolicy

        runs = [
            {"run_id": "run-1", "sealed_test_id": 17, "n_tested": 80, "result": {"candidates": [
                {"expression": "cs_rank(close)", "factor_key": "expr-a", "fitness": 0.1,
                 "cross_universe_reference": {"status": "available", "index_code": "000905.SH", "role": "report_only_not_for_selection_or_acceptance"}},
            ]}},
            {"run_id": "run-2", "sealed_test_id": 17, "n_tested": 10, "result": {"candidates": [
                {"expression": "cs_rank(close)", "factor_key": "expr-a", "fitness": 0.2},
                {"expression": "cs_rank(volume)", "factor_key": "expr-b", "fitness": 0.05},
            ]}},
        ]
        ledger = Mock()
        ledger.factor_mining_run.return_value = runs[0]
        ledger.get_sealed_test.return_value = {
            "id": 17, "bars": [], "data_version": "snapshot-v1",
            "sealed_start": "2024-05-01", "sealed_end": "2024-06-30",
        }
        ledger.claim_factor_mining_acceptance.return_value = {"allowed": True}
        ledger.factor_mining_runs_for_sealed_test.return_value = runs
        ledger.finish_factor_mining_acceptance.side_effect = lambda run_id, *, status, result: {
            "run_id": run_id, "status": status, "result": result,
        }
        factor_store = Mock()
        factor_store.definitions.return_value = []
        factor_store.expression_library.return_value = []
        factor_store.update_mined_candidate.return_value = True

        with patch.object(api_server, "RESEARCH_LEDGER", ledger), \
             patch.object(api_server, "FACTOR_STORE", factor_store), \
             patch.object(api_server, "_factor_mining_scope", return_value=("scope", "pipeline")), \
             patch.object(api_server, "_bars_from_payload", return_value=[]), \
             patch.object(api_server, "build_factor_panel", return_value=object()), \
             patch.object(api_server, "evaluate_expression", return_value=SimpleNamespace(values=[[1.0, 2.0, 3.0]])) as evaluate_expression, \
             patch.object(api_server, "evaluate_factor_panel", return_value={"horizons": {"5": {
                 "mean_ic": 0.1, "raw_p_value": 0.01, "dsr_p_value": 0.04,
             }}}), \
             patch.object(api_server, "_factor_panel_correlation", return_value=None), \
             patch("quant.api.mining_acceptance.cycle_policy", return_value=PortfolioPolicy()), \
             patch("quant.api.mining_acceptance.refresh_portfolios", return_value={}), \
             patch("quant.api.mining_acceptance.portfolio_report", return_value={}), \
             patch("quant.api.mining_acceptance.arena_candidate_filter", side_effect=lambda path, sid, rows: rows):
            accepted = api_server._accept_factor_mining_run("run-1")

        result = accepted["result"]
        self.assertEqual(result["N_tested"], 90)
        self.assertEqual([item["run_id"] for item in result["included_runs"]], ["run-1", "run-2"])
        self.assertEqual(result["evaluated_candidates"], 2)
        self.assertEqual(evaluate_expression.call_count, 2)
        duplicate = next(item for item in result["candidates"] if item["expression"] == "cs_rank(close)")
        self.assertEqual(duplicate["source_run_ids"], ["run-1", "run-2"])
        self.assertEqual(result["effective_discoveries"], 2)
        self.assertEqual(duplicate["status"], "accepted")
        self.assertEqual(duplicate["cross_universe_reference"]["index_code"], "000905.SH")
        self.assertEqual(duplicate["cross_universe_reference"]["role"], "report_only_not_for_selection_or_acceptance")

    def test_sealed_factor_batch_can_be_claimed_once_and_replay_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(Path(directory) / "research.sqlite3")
            first = ledger.save_factor_mining_run("run-1", sealed_test_id=17, data_version="d1", n_tested=80, result={"candidates": []})
            self.assertEqual(first["n_tested"], 80)
            ledger.save_factor_mining_run("run-2", sealed_test_id=17, data_version="d1", n_tested=10, result={"candidates": []})
            ledger.save_factor_mining_run("run-other", sealed_test_id=18, data_version="d2", n_tested=7, result={"candidates": []})
            self.assertEqual(ledger.factor_mining_trial_total(17), 90)
            self.assertEqual(ledger.factor_mining_trial_total(), 97)
            self.assertEqual({item["run_id"] for item in ledger.factor_mining_runs_for_sealed_test(17)}, {"run-1", "run-2"})
            self.assertTrue(ledger.claim_factor_mining_acceptance("run-1")["allowed"])
            ledger.finish_factor_mining_acceptance("run-1", status="needs_review", result={"passed": False})
            replay = ledger.claim_factor_mining_acceptance("run-2")
            self.assertFalse(replay["allowed"])
            self.assertEqual(replay["code"], "sealed_factor_batch_already_opened")
            connection = sqlite3.connect(Path(directory) / "research.sqlite3")
            try:
                count = connection.execute("SELECT COUNT(*) FROM factor_mining_acceptance_attempts WHERE status='blocked_repeat'").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 1)
            self.assertEqual({item["status"] for item in ledger.factor_mining_runs_for_sealed_test(17)}, {"acceptance_needs_review"})
            self.assertEqual(ledger.factor_mining_run("run-other")["status"], "awaiting_acceptance")

    def test_candidate_is_not_active_until_sealed_acceptance_and_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FactorRegistryStore(Path(directory) / "factors.sqlite3")
            row = store.save_mined_candidates(
                [{"expression": "cs_rank(close)", "mean_rank_ic": 0.2, "n_tested": 10}],
                data_version="d1", code_version="c1", sealed_test_id=2, run_id="run",
            )[0]
            self.assertNotIn(row["key"], [item.key for item in store.definitions(enabled_only=True)])
            self.assertTrue(store.update_mined_candidate(row["key"], status="accepted", metadata={"p": 0.01}))
            self.assertTrue(store.promote_expression(row["key"]))
            active = [item for item in store.definitions(enabled_only=True) if item.key == row["key"]]
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].expression, "cs_rank(close)")

    def test_noise_factors_do_not_survive_trial_correction(self) -> None:
        rng = random.Random(20260927)
        p_values = []
        for _ in range(2000):
            noise_ic = [rng.gauss(0.0, 0.03) for _ in range(120)]
            p_values.append(float(factor_ic_significance(noise_ic, n_tested=2000)["dsr_p_value"] or 1.0))
        adjusted = benjamini_hochberg(p_values, q=0.05)
        discoveries = sum(bool(value) for value in adjusted["rejected"])
        self.assertLessEqual(discoveries / len(p_values), 0.05)


if __name__ == "__main__":
    unittest.main()
