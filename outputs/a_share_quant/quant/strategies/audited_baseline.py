"""Fixed research candidates with a common factor basis and turnover controls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.strategy_fundamentals import METRICS, observed_financial_inputs
from ..factors.composite import FactorComposite
from ..factors.store import FactorDefinition
from .baseline import CrossSectionalMomentum

AUDITED_STRATEGIES = frozenset({"baseline_price_audited", "baseline_full_audited"})


def weight_profiles(full: bool) -> dict[str, dict[str, float]]:
    if full:
        return {
            "equal_factor": {"momentum": 1.0, "volatility": 1.0, "fundamental": 1.0},
            "group_balanced": {
                "momentum": 0.25,
                "volatility": 0.25,
                "fundamental": 0.125,
            },
            "price_priority": {
                "momentum": 0.375,
                "volatility": 0.375,
                "fundamental": 0.0625,
            },
        }
    return {
        "equal_factor": {"momentum": 1.0, "volatility": 1.0},
        "momentum_priority": {"momentum": 3.0, "volatility": 1.0},
        "low_volatility_priority": {"momentum": 1.0, "volatility": 3.0},
    }


class AuditedBaseline(CrossSectionalMomentum):
    def __init__(
        self,
        *,
        full: bool = False,
        profile: str = "equal_factor",
        rebalance_every: int = 5,
        factor_definitions: Sequence[FactorDefinition] | None = None,
        fundamental_provider: Callable[
            ..., tuple[dict[str, dict[str, float]], dict[str, Any]]
        ] = observed_financial_inputs,
    ) -> None:
        super().__init__(
            20, 5, rebalance_every, 0.20, factor_definitions=factor_definitions
        )
        self.full = full
        self.profile = profile
        self.fundamental_provider = fundamental_provider
        definitions = [
            item
            for item in self.factor_composite.definitions
            if (item.factor_type in {"momentum", "volatility"} and item.window == 20)
            or (full and item.factor_type == "fundamental" and item.metric in METRICS)
        ]
        expected = {"momentum", "volatility"} | (set(METRICS) if full else set())
        identities = [
            item.metric if item.factor_type == "fundamental" else item.factor_type
            for item in definitions
        ]
        if set(identities) != expected or len(identities) != len(expected):
            raise ValueError(
                "审计基准需要唯一的20日动量、20日波动率；完整版另需四项财务因子，缺失或重复时不替换"
            )
        self.factor_composite = FactorComposite(
            definitions,
            weights=weight_profiles(full)[profile],
            coverage_policy="complete",
            minimum_coverage=0.90,
        )
        self.financial_input_report: dict[str, Any] = {
            "status": "not_required" if not full else "not_loaded"
        }
        self.blocked_rebalances = 0

    def orders_for_date(
        self,
        trading_date: date,
        history: dict[str, list[Bar]],
        portfolio: PortfolioSnapshot,
    ) -> list[Order]:
        if (self._days_seen + 1) % self.rebalance_every == 0 and self.full:
            self.fundamental_values, self.financial_input_report = (
                self.fundamental_provider(trading_date, list(history))
            )
            if self.financial_input_report.get("status") != "available":
                self.factor_composite.rank_scores(history, fundamental_values={})
                self.blocked_rebalances += 1
                self._days_seen += 1
                return []
        return super().orders_for_date(trading_date, history, portfolio)

    def _select(
        self,
        scores: list[tuple[str, float, Bar, tuple[str, ...]]],
        portfolio: PortfolioSnapshot,
    ) -> dict[str, Bar]:
        if len(scores) < self.top_n:
            self.blocked_rebalances += 1
            return {}
        ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
        selected = {
            symbol: bar
            for symbol, _, bar, _ in ranked[:8]
            if portfolio.positions.get(symbol, 0) > 0
        }
        selected = dict(list(selected.items())[: self.top_n])
        for symbol, _, bar, _ in ranked:
            if len(selected) >= self.top_n:
                break
            selected.setdefault(symbol, bar)
        return selected

    def _investment_fraction(self) -> float:
        return 0.98

    def _skip_small_adjustment(
        self,
        symbol: str,
        current_value: float,
        delta: float,
        portfolio: PortfolioSnapshot,
    ) -> bool:
        if portfolio.positions.get(symbol, 0) <= 0 or portfolio.equity <= 0:
            return False
        if delta < 0 and current_value / portfolio.equity > self.max_weight:
            return False
        return abs(delta) / portfolio.equity < 0.02

    def factor_metadata(self) -> dict[str, object]:
        return {
            **super().factor_metadata(),
            "strategy_revision": "full-audit-v1" if self.full else "price-audit-v1",
            "weight_profile": self.profile,
            "research_candidate": True,
            "outperformance_proven": False,
            "retention_rank": 8,
            "rebalance_deadband": 0.02,
            "cash_reserve": 0.02,
            "blocked_rebalances": self.blocked_rebalances,
            "financial_input_report": self.financial_input_report,
        }
