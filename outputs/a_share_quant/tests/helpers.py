from __future__ import annotations

from datetime import date, timedelta

from quant.data.schema import Bar


def bar(
    symbol: str,
    day_offset: int,
    *,
    open_: float = 10.0,
    close: float = 10.0,
    volume: float = 1_000.0,
    limit_up: float | None = None,
    limit_down: float | None = None,
    paused: bool = False,
    is_delisted: bool = False,
) -> Bar:
    day = date(2024, 1, 2) + timedelta(days=day_offset)
    high = max(open_, close)
    low = min(open_, close)
    return Bar(
        symbol=symbol,
        date=day,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        limit_up=limit_up,
        limit_down=limit_down,
        paused=paused,
        is_delisted=is_delisted,
    )
