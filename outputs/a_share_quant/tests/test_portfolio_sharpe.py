import math
from dataclasses import replace
from statistics import NormalDist, mean, stdev

import pytest

from quant.api.mining_portfolio import (
    cycle_policy,
    set_cycle_policy,
    training_portfolios,
)
from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.portfolio_metrics import (
    PortfolioPolicy,
    daily_spreads,
    deflated_sharpe,
    portfolio_gate,
    portfolio_report,
    probability_sharpe,
    return_metrics,
)
from quant.research import ResearchLedger
from tests.test_factor_mining import _bars


def series():
    return [
        0.0008 + 0.009 * math.sin(i * 0.73) + 0.002 * math.cos(i * 0.31)
        for i in range(252)
    ]


def test_sharpe_annualization_nav_drawdown_calmar():
    returns = [0.02, -0.03, 0.01, 0.04, -0.02]
    result = return_metrics(returns)
    assert result["annual_sharpe"] == pytest.approx(
        mean(returns) / stdev(returns) * math.sqrt(252)
    )
    nav = math.prod(1 + x for x in returns)
    assert result["annual_return"] == pytest.approx(nav ** (252 / 5) - 1)
    assert result["max_drawdown"] == pytest.approx(0.03)
    assert result["calmar"] == pytest.approx(result["annual_return"] / 0.03)


def test_psr_reference_formula_uses_daily_sr_and_pearson_kurtosis():
    values = series()
    result = probability_sharpe(values, benchmark_daily=0.01)
    sr = mean(values) / stdev(values)
    average = mean(values)
    m2 = mean([(x - average) ** 2 for x in values])
    skew = mean([(x - average) ** 3 for x in values]) / m2**1.5
    kurtosis = mean([(x - average) ** 4 for x in values]) / m2**2
    z = (
        (sr - 0.01)
        * math.sqrt(251)
        / math.sqrt(1 - skew * sr + (kurtosis - 1) * sr * sr / 4)
    )
    assert result["probability"] == pytest.approx(NormalDist().cdf(z))


def test_dsr_known_N_reference_and_more_trials_deflate():
    values = series()
    first = deflated_sharpe(values, n_tested=1)
    result = deflated_sharpe(values, n_tested=100, trial_sharpes=[-0.1, 0.1])
    euler = 0.5772156649015329
    expected = math.sqrt(0.02) * (
        (1 - euler) * NormalDist().inv_cdf(0.99)
        + euler * NormalDist().inv_cdf(1 - 1 / (100 * math.e))
    )
    assert result["expected_max_daily_sr"] == pytest.approx(expected)
    assert result["probability"] == pytest.approx(
        probability_sharpe(values, benchmark_daily=expected)["probability"]
    )
    assert result["probability"] < first["probability"]
    assert first["probability"] == probability_sharpe(values)["probability"]
    assert result["variance_population_complete"] is False


@pytest.mark.parametrize("values", [[], [0, 0, 0], [float("nan"), 0.01, 0.02]])
def test_unavailable_not_infinite_or_fake_probability(values):
    assert probability_sharpe(values)["probability"] is None


def test_nonpositive_nav_is_not_annualized():
    assert return_metrics([0.1, -1.2, 0.2])["annual_return"] is None


def test_missing_execution_prices_remain_unavailable_not_an_acceptance_crash():
    panel = build_factor_panel(_bars(symbols=tuple("ABCDEFGHIJ")), data_version="fixture")
    values = evaluate_expression("close", panel).values
    del panel.fields["open"]
    report = portfolio_report(panel, values, n_tested=2)
    assert report["status"] == "insufficient_data"
    assert report["unavailable_reason"] == "execution_open_prices_unavailable"
    assert report["gross"]["annual_sharpe"] is None
    assert not portfolio_gate(report, PortfolioPolicy(mode="ic_and_portfolio"))["passed"]


def test_leading_warmup_is_not_a_missing_execution_day():
    panel = build_factor_panel(
        _bars(symbols=tuple("ABCDEFGHIJ")), data_version="fixture"
    )
    values = evaluate_expression("ts_mean(close, 5)", panel).values
    report = portfolio_report(panel, values, n_tested=1)
    assert len(report["warmup_dates"]) == 4
    assert report["skipped_dates"] == []
    assert report["status"] == "available"
    values[10] = [None] * len(panel.symbols)
    report = portfolio_report(panel, values, n_tested=1)
    assert report["status"] == "partial_data"
    assert report["skipped_dates"] == [panel.dates[12].isoformat()]


def test_daily_execution_lag_missing_future_does_not_select_survivors():
    panel = build_factor_panel(
        _bars(symbols=tuple("ABCDEFGHIJ")), data_version="fixture"
    )
    values = evaluate_expression("close", panel).values
    spread = daily_spreads(panel, values, quantiles=10)
    assert (
        spread["rows"][0]["signal_date"]
        < spread["rows"][0]["entry_date"]
        < spread["rows"][0]["date"]
    )
    assert len(spread["rows"]) == len(panel.dates) - 2
    panel.fields["open"][2][0] = None
    partial = daily_spreads(panel, values, quantiles=10)
    assert partial["skipped_dates"] == [
        panel.dates[2].isoformat(),
        panel.dates[3].isoformat(),
    ]
    report = portfolio_report(panel, values, n_tested=3)
    assert report["status"] == "partial_data"
    assert not portfolio_gate(report, PortfolioPolicy(mode="ic_and_portfolio"))[
        "passed"
    ]


def test_gross_net_three_turnovers_costs_dated_and_configurable():
    panel = build_factor_panel(
        _bars(symbols=tuple("ABCDEFGHIJ")), data_version="fixture"
    )
    values = evaluate_expression("close", panel).values
    result = portfolio_report(panel, values, n_tested=7)
    assert len(result["net"]) == 3
    sharpes = [row["metrics"]["annual_sharpe"] for row in result["net"]]
    assert result["gross"]["annual_sharpe"] > sharpes[0] > sharpes[1] > sharpes[2]
    changed = portfolio_report(
        panel, values, n_tested=7, policy=PortfolioPolicy(slippage_bps=20)
    )
    assert (
        changed["net"][0]["metrics"]["annual_return"]
        < result["net"][0]["metrics"]["annual_return"]
    )
    assert result["dsr"]["n_tested"] == 7


@pytest.mark.parametrize(
    "payload",
    [
        {"n_tested": 1},
        {"mode": "profit"},
        {"quantiles": 3},
        {"slippage_bps": -1},
        {"turnovers": [0.2]},
        {"min_dsr": 2},
        {"commission_rate": True},
    ],
)
def test_invalid_policy_and_spoofed_N_rejected(payload):
    with pytest.raises(ValueError):
        PortfolioPolicy.parse(payload)


def test_policy_default_legacy_preregister_and_dynamic_ledger_N(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    sealed = ledger.create_sealed_test(
        strategy_version="baseline",
        factor_pipeline_version="p",
        data_version="v",
        sealed_start="2026-01-01",
        sealed_end="2026-02-01",
        dataset_hash="fixture",
        bars=[],
        metadata={},
        parameters={},
    )
    sid = sealed["id"]
    assert cycle_policy(ledger.path, sid).mode == "ic_only"
    set_cycle_policy(ledger.path, sid, {"mode": "ic_and_portfolio", "min_dsr": 0.95})
    assert cycle_policy(ledger.path, sid).min_dsr == 0.95
    ledger.save_factor_mining_run(
        "r",
        sealed_test_id=sid,
        data_version="v",
        n_tested=19,
        result={"candidates": []},
    )
    with pytest.raises(PermissionError):
        set_cycle_policy(ledger.path, sid, {"mode": "ic_only"})
    panel = build_factor_panel(
        _bars(symbols=tuple("ABCDEFGHIJ")), data_version="fixture"
    )
    reports = training_portfolios(
        panel, evaluate_expression("close", panel).values, ledger=ledger, sealed_id=sid
    )
    assert reports["5"]["dsr"]["n_tested"] == reports["10"]["dsr"]["n_tested"] == 19
    assert ledger.factor_mining_trial_total(sid) == 19
    assert (
        portfolio_gate(
            reports["10"], replace(cycle_policy(ledger.path, sid), min_dsr=1)
        )["passed"]
        is False
    )
