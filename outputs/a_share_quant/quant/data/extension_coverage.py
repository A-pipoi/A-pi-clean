"""Symbol/date reconciliation, never treats missing bars as proven suspensions."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Any

from quant.time_utils import parse_yyyymmdd

from .extension_analytics import latest_rows
from .extension_store import ExtensionStore


@dataclass(frozen=True)
class ListingCalendar:
    sessions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.sessions != tuple(sorted(set(self.sessions))):
            raise ValueError('calendar must be sorted and unique')


def listing_eligibility(symbol: str, day: str, listings: list[dict[str, Any]],
                        sessions: list[str] | ListingCalendar, minimum_sessions: int = 120) -> dict[str, Any]:
    """Research-only historical eligibility; calendar coverage must reach listing."""
    if minimum_sessions < 0:
        raise ValueError('minimum_sessions must be nonnegative')
    parse_yyyymmdd(day)
    matches = [r for r in listings if r.get('ts_code') == symbol]
    if len(matches) != 1:
        return {'eligible': False, 'reason': 'missing_or_ambiguous_listing_metadata'}
    row = matches[0]
    listed, delisted = str(row.get('list_date') or ''), str(row.get('delist_date') or '')
    if not listed:
        return {'eligible': False, 'reason': 'missing_listing_date'}
    if listed > day or (delisted and delisted <= day):
        return {'eligible': False, 'reason': 'outside_listing_interval'}
    ordered = sessions.sessions if isinstance(sessions,ListingCalendar) else sorted(set(sessions))
    if not ordered or ordered[0] > listed or ordered[-1] < day or day not in ordered:
        return {'eligible': False, 'reason': 'insufficient_exchange_calendar'}
    age = bisect_left(ordered, day) - bisect_left(ordered, listed)
    return {'eligible': age >= minimum_sessions,
            'reason': 'eligible' if age >= minimum_sessions else 'new_listing_exclusion',
            'completed_sessions': age, 'minimum_sessions': minimum_sessions,
            'metadata_basis': 'listing_dates_not_historical_industry_evidence'}


def reconcile_daily(store: ExtensionStore, day: str) -> dict[str, Any]:
    parse_yyyymmdd(day)
    calendars = [r for r in latest_rows(store, 'trade_cal')
                 if r.get('exchange') == 'SSE' and r.get('cal_date') == day]
    if not calendars:
        return {'date': day, 'status': 'calendar_missing', 'complete': False}
    if calendars[-1].get('is_open') != 1:
        return {'date': day, 'status': 'market_closed', 'complete': False}
    listings = latest_rows(store, 'stock_basic')
    # Identity comparison only: keep every original bar and code unchanged.
    # This mapping has no historical availability timestamps and is NOT a PIT
    # universe or an industry classification source.
    aliases = {str(r['o_code']):str(r['n_code']) for r in latest_rows(store,'bse_mapping')
               if r.get('o_code') and r.get('n_code')}
    aliases['300114.SZ'] = '302132.SZ'
    def identity(symbol: str) -> str:
        return aliases.get(symbol,symbol)
    expected = {str(r['ts_code']) for r in listings if r.get('list_date')
                and str(r['list_date']) <= day
                and (not r.get('delist_date') or str(r['delist_date']) > day)}
    expected = {identity(s) for s in expected}
    populations = {endpoint: {str(r['ts_code']) for r in latest_rows(store, endpoint, period=day)
                              if r.get('trade_date') == day and r.get('ts_code')}
                   for endpoint in ('daily', 'daily_basic', 'adj_factor', 'stk_limit')}
    original_counts = {name:len(symbols) for name,symbols in populations.items()}
    populations = {name:{identity(s) for s in symbols} for name,symbols in populations.items()}
    traded = populations['daily']
    missing = {endpoint: sorted(traded - symbols) for endpoint, symbols in populations.items()}
    unknown = sorted(traded - expected)
    suspension_rows = [r for r in latest_rows(store,'suspend_d',period=day) if r.get('trade_date')==day and r.get('ts_code')]
    by_symbol: dict[str,list[dict[str,Any]]]={}
    for row in suspension_rows:
        by_symbol.setdefault(identity(str(row['ts_code'])),[]).append(row)
    full_day = {symbol for symbol, rows in by_symbol.items()
                if all(r.get('suspend_type')=='S' and r.get('suspend_timing') in (None,'') for r in rows)}
    proven = sorted((expected-traded) & full_day)
    unexplained = sorted(expected - traded - full_day)
    aligned = bool(traded) and bool(expected) and not unknown and not any(missing.values())
    return {'date': day, 'status': 'aligned_observed_quotes' if aligned else 'coverage_failed',
            'complete': aligned and not unexplained,
            'observed_quotes_aligned': aligned, 'expected_listed_symbols': len(expected),
            'observed_symbols': {name: len(symbols) for name, symbols in populations.items()},
            'original_quote_code_counts':original_counts,
            'identity_comparison_only':True, 'historical_pit_created':False,
            'identity_sources':['https://tushare.pro/document/2?doc_id=375',
                'https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-15/cedb693a-f5ee-4463-9682-ea33d406b569.PDF'],
            'missing_for_traded_symbols': missing, 'unexplained_no_daily_bar': unexplained,
            'proven_full_day_suspensions': proven,
            'unknown_listing_symbols': unknown,
            'suspension_evidence_available': bool(suspension_rows),
            'note': 'Missing daily bars are not silently classified as suspensions; historical listing dates are not PIT industry proof.'}
