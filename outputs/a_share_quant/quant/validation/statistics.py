from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryStats:
    annualized_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    observations: int
    confidence_note: str
    mean_return: float | None = None
    mean_ci95: tuple[float, float] | None = None
    p_value: float | None = None
    deflated_sharpe_p_value: float | None = None
    effective_observations: float | None = None


def summarize_returns(returns: list[float], periods_per_year: int = 252) -> SummaryStats:
    if not returns:
        return SummaryStats(None, None, None, 0, "无统计结论：没有收益观测")
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(len(returns) - 1, 1)
    long_run_variance = _newey_west_long_run_variance(returns, mean)
    effective_observations = (
        min(float(len(returns)), max(1.0, len(returns) * variance / long_run_variance))
        if long_run_variance > 0
        else float(len(returns))
    )
    sharpe = (
        math.sqrt(periods_per_year) * mean / math.sqrt(long_run_variance)
        if long_run_variance > 0
        else None
    )
    annualized = equity ** (periods_per_year / len(returns)) - 1.0
    notes = []
    if len(returns) < 10:
        notes.append("样本不足：策略触发次数/收益观测少于 10，禁止作统计结论")
    notes.append("年化 Sharpe 与均值显著性使用 Newey–West/HAC 长期方差（正态近似）；单次回测未作多重检验校正")
    mean_se = math.sqrt(long_run_variance / len(returns)) if long_run_variance > 0 else None
    mean_ci = _mean_confidence_interval(mean, mean_se)
    p_value = _two_sided_normal_p_value(mean, mean_se)
    return SummaryStats(
        annualized,
        sharpe,
        max_drawdown,
        len(returns),
        "；".join(notes),
        mean,
        mean_ci,
        p_value,
        None,
        effective_observations,
    )


def benjamini_hochberg(p_values: list[float], q: float = 0.05) -> dict[str, object]:
    """Apply the Benjamini-Hochberg false-discovery-rate correction."""

    if not 0 < q < 1:
        raise ValueError("q must be between 0 and 1")
    count = len(p_values)
    if not count:
        return {"adjusted": [], "rejected": [], "q": q}
    indexed = sorted((max(0.0, min(1.0, float(value))), index) for index, value in enumerate(p_values))
    adjusted = [1.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        value, original_index = indexed[rank - 1]
        running = min(running, value * count / rank)
        adjusted[original_index] = min(1.0, running)
    return {
        "adjusted": adjusted,
        "rejected": [value <= q for value in adjusted],
        "q": q,
    }


def deflated_sharpe_p_value(
    sharpe: float | None,
    observations: int,
    trials: int,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    effective_observations: float | None = None,
) -> float | None:
    """Return a conservative normal-approximation DSR p-value.

    This is deliberately surfaced as an adjusted screening statistic, not a
    claim of exact finite-sample inference.  The expected maximum Sharpe over
    ``sharpe`` is annualized and is converted back to a per-period Sharpe before
    applying the finite-sample approximation. ``trials`` is the explicit
    multiple-testing hurdle; callers without a defensible trial count should
    not report a DSR p-value.
    """

    sample_size = float(effective_observations or observations)
    if sharpe is None or sample_size < 2 or trials < 1:
        return None
    trials = max(1, int(trials))
    period_sharpe = float(sharpe) / math.sqrt(252.0)
    expected_max = 0.0 if trials == 1 else _normal_ppf(1.0 - 1.0 / trials)
    variance = (
        1.0
        - skew * period_sharpe
        + ((kurtosis - 1.0) / 4.0) * period_sharpe**2
    ) / max(sample_size - 1.0, 1.0)
    standard_error = math.sqrt(max(variance, 1e-12))
    z_score = (period_sharpe - expected_max) / standard_error
    return _normal_survival(z_score)


def factor_ic_significance(ic_values: list[float], *, n_tested: int) -> dict[str, float | int | None]:
    """Report HAC raw significance and a conservative N_tested DSR approximation."""

    values = [float(value) for value in ic_values if math.isfinite(float(value))]
    observations = len(values)
    if observations < 2 or n_tested < 1:
        return {
            "observations": observations,
            "mean_ic": sum(values) / observations if observations else None,
            "raw_p_value": None,
            "dsr_p_value": None,
            "effective_observations": float(observations),
        }
    mean = sum(values) / observations
    variance = _newey_west_long_run_variance(values, mean)
    standard_error = math.sqrt(variance / observations) if variance > 0 else None
    raw_p = _two_sided_normal_p_value(mean, standard_error)
    annualized_sharpe = mean / math.sqrt(variance) * math.sqrt(252.0) if variance > 0 else None
    sample_variance = sum((value - mean) ** 2 for value in values) / max(observations - 1, 1)
    effective_n = (
        min(float(observations), max(1.0, observations * sample_variance / variance))
        if variance > 0
        else float(observations)
    )
    dsr_p = deflated_sharpe_p_value(
        annualized_sharpe,
        observations,
        int(n_tested),
        effective_observations=effective_n,
    )
    return {
        "observations": observations,
        "mean_ic": mean,
        "raw_p_value": raw_p,
        "dsr_p_value": dsr_p,
        "effective_observations": effective_n,
    }


def _newey_west_long_run_variance(returns: list[float], mean: float) -> float:
    """Estimate per-period long-run variance with Bartlett-weighted HAC."""

    observations = len(returns)
    if observations < 2:
        return 0.0
    residuals = [value - mean for value in returns]
    lags = min(observations - 1, int(4 * (observations / 100.0) ** (2.0 / 9.0)))
    variance = sum(value * value for value in residuals) / observations
    for lag in range(1, lags + 1):
        autocovariance = sum(
            residuals[index] * residuals[index - lag]
            for index in range(lag, observations)
        ) / observations
        bartlett_weight = 1.0 - lag / (lags + 1.0)
        variance += 2.0 * bartlett_weight * autocovariance
    return max(variance, 0.0)


def _mean_confidence_interval(mean: float, standard_error: float | None) -> tuple[float, float] | None:
    if standard_error is None:
        return None
    margin = 1.96 * standard_error
    return (mean - margin, mean + margin)


def _two_sided_normal_p_value(mean: float, standard_error: float | None) -> float | None:
    if standard_error is None or standard_error <= 0:
        return None
    z_score = abs(mean) / standard_error
    return min(1.0, 2.0 * _normal_survival(z_score))


def _normal_survival(value: float) -> float:
    return 0.5 * math.erfc(value / math.sqrt(2.0))


def _normal_p_value(value: float) -> float:
    return 1.0 - _normal_survival(value)


def _normal_ppf(probability: float) -> float:
    """Acklam's rational approximation for the standard-normal quantile."""

    probability = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    a = (-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924)
    b = (-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857)
    c = (-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373, 4.37466414146497, 2.93816398269878)
    d = (0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742)
    if probability < 0.02425:
        q = math.sqrt(-2.0 * math.log(probability))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return numerator / denominator
    if probability > 1.0 - 0.02425:
        q = math.sqrt(-2.0 * math.log(1.0 - probability))
        numerator = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denominator = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return -numerator / denominator
    q = probability - 0.5
    r = q * q
    numerator = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    denominator = ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    return numerator / denominator
