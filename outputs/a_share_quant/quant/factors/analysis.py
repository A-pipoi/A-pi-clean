from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from ..validation.statistics import factor_ic_significance
from .decay import factor_decay_report
from .expression import FactorPanel


def _finite_value(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average = (cursor + end - 1) / 2.0 + 1.0
        for index in range(cursor, end):
            ranks[ordered[index][0]] = average
        cursor = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    return numerator / denominator if denominator else None


def rank_ic(factor_values: dict[str, float], forward_returns: dict[str, float]) -> float | None:
    symbols = [symbol for symbol in factor_values if symbol in forward_returns]
    if len(symbols) < 3:
        return None
    values = [factor_values[symbol] for symbol in symbols]
    returns = [forward_returns[symbol] for symbol in symbols]
    return _correlation(_rank(values), _rank(returns))


def quantile_returns(
    factor_values: dict[str, float], forward_returns: dict[str, float], quantiles: int = 5
) -> dict[int, float]:
    if quantiles < 2:
        raise ValueError("quantiles must be >= 2")
    symbols = [symbol for symbol in factor_values if symbol in forward_returns]
    symbols.sort(key=lambda symbol: factor_values[symbol])
    if not symbols:
        return {}
    buckets: dict[int, list[float]] = defaultdict(list)
    for position, symbol in enumerate(symbols):
        bucket = min(quantiles - 1, position * quantiles // len(symbols))
        buckets[bucket].append(forward_returns[symbol])
    return {bucket: sum(values) / len(values) for bucket, values in buckets.items()}


def neutralize_by_group(values: dict[str, float], groups: dict[str, str]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for symbol, value in values.items():
        if symbol in groups:
            grouped[groups[symbol]].append(value)
    means = {group: sum(items) / len(items) for group, items in grouped.items()}
    return {
        symbol: value - means[groups[symbol]] if symbol in groups and groups[symbol] in means else value
        for symbol, value in values.items()
    }


def evaluate_factor_panel(
    panel: FactorPanel,
    values: list[list[float | None]],
    *,
    start: date | None = None,
    end: date | None = None,
    n_tested: int = 1,
    horizons: Sequence[int] = (1, 5, 10, 20),
    quantiles: int = 10,
) -> dict[str, object]:
    """Summarize predictive power and simple tradability diagnostics on a date slice."""

    index_by_date = {day: index for index, day in enumerate(panel.dates)}
    close = panel.fields["close"]
    selected_dates = [
        day for day in panel.dates
        if (start is None or day >= start) and (end is None or day <= end)
    ]
    size_is_verified = panel.pit_quality.get("size") == "verified" and "market_cap" in panel.fields
    industry_is_verified = panel.pit_quality.get("industry") == "verified"
    ic_by_horizon: dict[str, dict[str, object]] = {}
    for horizon in horizons:
        ic_rows: list[tuple[date, float]] = []
        normal_ic_rows: list[tuple[date, float]] = []
        bucket_rows: list[dict[int, float]] = []
        size_slice_rows: dict[str, list[float]] = defaultdict(list)
        industry_slice_rows: dict[str, list[float]] = defaultdict(list)
        for day in selected_dates:
            row = index_by_date[day]
            future = row + int(horizon)
            if future >= len(panel.dates) or (end is not None and panel.dates[future] > end):
                continue
            factor_values: dict[str, float] = {}
            forward_returns: dict[str, float] = {}
            for column, symbol in enumerate(panel.symbols):
                left, right, factor = close[row][column], close[future][column], values[row][column]
                if left is None or right is None or factor is None or left <= 0 or not math.isfinite(factor):
                    continue
                factor_values[symbol] = factor
                forward_returns[symbol] = right / left - 1.0
            ic = rank_ic(factor_values, forward_returns)
            if ic is not None:
                ic_rows.append((day, ic))
                bucket_rows.append(quantile_returns(factor_values, forward_returns, quantiles))
                normal_ic = _correlation(
                    [factor_values[symbol] for symbol in factor_values if symbol in forward_returns],
                    [forward_returns[symbol] for symbol in factor_values if symbol in forward_returns],
                )
                if normal_ic is not None:
                    normal_ic_rows.append((day, normal_ic))
                for group_name, group_symbols in _environment_groups(
                    panel, row, factor_values, size_is_verified=size_is_verified,
                    industry_is_verified=industry_is_verified,
                ):
                    group_ic = rank_ic(
                        {symbol: factor_values[symbol] for symbol in group_symbols},
                        {symbol: forward_returns[symbol] for symbol in group_symbols},
                    )
                    if group_ic is not None:
                        if group_name.startswith("size:"):
                            size_slice_rows[group_name.removeprefix("size:")].append(group_ic)
                        elif group_name.startswith("industry:"):
                            industry_slice_rows[group_name.removeprefix("industry:")].append(group_ic)
        series = [value for _, value in ic_rows]
        normal_series = [value for _, value in normal_ic_rows]
        stats = factor_ic_significance(series, n_tested=n_tested)
        normal_stats = factor_ic_significance(normal_series, n_tested=n_tested)
        ic_std = _sample_std(series)
        monthly: dict[str, list[float]] = defaultdict(list)
        annual: dict[str, list[float]] = defaultdict(list)
        for day, value in ic_rows:
            monthly[day.strftime("%Y-%m")].append(value)
            annual[str(day.year)].append(value)
        monthly_means = [sum(values_) / len(values_) for values_ in monthly.values()]
        quantile_means = {
            str(bucket + 1): sum(row[bucket] for row in bucket_rows if bucket in row) / sum(1 for row in bucket_rows if bucket in row)
            for bucket in range(quantiles)
            if any(bucket in row for row in bucket_rows)
        }
        ordered_bucket_means = [quantile_means[str(bucket + 1)] for bucket in range(quantiles) if str(bucket + 1) in quantile_means]
        ic_by_horizon[str(horizon)] = {
            **stats,
            "normal_ic": {
                **normal_stats,
                "series": [{"date": day.isoformat(), "value": value} for day, value in normal_ic_rows],
            },
            "icir": (sum(series) / len(series)) / ic_std * math.sqrt(252.0) if series and ic_std > 0 else None,
            "monthly_hit_rate": sum(value > 0 for value in monthly_means) / len(monthly_means) if monthly_means else None,
            "monthly_observations": len(monthly_means),
            "annual_ic": {year: sum(values_) / len(values_) for year, values_ in annual.items()},
            "quantile_forward_returns": quantile_means,
            "long_short_return": ordered_bucket_means[-1] - ordered_bucket_means[0] if len(ordered_bucket_means) >= 2 else None,
            "monotonicity": _monotonicity(ordered_bucket_means),
            "ic_series": [{"date": day.isoformat(), "value": value} for day, value in ic_rows],
            "decay_monitor": factor_decay_report([
                {"date": day.isoformat(), "value": value} for day, value in ic_rows]),
            "environment_slices": {
                "market_cap": _summarize_environment_slice(
                    size_slice_rows,
                    available=size_is_verified,
                    unavailable_reason="没有通过 PIT 覆盖门槛的历史市值，无法进行规模分组。",
                ),
                "industry": _summarize_environment_slice(
                    industry_slice_rows,
                    available=industry_is_verified,
                    unavailable_reason=(
                        "严格 PIT 门禁：当前历史行业源只有生效区间，没有可靠 available_at/first_seen_at；"
                        "行业分组评估已禁用。"
                    ),
                ),
            },
        }
    coverage_values: list[float] = []
    top_sets: list[set[str]] = []
    flattened: list[float] = []
    values_by_year: dict[str, list[float]] = defaultdict(list)
    for day in selected_dates:
        row = index_by_date[day]
        valid = [(panel.symbols[column], float(value)) for column, value in enumerate(values[row]) if value is not None and math.isfinite(value)]
        coverage_values.append(len(valid) / max(len(panel.symbols), 1))
        flattened.extend(value for _, value in valid)
        values_by_year[str(day.year)].extend(value for _, value in valid)
        if valid:
            valid.sort(key=lambda pair: pair[1], reverse=True)
            top_sets.append({symbol for symbol, _ in valid[: max(1, len(valid) // 5)]})
    turnover = [1.0 - len(previous & current) / max(len(previous), 1) for previous, current in itertools.pairwise(top_sets)]
    return {
        "start": selected_dates[0].isoformat() if selected_dates else None,
        "end": selected_dates[-1].isoformat() if selected_dates else None,
        "coverage": sum(coverage_values) / len(coverage_values) if coverage_values else 0.0,
        "factor_observations": len(flattened),
        "factor_autocorrelation": _panel_autocorrelation(values, panel, selected_dates),
        "top_quintile_turnover": sum(turnover) / len(turnover) if turnover else None,
        "distribution": {
            "min": min(flattened) if flattened else None,
            "p10": _percentile(flattened, 0.10),
            "median": _percentile(flattened, 0.50),
            "p90": _percentile(flattened, 0.90),
            "max": max(flattened) if flattened else None,
        },
        "annual_distribution_drift": _annual_distribution_drift(values_by_year),
        "horizons": ic_by_horizon,
        "n_tested": int(n_tested),
        "note": "IC p 值为 Newey–West/HAC 正态近似；DSR 为按全部 N_tested 试验数计算的保守近似，不构成收益保证。",
    }


def _environment_groups(
    panel: FactorPanel,
    row: int,
    factor_values: dict[str, float],
    *,
    size_is_verified: bool,
    industry_is_verified: bool,
) -> list[tuple[str, list[str]]]:
    groups: list[tuple[str, list[str]]] = []
    symbol_columns = {symbol: column for column, symbol in enumerate(panel.symbols)}
    if size_is_verified:
        market_caps = panel.fields["market_cap"][row]
        ranked_caps = sorted(
            (
                (market_cap, symbol)
                for symbol in factor_values
                if (market_cap := _finite_value(market_caps[symbol_columns[symbol]])) is not None
                and market_cap > 0
            ),
            key=lambda item: (item[0], item[1]),
        )
        size_buckets: dict[str, list[str]] = {"small": [], "mid": [], "large": []}
        for position, (_, symbol) in enumerate(ranked_caps):
            bucket = min(2, position * 3 // max(len(ranked_caps), 1))
            size_buckets[("small", "mid", "large")[bucket]].append(symbol)
        groups.extend((f"size:{name}", symbols) for name, symbols in size_buckets.items() if len(symbols) >= 3)
    if industry_is_verified:
        industry_buckets: dict[str, list[str]] = defaultdict(list)
        for symbol in factor_values:
            industry = panel.industries[row][symbol_columns[symbol]]
            if industry:
                industry_buckets[str(industry)].append(symbol)
        groups.extend(
            (f"industry:{name}", symbols)
            for name, symbols in sorted(industry_buckets.items())
            if len(symbols) >= 3
        )
    return groups


def _summarize_environment_slice(
    rows: dict[str, list[float]],
    *,
    available: bool,
    unavailable_reason: str,
) -> dict[str, object]:
    groups = {
        name: {
            "mean_rank_ic": sum(values) / len(values),
            "observations": len(values),
            "positive_rate": sum(value > 0 for value in values) / len(values),
        }
        for name, values in sorted(rows.items())
        if values
    }
    if not available:
        return {"status": "unavailable", "reason": unavailable_reason, "groups": {}}
    if not groups:
        return {
            "status": "insufficient_data",
            "reason": "可用 PIT 分组未形成至少 3 个标的的有效截面。",
            "groups": {},
        }
    return {"status": "available", "reason": None, "groups": groups}


def _annual_distribution_drift(values_by_year: dict[str, list[float]]) -> dict[str, object]:
    quantiles = (0.10, 0.25, 0.50, 0.75, 0.90)
    labels = {0.10: "p10", 0.25: "p25", 0.50: "p50", 0.75: "p75", 0.90: "p90"}
    annual: dict[str, dict[str, object]] = {}
    previous: dict[str, float] | None = None
    previous_year: str | None = None
    for year in sorted(values_by_year):
        values = [value for value in values_by_year[year] if math.isfinite(value)]
        current = {labels[q]: _percentile(values, q) for q in quantiles}
        drift = {
            label: current_value - previous[label]
            for label in labels.values()
            if previous is not None
            and label in previous
            and (current_value := current[label]) is not None
        }
        annual[year] = {
            "observations": len(values),
            "quantiles": current,
            "drift_vs_previous_year": drift if previous is not None else None,
            "compared_to": previous_year,
        }
        previous = {key: float(value) for key, value in current.items() if value is not None}
        previous_year = year
    return {
        "basis": "yearly cross-section-date factor observations; absolute quantile changes",
        "years": annual,
    }


def _sample_std(values: list[float]) -> float:
    return math.sqrt(sum((value - sum(values) / len(values)) ** 2 for value in values) / (len(values) - 1)) if len(values) > 1 else 0.0


def _monotonicity(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    ranks = _rank(values)
    return _correlation(ranks, list(range(len(values))))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _panel_autocorrelation(values: list[list[float | None]], panel: FactorPanel, dates: list[date]) -> float | None:
    indexes = {day: index for index, day in enumerate(panel.dates)}
    previous: list[float] = []
    current: list[float] = []
    for before, after in itertools.pairwise(dates):
        left, right = values[indexes[before]], values[indexes[after]]
        for a, b in zip(left, right):
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b):
                previous.append(a)
                current.append(b)
    return _correlation(previous, current)
