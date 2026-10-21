"""Isolated fixtures: never use production ledgers, data, credentials or sealed sets."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.backtest_service import run_backtest_request
from quant.backtest import CostModel
from quant.backtest.engine import PortfolioSnapshot
from quant.data.schema import Bar, DataSnapshot
from quant.factors.composite import FactorComposite
from quant.factors.participation import ParticipationAudit
from quant.reports.training_weights import compare_training_weights
from quant.research import ExperimentRecord, ResearchLedger
from quant.strategies.audited_baseline import AuditedBaseline, weight_profiles
from quant.strategies.registry import (
    build_research_strategy,
    build_strategy,
    resolve_strategy_config,
)
from quant.validation.strategy import validate_strategy


def history(days=21, count=10):
    return {
        f"000{i:03}.SZ": [
            Bar(
                f"000{i:03}.SZ",
                date(2025, 1, 1) + timedelta(days=j),
                10 + i + j * 0.01,
                12 + i + j * 0.01,
                9 + i + j * 0.01,
                10 + i + j * 0.01,
                1e7,
            )
            for j in range(days)
        ]
        for i in range(count)
    }


def portfolio(positions=None):
    return PortfolioSnapshot(date(2025, 2, 1), 1e6, positions or {}, 0, 1e6)


def financials(day, symbols):
    return {
        symbol: {"roe": 0.1, "netprofit_yoy": 0.2, "tr_yoy": 0.3, "debt_to_assets": 0.4}
        for symbol in symbols
    }, {"status": "available"}


def test_strict_complete_and_legacy_available_are_distinct():
    rows = history()
    financial = financials(None, list(rows))[0]
    financial.pop(next(iter(rows)))
    legacy = FactorComposite()
    strict = FactorComposite(coverage_policy="complete", minimum_coverage=0.9)
    assert len(legacy.rank_scores(rows, fundamental_values=financial)) == 10
    scores = strict.rank_scores(rows, fundamental_values=financial)
    assert len(scores) == 9
    assert all(len(item.active_factor_keys) == 6 for item in scores)
    report = strict.audit.report()
    assert report["latest"]["eligible_symbols"] == 9
    assert all(item["used"] == 9 for item in report["factors"])
    financial.pop(list(rows)[1])
    assert strict.rank_scores(rows, fundamental_values=financial) == []
    assert strict.audit.report()["latest"]["eligible_symbols"] == 0
    assert any(
        item["missing_reason"] == "missing_point_in_time_financial"
        for item in report["factors"]
    )


def test_invalid_financial_values_are_not_coverage():
    rows = history()
    values = financials(None, list(rows))[0]
    values[next(iter(rows))]["roe"] = float("nan")
    values[list(rows)[1]]["roe"] = float("inf")
    composite = FactorComposite(coverage_policy="complete", minimum_coverage=0.9)
    assert composite.rank_scores(rows, fundamental_values=values) == []
    roe = next(
        item for item in composite.audit.report()["factors"] if "净资产" in item["name"]
    )
    assert roe["covered"] == 8 and roe["used"] == 0


def test_individual_weights_and_finite_legacy_scores():
    rows = history()
    composite = FactorComposite()
    scores = composite.rank_scores(rows)
    assert len(scores) == 10
    assert all(len(item.active_factor_keys) == 2 for item in scores)
    # Explicit key weights override type weights; zero-weight factors are not used.
    key = composite.definitions[0].key
    override = FactorComposite(weights={key: 0})
    override.rank_scores(rows)
    assert (
        next(item for item in override.audit.report()["factors"] if item["key"] == key)[
            "used"
        ]
        == 0
    )
    with pytest.raises(ValueError, match="nonnegative"):
        FactorComposite(weights={"momentum": -1})


def test_pair_diagnostics_require_twenty_nonconstant_common_values():
    definitions = FactorComposite().definitions[:2]
    left, right = [item.key for item in definitions]
    symbols = [str(i) for i in range(20)]
    ranks = {
        symbol: {left: i / 20, right: 1 - i / 20} for i, symbol in enumerate(symbols)
    }
    audit = ParticipationAudit()
    audit.observe(
        definitions, ranks, ranks, symbols, set(symbols), {left: 1, right: 1}, "fixture"
    )
    pair = audit.report()["correlations"][0]
    assert pair["mean_rank_correlation"] == pytest.approx(-1)
    assert pair["redundancy_warning"]
    short = ParticipationAudit()
    short.observe(
        definitions,
        ranks,
        ranks,
        symbols[:19],
        set(symbols[:19]),
        {left: 1, right: 1},
        "fixture",
    )
    assert short.report()["correlations"] == []
    assert ParticipationAudit().report()["status"] == "not_evaluated"


def test_candidate_price_full_missing_proof_and_cash_reserve():
    rows = history()
    price = AuditedBaseline(rebalance_every=1)
    orders = price.orders_for_date(date(2025, 2, 1), rows, portfolio())
    assert len(orders) == 5
    assert all(order.quantity % 100 == 0 for order in orders)
    assert (
        sum(order.quantity * rows[order.symbol][-1].close for order in orders) <= 980000
    )
    assert len(price.factor_composite.definitions) == 2
    full = AuditedBaseline(
        full=True, rebalance_every=1, fundamental_provider=financials
    )
    assert full.orders_for_date(date(2025, 2, 1), rows, portfolio())
    assert len(full.factor_composite.definitions) == 6
    blocked = AuditedBaseline(
        full=True,
        rebalance_every=1,
        fundamental_provider=lambda day, symbols: ({}, {"status": "missing_proof"}),
    )
    assert blocked.orders_for_date(date(2025, 2, 1), rows, portfolio()) == []
    assert blocked.factor_metadata()["blocked_rebalances"] == 1
    assert all(
        item["used"] == 0 for item in blocked.factor_composite.audit.report()["factors"]
    )


def test_retention_deadband_and_caps_cannot_retain_rank_nine():
    strategy = AuditedBaseline()
    bar = next(iter(history().values()))[-1]
    scores = [(str(i), 10 - i, bar, ()) for i in range(1, 10)]
    selected = strategy._select(scores, portfolio({"8": 100, "9": 100}))
    assert "8" in selected and "9" not in selected and len(selected) == 5
    assert strategy._select(scores[:4], portfolio()) == {}
    assert strategy._skip_small_adjustment("8", 196000, -1000, portfolio({"8": 100}))
    assert not strategy._skip_small_adjustment(
        "8", 210000, -1000, portfolio({"8": 100})
    )
    assert not strategy._skip_small_adjustment("new", 0, 1000, portfolio())
    assert strategy.factor_metadata()["outperformance_proven"] is False


@pytest.mark.parametrize("name", ["baseline_price_audited", "baseline_full_audited"])
def test_registry_presets_are_fixed_and_not_sensitivity_candidates(name):
    assert isinstance(build_strategy(name, rebalance_every=5), AuditedBaseline)
    with pytest.raises(ValueError):
        resolve_strategy_config(name, 10, 5)
    with pytest.raises(ValueError, match="不开放"):
        build_research_strategy(name, lookback=20, top_n=5, rebalance_every=5)


def compare(tmp_path, *, full=False, factory=AuditedBaseline, **extra):
    ledger = ResearchLedger(tmp_path / "isolated.sqlite3")
    name = "baseline_full_audited" if full else "baseline_price_audited"

    def record(strategy, dataset, parameters, status, notes):
        return ledger.record(
            ExperimentRecord(strategy, dataset, parameters, status, notes)
        )

    parent = record(name, "fixture", {}, "started", "parent")
    bars = [bar for values in history(days=70).values() for bar in values]
    result = compare_training_weights(
        bars,
        strategy_name=name,
        parent_id=parent,
        dataset="fixture",
        definitions=FactorComposite().definitions,
        initial_cash=1e6,
        cost_model=CostModel(),
        rebalance_every=5,
        ledger=ledger,
        record=record,
        factory=factory,
        **extra,
    )
    return result, ledger, name


def test_weight_trials_use_only_first_training_window_and_all_are_counted(tmp_path):
    result, ledger, name = compare(tmp_path)
    assert result["training_end"] == "2025-02-09"
    assert result["trial_count"] == 3 and not result["automatic_selection"]
    assert ledger.count(name) == 4
    assert {row["profile"] for row in result["rows"]} == set(weight_profiles(False))
    assert all(row["status"] == "available" for row in result["rows"])
    assert all(
        row["participation"]["latest"]["day"] < result["training_end"]
        for row in result["rows"]
    )
    assert all(row["experiment_id"] > 1 for row in result["rows"])


def test_missing_pit_is_incomplete_not_zero_return_success(tmp_path):
    def unavailable(**kwargs):
        return AuditedBaseline(
            **kwargs,
            fundamental_provider=lambda d, s: ({}, {"status": "missing_proof"}),
        )

    result, ledger, name = compare(tmp_path, full=True, factory=unavailable)
    assert result["status"] == "insufficient_factor_coverage"
    assert all(row["annualized_return"] is None for row in result["rows"])
    assert ledger.count(name) == 4


def test_training_comparison_preserves_corporate_action_policy(tmp_path):
    calls = []
    compare(
        tmp_path,
        corporate_actions=True,
        action_provider=lambda day: calls.append(day) or [],
    )
    assert calls and max(calls) <= date(2025, 2, 9)


def test_failed_comparison_is_retained_in_budget(tmp_path):
    def broken(**kwargs):
        raise ValueError("fixture failure")

    with pytest.raises(ValueError, match="fixture failure"):
        compare(tmp_path, factory=broken)
    ledger = ResearchLedger(tmp_path / "isolated.sqlite3")
    assert ledger.count("baseline_price_audited") == 2
    assert ledger.latest("baseline_price_audited")["status"] == "failed"


def test_audited_budget_guard_precedes_market_access(tmp_path):
    ledger = ResearchLedger(tmp_path / "budget.sqlite3", budget=3)
    dependencies = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _require_strategy_dependencies=Mock(),
        _bounded_int=lambda value, low, high: int(value),
        _as_float=float,
        _optional_date=lambda value: None,
    )
    status, result = run_backtest_request(
        {"strategy": "baseline_price_audited"}, dependencies
    )
    assert status == 409 and result["code"] == "research_budget_exceeded"
    assert ledger.count("baseline_price_audited") == 0
    status, _ = run_backtest_request(
        {"strategy": "baseline_price_audited", "policy_comparison": True}, dependencies
    )
    assert status == 400


def test_service_persists_audit_and_three_train_trials_without_live_data(tmp_path):
    from datetime import UTC, datetime

    ledger = ResearchLedger(tmp_path / "api-isolated.sqlite3")
    def record(strategy, dataset, parameters, status, notes):
        return ledger.record(ExperimentRecord(strategy, dataset, parameters, status, notes))
    rows = history(days=75, count=22)
    bars = [bar for values in rows.values() for bar in values]
    deps = SimpleNamespace(RESEARCH_LEDGER=ledger, _require_strategy_dependencies=Mock(),
        _bounded_int=lambda value, low, high: int(value), _as_float=float,
        _optional_date=lambda value: None, _report_backtest_progress=Mock(),
        _filter_backtest_universe=lambda values, universe: values,
        _require_complete_universe=Mock(), _scoped_dataset_version=lambda value, version: value,
        _dataset_metadata_with_universe=lambda metadata, universe: {"limits_available": False},
        _sealed_scope=lambda *args, **kwargs: "fixture-only", _sealed_pipeline_key=lambda scope: scope,
        _ensure_sealed_snapshot=lambda *args, **kwargs: {
            "id": 1, "sealed_start": "2025-05-01", "sealed_end": "2025-05-30", "status": "sealed"},
        SEALED_TEST_DAYS=30, _research_bars=lambda values, sealed: values,
        FACTOR_STORE=SimpleNamespace(definitions=lambda **kwargs: FactorComposite().definitions),
        _record_research_experiment=record, build_strategy=build_strategy,
        _backtest_operational_reports=lambda *args, **kwargs: {"capacity": {}, "industry_trade": {}},
        _strict_pit_brinson_report=lambda *args: {}, validate_strategy=validate_strategy,
        _validation_gate=lambda *args, **kwargs: {"status": "not_accepted"},
        _limits_data_quality=lambda *args, **kwargs: {"status": "unknown"})
    prepared = {"universe": {"symbols": list(rows), "version": "fixture", "metadata": {}},
        "bars": bars, "snapshot": DataSnapshot("fixture", "fixture", datetime.now(UTC))}
    status, result = run_backtest_request({"strategy": "baseline_price_audited"}, deps,
        _prepared_market_data=prepared)
    assert status == 200, result
    assert result["research"]["run_count"] == 4
    assert result["factor_pipeline"]["coverage_policy"] == "complete"
    assert len(result["training_weight_comparison"]["rows"]) == 3
    assert result["strategy_validation"]["passed"] is False  # short fixture is NOT acceptance
    saved = ledger.latest("baseline_price_audited", kind="backtest")
    assert saved["parameters"]["factor_pipeline"]["participation"]["evaluations"] > 0
    assert saved["parameters"]["training_weight_comparison"]["automatic_selection"] is False
