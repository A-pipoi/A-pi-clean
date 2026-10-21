"""Exact security/cost P&L attribution, independent of unverified industries."""
from __future__ import annotations

from collections import defaultdict

from ..backtest.engine import BacktestResult
from ..data.schema import Bar


def build_security_contribution(result: BacktestResult, bars: list[Bar]) -> dict[str, object]:
    by_day = {(b.symbol, b.date): b for b in bars}
    fills = defaultdict(list)
    for fill in result.fills:
        fills[fill.date].append(fill)
    quantities: dict[str, int] = {}
    marks: dict[str, float] = {}
    totals: dict[str, float] = defaultdict(float)
    daily = []
    fees = 0.0
    prior_equity = result.initial_cash
    max_daily_error = 0.0
    for point in result.equity_curve:
        values: dict[str, float] = defaultdict(float)
        symbols = set(quantities) | {fill.order.symbol for fill in fills[point.date]}
        for symbol in symbols:
            bar = by_day.get((symbol, point.date))
            current = bar.close if bar and not bar.paused else marks.get(symbol)
            previous = marks.get(symbol, current)
            if current is None or previous is None:
                return {'status': 'unavailable', 'reason': f'missing valuation price: {symbol}'}
            values[symbol] += quantities.get(symbol, 0) * (current - previous)
            for fill in fills[point.date]:
                if fill.order.symbol != symbol:
                    continue
                values[symbol] += fill.quantity * ((current - fill.price) if fill.order.side == 'BUY'
                    else (fill.price - current))
            marks[symbol] = current
        day_fees = sum(f.costs.total - f.costs.slippage for f in fills[point.date])
        change = point.equity - prior_equity
        residual = change - (sum(values.values()) - day_fees)
        max_daily_error = max(max_daily_error, abs(residual))
        daily.append({'date': point.date.isoformat(), 'security_pnl': dict(values),
            'fees': day_fees, 'net_pnl': change, 'reconciliation_error': residual})
        for symbol, value in values.items():
            totals[symbol] += value
        fees += day_fees
        quantities = dict(point.positions)
        prior_equity = point.equity
    net = result.final_equity - result.initial_cash
    error = net - (sum(totals.values()) - fees)
    tolerance = max(1e-6, result.initial_cash * 1e-10)
    return {'status': 'available' if abs(error) <= tolerance and max_daily_error <= tolerance else 'needs_review',
        'method': 'position and actual fill price P&L; slippage included once in fill price',
        'security_pnl': dict(sorted(totals.items())), 'fees': fees, 'net_pnl': net,
        'reconciliation_error': error, 'daily': daily,
        'max_daily_reconciliation_error': max_daily_error,
        'industry_attribution': 'separate strict PIT Brinson gate; never current stock_basic substitution'}
