"""Shared, durable Shanghai data jobs. Never submits paper or broker orders."""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Any

from quant.time_utils import SHANGHAI_TZ as SHANGHAI

from .extension_catalog import SCHEDULE
from .extension_store import ExtensionStore, canonical
from .extension_sync import load_token, sync
from .extension_transport import query

LOGGER = logging.getLogger(__name__)
FINANCIAL_INDICATOR_FIELDS = 'ts_code,ann_date,end_date,roa,netprofit_yoy,ocf_to_or'


class ExtensionScheduler:
    def __init__(self, store: ExtensionStore, notify: Callable[[str, str], None]) -> None:
        self.store, self.notify = store, notify
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        store.initialize()
        with closing(store.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS ts_schedule (identity TEXT PRIMARY KEY, endpoint TEXT NOT NULL, params TEXT NOT NULL, status TEXT NOT NULL, report TEXT, updated_at TEXT NOT NULL)')
            for row in db.execute("SELECT identity FROM ts_schedule WHERE status='pending' AND identity LIKE '%:financial:%'").fetchall():
                parts = str(row[0]).split(':')
                if len(parts)>2 and parts[2] in ('disclosure_date','forecast','express','dividend','repurchase','income','balancesheet','cashflow','fina_indicator','fina_audit','stk_holdernumber'):
                    db.execute("UPDATE ts_schedule SET status='superseded',report=? WHERE identity=?",
                               (canonical({'reason':'replaced_by_disclosure_driven_bounded_financial_jobs','source_rows_deleted':False}),row[0]))
            for identity,endpoint,serialized in db.execute("SELECT identity,endpoint,params FROM ts_schedule WHERE status='pending' AND identity LIKE '%:financial:%'").fetchall():
                if endpoint in {'income','balancesheet','cashflow','fina_indicator'} and 'period' not in json.loads(serialized):
                    db.execute("UPDATE ts_schedule SET status='superseded',report=? WHERE identity=?",
                               (canonical({'reason':'run_date_was_used_as_report_period; replaced_by_disclosure_period_jobs','source_rows_deleted':False}),identity))
                elif endpoint in {'income','balancesheet','cashflow','fina_indicator'} and json.loads(serialized).get('ts_code'):
                    db.execute("UPDATE ts_schedule SET status='superseded',report=? WHERE identity=?",
                               (canonical({'reason':'replaced_by_5000_point_report_period_bulk_jobs','source_rows_deleted':False}),identity))

    def enqueue(self, identity: str, endpoint: str, params: dict[str, Any], now: datetime) -> None:
        with closing(self.store.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO ts_schedule VALUES(?,?,?,'pending',NULL,?)", (identity, endpoint, canonical(params), now.isoformat()))

    def plan(self, now: datetime) -> None:
        local = now.astimezone(SHANGHAI)
        if local.weekday() >= 5:
            return
        day = local.strftime('%Y%m%d')
        # Calendar and L/D/P universe are prerequisites, not inferred weekdays.
        for endpoint, params, suffix in [('trade_cal', {'exchange':'SSE','start_date':(local-timedelta(days=60)).strftime('%Y%m%d'),'end_date':day}, 'calendar'),
                *[('stock_basic', {'exchange':'','list_status':s}, s) for s in ('L','D','P')]]:
            self.enqueue(f'{day}:{suffix}', endpoint, params, local)
        for window, endpoints in SCHEDULE.items():
            if local.strftime('%H:%M') < window:
                continue
            for endpoint in endpoints:
                target = day
                if endpoint == 'hk_hold':
                    from .extension_analytics import latest_rows
                    sessions = sorted(str(r['cal_date']) for r in latest_rows(self.store,'trade_cal') if r.get('is_open') == 1 and str(r['cal_date']) < day)
                    if not sessions:
                        continue
                    target = sessions[-1]
                params = {'trade_date':target} if endpoint not in {'fund_nav','new_share','stk_holdertrade','pledge_stat','pledge_detail'} else {'start_date':target,'end_date':target}
                if endpoint in {'index_daily','index_dailybasic'}:
                    for code in ('000001.SH','000300.SH','000905.SH','399006.SZ','000688.SH'):
                        self.enqueue(f'{day}:{window}:{endpoint}:{code}', endpoint, {**params,'ts_code':code}, local)
                    continue
                self.enqueue(f'{day}:{window}:{endpoint}', endpoint, params, local)
        # No automatic historical backfill. Financial single-code jobs bounded
        # by the observed universe and explicitly monitored in the durable queue.
        financial_windows = ['19:10', '21:10']
        if local.month in (1, 3, 4, 7, 8, 10):
            financial_windows = ['09:10', '13:10', '19:10', '21:10']
        for financial_window in financial_windows:
            if local.strftime('%H:%M') < financial_window:
                continue
            # disclosure_date.end_date is a REPORT PERIOD, not the query day.
            # Its supported publication/effective date filters must be separate.
            for field in ('actual_date','pre_date','ann_date'):
                self.enqueue(f'{day}:financial:{financial_window}:disclosure_date:{field}',
                             'disclosure_date', {field:day}, local)
            for field in ('ann_date','imp_ann_date','record_date','ex_date'):
                self.enqueue(f'{day}:financial:{financial_window}:dividend:{field}',
                             'dividend', {field:day}, local)
            for endpoint in ('forecast','express','repurchase'):
                self.enqueue(f'{day}:financial:{financial_window}:{endpoint}', endpoint, {'start_date':day,'end_date':day}, local)
            from .extension_analytics import latest_rows
            recent = (local-timedelta(days=7)).strftime('%Y%m%d')
            reports = sorted({(str(r['ts_code']),str(r['end_date'])) for r in latest_rows(self.store,'disclosure_date')
                              if r.get('ts_code') and len(str(r.get('end_date') or ''))==8
                              and str(r['end_date']).isdigit() and str(r['end_date'])<=day
                              and any(recent<=str(r.get(field) or '')<=day for field in ('actual_date','pre_date'))})
            for period in sorted({period for _,period in reports}):
                for endpoint in ('income','balancesheet','cashflow','fina_indicator'):
                    self.enqueue(f'{day}:financial:{financial_window}:{endpoint}:vip-bulk:{period}', endpoint, {'period':period}, local)
            for symbol,period in reports:
                for endpoint in ('fina_audit','stk_holdernumber'):
                    params={'ts_code':symbol}
                    self.enqueue(f'{day}:financial:{financial_window}:{endpoint}:{symbol}:{period}', endpoint, params, local)
        if local.strftime('%H:%M') >= '08:30':
            from .extension_analytics import latest_rows
            sessions = sorted(str(r['cal_date']) for r in latest_rows(self.store,'trade_cal') if r.get('is_open') == 1 and str(r['cal_date']) < day)
            if sessions:
                for endpoint in ('daily','daily_basic','adj_factor','stk_limit','moneyflow','top_list','top_inst','block_trade'):
                    self.enqueue(f'{day}:reconcile:{endpoint}', endpoint, {'trade_date':sessions[-1]}, local)
        # Monthly identities survive a weekend/holiday on the first day.
        month_start = local.strftime('%Y%m01')
        month = local.strftime('%Y%m')
        self.enqueue(f'{month}:monthly:share_float', 'share_float', {'start_date':month_start,'end_date':month_start}, local)
        for code in ('000300.SH','000905.SH','000852.SH'):
            self.enqueue(f'{month}:monthly:index_weight:{code}', 'index_weight',
                         {'index_code':code,'start_date':month_start,'end_date':day}, local)
        # Disclosure lag is not the fiscal period. Reconcile the prior quarter
        # daily from day 15, retaining each revision rather than assuming one pull is final.
        if local.month in (1,4,7,10) and local.day >= 15 and local.strftime('%H:%M') >= '19:10':
            quarter_end = date(local.year, ((local.month-1)//3)*3+1, 1)-timedelta(days=1)
            from .extension_analytics import latest_rows
            funds = sorted({str(r['ts_code']) for r in latest_rows(self.store,'fund_portfolio') if r.get('ts_code')})
            for fund in funds:
                self.enqueue(f'{day}:quarterly:fund_portfolio:{fund}', 'fund_portfolio',
                             {'ts_code':fund,'period':quarter_end.strftime('%Y%m%d')}, local)

    def run_once(self, now: datetime) -> None:
        if now.astimezone(SHANGHAI).weekday() >= 5:
            return
        self.plan(now)
        from .extension_analytics import latest_rows
        local = now.astimezone(SHANGHAI)
        day = local.strftime('%Y%m%d')
        with closing(self.store.connect()) as db:
            jobs = db.execute("SELECT identity,endpoint,params,report FROM ts_schedule WHERE status='pending' ORDER BY CASE WHEN endpoint='trade_cal' THEN 0 WHEN endpoint='stock_basic' THEN 1 WHEN endpoint IN ('daily','daily_basic','stk_limit','adj_factor') THEN 2 WHEN endpoint='disclosure_date' THEN 3 ELSE 4 END,identity LIMIT 4").fetchall()
        token = load_token()
        if not token:
            return
        for job in jobs:
            if self.stop_event.is_set():
                return
            identity, endpoint, serialized, previous_report = job
            calendar_dependent = endpoint in {'daily','daily_basic','adj_factor','stk_limit','suspend_d','hk_hold',
                'margin','margin_detail','index_daily','index_dailybasic','moneyflow',
                'top_list','top_inst','block_trade','opt_daily','fund_daily','fund_adj'}
            params = json.loads(serialized)
            target_day = str(params.get('trade_date', day))
            target_calendar = [r for r in latest_rows(self.store, 'trade_cal') if r.get('cal_date') == target_day and r.get('exchange') == 'SSE']
            if calendar_dependent and (not target_calendar or target_calendar[-1].get('is_open') != 1):
                if target_calendar and target_calendar[-1].get('is_open') == 0:
                    with closing(self.store.connect()) as db, db:
                        db.execute("UPDATE ts_schedule SET status='market_closed',updated_at=? WHERE identity=?", (local.isoformat(),identity))
                continue
            fields='ts_code,symbol,name,industry,list_status,list_date,delist_date' if endpoint=='stock_basic' else ''
            if endpoint=='fina_indicator':
                fields=FINANCIAL_INDICATOR_FIELDS
            result = sync(self.store, endpoint, params, token, max_pages=20, refresh=not previous_report and (':reconcile:' in identity or ':financial:' in identity or ':quarterly:' in identity or endpoint=='stock_basic'),
                          fetch=lambda access,name,params,fields=fields:query(access,name,params,fields=fields))
            status = str(result['status'])
            if status in {'partial','busy'}:
                if status == 'partial':
                    with closing(self.store.connect()) as db, db:
                        db.execute('UPDATE ts_schedule SET report=?,updated_at=? WHERE identity=?', (canonical(result),local.isoformat(),identity))
                continue
            rows = int(result.get('rows',0))
            if status == 'complete' and endpoint in {'daily','daily_basic','adj_factor','stk_limit'} and rows == 0:
                result['coverage_status'] = 'empty_daily_response'
                status = 'coverage_failed'
            if status == 'complete' and endpoint in {'daily','daily_basic','adj_factor','stk_limit'}:
                from .extension_coverage import reconcile_daily
                result['symbol_reconciliation'] = reconcile_daily(self.store, target_day)
                if not result['symbol_reconciliation']['complete']:
                    self.notify(identity, f'{endpoint}: symbol coverage not proven; inspect missing symbols and suspension evidence. No orders were generated.')
            with closing(self.store.connect()) as db, db:
                db.execute('UPDATE ts_schedule SET status=?,report=?,updated_at=? WHERE identity=?', (status, canonical(result), local.isoformat(), identity))
            if status != 'complete':
                self.notify(identity, f'{endpoint}: {status}; inspect the shared data coverage report. No orders were generated.')

    def start(self) -> None:
        if os.getenv('A_PI_DATA_AUTO','1').lower() in {'0','false','no'}:
            return
        def loop() -> None:
            while not self.stop_event.is_set():
                try:
                    self.run_once(datetime.now(SHANGHAI))
                except Exception as error:
                    logging.getLogger(__name__).exception("quant.data.extension_scheduler failed: %s", type(error).__name__, exc_info=None)
                self.stop_event.wait(30)
        self.thread = threading.Thread(target=loop, name='a-pi-shared-data', daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
