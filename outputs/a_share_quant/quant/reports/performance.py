from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..backtest.engine import BacktestResult, DailyEquity
from ..numeric import as_float as _as_float


@dataclass(frozen=True)
class PerformanceReport:
    annualized_return: float | None
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float
    recovery_days: int | None
    turnover: float
    total_cost: float
    cost_to_gross_profit: float | None


def build_monthly_returns(
    equity_curve: Sequence[DailyEquity],
    initial_cash: float,
) -> list[dict[str, object]]:
    """Aggregate realized daily equity into auditable calendar-month returns."""

    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    rows = sorted(equity_curve, key=lambda point: point.date)
    previous = float(initial_cash)
    by_month: dict[str, list[DailyEquity]] = {}
    for point in rows:
        by_month.setdefault(point.date.strftime("%Y-%m"), []).append(point)
    result: list[dict[str, object]] = []
    for month, points in by_month.items():
        ending = float(points[-1].equity)
        result.append(
            {
                "month": month,
                "start_date": points[0].date.isoformat(),
                "end_date": points[-1].date.isoformat(),
                "return": ending / previous - 1.0 if previous > 0 else None,
                "ending_equity": ending,
                "observations": len(points),
            }
        )
        previous = ending
    return result


def build_excess_return_report(
    strategy_initial: float,
    strategy_final: float,
    benchmark_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Compare a strategy and an index over the exact rows returned by Tushare."""

    rows = sorted(benchmark_rows, key=lambda item: str(item.get("date") or ""))
    closes = [_as_float(item["close"]) for item in rows if item.get("close") not in (None, "")]
    if strategy_initial <= 0 or len(closes) < 2:
        return {"available": False, "reason": "基准区间数据不足"}
    benchmark_return = closes[-1] / closes[0] - 1.0
    strategy_return = strategy_final / strategy_initial - 1.0
    return {
        "available": True,
        "start_date": rows[0].get("date"),
        "end_date": rows[-1].get("date"),
        "observations": len(closes),
        "benchmark_return": benchmark_return,
        "strategy_return": strategy_return,
        "excess_return": strategy_return - benchmark_return,
    }


def build_performance_report(result: BacktestResult, periods_per_year: int = 252) -> PerformanceReport:
    equities = [result.initial_cash] + [point.equity for point in result.equity_curve]
    returns = [equities[index] / equities[index - 1] - 1.0 for index in range(1, len(equities)) if equities[index - 1] > 0]
    if not returns:
        return PerformanceReport(None, None, None, None, 0.0, None, result.turnover, result.total_cost, None)
    equity = 1.0
    peak = 1.0
    drawdowns: list[float] = []
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdowns.append(equity / peak - 1.0)
    max_drawdown = min(drawdowns)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / max(len(returns) - 1, 1)
    standard_deviation = math.sqrt(variance)
    downside = [min(value, 0.0) ** 2 for value in returns]
    downside_deviation = math.sqrt(sum(downside) / max(len(returns) - 1, 1))
    annualized = (result.final_equity / result.initial_cash) ** (periods_per_year / len(returns)) - 1.0
    sharpe = math.sqrt(periods_per_year) * mean / standard_deviation if standard_deviation else None
    sortino = math.sqrt(periods_per_year) * mean / downside_deviation if downside_deviation else None
    calmar = annualized / abs(max_drawdown) if max_drawdown < 0 else None
    recovery_days = _recovery_days(drawdowns)
    gross_profit = result.final_equity - result.initial_cash + result.total_cost
    cost_ratio = result.total_cost / gross_profit if gross_profit > 0 else None
    return PerformanceReport(annualized, sharpe, sortino, calmar, max_drawdown, recovery_days, result.turnover, result.total_cost, cost_ratio)


def _recovery_days(drawdowns: list[float]) -> int | None:
    if not drawdowns:
        return None
    trough = min(range(len(drawdowns)), key=lambda index: drawdowns[index])
    if drawdowns[trough] >= 0:
        return 0
    for index in range(trough + 1, len(drawdowns)):
        if drawdowns[index] >= 0:
            return index - trough
    return None
