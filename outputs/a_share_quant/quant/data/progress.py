"""Optional per-request data progress, with no API or storage dependency."""

from __future__ import annotations

import contextvars
from collections.abc import Callable

DATA_PROGRESS: contextvars.ContextVar[Callable[[int, int, str], None] | None] = (
    contextvars.ContextVar("data_progress", default=None)
)
DATA_CACHE_NOT_BEFORE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "data_cache_not_before", default=None
)


def report_data_progress(current: int, total: int, symbol: str) -> None:
    callback = DATA_PROGRESS.get()
    if callback:
        callback(current, total, symbol)
