"""Idempotent prospective dividend entitlements; never reconstruct old holdings."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from quant.time_utils import parse_yyyymmdd


def initialize(db: sqlite3.Connection) -> None:
    db.execute('CREATE TABLE IF NOT EXISTS paper_entitlement_days (day TEXT PRIMARY KEY, captured_at TEXT NOT NULL, positions_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS paper_dividends (event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, record_date TEXT NOT NULL, source_json TEXT NOT NULL, quantity INTEGER NOT NULL, cash_paid INTEGER NOT NULL DEFAULT 0, shares_paid INTEGER NOT NULL DEFAULT 0, cash_amount REAL NOT NULL DEFAULT 0, share_amount INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL)')


def snapshot(db: sqlite3.Connection, day: date) -> None:
    positions = {str(r[0]):int(r[1]) for r in db.execute('SELECT symbol,SUM(quantity) FROM paper_lots GROUP BY symbol')}
    db.execute('INSERT OR IGNORE INTO paper_entitlement_days VALUES(?,?,?)',
               (day.isoformat(),datetime.now(UTC).isoformat(),json.dumps(positions,sort_keys=True)))


def _date(value: Any) -> date:
    text = str(value or '').replace('-','')
    return parse_yyyymmdd(text)


def _number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result<0:
        raise ValueError('nonnegative finite distribution required')
    return result


def settle(db: sqlite3.Connection, day: date, events: list[dict[str, Any]]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for event in events:
        if event.get('__invalid_event'):
            reports.append({'symbol':event.get('ts_code'),'status':'blocked_invalid_provider_distribution',
                            'reason':event['__invalid_event']})
            continue
        if event.get('div_proc') != '实施':
            continue
        try:
            symbol = str(event['ts_code'])
            record = _date(event['record_date'])
            ex = _date(event['ex_date'])
            publication = max(_date(event['ann_date']),_date(event.get('imp_ann_date') or event['ann_date']))
            if publication>day or record>=day or ex<=record:
                continue
            identity = hashlib.sha256(f'{symbol}:{record}:{ex}:{event.get("end_date")}'.encode()).hexdigest()
            raw = json.dumps(event,ensure_ascii=False,sort_keys=True,allow_nan=False)
            saved = db.execute('SELECT * FROM paper_dividends WHERE event_id=?',(identity,)).fetchone()
            if saved is None:
                captured = db.execute('SELECT positions_json FROM paper_entitlement_days WHERE day=?',(record.isoformat(),)).fetchone()
                if captured is None:
                    reports.append({'symbol':symbol,'record_date':record.isoformat(),'status':'blocked_missing_record_date_snapshot'})
                    continue
                quantity = int(json.loads(captured[0]).get(symbol,0))
                db.execute('INSERT INTO paper_dividends(event_id,symbol,record_date,source_json,quantity,status) VALUES(?,?,?,?,?,?)',
                           (identity,symbol,record.isoformat(),raw,quantity,'captured'))
                saved = db.execute('SELECT * FROM paper_dividends WHERE event_id=?',(identity,)).fetchone()
            if saved['source_json']!=raw:
                old=json.loads(saved['source_json'])
                # Missing payment/listing evidence may arrive later. A change
                # to any already known entitlement amount requires review.
                changed_known_amount=any(old.get(field) is not None and old.get(field)!=event.get(field)
                                         for field in ('cash_div','stk_div'))
                changed_posted_date=(bool(saved['cash_paid']) and old.get('pay_date')!=event.get('pay_date')) or (bool(saved['shares_paid']) and old.get('div_listdate')!=event.get('div_listdate'))
                if changed_known_amount or changed_posted_date:
                    reports.append({'symbol':symbol,'status':'blocked_source_revision_requires_review'})
                    continue
                db.execute('UPDATE paper_dividends SET source_json=? WHERE event_id=?',(raw,identity))
            quantity = int(saved['quantity'])
            cash_status, share_status = 'pending', 'pending'
            cash = event.get('cash_div')
            ratio = event.get('stk_div')
            if not saved['cash_paid']:
                if cash is None:
                    cash_status = 'blocked_missing_net_cash_dividend'
                elif _number(cash)==0:
                    db.execute('UPDATE paper_dividends SET cash_paid=1 WHERE event_id=?',(identity,))
                    cash_status = 'no_cash_distribution'
                elif event.get('pay_date') and _date(event['pay_date'])>=record and _date(event['pay_date'])<=day:
                    amount = (_number(cash)*quantity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    db.execute('UPDATE paper_account SET cash=cash+?,updated_at=? WHERE id=1',
                               (float(amount),datetime.now(UTC).isoformat()))
                    db.execute('UPDATE paper_dividends SET cash_paid=1,cash_amount=? WHERE event_id=?',(float(amount),identity))
                    cash_status = 'credited_provider_net_cash'
                elif not event.get('pay_date'):
                    cash_status = 'blocked_missing_pay_date'
            else:
                cash_status = 'already_processed'
            if not saved['shares_paid']:
                if ratio is None:
                    share_status = 'blocked_missing_stock_distribution'
                elif _number(ratio)==0:
                    db.execute('UPDATE paper_dividends SET shares_paid=1 WHERE event_id=?',(identity,))
                    share_status = 'no_stock_distribution'
                elif event.get('div_listdate') and ex<=_date(event['div_listdate'])<=day:
                    shares = _number(ratio)*quantity
                    if shares!=shares.to_integral_value():
                        share_status = 'blocked_fractional_share_allocation_unproven'
                    else:
                        listing = _date(event['div_listdate'])
                        if shares:
                            db.execute('INSERT INTO paper_lots(symbol,acquired_on,quantity,price) VALUES(?,?,?,0)',
                                       (symbol,(listing-timedelta(days=1)).isoformat(),int(shares)))
                        db.execute('UPDATE paper_dividends SET shares_paid=1,share_amount=? WHERE event_id=?',(int(shares),identity))
                        share_status = 'credited_on_bonus_listing_date'
                elif not event.get('div_listdate'):
                    share_status = 'blocked_missing_bonus_listing_date'
            else:
                share_status = 'already_processed'
            finished = db.execute('SELECT cash_paid,shares_paid FROM paper_dividends WHERE event_id=?',(identity,)).fetchone()
            status = 'complete' if all(finished) else 'pending_or_blocked'
            db.execute('UPDATE paper_dividends SET status=? WHERE event_id=?',(status,identity))
            reports.append({'symbol':symbol,'quantity_at_record_close':quantity,'status':status,
                            'cash_status':cash_status,'share_status':share_status})
        except (KeyError,ValueError,TypeError,ArithmeticError):
            # A malformed event must not partially post cash or shares.
            raise ValueError('invalid corporate action; transaction must be rolled back') from None
    return {'events':reports,'price_basis':'raw quotes; no manual qfq/adj_factor repricing',
            'cash_basis':'provider cash_div (net); not personalized holding-period tax accounting',
            'historical_entitlements_reconstructed':False}


def observed_events(cutoff: datetime) -> list[dict[str, Any]]:
    from quant.data.extension_store import default_store_path
    path = default_store_path()
    if not path.exists():
        return []
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ts_dividend'").fetchone():
            return []
        latest = {}
        for key, raw in db.execute('SELECT business_key,payload_json FROM ts_dividend WHERE ingested_at<=? ORDER BY ingested_at,rowid',
                                   (cutoff.astimezone(UTC).isoformat(),)):
            latest[key] = json.loads(raw)
    output=[]
    for event in latest.values():
        if event.get('div_proc')!='实施':
            continue
        try:
            for field in ('record_date','ex_date'):
                _date(event[field])
            _date(event.get('ann_date') or event.get('imp_ann_date'))
            # An absent original plan date is not invented from implementation.
            if not event.get('ann_date'):
                raise ValueError('missing original announcement date')
        except (KeyError,TypeError,ValueError):
            output.append({'__invalid_event':'missing_or_invalid_implemented_action_dates','ts_code':event.get('ts_code')})
            continue
        output.append(event)
    return output
