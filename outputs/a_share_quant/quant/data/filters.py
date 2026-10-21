from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from .schema import Bar
from .universe import HistoricalUniverse


@dataclass(frozen=True)
class UniverseRules:
    min_listing_days: int = 60
    exclude_st: bool = True
    exclude_paused: bool = True
    exclude_delisted: bool = True
    min_price_volume: float = 0.0


def filter_universe(
    bars: list[Bar],
    as_of: object | None,
    rules: UniverseRules | None = None,
    *,
    listing_dates: Mapping[str, date | str] | None = None,
    delisting_dates: Mapping[str, date | str] | None = None,
    historical_universe: HistoricalUniverse | None = None,
) -> list[Bar]:
    """Apply explicit point-in-time eligibility rules to known daily bars.

    Listing-age filtering fails closed when its static ``stock_basic.list_date``
    input is omitted. Historical index membership is always looked up using
    each bar's own date, never the current constituent list.
    """

    rules = rules or UniverseRules()
    if rules.min_listing_days < 0:
        raise ValueError("min_listing_days cannot be negative")
    if rules.min_listing_days and listing_dates is None:
        raise ValueError("listing_dates from stock_basic.list_date are required for listing-age filtering")
    cutoff = _coerce_date(as_of) if as_of is not None else None
    normalized_dates = _normalize_listing_dates(listing_dates or {})
    normalized_delisting_dates = _normalize_listing_dates(delisting_dates or {})
    result: list[Bar] = []
    for bar in bars:
        observation_day = bar.date
        if cutoff is not None and observation_day > cutoff:
            continue
        listing_day = normalized_dates.get(bar.symbol.upper()) or normalized_dates.get(bar.symbol.split(".", 1)[0])
        delisting_day = normalized_delisting_dates.get(bar.symbol.upper()) or normalized_delisting_dates.get(
            bar.symbol.split(".", 1)[0]
        )
        if rules.min_listing_days:
            if listing_day is None or listing_day > observation_day:
                continue
            if (observation_day - listing_day).days < rules.min_listing_days:
                continue
        # A symbol may remain in a stale index-weight snapshot after delisting.
        # Its delisting date is an unconditional point-in-time eligibility bound.
        if delisting_day is not None and observation_day > delisting_day:
            continue
        if historical_universe is not None and not historical_universe.contains(bar.symbol.upper(), observation_day):
            continue
        if not bar.listed:
            continue
        if rules.exclude_st and bar.is_st:
            continue
        if rules.exclude_paused and bar.paused:
            continue
        if rules.exclude_delisted and bar.is_delisted:
            continue
        if bar.close * bar.volume < rules.min_price_volume:
            continue
        result.append(bar)
    return result


def _normalize_listing_dates(values: Mapping[str, date | str]) -> dict[str, date]:
    result: dict[str, date] = {}
    for symbol, raw_day in values.items():
        try:
            if isinstance(raw_day, datetime):
                day = raw_day.date()
            elif isinstance(raw_day, date):
                day = raw_day
            else:
                day = date.fromisoformat(str(raw_day).strip()[:10])
        except (TypeError, ValueError):
            continue
        code = str(symbol).strip().upper()
        if not code:
            continue
        result[code] = day
        result.setdefault(code.split(".", 1)[0], day)
    return result


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError) as error:
        raise ValueError("as_of must be a date or ISO date string") from error
