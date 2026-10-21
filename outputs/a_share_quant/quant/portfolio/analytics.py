from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

from ..data.schema import Bar


def build_portfolio_risk_report(result: object, bars: Sequence[Bar], *, symbol_limit: int = 20) -> dict[str, Any]:
    """Calculate realized risk diagnostics from backtest equity and daily bars.

    VaR/ES and stress windows are historical sample diagnostics, not forecasts.
    Correlations are restricted to the most traded names so the API response
    stays bounded even when the PIT universe contains hundreds of stocks.
    """
    curve = list(getattr(result, "equity_curve", []))
    ordered_curve = sorted(curve, key=lambda point: point.date)
    daily_returns = [
        float(current.equity) / float(previous.equity) - 1.0
        for previous, current in itertools.pairwise(ordered_curve)
        if float(previous.equity) > 0
    ]
    losses = sorted(-value for value in daily_returns)
    var_95 = _quantile(losses, 0.95) if len(losses) >= 20 else None
    tail_losses = [loss for loss in losses if var_95 is not None and loss >= var_95]
    mean = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    volatility = (
        math.sqrt(sum((value - mean) ** 2 for value in daily_returns) / (len(daily_returns) - 1) * 252)
        if len(daily_returns) >= 2 else None
    )
    stress = {
        f"worst_{horizon}d": _worst_horizon(ordered_curve, horizon)
        for horizon in (1, 5, 20)
    }
    period_stress = {
        "2015-2016": _period_max_drawdown(ordered_curve, date(2015, 1, 1), date(2016, 12, 31)),
        "2020-2022": _period_max_drawdown(ordered_curve, date(2020, 1, 1), date(2022, 12, 31)),
        "2024-present": _period_max_drawdown(ordered_curve, date(2024, 1, 1), date.max),
    }
    correlations = _correlation_report(result, bars, symbol_limit=symbol_limit)
    return {
        "status": "available" if daily_returns else "insufficient_data",
        "basis": "realized daily equity returns and Tushare daily close returns; historical diagnostics, not forecasts",
        "observations": len(daily_returns),
        "annualized_volatility": volatility,
        "historical_var_95": var_95,
        "historical_expected_shortfall_95": sum(tail_losses) / len(tail_losses) if tail_losses else None,
        "max_drawdown": _max_drawdown(ordered_curve),
        "stress_horizons": stress,
        "historical_periods": period_stress,
        "correlation": correlations,
        "limits": {
            "var_95": "report_only",
            "correlation": "monitor_only",
            "stress": "requires date coverage; absent periods are marked unavailable",
        },
    }


def _quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * percentile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[low]
    return values[low] * (high - position) + values[high] * (position - low)


def _worst_horizon(curve: Sequence[Any], horizon: int) -> dict[str, Any] | None:
    if len(curve) <= horizon:
        return None
    worst: tuple[float, date, date] | None = None
    for end_index in range(horizon, len(curve)):
        start = curve[end_index - horizon]
        end = curve[end_index]
        if float(start.equity) <= 0:
            continue
        change = float(end.equity) / float(start.equity) - 1.0
        if worst is None or change < worst[0]:
            worst = (change, start.date, end.date)
    if worst is None:
        return None
    return {"return": worst[0], "start": worst[1].isoformat(), "end": worst[2].isoformat()}


def _max_drawdown(curve: Sequence[Any]) -> float | None:
    peak = 0.0
    worst = 0.0
    for point in curve:
        equity = float(point.equity)
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, equity / peak - 1.0)
    return worst if curve else None


def _period_max_drawdown(curve: Sequence[Any], start: date, end: date) -> dict[str, Any]:
    selected = [point for point in curve if start <= point.date <= end]
    if len(selected) < 2:
        return {"status": "unavailable", "observations": len(selected), "max_drawdown": None}
    return {
        "status": "available",
        "observations": len(selected),
        "start": selected[0].date.isoformat(),
        "end": selected[-1].date.isoformat(),
        "max_drawdown": _max_drawdown(selected),
    }


def _correlation_report(result: object, bars: Sequence[Bar], *, symbol_limit: int) -> dict[str, Any]:
    turnover: dict[str, float] = defaultdict(float)
    for fill in getattr(result, "fills", []):
        turnover[str(fill.order.symbol)] += float(fill.costs.notional)
    if turnover:
        symbols = [symbol for symbol, _ in sorted(turnover.items(), key=lambda item: item[1], reverse=True)[:symbol_limit]]
    else:
        symbols = sorted({bar.symbol for bar in bars})[:symbol_limit]
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for bar in bars:
        if bar.symbol in symbols and bar.close > 0:
            closes[bar.symbol][bar.date] = float(bar.close)
    returns: dict[str, dict[date, float]] = {}
    for symbol, observations in closes.items():
        ordered = sorted(observations.items())
        returns[symbol] = {
            right_day: right_close / left_close - 1.0
            for (left_day, left_close), (right_day, right_close) in itertools.pairwise(ordered)
            if left_close > 0
        }
    matrix: dict[str, dict[str, float | None]] = {}
    counts: dict[str, dict[str, int]] = {}
    for left in symbols:
        matrix[left] = {}
        counts[left] = {}
        for right in symbols:
            common = sorted(set(returns.get(left, {})) & set(returns.get(right, {})))
            counts[left][right] = len(common)
            matrix[left][right] = _correlation(
                [returns[left][day] for day in common],
                [returns[right][day] for day in common],
            ) if len(common) >= 20 else None
    return {"status": "available" if symbols else "no_symbols", "symbols": symbols, "observations": counts, "matrix": matrix}


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_variance = sum(value * value for value in left_centered)
    right_variance = sum(value * value for value in right_centered)
    denominator = math.sqrt(left_variance * right_variance)
    if denominator <= 1e-15:
        return None
    return sum(a * b for a, b in zip(left_centered, right_centered)) / denominator
