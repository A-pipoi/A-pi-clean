"""Auditable daily-close overlays; never infer an intraday stop execution."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from .baseline import CrossSectionalMomentum


@dataclass(frozen=True)
class OverlayRules:
    trend_window: int = 20
    risk_off_breadth: float = 0.30
    reduced_risk_breadth: float = 0.60
    reduced_exposure: float = 0.50
    stop_loss: float = 0.08
    take_profit: float = 0.25

    def __post_init__(self) -> None:
        values = (self.risk_off_breadth, self.reduced_risk_breadth,
                  self.reduced_exposure, self.stop_loss, self.take_profit)
        if self.trend_window < 2 or any(not math.isfinite(v) or not 0 < v < 1 for v in values):
            raise ValueError('invalid multi-signal overlay rules')
        if self.risk_off_breadth >= self.reduced_risk_breadth:
            raise ValueError('risk-off breadth must be below reduced-risk breadth')


class MultiSignalStrategy(CrossSectionalMomentum):
    """Factor ranking + trend eligibility + breadth timing + closing-price exits.

    All decisions use strictly earlier bars. Stop/take-profit means a next-session
    order after an observed close crosses the threshold, NOT a fill at the stop.
    The existing engine enforces next open, T+1, lots, limits and participation.
    """
    def __init__(self, *, rebalance_every: int = 5, rules: OverlayRules | None = None) -> None:
        super().__init__(20, 5, rebalance_every, max_weight=0.20,
            factor_weights={'momentum': 1.0, 'volatility': 0.75, 'reversal': 0.25})
        self.rules = rules or OverlayRules()
        self._scale = 0.0
        self._breadth: float | None = None

    def orders_for_date(self, trading_date: date, history: dict[str, list[Bar]],
                        portfolio: PortfolioSnapshot) -> list[Order]:
        if any(bar.date >= trading_date for rows in history.values() for bar in rows):
            raise ValueError('multi-signal history contains current/future bars')
        eligible = {}
        considered = 0
        for symbol, rows in history.items():
            if len(rows) < self.rules.trend_window or rows[-1].paused:
                continue
            window = rows[-self.rules.trend_window:]
            if any(not math.isfinite(b.close) or b.close <= 0 for b in window):
                continue
            considered += 1
            if rows[-1].close > sum(b.close for b in window) / len(window):
                eligible[symbol] = rows
        self._breadth = len(eligible) / considered if considered else None
        self._scale = (0.0 if self._breadth is None or self._breadth < self.rules.risk_off_breadth
            else self.rules.reduced_exposure if self._breadth < self.rules.reduced_risk_breadth else 1.0)
        exits: dict[str, Order] = {}
        for symbol, quantity in portfolio.positions.items():
            rows = history.get(symbol, [])
            entry = portfolio.average_entry_prices.get(symbol)
            if not rows or not entry or entry <= 0:
                continue
            change = rows[-1].close / entry - 1
            reason = ('close_stop_loss' if change <= -self.rules.stop_loss
                else 'close_take_profit' if change >= self.rules.take_profit else '')
            if reason:
                exits[symbol] = Order(symbol, 'SELL', quantity, tag=reason)
        # Missing warm-up is not a fabricated bearish signal: no order at all.
        if not considered:
            self._days_seen += 1
            return list(exits.values())
        if self._scale == 0:
            self._days_seen += 1
            return [Order(symbol, 'SELL', qty, tag='breadth_risk_off')
                for symbol, qty in portfolio.positions.items()]
        # Keep the original equity for sizing; scaling max_weight reduces exposure.
        self.max_weight = 0.20 * self._scale
        filtered = {symbol: rows for symbol, rows in eligible.items() if symbol not in exits}
        orders = super().orders_for_date(trading_date, filtered, portfolio)
        return list(exits.values()) + [order for order in orders if order.symbol not in exits]

    def factor_metadata(self) -> dict[str, object]:
        return {**super().factor_metadata(), 'overlay': {
            'name': 'multi-signal-v1', 'breadth': self._breadth, 'exposure_scale': self._scale,
            'stop_loss': self.rules.stop_loss, 'take_profit': self.rules.take_profit,
            'execution': 'next-session executable open; no intraday stop-price fill',
            'research_only': True,
        }}
