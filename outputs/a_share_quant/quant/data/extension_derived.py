"""Dimensionless changes and same-date execution features, observed-only.

No forward filling across dates, no conversion of missing values to zero, and
no claim that today's ingestion was historically available.
"""
from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from quant.time_utils import SHANGHAI_TZ

from .extension_store import ExtensionStore, canonical

FORMULAS = {
    'margin_balance_change': '(latest rzye - previous rzye) / abs(previous rzye)',
    'northbound_ratio_change': 'latest ratio - previous ratio; percentage points',
    'holder_count_change': '(previous holder_num - latest holder_num) / previous holder_num',
    'main_moneyflow_net': 'buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount; provider amount unit',
    'main_moneyflow_intensity': 'main net amount / sum of large and extra-large buy and sell amounts; bounded imbalance, not total-market turnover',
    'block_discount': '1 - volume-weighted block price / same-trade-date raw close',
    'fund_consensus_count': 'distinct observed funds holding each stock in latest common report period; partial observed universe, not all-fund coverage',
}


def number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def derived_values(store: ExtensionStore, cutoff: datetime) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    if cutoff.tzinfo is None:
        raise ValueError('timezone-aware cutoff required')
    boundary = cutoff.astimezone(UTC).isoformat()
    day = cutoff.astimezone(SHANGHAI_TZ).strftime('%Y%m%d')
    result: dict[str, dict[str, float]] = {name: {} for name in FORMULAS}
    evidence: dict[str, dict[str, Any]] = {name: {} for name in FORMULAS}
    tables: dict[str, list[dict[str, Any]]] = {}
    with closing(store.connect()) as db:
        for endpoint in ('margin_detail', 'hk_hold', 'stk_holdernumber', 'moneyflow', 'block_trade', 'daily', 'fund_portfolio'):
            revisions: dict[str, dict[str, Any]] = {}
            scope = ''
            parameters: tuple[str, ...] = (boundary,)
            if endpoint == 'daily':
                # This feature only needs closes on actual block-trade dates.
                # Preserve same-date matching without loading the whole market.
                scope = (' AND EXISTS (SELECT 1 FROM ts_block_trade b '
                         'WHERE b.ts_code=ts_daily.ts_code AND b.period_date=ts_daily.period_date '
                         'AND b.ingested_at<=?)')
                parameters = (boundary, boundary)
            elif endpoint == 'fund_portfolio':
                # Only the latest eligible, positive common reporting period
                # contributes to this feature. Historical quarters stay stored.
                latest = db.execute(
                    'SELECT a.period_date FROM ts_fund_portfolio a WHERE a.ingested_at<=? '
                    'AND length(a.ann_date)=8 AND a.ann_date<=? AND a.period_date<=? '
                    'AND json_extract(a.payload_json,\'$.amount\')>0 '
                    'AND json_type(a.payload_json,\'$.amount\') IN (\'integer\',\'real\') '
                    'AND json_extract(a.payload_json,\'$.ts_code\') IS NOT NULL '
                    'AND json_extract(a.payload_json,\'$.symbol\') IS NOT NULL '
                    'AND NOT EXISTS (SELECT 1 FROM ts_fund_portfolio b WHERE b.business_key=a.business_key '
                    'AND b.ingested_at<=? AND length(b.ann_date)=8 AND b.ann_date<=? '
                    'AND (b.ingested_at>a.ingested_at OR (b.ingested_at=a.ingested_at AND b.rowid>a.rowid))) '
                    'ORDER BY a.period_date DESC LIMIT 1', (boundary,day,day,boundary,day)).fetchone()
                scope = ' AND period_date=?'
                parameters = (boundary,str(latest[0]) if latest else '')
            for key, raw, digest, observed in db.execute(
                f'SELECT business_key,payload_json,source_hash,ingested_at FROM ts_{endpoint} '
                f'WHERE ingested_at<=?{scope} ORDER BY ingested_at,rowid', parameters
            ):
                row = json.loads(raw)
                announced = str(row.get('ann_date') or row.get('trade_date') or '').replace('-', '')
                if len(announced) != 8 or not announced.isdigit() or announced > day:
                    continue
                # Legacy pages used only symbol/date as the block business key.
                # Distinct counterparties/prices must not overwrite one another.
                if endpoint == 'block_trade':
                    key = canonical({field: row.get(field) for field in
                                     ('ts_code','trade_date','buyer','seller','price','vol','amount')})
                revisions[key] = {**row, '_hash': digest, '_observed': observed}
            tables[endpoint] = list(revisions.values())

    def record(name: str, symbol: str, value: float, rows: list[dict[str, Any]]) -> None:
        if math.isfinite(value):
            result[name][symbol] = value
            evidence[name][symbol] = {'source_hashes': sorted({r['_hash'] for r in rows}),
                                     'available_at': max(r['_observed'] for r in rows),
                                     'source_dates': [r.get('trade_date') or r.get('end_date') for r in rows]}

    for name, endpoint, column in (
        ('margin_balance_change', 'margin_detail', 'rzye'),
        ('northbound_ratio_change', 'hk_hold', 'ratio'),
        ('holder_count_change', 'stk_holdernumber', 'holder_num'),
    ):
        groups: dict[str, dict[str, dict[str, Any]]] = {}
        for row in sorted(tables[endpoint], key=lambda r: (str(r.get('ann_date') or ''), r['_observed'])):
            symbol = row.get('ts_code')
            period = str(row.get('trade_date') or row.get('end_date') or '')
            if symbol and len(period) == 8 and period.isdigit() and period <= day:
                groups.setdefault(str(symbol), {})[period] = row
        for symbol, periods in groups.items():
            ordered = sorted(periods)
            if len(ordered) < 2:
                continue
            previous, latest = periods[ordered[-2]], periods[ordered[-1]]
            old, new = number(previous.get(column)), number(latest.get(column))
            if old is None or new is None or (name != 'northbound_ratio_change' and old <= 0):
                continue
            change = new - old if name == 'northbound_ratio_change' else (old - new) / old if name == 'holder_count_change' else (new - old) / abs(old)
            record(name, symbol, change, [previous, latest])

    latest_flows: dict[str, dict[str, Any]] = {}
    for row in sorted(tables['moneyflow'], key=lambda r: (str(r.get('trade_date')), r['_observed'])):
        if row.get('ts_code'):
            latest_flows[str(row['ts_code'])] = row
    for symbol, row in latest_flows.items():
        values = [number(row.get(c)) for c in ('buy_lg_amount', 'buy_elg_amount', 'sell_lg_amount', 'sell_elg_amount')]
        if all(v is not None and v >= 0 for v in values):
            buy_lg, buy_elg, sell_lg, sell_elg = [float(v) for v in values if v is not None]
            record('main_moneyflow_net', symbol, buy_lg + buy_elg - sell_lg - sell_elg, [row])
            total = buy_lg + buy_elg + sell_lg + sell_elg
            if total > 0:
                record('main_moneyflow_intensity', symbol, (buy_lg + buy_elg - sell_lg - sell_elg) / total, [row])

    closes = {(str(r.get('ts_code')), str(r.get('trade_date'))): r for r in tables['daily']}
    blocks: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in tables['block_trade']:
        if row.get('ts_code'):
            blocks.setdefault(str(row['ts_code']), {}).setdefault(str(row.get('trade_date')), []).append(row)
    for symbol, dates in blocks.items():
        date = max(dates)
        rows = dates[date]
        close_row = closes.get((symbol, date))
        close = number(close_row.get('close')) if close_row else None
        valid = [(number(r.get('price')), number(r.get('vol'))) for r in rows]
        if close is None or close <= 0 or any(p is None or v is None or p <= 0 or v <= 0 for p, v in valid):
            continue
        pairs = [(p, v) for p, v in valid if p is not None and v is not None]
        weighted = sum(p * v for p, v in pairs) / sum(v for _, v in pairs)
        assert close_row is not None
        record('block_discount', symbol, 1 - weighted / close, [*rows, close_row])
    holdings = [r for r in tables['fund_portfolio'] if r.get('ts_code') and r.get('symbol')
                and len(str(r.get('end_date') or '')) == 8 and str(r['end_date']).isdigit()
                and str(r['end_date']) <= day and (number(r.get('amount')) or 0) > 0]
    if holdings:
        period = max(str(r['end_date']) for r in holdings)
        positions: dict[str, dict[str, dict[str, Any]]] = {}
        for row in holdings:
            if str(row['end_date']) == period:
                positions.setdefault(str(row['symbol']), {})[str(row['ts_code'])] = row
        for symbol, funds in positions.items():
            record('fund_consensus_count', symbol, float(len(funds)), list(funds.values()))
    return result, {'formulas': FORMULAS, 'evidence': evidence,
                    'historical_backfill_enabled': False, 'missing_values_imputed': False}
