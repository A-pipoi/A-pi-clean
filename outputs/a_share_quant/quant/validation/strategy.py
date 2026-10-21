from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from ..backtest.costs import CostModel
from ..backtest.engine import (
    EventDrivenBacktester,
    PortfolioSnapshot,
    Strategy,
)
from ..data.schema import Bar
from .statistics import summarize_returns
from .walk_forward import PurgedWalkForward


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    status: str
    evidence: str


@dataclass(frozen=True)
class StrategyValidationReport:
    passed: bool
    data_days: int
    folds: int
    oos_observations: int
    annualized_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    checks: tuple[ValidationCheck, ...]
    note: str
    embargo: int = 5
    effective_observations: float | None = None
    fold_ranges: tuple[dict[str, str], ...] = ()


class _HistoryGuard:
    """Reject a strategy if the engine ever exposes the current/future bar."""

    def __init__(self, delegate: Strategy) -> None:
        self.delegate = delegate

    def orders_for_date(
        self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot
    ) -> list:
        for symbol, bars in history.items():
            if bars and max(bar.date for bar in bars) >= trading_date:
                raise ValueError(f"point-in-time violation: {symbol} history reaches {trading_date}")
        return self.delegate.orders_for_date(trading_date, history, portfolio)


def validate_strategy(
    bars: Sequence[Bar],
    strategy_factory: Callable[[], Strategy],
    *,
    initial_cash: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    train_window: int = 40,
    test_window: int = 15,
    embargo: int | None = 5,
    rebalance_every: int = 5,
    minimum_oos_observations: int = 60,
) -> StrategyValidationReport:
    """Run a chronological, purged, out-of-sample validation protocol.

    This validates the research protocol and reports OOS statistics. It does
    not turn a positive historical result into an investment recommendation.
    """

    if rebalance_every <= 0:
        raise ValueError("rebalance_every must be positive")
    effective_embargo = max(2, 2 * rebalance_every) if embargo is None else int(embargo)
    if effective_embargo < 0:
        raise ValueError("embargo cannot be negative")
    ordered_bars = sorted(bars, key=lambda bar: (bar.date, bar.symbol))
    dates = sorted({bar.date for bar in ordered_bars})
    checks: list[ValidationCheck] = []
    if not dates:
        return StrategyValidationReport(
            False, 0, 0, 0, None, None, None,
            (ValidationCheck("数据长度", "不通过", "没有可验证的交易日"),),
            "没有可用于样本外验证的行情数据",
            effective_embargo,
        )

    try:
        splitter = PurgedWalkForward(train_window, test_window, effective_embargo)
        folds = splitter.split(dates)
        splitter.validate_no_overlap(folds)
    except ValueError as error:
        return StrategyValidationReport(
            False,
            len(dates),
            0,
            0,
            None,
            None,
            None,
            (ValidationCheck("Purged Walk-Forward", "不通过", str(error)),),
            "数据长度不足以形成训练、embargo 和测试窗口",
            effective_embargo,
        )

    checks.append(ValidationCheck(
        "Purged Walk-Forward",
        "通过",
        f"{len(folds)} 个折叠；训练 {train_window} 日、embargo {effective_embargo} 日、测试 {test_window} 日",
    ))

    oos_returns: list[float] = []
    try:
        for fold in folds:
            test_end = dates[fold.test_indices[-1]]
            # Replay from the global origin for every fold.  This keeps a
            # weekly/monthly rebalance phase tied to the real trading calendar
            # instead of silently restarting it at each test window.
            prefix = [bar for bar in ordered_bars if bar.date <= test_end]
            result = EventDrivenBacktester(
                initial_cash=initial_cash,
                cost_model=cost_model or CostModel(),
            ).run(prefix, _HistoryGuard(strategy_factory()))
            curve_by_date = {point.date: point.equity for point in result.equity_curve}
            test_dates = dates[fold.test_indices[0] : fold.test_indices[-1] + 1]
            previous = curve_by_date.get(dates[fold.test_indices[0] - 1], initial_cash)
            for day in test_dates:
                current = curve_by_date[day]
                if previous > 0:
                    oos_returns.append(current / previous - 1.0)
                previous = current
    except ValueError as error:
        checks.append(ValidationCheck("点时历史检查", "不通过", str(error)))
        return StrategyValidationReport(
            False, len(dates), len(folds), len(oos_returns), None, None, None,
            tuple(checks), "策略读取了当前交易日或未来的历史数据", effective_embargo,
        )

    checks.append(ValidationCheck(
        "点时历史检查",
        "通过",
        "所有折叠中，策略仅收到当前交易日之前的历史 K 线",
    ))
    if minimum_oos_observations < 20:
        raise ValueError("minimum_oos_observations must be at least 20")
    sample_status = "通过" if len(oos_returns) >= minimum_oos_observations else "不通过"
    checks.append(ValidationCheck(
        "样本外观测数量",
        sample_status,
        f"累计 {len(oos_returns)} 个样本外日收益；最低要求 {minimum_oos_observations}",
    ))

    stats = summarize_returns(oos_returns)
    passed = all(check.status == "通过" for check in checks)
    note = (
        "验证流程通过；样本外统计仅描述当前数据集，不构成收益保证"
        if passed
        else "验证流程未通过，不能把当前结果作为策略结论"
    )
    return StrategyValidationReport(
        passed,
        len(dates),
        len(folds),
        len(oos_returns),
        stats.annualized_return,
        stats.sharpe,
        stats.max_drawdown,
        tuple(checks),
        note,
        effective_embargo,
        stats.effective_observations,
        tuple({
            "train_start": dates[fold.train_indices[0]].isoformat(),
            "train_end": dates[fold.train_indices[-1]].isoformat(),
            "embargo_start": dates[fold.embargo_indices[0]].isoformat() if fold.embargo_indices else "",
            "embargo_end": dates[fold.embargo_indices[-1]].isoformat() if fold.embargo_indices else "",
            "test_start": dates[fold.test_indices[0]].isoformat(),
            "test_end": dates[fold.test_indices[-1]].isoformat(),
        } for fold in folds),
    )
