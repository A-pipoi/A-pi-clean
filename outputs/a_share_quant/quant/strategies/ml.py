from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.trading_lots import rounded_buy_quantity
from ..models.linear import LinearFactorModel


class WalkForwardLinearStrategy:
    """Ridge model retrained only on past daily bars at each rebalance.

    Each training label is the next observed close-to-close return. A label is
    eligible only after its ending bar is already in ``history``; the current
    session and all future bars are unavailable to this strategy method.
    """

    FEATURE_NAMES = ("momentum_5", "momentum_base", "momentum_slow", "volatility_base")

    def __init__(
        self,
        lookback: int = 20,
        top_n: int = 5,
        rebalance_every: int = 5,
        max_weight: float = 0.20,
        *,
        training_window: int = 126,
        min_training_samples: int = 60,
        ridge_l2: float = 10.0,
    ) -> None:
        if min(lookback, top_n, rebalance_every, training_window, min_training_samples) <= 0:
            raise ValueError("walk-forward model parameters must be positive")
        if not 0 < max_weight <= 1 or ridge_l2 < 0:
            raise ValueError("invalid model allocation or ridge parameter")
        self.lookback = lookback
        self.top_n = top_n
        self.rebalance_every = rebalance_every
        self.max_weight = max_weight
        self.training_window = training_window
        self.min_training_samples = min_training_samples
        self.ridge_l2 = ridge_l2
        self._days_seen = 0
        self._last_fit: dict[str, object] = {"status": "waiting_for_history"}

    def orders_for_date(
        self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot
    ) -> list[Order]:
        del trading_date
        self._days_seen += 1
        if self._days_seen % self.rebalance_every:
            return []
        rows: list[dict[str, float]] = []
        targets: list[float] = []
        predictions: dict[str, tuple[dict[str, float], Bar]] = {}
        min_history = max(60, self.lookback * 3)
        for symbol, bars in history.items():
            usable = [bar for bar in bars if bar.close > 0]
            if len(usable) < min_history + 1:
                continue
            first_index = max(min_history, len(usable) - self.training_window - 1)
            for index in range(first_index, len(usable) - 1):
                feature_row = self._features(usable, index)
                if feature_row is None:
                    continue
                target = usable[index + 1].close / usable[index].close - 1.0
                if target > -1.0:
                    rows.append(feature_row)
                    targets.append(target)
            prediction_row = self._features(usable, len(usable) - 1)
            if prediction_row is not None:
                predictions[symbol] = (prediction_row, usable[-1])

        if len(rows) < self.min_training_samples or not predictions:
            self._last_fit = {"status": "insufficient_history", "training_samples": len(rows)}
            return []
        model = LinearFactorModel(self.FEATURE_NAMES, l2=self.ridge_l2).fit(rows, targets)
        scored = sorted(
            (
                (symbol, model.predict(row), bar)
                for symbol, (row, bar) in predictions.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        selected = scored[: self.top_n]
        if not selected:
            return []
        weights = {symbol: min(1.0 / len(selected), self.max_weight) for symbol, _, _ in selected}
        orders: list[Order] = []
        selected_symbols = {symbol for symbol, _, _ in selected}
        for symbol, quantity in portfolio.positions.items():
            if symbol not in selected_symbols:
                orders.append(Order(symbol, "SELL", quantity, tag="walk_forward_exit"))
        for symbol, _, bar in selected:
            current_value = portfolio.positions.get(symbol, 0) * bar.close
            target_value = portfolio.equity * weights[symbol]
            delta = target_value - current_value
            if delta > bar.close:
                quantity = rounded_buy_quantity(symbol, int(delta // bar.close))
                if quantity:
                    orders.append(Order(symbol, "BUY", quantity, tag="walk_forward_entry"))
            elif delta < -bar.close:
                quantity = int((-delta) // bar.close)
                if quantity:
                    orders.append(Order(symbol, "SELL", quantity, tag="walk_forward_reduce"))
        self._last_fit = {
            "status": "trained",
            "training_samples": len(rows),
            "training_window_per_symbol": self.training_window,
            "ridge_l2": self.ridge_l2,
            "features": list(self.FEATURE_NAMES),
            "intercept": model.intercept,
            "weights": dict(zip(self.FEATURE_NAMES, model.weights or ())),
        }
        return orders

    def _features(self, bars: Sequence[Bar], index: int) -> dict[str, float] | None:
        slow = self.lookback * 3
        if index < max(60, slow):
            return None
        current = bars[index].close
        base = bars[index - self.lookback].close
        slow_base = bars[index - slow].close
        fast_base = bars[index - 5].close
        if min(current, base, slow_base, fast_base) <= 0:
            return None
        close_window = [bar.close for bar in bars[index - self.lookback : index + 1] if bar.close > 0]
        returns = [right / left - 1.0 for left, right in itertools.pairwise(close_window) if left > 0]
        if len(returns) < 2:
            return None
        average = sum(returns) / len(returns)
        volatility = (sum((item - average) ** 2 for item in returns) / (len(returns) - 1)) ** 0.5
        return {
            "momentum_5": current / fast_base - 1.0,
            "momentum_base": current / base - 1.0,
            "momentum_slow": current / slow_base - 1.0,
            "volatility_base": volatility,
        }

    def factor_metadata(self) -> dict[str, object]:
        return {
            "model": "ridge_linear_walk_forward",
            "retraining": "each_rebalance_using_observed_labels_only",
            "selection": "top_n_predicted_next_observed_close_return",
            "automatic_promotion": False,
            "latest_fit": dict(self._last_fit),
        }
