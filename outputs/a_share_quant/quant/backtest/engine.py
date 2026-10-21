from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Protocol

from ..data.schema import Bar
from ..data.trading_lots import buy_quantity_error
from .costs import CostBreakdown, CostModel


class OrderRejectReason(str, Enum):
    BOARD_LOT = "buy_quantity_must_be_multiple_of_100"
    PAUSED = "paused"
    LIMIT_UP_BUY = "limit_up_buy"
    LIMIT_DOWN_SELL = "limit_down_sell"
    T_PLUS_ONE = "t_plus_one"
    VOLUME_LIMIT = "volume_limit"
    AMOUNT_LIMIT = "amount_limit"
    INSUFFICIENT_CASH = "insufficient_cash"
    NO_POSITION = "no_position"
    INVALID_ORDER = "invalid_order"


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    quantity: int
    price: float | None = None
    tag: str = ""


@dataclass(frozen=True)
class Fill:
    order: Order
    date: date
    quantity: int
    price: float
    costs: CostBreakdown


@dataclass(frozen=True)
class RejectedOrder:
    order: Order
    date: date
    reason: OrderRejectReason


@dataclass
class Position:
    symbol: str
    lots: list[tuple[date, int, float]] = field(default_factory=list)

    @property
    def quantity(self) -> int:
        return sum(quantity for _, quantity, _ in self.lots)

    def available_to_sell(self, as_of: date) -> int:
        return sum(quantity for acquired_on, quantity, _ in self.lots if acquired_on < as_of)

    def add(self, acquired_on: date, quantity: int, price: float) -> None:
        self.lots.append((acquired_on, quantity, price))

    def remove(self, as_of: date, quantity: int) -> None:
        remaining = quantity
        new_lots: list[tuple[date, int, float]] = []
        for acquired_on, lot_quantity, price in self.lots:
            if remaining and acquired_on < as_of:
                used = min(remaining, lot_quantity)
                lot_quantity -= used
                remaining -= used
            if lot_quantity:
                new_lots.append((acquired_on, lot_quantity, price))
        if remaining:
            raise ValueError("attempted to remove more eligible shares than available")
        self.lots = new_lots


@dataclass(frozen=True)
class PortfolioSnapshot:
    as_of: date
    cash: float
    positions: dict[str, int]
    market_value: float
    equity: float
    average_entry_prices: dict[str, float] = field(default_factory=dict)
    receivables: float = 0.0


@dataclass(frozen=True)
class DailyEquity:
    date: date
    cash: float
    market_value: float
    equity: float
    weights: dict[str, float] = field(default_factory=dict)
    positions: dict[str, int] = field(default_factory=dict)
    receivables: float = 0.0


@dataclass
class BacktestResult:
    initial_cash: float
    final_cash: float
    final_equity: float
    fills: list[Fill]
    rejected: list[RejectedOrder]
    equity_curve: list[DailyEquity]
    corporate_action_report: dict[str, object] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return sum(fill.costs.total for fill in self.fills)

    @property
    def turnover(self) -> float:
        if not self.initial_cash:
            return 0.0
        return sum(fill.costs.notional for fill in self.fills) / self.initial_cash


class Strategy(Protocol):
    def orders_for_date(
        self, trading_date: date, history: dict[str, list[Bar]], portfolio: PortfolioSnapshot
    ) -> list[Order]:
        ...


class EventDrivenBacktester:
    """Small, auditable daily event loop with no access to future bars."""

    def __init__(self, initial_cash: float = 1_000_000.0, cost_model: CostModel | None = None, *, amount_participation: float = .10) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.initial_cash = initial_cash
        self.cost_model = cost_model or CostModel()
        if not 0 < amount_participation <= 1:
            raise ValueError('amount_participation must be in (0,1]')
        self.amount_participation = amount_participation

    def run(
        self,
        bars: list[Bar],
        strategy: Strategy,
        *,
        evaluation_start: date | None = None,
        evaluation_end: date | None = None,
        corporate_actions: bool = False,
        action_provider: object | None = None,
    ) -> BacktestResult:
        """Run the event loop, optionally scoring only a held-out window.

        Bars before ``evaluation_start`` are exposed to the strategy as warm-up
        history, but orders and equity are not recorded. This is the primitive
        used by the sealed test protocol: the portfolio starts with fresh cash
        and all reported metrics come only from the sealed interval.
        """
        if not bars:
            raise ValueError("bars cannot be empty")
        if evaluation_start and evaluation_end and evaluation_start > evaluation_end:
            raise ValueError("evaluation_start cannot be later than evaluation_end")
        by_date: dict[date, dict[str, Bar]] = {}
        for bar in bars:
            by_date.setdefault(bar.date, {})[bar.symbol] = bar
        ordered_dates = sorted(by_date)
        histories: dict[str, list[Bar]] = {}
        positions: dict[str, Position] = {}
        cash = self.initial_cash
        fills: list[Fill] = []
        rejected: list[RejectedOrder] = []
        curve: list[DailyEquity] = []
        last_prices: dict[str, float] = {}
        action_book = None
        if corporate_actions:
            if action_provider is None or not callable(action_provider):
                raise ValueError("corporate_actions requires an observed action provider")
            from .corporate_actions import BacktestCorporateActionBook

            action_book = BacktestCorporateActionBook(action_provider)

        for trading_date in ordered_dates:
            if evaluation_end and trading_date > evaluation_end:
                break
            day_bars = by_date[trading_date]
            in_evaluation_window = evaluation_start is None or trading_date >= evaluation_start
            if in_evaluation_window:
                receivables = action_book.receivables(trading_date, last_prices) if action_book else 0.0
                snapshot = self._snapshot(trading_date, cash, positions, last_prices, receivables)
                orders = strategy.orders_for_date(trading_date, self._copy_histories(histories), snapshot)
                orders = sorted(orders, key=lambda order: (order.side != "SELL", order.symbol))
                for order in orders:
                    result = self._execute(order, trading_date, day_bars, positions, cash)
                    if isinstance(result, RejectedOrder):
                        rejected.append(result)
                        continue
                    fill, cash = result
                    fills.append(fill)
            # A final quote is not proof of an executable liquidation or payout.
            for symbol, bar in day_bars.items():
                if bar.is_delisted and symbol in positions and positions[symbol].quantity:
                    raise ValueError(
                        f"delisting settlement evidence required: {symbol} on {trading_date}; "
                        "a final close cannot be credited as cash"
                    )
                if not bar.paused:
                    last_prices[symbol] = bar.close
            if action_book:
                cash += action_book.process_day(trading_date, positions, last_prices)
            market_value = sum(pos.quantity * last_prices.get(symbol, 0.0) for symbol, pos in positions.items())
            receivables = action_book.receivables(trading_date, last_prices) if action_book else 0.0
            if in_evaluation_window:
                equity = cash + market_value + receivables
                weights = {
                    symbol: (position.quantity * last_prices.get(symbol, 0.0)) / equity
                    for symbol, position in positions.items()
                    if equity > 0 and position.quantity > 0 and last_prices.get(symbol, 0.0) > 0
                }
                curve.append(DailyEquity(trading_date, cash, market_value, equity, weights,
                    {symbol: position.quantity for symbol, position in positions.items()}, receivables))
            for symbol, bar in day_bars.items():
                histories.setdefault(symbol, []).append(bar)

        if not curve:
            raise ValueError("evaluation window contains no trading dates")
        final = curve[-1]
        return BacktestResult(
            self.initial_cash, final.cash, final.equity, fills, rejected, curve,
            action_book.report() if action_book else {},
        )

    def _execute(
        self,
        order: Order,
        trading_date: date,
        day_bars: dict[str, Bar],
        positions: dict[str, Position],
        cash: float,
    ) -> tuple[Fill, float] | RejectedOrder:
        bar = day_bars.get(order.symbol)
        if bar is None or order.side not in {"BUY", "SELL"} or order.quantity <= 0:
            return RejectedOrder(order, trading_date, OrderRejectReason.INVALID_ORDER)
        if order.side == "BUY" and buy_quantity_error(order.symbol, order.quantity):
            return RejectedOrder(order, trading_date, OrderRejectReason.BOARD_LOT)
        if bar.paused:
            return RejectedOrder(order, trading_date, OrderRejectReason.PAUSED)
        reference = order.price if order.price is not None else bar.open
        if reference <= 0 or bar.volume <= 0:
            return RejectedOrder(order, trading_date, OrderRejectReason.INVALID_ORDER)
        max_quantity = int(bar.volume * self.cost_model.max_participation)
        if order.quantity > max_quantity:
            return RejectedOrder(order, trading_date, OrderRejectReason.VOLUME_LIMIT)
        if order.side == "SELL":
            position = positions.get(order.symbol)
            if position is None or position.quantity < order.quantity:
                return RejectedOrder(order, trading_date, OrderRejectReason.NO_POSITION)
            if position.available_to_sell(trading_date) < order.quantity:
                return RejectedOrder(order, trading_date, OrderRejectReason.T_PLUS_ONE)
        executed_price, _ = self.cost_model.execution_price(order.side, reference, order.quantity, bar.volume)
        if bar.amount is not None and order.quantity * executed_price > bar.amount * self.amount_participation + 1e-9:
            return RejectedOrder(order, trading_date, OrderRejectReason.AMOUNT_LIMIT)
        if order.side == "BUY" and bar.limit_up is not None and executed_price >= bar.limit_up - 1e-10:
            return RejectedOrder(order, trading_date, OrderRejectReason.LIMIT_UP_BUY)
        if order.side == "SELL" and bar.limit_down is not None and executed_price <= bar.limit_down + 1e-10:
            return RejectedOrder(order, trading_date, OrderRejectReason.LIMIT_DOWN_SELL)
        # Keep the reference notional separate from explicit slippage so the
        # cash ledger and the cost report cannot count market impact twice.
        costs = self.cost_model.calculate(order.side, reference, order.quantity, bar.volume, trading_date=trading_date)
        if order.side == "BUY":
            required = costs.notional + costs.total
            if required > cash + 1e-9:
                return RejectedOrder(order, trading_date, OrderRejectReason.INSUFFICIENT_CASH)
            positions.setdefault(order.symbol, Position(order.symbol)).add(trading_date, order.quantity, executed_price)
            cash -= required
        else:
            position = positions[order.symbol]
            position.remove(trading_date, order.quantity)
            cash += costs.notional - costs.total
            if position.quantity == 0:
                del positions[order.symbol]
        return Fill(order, trading_date, order.quantity, executed_price, costs), cash

    @staticmethod
    def _copy_histories(histories: dict[str, list[Bar]]) -> dict[str, list[Bar]]:
        return {symbol: list(items) for symbol, items in histories.items()}

    @staticmethod
    def _snapshot(
        as_of: date, cash: float, positions: dict[str, Position], last_prices: dict[str, float],
        receivables: float = 0.0,
    ) -> PortfolioSnapshot:
        quantities = {symbol: position.quantity for symbol, position in positions.items()}
        market_value = sum(quantity * last_prices.get(symbol, 0.0) for symbol, quantity in quantities.items())
        entry_prices = {symbol: sum(qty * price for _, qty, price in position.lots) / position.quantity
            for symbol, position in positions.items() if position.quantity > 0}
        return PortfolioSnapshot(as_of, cash, quantities, market_value, cash + market_value + receivables, entry_prices, receivables)
