"""Explicit China-market calendar helpers shared across data services."""

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SHANGHAI_TZ: tzinfo
try:
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    # Current China-market clocks use UTC+8 without DST. This fallback is
    # not a substitute for historical timezone conversion rules.
    SHANGHAI_TZ = timezone(timedelta(hours=8), "CST")


def china_today() -> date:
    """Return the current calendar date in the A-share market timezone."""
    return datetime.now(SHANGHAI_TZ).date()


def parse_yyyymmdd(value: str) -> date:
    """Parse an exchange date as a date, never as a timezone-naive instant."""
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise ValueError("date must use YYYYMMDD")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
