"""Fixed, prospective financial baseline candidate; not a promoted winner."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.strategy_fundamentals import METRICS, observed_financial_inputs
from ..factors.store import FactorDefinition
from .baseline import CrossSectionalMomentum


class CostAwareBaseline(CrossSectionalMomentum):
    def __init__(self, *, rebalance_every: int = 5, factor_definitions: Sequence[FactorDefinition] | None = None,
                 fundamental_provider: Callable[[date, Sequence[str]], tuple[dict[str, dict[str, float]], dict[str, Any]]] = observed_financial_inputs) -> None:
        super().__init__(20, 5, rebalance_every, .20, factor_definitions=factor_definitions)
        configured = {item.metric for item in self.factor_composite.definitions if item.factor_type == "fundamental" and self.factor_composite.weights.get("fundamental", 0) > 0}
        if not set(METRICS).issubset(configured):
            raise ValueError("财务baseline要求冻结ROE、利润增长、收入增长、负债率四项因子")
        self.fundamental_provider = fundamental_provider
        self.financial_input_report: dict[str, Any] = {"status": "not_loaded"}
        self.signal_scores: list[dict[str, Any]] = []

    def orders_for_date(self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot) -> list[Order]:
        if (self._days_seen+1) % self.rebalance_every == 0:
            values, report = self.fundamental_provider(trading_date, list(history))
            self.financial_input_report = report
            self.fundamental_values = values
            if report.get("status") != "available":
                self._days_seen += 1
                return []
            history = {symbol: bars for symbol, bars in history.items() if symbol in values and all(metric in values[symbol] for metric in METRICS)}
            history = {symbol: bars for symbol, bars in history.items() if len(bars) >= self.lookback + 1}
            if len(history) < self.top_n:
                self.financial_input_report = {**report, "status": "insufficient_price_history", "eligible_price_symbols": len(history)}
                self._days_seen += 1
                return []
        return super().orders_for_date(trading_date, history, portfolio)

    def _select(self, scores: list[tuple[str, float, Bar, tuple[str, ...]]], portfolio: PortfolioSnapshot) -> dict[str, Bar]:
        ranked = sorted(scores, key=lambda entry: (-entry[1], entry[0]))
        self.signal_scores = [{"symbol": symbol, "score": score, "active_factor_keys": list(keys),
                               "financial_values": self.fundamental_values.get(symbol, {})}
                              for symbol, score, _, keys in ranked]
        # Retain qualified incumbents in ranks 1..8; never retain exited PIT
        # constituents or names without eligible observed financial evidence.
        held = [entry for entry in ranked[:8] if portfolio.positions.get(entry[0], 0) > 0][:self.top_n]
        selected = {entry[0]: entry[2] for entry in held}
        for symbol, _, bar, _ in ranked:
            if len(selected) >= self.top_n:
                break
            selected.setdefault(symbol, bar)
        return selected

    def _investment_fraction(self) -> float:
        return .98

    def _skip_small_adjustment(self, symbol: str, current_value: float, delta: float, portfolio: PortfolioSnapshot) -> bool:
        if portfolio.positions.get(symbol, 0) <= 0 or portfolio.equity <= 0:
            return False
        if delta < 0 and current_value/portfolio.equity > self.max_weight:
            return False
        return abs(delta)/portfolio.equity < .02

    def factor_metadata(self) -> dict[str, object]:
        return {**super().factor_metadata(), "strategy_revision": "baseline-cost-financial-v1",
                "retention_rank": 8, "rebalance_deadband": .02, "cash_reserve": .02,
                "financial_input_report": self.financial_input_report,
                "signal_scores": self.signal_scores,
                "research_candidate": True, "outperformance_proven": False}
