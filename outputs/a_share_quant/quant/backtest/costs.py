from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CostBreakdown:
    notional: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    slippage: float
    total: float


@dataclass(frozen=True)
class CostModel:
    """Mainland A-share cost model; rates are explicit and never default to zero."""

    commission_rate: float = 0.00025
    min_commission: float = 5.0
    # None selects the dated statutory schedule; a number is an explicit
    # isolated experiment override, including zero-cost control tests.
    stamp_tax_rate: float | None = None
    transfer_fee_rate: float = 0.00001
    fixed_slippage_bps: float = 5.0
    impact_bps: float = 10.0
    max_participation: float = 0.10

    def execution_price(self, side: str, reference_price: float, quantity: float, volume: float) -> tuple[float, float]:
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        if volume <= 0:
            raise ValueError("volume must be positive for an executable order")
        participation = min(max(quantity / volume, 0.0), 1.0)
        bps = self.fixed_slippage_bps + self.impact_bps * participation
        slippage = reference_price * bps / 10_000.0
        return (reference_price + slippage if side == "BUY" else reference_price - slippage, slippage * quantity)

    def stamp_rate(self, trading_date: date | None = None) -> float:
        if self.stamp_tax_rate is not None:
            return self.stamp_tax_rate
        if trading_date is not None and trading_date < date(2008, 9, 19):
            raise ValueError("pre-2008-09-19 stamp duty requires an explicit historical schedule")
        return 0.001 if trading_date is not None and trading_date < date(2023, 8, 28) else 0.0005

    def calculate(self, side: str, price: float, quantity: float, volume: float, *, trading_date: date | None = None) -> CostBreakdown:
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported side: {side}")
        notional = price * quantity
        commission = max(notional * self.commission_rate, self.min_commission) if notional else 0.0
        stamp_tax = notional * self.stamp_rate(trading_date) if side == "SELL" else 0.0
        transfer_fee = notional * self.transfer_fee_rate
        _, slippage = self.execution_price(side, price, quantity, volume)
        return CostBreakdown(
            notional=notional,
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
            slippage=slippage,
            total=commission + stamp_tax + transfer_fee + slippage,
        )
