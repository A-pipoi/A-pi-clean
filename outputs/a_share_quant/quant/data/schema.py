from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class Bar:
    """Daily bar with raw execution OHLC and optional separate signal-layer OHLC."""

    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    limit_up: float | None = None
    limit_down: float | None = None
    paused: bool = False
    listed: bool = True
    is_st: bool = False
    industry: str | None = None
    is_delisted: bool = False
    amount: float | None = None  # Actual unadjusted CNY turnover, never inferred from close*volume.
    signal_open: float | None = None
    signal_high: float | None = None
    signal_low: float | None = None
    signal_close: float | None = None

    def __post_init__(self) -> None:
        import math
        if self.amount is not None and (not math.isfinite(self.amount) or self.amount < 0):
            raise ValueError('amount must be finite nonnegative CNY')
        for name in ("open", "high", "low", "close", "volume"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        signal_prices = (self.signal_open, self.signal_high, self.signal_low, self.signal_close)
        if any(value is not None for value in signal_prices):
            import math

            if any(value is None or not math.isfinite(value) or value <= 0 for value in signal_prices):
                raise ValueError("signal OHLC must be finite positive values when provided")
            assert self.signal_open is not None
            assert self.signal_high is not None
            assert self.signal_low is not None
            assert self.signal_close is not None
            if self.signal_high < max(self.signal_open, self.signal_close):
                raise ValueError("signal high must cover signal open and close")
            if self.signal_low > min(self.signal_open, self.signal_close):
                raise ValueError("signal low must cover signal open and close")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC invariant violated")


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: date
    cash_dividend: float = 0.0
    stock_dividend_ratio: float = 0.0
    rights_issue_ratio: float = 0.0
    rights_issue_price: float | None = None


@dataclass(frozen=True)
class DataSnapshot:
    version: str
    source: str
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureObservation:
    symbol: str
    as_of: date
    available_at: datetime
    name: str
    value: float | None
    source_version: str
    report_date: date | None = None
    announcement_date: date | None = None
    availability_basis: str = "unknown"


@dataclass(frozen=True)
class PITIndustryObservation:
    symbol: str
    as_of: date
    available_at: datetime
    industry: str
    source_version: str
    availability_basis: str = "unknown"
