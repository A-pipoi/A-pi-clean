"""Daily theoretical long-short analytics, separate from IC's legacy DSR p."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from statistics import NormalDist, mean, stdev, variance
from typing import cast

from quant.backtest.costs import CostModel
from quant.factors.expression import FactorPanel, Panel


@dataclass(frozen=True)
class PortfolioPolicy:
    mode: str = "ic_only"
    quantiles: int = 10
    benchmark_annual_sharpe: float = 0.0
    commission_rate: float = 0.00025
    stamp_tax_rate: float | None = None
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    turnovers: tuple[float, ...] = (0.05, 0.10, 0.20)
    neutral_turnover: float = 0.10
    min_dsr: float = 0.0
    min_net_sharpe: float = 0.0

    @classmethod
    def parse(cls, value: dict | None = None) -> PortfolioPolicy:
        data = dict(value or {})
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError(
                "Unknown portfolio policy fields; N_tested is server-owned"
            )
        if "turnovers" in data:
            if not isinstance(data["turnovers"], (tuple, list)):
                raise ValueError("turnovers must be a list")
            data["turnovers"] = tuple(data["turnovers"])
        policy = cls(**data)
        if policy.mode not in {"ic_only", "ic_and_portfolio"}:
            raise ValueError("Invalid acceptance mode")
        if type(policy.quantiles) is not int or policy.quantiles not in {5, 10}:
            raise ValueError("Use 5 or 10 layers")
        for key in (
            "benchmark_annual_sharpe",
            "commission_rate",
            "transfer_fee_rate",
            "slippage_bps",
            "neutral_turnover",
            "min_dsr",
            "min_net_sharpe",
        ):
            number = getattr(policy, key)
            if type(number) not in (int, float) or not math.isfinite(number):
                raise ValueError("Finite numeric policy parameters required")
        if policy.stamp_tax_rate is not None and (
            type(policy.stamp_tax_rate) not in (int, float)
            or not math.isfinite(policy.stamp_tax_rate)
            or not 0 <= policy.stamp_tax_rate <= 0.1
        ):
            raise ValueError("Invalid stamp duty override")
        if (
            not 0 <= policy.commission_rate <= 0.1
            or not 0 <= policy.transfer_fee_rate <= 0.1
            or not 0 <= policy.slippage_bps <= 1000
            or not 0 <= policy.min_dsr <= 1
            or not 1 <= len(policy.turnovers) <= 10
            or any(
                type(x) not in (int, float) or not math.isfinite(x) or not 0 < x <= 1
                for x in policy.turnovers
            )
            or len(set(policy.turnovers)) != len(policy.turnovers)
            or policy.neutral_turnover not in policy.turnovers
        ):
            raise ValueError("Invalid cost or threshold policy")
        return policy


def moments(returns: list[float]) -> dict:
    n = len(returns)
    if n < 3 or any(not math.isfinite(x) for x in returns):
        return {"status": "insufficient_data", "observations": n}
    average = mean(returns)
    sd = stdev(returns)
    if sd == 0:
        return {"status": "zero_variance", "observations": n}
    m2 = mean([(x - average) ** 2 for x in returns])
    skew = mean([(x - average) ** 3 for x in returns]) / m2**1.5
    kurtosis = mean([(x - average) ** 4 for x in returns]) / m2**2
    sr = average / sd
    denominator = 1 - skew * sr + (kurtosis - 1) * sr * sr / 4
    return {
        "status": "available" if denominator > 0 else "invalid_moments",
        "observations": n,
        "daily_sharpe": sr,
        "skewness": skew,
        "pearson_kurtosis": kurtosis,
        "sr_sampling_variance": denominator / (n - 1),
    }


def probability_sharpe(returns: list[float], *, benchmark_daily: float = 0) -> dict:
    result = moments(returns)
    if result["status"] != "available":
        return {**result, "probability": None, "benchmark_daily": benchmark_daily}
    z = (result["daily_sharpe"] - benchmark_daily) / math.sqrt(
        result["sr_sampling_variance"]
    )
    probability = NormalDist().cdf(z)
    return {
        **result,
        "probability": probability,
        "benchmark_daily": benchmark_daily,
        "interpretation": "above_95_percent"
        if probability >= 0.95
        else "below_5_percent"
        if probability <= 0.05
        else "inconclusive",
        "assumption": "IID daily returns, unannualized SR; not a posterior guarantee",
    }


def deflated_sharpe(
    returns: list[float], *, n_tested: int, trial_sharpes: list[float] | None = None
) -> dict:
    if type(n_tested) is not int or n_tested < 1:
        return {
            "status": "no_counted_trials",
            "probability": None,
            "n_tested": n_tested,
        }
    selected = moments(returns)
    if selected["status"] != "available":
        return {**selected, "probability": None, "n_tested": n_tested}
    population = [x for x in (trial_sharpes or []) if math.isfinite(x)]
    empirical = variance(population) if len(population) >= 2 else None
    # Old GP records kept finalists, not every trial's SR. Explicit fallback,
    # never pretend that finalist dispersion is the full trial population.
    floor = max(1 / (len(returns) - 1), selected["sr_sampling_variance"])
    sr_variance = max(floor, empirical or 0)
    normal = NormalDist()
    euler = 0.5772156649015329
    expected = (
        0.0
        if n_tested == 1
        else math.sqrt(sr_variance)
        * (
            (1 - euler) * normal.inv_cdf(1 - 1 / n_tested)
            + euler * normal.inv_cdf(1 - 1 / (n_tested * math.e))
        )
    )
    return {
        **probability_sharpe(returns, benchmark_daily=expected),
        "n_tested": n_tested,
        "variance_daily_sr": sr_variance,
        "expected_max_daily_sr": expected,
        "observed_trial_sharpes": len(population),
        "empirical_variance": empirical,
        "variance_method": (
            "max(observed_training_SR_dispersion, sampling_variance_floor)"
        ),
        "variance_population_complete": len(population) == n_tested,
        "trial_count_source": "research_ledger_same_sealed_window",
        "warning": (
            "Full counted N retained, no effective-N discount. Approximation: "
            "dependent trials and incomplete historic SR population; "
            "daily IID assumption."
        ),
    }


def return_metrics(returns: list[float]) -> dict:
    result = moments(returns)
    empty = {
        "observations": len(returns),
        "annual_sharpe": None,
        "annual_return": None,
        "annual_volatility": None,
        "max_drawdown": None,
        "calmar": None,
        "total_return": None,
    }
    if not returns or any(not math.isfinite(x) or x <= -1 for x in returns):
        return {**empty, "status": "invalid_nav" if returns else "insufficient_data"}
    nav, peak, drawdown = 1.0, 1.0, 0.0
    for value in returns:
        nav *= 1 + value
        peak = max(peak, nav)
        drawdown = max(drawdown, 1 - nav / peak)
    annual_log = sum(math.log1p(x) for x in returns) * 252 / len(returns)
    annual = math.expm1(annual_log) if annual_log < 700 else None
    daily_sr = result.get("daily_sharpe")
    return {
        **empty,
        "status": result["status"],
        "annual_sharpe": daily_sr * math.sqrt(252) if daily_sr is not None else None,
        "annual_return": annual,
        "total_return": nav - 1,
        "annual_volatility": stdev(returns) * math.sqrt(252)
        if len(returns) > 1
        else None,
        "max_drawdown": drawdown,
        "calmar": annual / drawdown if annual is not None and drawdown > 0 else None,
    }


def daily_spreads(
    panel: FactorPanel,
    values: Panel,
    *,
    quantiles: int,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    if quantiles not in {5, 10}:
        raise ValueError("Use 5 or 10 layers")
    prices = panel.fields.get("open")
    if prices is None:
        return {
            "rows": [],
            "skipped_dates": [],
            "warmup_dates": [],
            "reason": "execution_open_prices_unavailable",
        }
    rows, skipped, warmup = [], [], []
    signal_started = False
    for signal in range(len(panel.dates) - 2):
        entry, exit_row = signal + 1, signal + 2
        if (start is not None and panel.dates[signal] < start) or (
            end is not None and panel.dates[exit_row] > end
        ):
            continue
        selected = [
            i
            for i, x in enumerate(values[signal])
            if x is not None and math.isfinite(x)
        ]
        selected.sort(key=lambda i: (values[signal][i], panel.symbols[i]))
        if not selected and not signal_started:
            warmup.append(panel.dates[exit_row].isoformat())
            continue
        signal_started = True
        if (
            len(selected) < quantiles
            or values[signal][selected[0]] == values[signal][selected[-1]]
        ):
            skipped.append(panel.dates[exit_row].isoformat())
            continue
        # Do not select survivors based on future price availability.
        if any(
            (price := prices[r][i]) is None or not math.isfinite(price) or price <= 0
            for r in (entry, exit_row)
            for i in selected
        ):
            skipped.append(panel.dates[exit_row].isoformat())
            continue
        buckets: list[list[float]] = [[] for _ in range(quantiles)]
        for position, column in enumerate(selected):
            buckets[min(quantiles - 1, position * quantiles // len(selected))].append(
                cast(float, prices[exit_row][column])
                / cast(float, prices[entry][column])
                - 1
            )
        rows.append(
            {
                "signal_date": panel.dates[signal].isoformat(),
                "entry_date": panel.dates[entry].isoformat(),
                "date": panel.dates[exit_row].isoformat(),
                "return": mean(buckets[-1]) - mean(buckets[0]),
            }
        )
    return {"rows": rows, "skipped_dates": skipped, "warmup_dates": warmup}


def portfolio_report(
    panel: FactorPanel,
    values: Panel,
    *,
    n_tested: int,
    policy: PortfolioPolicy | None = None,
    trial_sharpes: list[float] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    policy = policy or PortfolioPolicy()
    spread = daily_spreads(
        panel, values, quantiles=policy.quantiles, start=start, end=end
    )
    returns = [row["return"] for row in spread["rows"]]
    cost = CostModel(
        commission_rate=policy.commission_rate,
        stamp_tax_rate=policy.stamp_tax_rate,
        transfer_fee_rate=policy.transfer_fee_rate,
        fixed_slippage_bps=policy.slippage_bps,
    )
    scenarios = []
    curve = []
    gross_nav = 1.0
    gross_bankrupt = False
    net_nav = {str(rate): 1.0 for rate in policy.turnovers}
    net_bankrupt = {key: False for key in net_nav}
    net_returns: dict[str, list[float]] = {key: [] for key in net_nav}
    for row in spread["rows"]:
        round_trip = (
            2 * cost.commission_rate
            + cost.stamp_rate(date.fromisoformat(row["date"]))
            + 2 * cost.transfer_fee_rate
            + 2 * cost.fixed_slippage_bps / 10000
        )
        gross_nav *= 1 + row["return"]
        gross_bankrupt |= row["return"] <= -1
        for rate in policy.turnovers:
            key = str(rate)
            net = row["return"] - 2 * rate * round_trip
            net_returns[key].append(net)
            net_nav[key] *= 1 + net
            net_bankrupt[key] |= net <= -1
        curve.append(
            {
                **row,
                "gross_nav": gross_nav if not gross_bankrupt else None,
                "net_nav": {
                    k: v if not net_bankrupt[k] else None for k, v in net_nav.items()
                },
            }
        )
    for rate in policy.turnovers:
        scenarios.append(
            {
                "daily_one_way_turnover": rate,
                "metrics": return_metrics(net_returns[str(rate)]),
            }
        )
    gross = return_metrics(returns)
    return {
        "status": "partial_data" if spread["skipped_dates"] else gross["status"],
        "quantiles": policy.quantiles,
        "gross": gross,
        "net": scenarios,
        "psr": probability_sharpe(
            returns, benchmark_daily=policy.benchmark_annual_sharpe / math.sqrt(252)
        ),
        "dsr": deflated_sharpe(returns, n_tested=n_tested, trial_sharpes=trial_sharpes),
        "curve": curve,
        "skipped_dates": spread["skipped_dates"],
        "warmup_dates": spread["warmup_dates"],
        "unavailable_reason": spread.get("reason"),
        "policy": asdict(policy),
        "protocol": {
            "entry": "next_session_open",
            "exit": "following_session_open",
            "frequency": "daily_nonoverlapping",
            "gross_exposure": 2,
            "annual_sessions": 252,
            "future_filtering": False,
            "execution_evidence": False,
        },
        "note": (
            "Theoretical Top-Bottom, not executable A-share shorting. "
            "Cost scenarios are assumed turnover per leg; excludes minimum "
            "commission, borrow/finance costs, limits, suspension and market "
            "impact. Missing days not filled; PSR/DSR are IID approximations, "
            "not profit guarantees."
        ),
    }


def portfolio_gate(report: dict | None, policy: PortfolioPolicy) -> dict:
    report = report or {}
    dsr = report.get("dsr", {}).get("probability")
    neutral = next(
        (
            row["metrics"].get("annual_sharpe")
            for row in report.get("net", [])
            if row["daily_one_way_turnover"] == policy.neutral_turnover
        ),
        None,
    )
    passed = (
        report.get("status") == "available"
        and dsr is not None
        and dsr > policy.min_dsr
        and neutral is not None
        and neutral > policy.min_net_sharpe
    )
    return {
        "mode": policy.mode,
        "passed": passed,
        "training_dsr": dsr,
        "neutral_net_sharpe": neutral,
        "thresholds": asdict(policy),
        "required": policy.mode == "ic_and_portfolio",
        "basis": "training_only; never optimize using sealed returns",
    }
