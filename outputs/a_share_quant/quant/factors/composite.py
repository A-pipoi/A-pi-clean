from __future__ import annotations

"""Point-in-time factor composition used by all registered strategies.

The composite deliberately uses cross-sectional percentile ranks instead of
raw factor magnitudes.  That keeps momentum, volatility and fundamental
metrics comparable and makes the active factor set auditable in every run.
Fundamental values are optional for historical runs: callers must provide
announcement-date-aligned observations before they can affect a score.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..data.schema import Bar
from .expression import build_factor_panel, evaluate_expression
from .participation import ParticipationAudit
from .store import FactorDefinition, calculate_factor

FACTOR_PIPELINE_VERSION = "factor-pipeline-v1"
DEFAULT_FACTOR_WEIGHTS = {
    "momentum": 1.0,
    "reversal": 1.0,
    "volatility": 1.0,
    "fundamental": 1.0,
}


@dataclass(frozen=True)
class CompositeScore:
    symbol: str
    score: float
    factor_values: Mapping[str, float]
    active_factor_keys: tuple[str, ...]


class FactorComposite:
    """Calculate one auditable composite signal from registered factors."""

    def __init__(
        self,
        definitions: Sequence[FactorDefinition] | None = None,
        *,
        weights: Mapping[str, float] | None = None,
        coverage_policy: str = "available",
        minimum_coverage: float = 0.0,
    ) -> None:
        self.definitions = tuple(
            item for item in (definitions or _builtin_definitions()) if item.enabled
        )
        self.weights = {
            **DEFAULT_FACTOR_WEIGHTS,
            **{str(key): float(value) for key, value in (weights or {}).items()},
        }
        if (
            coverage_policy not in {"available", "complete"}
            or not 0 <= minimum_coverage <= 1
        ):
            raise ValueError("invalid factor coverage policy")
        if any(
            not math.isfinite(value) or value < 0 for value in self.weights.values()
        ):
            raise ValueError("factor weights must be finite and nonnegative")
        self.coverage_policy = coverage_policy
        self.minimum_coverage = minimum_coverage
        self.audit = ParticipationAudit()
        if not self.definitions:
            raise ValueError("factor composite requires at least one enabled factor")

    def rank_scores(
        self,
        history: Mapping[str, Sequence[Bar]],
        *,
        fundamental_values: Mapping[str, Mapping[str, float]] | None = None,
    ) -> list[CompositeScore]:
        raw_by_symbol: dict[str, dict[str, float]] = {}
        expression_values: dict[str, dict[str, float]] = {}
        expression_definitions = [
            item
            for item in self.definitions
            if item.factor_type == "expression" and item.expression
        ]
        if expression_definitions:
            bars = [bar for symbol_bars in history.values() for bar in symbol_bars]
            panel = build_factor_panel(bars)
            for definition in expression_definitions:
                evaluated = evaluate_expression(definition.expression, panel)
                if not evaluated.dates:
                    continue
                latest_values = evaluated.values[-1]
                expression_values[definition.key] = {
                    symbol: float(value)
                    for symbol, value in zip(evaluated.symbols, latest_values)
                    if value is not None
                }
        for symbol, symbol_bars in history.items():
            values: dict[str, float] = {}
            for definition in self.definitions:
                value: float | None
                if definition.factor_type == "expression":
                    value = expression_values.get(definition.key, {}).get(symbol)
                elif definition.factor_type == "fundamental":
                    value = _fundamental_value(
                        fundamental_values, symbol, definition.metric
                    )
                else:
                    value = calculate_factor(definition, list(symbol_bars))
                if value is not None and math.isfinite(value):
                    values[definition.key] = float(value)
            if values:
                raw_by_symbol[str(symbol)] = values

        ranks: dict[str, dict[str, float]] = {symbol: {} for symbol in raw_by_symbol}
        for definition in self.definitions:
            observations = {
                symbol: values[definition.key]
                for symbol, values in raw_by_symbol.items()
                if definition.key in values
            }
            if not observations:
                continue
            signed = {
                symbol: _signed_value(definition, value)
                for symbol, value in observations.items()
            }
            for symbol, value in signed.items():
                ranks[symbol][definition.key] = _percentile(
                    value, list(signed.values())
                )

        weights = {
            item.key: self.weights.get(
                item.key, self.weights.get(item.factor_type, 1.0)
            )
            for item in self.definitions
        }
        required = {key for key, weight in weights.items() if weight > 0}
        eligible = {
            symbol
            for symbol in raw_by_symbol
            if self.coverage_policy == "available" or required.issubset(ranks[symbol])
        }
        if len(eligible) < len(history) * self.minimum_coverage:
            eligible = set()
        if self.coverage_policy == "complete":
            ranks = {symbol: {} for symbol in eligible}
            for definition in self.definitions:
                signed = {
                    symbol: _signed_value(
                        definition, raw_by_symbol[symbol][definition.key]
                    )
                    for symbol in eligible
                    if definition.key in raw_by_symbol[symbol]
                }
                for symbol, value in signed.items():
                    ranks[symbol][definition.key] = _percentile(
                        value, list(signed.values())
                    )
        self.audit.observe(
            self.definitions,
            raw_by_symbol,
            ranks,
            list(history),
            eligible,
            weights,
            str(
                max((bar.date for rows in history.values() for bar in rows), default="")
            ),
        )
        result: list[CompositeScore] = []
        for symbol, values in raw_by_symbol.items():
            if symbol not in eligible:
                continue
            weighted = [
                (ranks[symbol][key] - 0.5) * weights[key]
                for definition in self.definitions
                for key in [definition.key]
                if key in ranks[symbol] and weights[key] > 0
            ]
            total_weight = sum(
                weights[definition.key]
                for definition in self.definitions
                if definition.key in ranks[symbol] and weights[definition.key] > 0
            )
            score = sum(weighted) / total_weight if total_weight else 0.0
            result.append(
                CompositeScore(
                    symbol=symbol,
                    score=score,
                    factor_values=values,
                    active_factor_keys=tuple(sorted(ranks[symbol])),
                )
            )
        return result

    def metadata(self) -> dict[str, Any]:
        return {
            "version": FACTOR_PIPELINE_VERSION,
            "factor_keys": [item.key for item in self.definitions],
            "factor_names": [item.name for item in self.definitions],
            "weights": dict(self.weights),
            "point_in_time": True,
            "coverage_policy": self.coverage_policy,
            "minimum_coverage": self.minimum_coverage,
            "participation": self.audit.report(),
        }


def _fundamental_value(
    values: Mapping[str, Mapping[str, float]] | None,
    symbol: str,
    metric: str,
) -> float | None:
    if not values:
        return None
    row = (
        values.get(symbol)
        or values.get(symbol.upper())
        or values.get(symbol.split(".", 1)[0])
    )
    if not row or metric not in row:
        return None
    try:
        return float(row[metric])
    except (TypeError, ValueError):
        return None


def _signed_value(definition: FactorDefinition, value: float) -> float:
    if definition.factor_type == "volatility" or definition.metric == "debt_to_assets":
        return -value
    return value


def _percentile(value: float, values: list[float]) -> float:
    if len(values) <= 1:
        return 0.5
    less = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    return (less + 0.5 * equal) / len(values)


def _builtin_definitions() -> tuple[FactorDefinition, ...]:
    from .store import DEFAULT_FACTOR_DEFINITIONS

    return tuple(
        FactorDefinition(
            key=f"builtin_{factor_type}_{window or metric}",
            name=name,
            factor_type=factor_type,
            window=window,
            enabled=True,
            created_at="builtin",
            metric=metric,
        )
        for name, factor_type, window, metric in DEFAULT_FACTOR_DEFINITIONS
    )
