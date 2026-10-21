from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from ..data.schema import PITIndustryObservation

SHANGHAI = timezone(timedelta(hours=8))


def inverse_volatility_weights(
    volatilities: dict[str, float], *, max_weight: float = 1.0
) -> dict[str, float]:
    """Allocate by inverse realized volatility with a hard per-name cap.

    This is a transparent diagonal-risk approximation, not full covariance
    risk parity. If caps prevent full investment, the residual remains cash.
    """
    if not 0 < max_weight <= 1:
        raise ValueError("max_weight must be in (0, 1]")
    scores = {
        symbol: 1.0 / max(float(volatility), 1e-4)
        for symbol, volatility in volatilities.items()
        if volatility > 0
    }
    if not scores:
        return {}
    remaining = 1.0
    active = dict(scores)
    weights: dict[str, float] = {}
    while active and remaining > 1e-12:
        total = sum(active.values())
        proposed = {symbol: remaining * score / total for symbol, score in active.items()}
        capped = [symbol for symbol, weight in proposed.items() if weight > max_weight]
        if not capped:
            weights.update(proposed)
            break
        for symbol in capped:
            weights[symbol] = max_weight
            remaining -= max_weight
            active.pop(symbol)
        if remaining <= 1e-12:
            break
    return weights


def volatility_target_scale(
    histories: Mapping[str, Sequence[Any]],
    weights: Mapping[str, float],
    *,
    target_annual_volatility: float = 0.15,
    window: int = 60,
) -> float:
    """Return a no-leverage volatility cap scale based only on prior closes."""
    if target_annual_volatility <= 0 or window < 2:
        raise ValueError("target volatility and window must be positive")
    returns_by_symbol: dict[str, dict[date, float]] = {}
    for symbol, bars in histories.items():
        ordered = [
            bar for bar in bars
            if float(bar.signal_close if bar.signal_close is not None else bar.close) > 0
        ][-window - 1:]
        returns_by_symbol[symbol] = {
            right.date: float(right.signal_close if right.signal_close is not None else right.close)
            / float(left.signal_close if left.signal_close is not None else left.close) - 1.0
            for left, right in itertools.pairwise(ordered)
            if float(left.signal_close if left.signal_close is not None else left.close) > 0
        }
    active_symbols = [symbol for symbol, weight in weights.items() if weight > 0 and returns_by_symbol.get(symbol)]
    if not active_symbols:
        return 1.0
    common_dates = set(returns_by_symbol[active_symbols[0]])
    for symbol in active_symbols[1:]:
        common_dates.intersection_update(returns_by_symbol[symbol])
    dates = sorted(common_dates)
    portfolio_returns: list[float] = []
    for day in dates:
        daily = sum(
            float(weight) * returns_by_symbol.get(symbol, {}).get(day, 0.0)
            for symbol, weight in weights.items()
        )
        portfolio_returns.append(daily)
    if len(portfolio_returns) < 20:
        return 1.0
    mean = sum(portfolio_returns) / len(portfolio_returns)
    variance = sum((value - mean) ** 2 for value in portfolio_returns) / (len(portfolio_returns) - 1)
    realized = math.sqrt(max(0.0, variance) * 252.0)
    if realized <= target_annual_volatility or realized <= 1e-12:
        return 1.0
    return max(0.0, min(1.0, target_annual_volatility / realized))


def mean_variance_weights(
    expected_returns: Mapping[str, float],
    daily_returns: Mapping[str, Mapping[date, float]],
    *,
    industry_observations: Sequence[PITIndustryObservation],
    benchmark_constituent_weights: Mapping[str, float],
    as_of: date,
    max_weight: float = 0.20,
    max_active_industry_deviation: float = 0.05,
    risk_aversion: float = 8.0,
    window: int = 60,
    min_observations: int = 20,
    iterations: int = 250,
) -> dict[str, float]:
    """Optimize a long-only portfolio on full covariance with strict PIT industry bands.

    Returns are daily forecast returns (the caller owns forecast calibration).
    Industry labels come only from availability-stamped PIT observations for the
    requested signal date. Benchmark sector budgets are derived from PIT labels
    applied to the supplied point-in-time constituent weights. The optimizer
    fails closed if either side lacks dated industry coverage.
    """

    if not expected_returns:
        return {}
    if not 0 < max_weight <= 1:
        raise ValueError("max_weight must be in (0, 1]")
    if not 0 <= max_active_industry_deviation < 1:
        raise ValueError("max_active_industry_deviation must be in [0, 1)")
    if risk_aversion <= 0 or window < 2 or min_observations < 2 or iterations < 1:
        raise ValueError("risk_aversion, window, min_observations, and iterations must be positive")

    cutoff = datetime.combine(as_of, time(15, 0), tzinfo=SHANGHAI)
    labels: dict[str, str] = {}
    for observation in industry_observations:
        symbol = str(observation.symbol).upper()
        if observation.as_of != as_of:
            continue
        available_at = observation.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=SHANGHAI)
        else:
            available_at = available_at.astimezone(SHANGHAI)
        if (
            available_at > cutoff
            or not observation.industry.strip()
            or not observation.source_version.strip()
            or observation.availability_basis in {"", "unknown", "effective_date_only"}
        ):
            continue
        labels[symbol] = observation.industry.strip()

    candidate_symbols = sorted(
        symbol for symbol, value in expected_returns.items()
        if symbol in labels and symbol in daily_returns and math.isfinite(float(value))
    )
    if not candidate_symbols:
        raise ValueError("no candidates have both expected returns and strict PIT industry labels")
    benchmark_industries: dict[str, float] = {}
    benchmark_total = 0.0
    for raw_symbol, raw_weight in benchmark_constituent_weights.items():
        symbol = str(raw_symbol).upper()
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight <= 0:
            continue
        if symbol not in labels:
            raise ValueError(f"benchmark constituent {symbol} is missing strict PIT industry coverage")
        group = labels[symbol]
        benchmark_industries[group] = benchmark_industries.get(group, 0.0) + weight
        benchmark_total += weight
    if benchmark_total <= 0:
        raise ValueError("benchmark constituent weights are empty")
    benchmark_industries = {group: value / benchmark_total for group, value in benchmark_industries.items()}

    represented_groups = {labels[symbol] for symbol in candidate_symbols}
    missing_benchmark_groups = represented_groups - benchmark_industries.keys()
    if missing_benchmark_groups:
        raise ValueError("candidate industries are absent from PIT benchmark weights: " + ", ".join(sorted(missing_benchmark_groups)))
    represented_benchmark_total = sum(benchmark_industries[group] for group in represented_groups)
    targets = {
        group: benchmark_industries[group] / represented_benchmark_total
        for group in represented_groups
    }
    lower = {
        group: max(0.0, target - max_active_industry_deviation)
        for group, target in targets.items()
    }
    upper = {
        group: min(1.0, target + max_active_industry_deviation)
        for group, target in targets.items()
    }
    for group in represented_groups:
        upper[group] = min(upper[group], sum(labels[symbol] == group for symbol in candidate_symbols) * max_weight)
    if sum(lower.values()) > 1.0 + 1e-9 or sum(upper.values()) < 1.0 - 1e-9:
        raise ValueError("candidate pool and position cap cannot satisfy the industry-neutral bands")

    common_dates = set(daily_returns[candidate_symbols[0]])
    for symbol in candidate_symbols[1:]:
        common_dates.intersection_update(daily_returns[symbol])
    ordered_dates = sorted(common_dates)[-window:]
    if len(ordered_dates) < min_observations:
        raise ValueError(f"full covariance needs at least {min_observations} common PIT return observations")
    matrix = [
        [float(daily_returns[symbol][day]) for symbol in candidate_symbols]
        for day in ordered_dates
    ]
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise ValueError("daily returns must be finite")
    means = [sum(row[column] for row in matrix) / len(matrix) for column in range(len(candidate_symbols))]
    covariance = [[0.0] * len(candidate_symbols) for _ in candidate_symbols]
    for left in range(len(candidate_symbols)):
        for right in range(left, len(candidate_symbols)):
            value = sum(
                (row[left] - means[left]) * (row[right] - means[right])
                for row in matrix
            ) / (len(matrix) - 1)
            covariance[left][right] = covariance[right][left] = value
    # Ten percent diagonal shrinkage plus a tiny ridge stabilizes short/noisy panels.
    for index in range(len(candidate_symbols)):
        covariance[index][index] = max(covariance[index][index], 1e-10) * 1.10
    for left in range(len(candidate_symbols)):
        for right in range(len(candidate_symbols)):
            if left != right:
                covariance[left][right] *= 0.90

    symbol_groups = [labels[symbol] for symbol in candidate_symbols]
    alpha = [float(expected_returns[symbol]) for symbol in candidate_symbols]
    lipschitz = max(
        1e-9,
        2.0 * risk_aversion * max(
            sum(abs(value) for value in row) for row in covariance
        ),
    )
    step = 1.0 / lipschitz
    weights = _project_industry_simplex(
        [1.0 / len(candidate_symbols)] * len(candidate_symbols),
        symbol_groups,
        lower,
        upper,
        max_weight,
    )
    for _ in range(iterations):
        gradient = [
            alpha[index] - 2.0 * risk_aversion * sum(
                covariance[index][other] * weights[other]
                for other in range(len(weights))
            )
            for index in range(len(weights))
        ]
        updated = _project_industry_simplex(
            [weight + step * gradient[index] for index, weight in enumerate(weights)],
            symbol_groups,
            lower,
            upper,
            max_weight,
        )
        if max(abs(left - right) for left, right in zip(weights, updated)) < 1e-9:
            weights = updated
            break
        weights = updated
    return {symbol: weight for symbol, weight in zip(candidate_symbols, weights) if weight > 1e-10}


def _project_industry_simplex(
    values: Sequence[float],
    groups: Sequence[str],
    lower: Mapping[str, float],
    upper: Mapping[str, float],
    max_weight: float,
) -> list[float]:
    """Euclidean projection onto capped-simplex and industry-sum intervals."""

    grouped_indices: dict[str, list[int]] = {}
    for index, group in enumerate(groups):
        grouped_indices.setdefault(group, []).append(index)

    def project_group(indices: list[int], target: float, shift: float) -> list[float]:
        raw = [values[index] - shift for index in indices]
        minimum_shift = min(raw) - max_weight - 1.0
        maximum_shift = max(raw) + 1.0
        for _ in range(70):
            middle = (minimum_shift + maximum_shift) / 2.0
            total = sum(min(max_weight, max(0.0, value - middle)) for value in raw)
            if total > target:
                minimum_shift = middle
            else:
                maximum_shift = middle
        final_shift = (minimum_shift + maximum_shift) / 2.0
        return [min(max_weight, max(0.0, value - final_shift)) for value in raw]

    def at_shift(shift: float) -> list[float]:
        projected = [0.0] * len(values)
        for group, indices in grouped_indices.items():
            raw = [min(max_weight, max(0.0, values[index] - shift)) for index in indices]
            target = min(upper[group], max(lower[group], sum(raw)))
            adjusted = project_group(indices, target, shift)
            for index, weight in zip(indices, adjusted):
                projected[index] = weight
        return projected

    minimum_shift = min(values) - max_weight - 1.0
    maximum_shift = max(values) + 1.0
    for _ in range(80):
        middle = (minimum_shift + maximum_shift) / 2.0
        projected = at_shift(middle)
        if sum(projected) > 1.0:
            minimum_shift = middle
        else:
            maximum_shift = middle
    projected = at_shift((minimum_shift + maximum_shift) / 2.0)
    total = sum(projected)
    if abs(total - 1.0) > 1e-6:
        raise ValueError("industry-neutral portfolio projection did not converge")
    return projected
