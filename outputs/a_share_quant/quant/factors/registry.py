from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..data.schema import Bar

FactorFunction = Callable[[list[Bar]], float | None]


@dataclass(frozen=True)
class FactorContext:
    as_of: object
    histories: dict[str, list[Bar]]


class FactorRegistry:
    def __init__(self) -> None:
        self._factors: dict[str, FactorFunction] = {}

    def register(self, name: str, function: FactorFunction) -> None:
        if not name or name in self._factors:
            raise ValueError(f"invalid or duplicate factor: {name!r}")
        self._factors[name] = function

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factors))

    def compute(self, context: FactorContext) -> dict[str, dict[str, float | None]]:
        # context.histories must contain bars strictly before context.as_of;
        # that contract is enforced by the event-driven caller.
        return {
            symbol: {name: function(history) for name, function in self._factors.items()}
            for symbol, history in context.histories.items()
        }


def momentum(window: int = 20) -> FactorFunction:
    if window < 2:
        raise ValueError("momentum window must be >= 2")

    def calculate(history: list[Bar]) -> float | None:
        if len(history) < window:
            return None
        old = history[-window].close
        return history[-1].close / old - 1.0 if old > 0 else None

    return calculate


def reversal(window: int = 5) -> FactorFunction:
    factor = momentum(window)

    def calculate(history: list[Bar]) -> float | None:
        value = factor(history)
        return -value if value is not None else None

    return calculate


def realized_volatility(window: int = 20) -> FactorFunction:
    if window < 3:
        raise ValueError("volatility window must be >= 3")

    def calculate(history: list[Bar]) -> float | None:
        if len(history) < window + 1:
            return None
        returns = [history[index].close / history[index - 1].close - 1.0 for index in range(-window + 1, 0)]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return variance**0.5

    return calculate
