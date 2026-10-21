from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class PaperOrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int = 0
    status: PaperOrderStatus = PaperOrderStatus.NEW
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reject_reason: str | None = None


class PaperBroker:
    """In-memory OMS used for signal/paper-trading rehearsal; no real orders leave this class."""

    def __init__(self) -> None:
        self.orders: dict[str, PaperOrder] = {}

    def submit(self, order_id: str, symbol: str, side: str, quantity: int) -> PaperOrder:
        if order_id in self.orders:
            raise ValueError(f"duplicate order id: {order_id}")
        if side not in {"BUY", "SELL"} or quantity <= 0:
            order = PaperOrder(order_id, symbol, side, quantity, status=PaperOrderStatus.REJECTED, reject_reason="invalid order")
        else:
            order = PaperOrder(order_id, symbol, side, quantity)
        self.orders[order_id] = order
        return order

    def acknowledge_fill(self, order_id: str, quantity: int) -> PaperOrder:
        order = self.orders[order_id]
        if order.status in {PaperOrderStatus.REJECTED, PaperOrderStatus.CANCELLED, PaperOrderStatus.FILLED}:
            raise ValueError(f"order {order_id} cannot accept a fill in status {order.status}")
        if quantity <= 0 or order.filled_quantity + quantity > order.quantity:
            raise ValueError("invalid fill quantity")
        order.filled_quantity += quantity
        order.status = PaperOrderStatus.FILLED if order.filled_quantity == order.quantity else PaperOrderStatus.PARTIALLY_FILLED
        return order

    def cancel(self, order_id: str) -> PaperOrder:
        order = self.orders[order_id]
        if order.status in {PaperOrderStatus.FILLED, PaperOrderStatus.REJECTED}:
            raise ValueError(f"order {order_id} cannot be cancelled")
        order.status = PaperOrderStatus.CANCELLED
        return order
