from __future__ import annotations

from datetime import date

from ..backtest.engine import Order, PortfolioSnapshot
from ..data.schema import Bar
from ..data.trading_lots import rounded_buy_quantity
from ..features.store import FEATURE_NAMES, FeatureStore


class WalkForwardLightGBMStrategy:
    """A separately registered, past-only model strategy; never a paper default."""

    def __init__(
        self,
        feature_store: FeatureStore,
        data_version: str,
        *,
        top_n: int = 5,
        rebalance_every: int = 5,
        max_weight: float = 0.20,
        training_window: int = 252,
        min_training_samples: int = 120,
    ) -> None:
        if not data_version.strip() or min(top_n, rebalance_every, training_window, min_training_samples) <= 0:
            raise ValueError("invalid LightGBM walk-forward configuration")
        self.feature_store = feature_store
        self.data_version = data_version
        self.top_n = top_n
        self.rebalance_every = rebalance_every
        self.max_weight = max_weight
        self.training_window = training_window
        self.min_training_samples = min_training_samples
        self._days_seen = 0
        self._last_fit: dict[str, object] = {"status": "waiting_for_history"}

    def orders_for_date(
        self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot
    ) -> list[Order]:
        self._days_seen += 1
        if self._days_seen % self.rebalance_every:
            return []
        eligible_histories = {
            symbol: [bar for bar in bars if bar.close > 0]
            for symbol, bars in history.items()
        }
        first_indexes = {
            symbol: max(60, len(bars) - self.training_window - 1)
            for symbol, bars in eligible_histories.items()
            if len(bars) >= 62
        }
        feature_start = min(
            (eligible_histories[symbol][index].date for symbol, index in first_indexes.items()),
            default=trading_date,
        )
        feature_maps = self.feature_store.features_for_symbols(
            list(eligible_histories),
            before=trading_date,
            after=feature_start,
            data_version=self.data_version,
        )
        training_x: list[list[float]] = []
        training_y: list[float] = []
        predictions: dict[str, tuple[list[float], Bar]] = {}
        for symbol, usable in eligible_histories.items():
            vectors = feature_maps.get(symbol, {})
            if len(usable) < 62:
                continue
            first = first_indexes[symbol]
            for index in range(first, len(usable) - 1):
                vector = vectors.get(usable[index].date)
                next_bar = usable[index + 1]
                # Both feature and label must have existed before this session.
                if vector is None or next_bar.date >= trading_date:
                    continue
                target = next_bar.close / usable[index].close - 1.0
                if target > -1.0:
                    training_x.append([vector[name] for name in FEATURE_NAMES])
                    training_y.append(target)
            latest = vectors.get(usable[-1].date)
            if latest is not None:
                predictions[symbol] = ([latest[name] for name in FEATURE_NAMES], usable[-1])

        if len(training_y) < self.min_training_samples or not predictions:
            self._last_fit = {"status": "insufficient_history", "training_samples": len(training_y)}
            return []
        try:
            import lightgbm as lgb
            import numpy as np
        except ImportError as error:
            raise RuntimeError("LightGBM 或 NumPy 未安装；请安装可选依赖 `.[ml]` 后再运行该研究策略") from error

        training_set = lgb.Dataset(
            np.asarray(training_x, dtype=np.float32),
            label=np.asarray(training_y, dtype=np.float32),
            feature_name=list(FEATURE_NAMES),
            free_raw_data=True,
        )
        model = lgb.train(
            {
                "objective": "regression",
                "learning_rate": 0.03,
                "max_depth": 3,
                "num_leaves": 7,
                "min_data_in_leaf": 25,
                "bagging_fraction": 0.8,
                "feature_fraction": 0.8,
                "bagging_freq": 1,
                "lambda_l2": 5.0,
                "seed": 20260928,
                "feature_fraction_seed": 20260928,
                "bagging_seed": 20260928,
                "data_random_seed": 20260928,
                "num_threads": 1,
                "verbosity": -1,
                "deterministic": True,
                "force_col_wise": True,
            },
            training_set,
            num_boost_round=120,
        )
        scored = sorted(
            ((symbol, float(model.predict(np.asarray([vector], dtype=np.float32), num_threads=1)[0]), bar)
             for symbol, (vector, bar) in predictions.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        selected = scored[: self.top_n]
        if not selected:
            return []
        weights = {symbol: min(1.0 / len(selected), self.max_weight) for symbol, _, _ in selected}
        selected_symbols = {symbol for symbol, _, _ in selected}
        orders: list[Order] = []
        for symbol, quantity in portfolio.positions.items():
            if symbol not in selected_symbols:
                orders.append(Order(symbol, "SELL", quantity, tag="lightgbm_walk_forward_exit"))
        for symbol, _, bar in selected:
            current_value = portfolio.positions.get(symbol, 0) * bar.close
            target_value = portfolio.equity * weights[symbol]
            delta = target_value - current_value
            if delta > bar.close:
                quantity = rounded_buy_quantity(symbol, int(delta // bar.close))
                if quantity:
                    orders.append(Order(symbol, "BUY", quantity, tag="lightgbm_walk_forward_entry"))
            elif delta < -bar.close:
                quantity = int((-delta) // bar.close)
                if quantity:
                    orders.append(Order(symbol, "SELL", quantity, tag="lightgbm_walk_forward_reduce"))
        self._last_fit = {
            "status": "trained",
            "training_samples": len(training_y),
            "training_window_sessions": self.training_window,
            "features": list(FEATURE_NAMES),
            "objective": "next_observed_close_to_close_return",
            "labels_available_before_signal": True,
            "data_version": self.data_version,
            "feature_importance_gain": {
                name: float(value)
                for name, value in zip(FEATURE_NAMES, model.feature_importance(importance_type="gain"))
            },
            "automatic_promotion": False,
        }
        return orders

    def factor_metadata(self) -> dict[str, object]:
        return {
            "model": "lightgbm_walk_forward",
            "model_library": "LightGBM",
            "feature_schema_version": "daily-price-features-v2",
            "retraining": "each_rebalance_using_only_available_features_and_labels",
            "selection": "top_n_predicted_next_observed_close_return",
            "automatic_promotion": False,
            "latest_fit": dict(self._last_fit),
        }
