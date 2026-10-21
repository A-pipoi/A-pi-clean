from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from datetime import date

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.trading_lots import rounded_buy_quantity
from ..factors.composite import FactorComposite
from ..factors.store import FactorDefinition
from ..portfolio.allocator import inverse_volatility_weights, volatility_target_scale


class CrossSectionalMomentum:
    """Simple, auditable baseline: top-N momentum, periodic equal-weight rebalance."""

    def __init__(
        self,
        lookback: int = 20,
        top_n: int = 5,
        rebalance_every: int = 5,
        max_weight: float = 0.20,
        *,
        factor_definitions: Sequence[FactorDefinition] | None = None,
        factor_weights: Mapping[str, float] | None = None,
        fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
        allocation_mode: str = "equal_weight",
    ) -> None:
        if min(lookback, top_n, rebalance_every) <= 0 or not 0 < max_weight <= 1:
            raise ValueError("invalid baseline strategy parameters")
        if allocation_mode not in {"equal_weight", "inverse_volatility", "volatility_targeted"}:
            raise ValueError("unsupported allocation_mode")
        self.lookback = lookback
        self.top_n = top_n
        self.rebalance_every = rebalance_every
        self.max_weight = max_weight
        self.allocation_mode = allocation_mode
        self.factor_composite = FactorComposite(factor_definitions, weights=factor_weights)
        self.fundamental_values = fundamental_values or {}
        self._days_seen = 0
        self._last_allocation_scale = 1.0

    def orders_for_date(
        self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot
    ) -> list[Order]:
        del trading_date
        self._days_seen += 1
        if self._days_seen % self.rebalance_every != 0:
            return []
        scores = self._rank_scores(history)
        scores.sort(key=lambda item: item[1], reverse=True)
        selected = self._select(scores, portfolio)
        if not selected:
            return []
        if self.allocation_mode == "inverse_volatility":
            weights = inverse_volatility_weights(
                {symbol: self._realized_volatility(history.get(symbol, []), self.lookback) for symbol in selected},
                max_weight=self.max_weight,
            )
        else:
            weights = {symbol: min(1.0 / len(selected), self.max_weight) for symbol in selected}
            self._last_allocation_scale = 1.0
            if self.allocation_mode == "volatility_targeted":
                scale = volatility_target_scale(history, weights, target_annual_volatility=0.15)
                self._last_allocation_scale = scale
                weights = {symbol: weight * scale for symbol, weight in weights.items()}
        orders: list[Order] = []
        for symbol, quantity in portfolio.positions.items():
            if symbol not in selected:
                orders.append(Order(symbol, "SELL", quantity, tag="rebalance_exit"))
        for symbol, bar in selected.items():
            current_value = portfolio.positions.get(symbol, 0) * bar.close
            target = portfolio.equity * weights.get(symbol, 0.0)
            target *= self._investment_fraction()
            delta = target - current_value
            if self._skip_small_adjustment(symbol, current_value, delta, portfolio):
                continue
            if delta > bar.close:
                quantity = rounded_buy_quantity(symbol, int(delta // bar.close))
                if quantity:
                    orders.append(Order(symbol, "BUY", quantity, tag="rebalance_entry"))
            elif delta < -bar.close:
                orders.append(Order(symbol, "SELL", int((-delta) // bar.close), tag="rebalance_reduce"))
        return [order for order in orders if order.quantity > 0]

    def _select(self, scores: list[tuple[str, float, Bar, tuple[str, ...]]], portfolio: PortfolioSnapshot) -> dict[str, Bar]:
        return {symbol: bar for symbol, _, bar, _ in scores[:self.top_n]}

    def _investment_fraction(self) -> float:
        return 1.0

    def _skip_small_adjustment(self, symbol: str, current_value: float, delta: float, portfolio: PortfolioSnapshot) -> bool:
        return False

    def _rank_scores(self, history: dict[str, list[Bar]]) -> list[tuple[str, float, Bar, tuple[str, ...]]]:
        composite_scores = self.factor_composite.rank_scores(
            history,
            fundamental_values=self.fundamental_values,
        )
        bars_by_symbol = {symbol: bars for symbol, bars in history.items() if bars}
        return [
            (item.symbol, item.score, bars_by_symbol[item.symbol][-1], item.active_factor_keys)
            for item in composite_scores
            if item.symbol in bars_by_symbol
        ]

    def factor_metadata(self) -> dict[str, object]:
        return {
            **self.factor_composite.metadata(),
            "fundamental_values_supplied": bool(self.fundamental_values),
            "allocation_mode": self.allocation_mode,
            "target_annual_volatility": 0.15 if self.allocation_mode == "volatility_targeted" else None,
            "latest_volatility_scale": self._last_allocation_scale,
        }

    @staticmethod
    def _realized_volatility(bars: list[Bar], window: int) -> float:
        closes = [
            bar.signal_close if bar.signal_close is not None else bar.close
            for bar in bars
            if (bar.signal_close if bar.signal_close is not None else bar.close) > 0
        ][-window - 1:]
        returns = [right / left - 1.0 for left, right in itertools.pairwise(closes) if left > 0]
        if len(returns) < 2:
            return 0.05
        mean = sum(returns) / len(returns)
        return max(1e-4, (sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)) ** 0.5)


class DualHorizonMomentum(CrossSectionalMomentum):
    """Momentum-priority composite; no parameter search is performed."""

    def __init__(
        self,
        lookback: int = 12,
        top_n: int = 5,
        rebalance_every: int = 5,
        max_weight: float = 0.20,
        *,
        factor_definitions: Sequence[FactorDefinition] | None = None,
        fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
        allocation_mode: str = "equal_weight",
    ) -> None:
        super().__init__(
            lookback,
            top_n,
            rebalance_every,
            max_weight,
            factor_definitions=factor_definitions,
            factor_weights={"momentum": 1.25, "volatility": 0.25},
            fundamental_values=fundamental_values,
            allocation_mode=allocation_mode,
        )


class LowVolatilityDefensive(CrossSectionalMomentum):
    """Volatility-priority composite with a fixed risk-first score."""

    def __init__(
        self,
        lookback: int = 30,
        top_n: int = 5,
        rebalance_every: int = 5,
        max_weight: float = 0.20,
        *,
        factor_definitions: Sequence[FactorDefinition] | None = None,
        fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
        allocation_mode: str = "equal_weight",
    ) -> None:
        super().__init__(
            lookback,
            top_n,
            rebalance_every,
            max_weight,
            factor_definitions=factor_definitions,
            factor_weights={"momentum": 0.25, "volatility": 1.25},
            fundamental_values=fundamental_values,
            allocation_mode=allocation_mode,
        )
