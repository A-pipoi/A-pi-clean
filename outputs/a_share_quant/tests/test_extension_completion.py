import json
from contextlib import closing
from datetime import date, datetime, timedelta

import pytest

from quant.data.extension_backfill import estimate
from quant.data.extension_coverage import listing_eligibility, reconcile_daily
from quant.data.extension_execution_audit import audit
from quant.data.extension_scheduler import SHANGHAI, ExtensionScheduler
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync
from quant.data.extension_technical import compare, indicator_series


def save(store, endpoint, rows):
    sync(store, endpoint, {}, '', fetch=lambda *args: rows)


def test_coverage_matches_symbols_not_counts(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    save(store, 'trade_cal', [{'exchange':'SSE','cal_date':'20261008','is_open':1}])
    save(store, 'stock_basic', [{'ts_code':'a','list_date':'20200101'}, {'ts_code':'b','list_date':'20200101'}])
    for endpoint in ('daily','adj_factor','stk_limit'):
        save(store, endpoint, [{'ts_code':s,'trade_date':'20261008'} for s in ('a','b')])
    save(store, 'daily_basic', [{'ts_code':s,'trade_date':'20261008'} for s in ('a','c')])
    report = reconcile_daily(store, '20261008')
    assert report['missing_for_traded_symbols']['daily_basic'] == ['b']
    assert not report['complete']


def test_coverage_resolves_proven_identity_without_rewriting_quotes(tmp_path):
    from quant.data.extension_analytics import latest_rows
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    save(store,'trade_cal',[{'exchange':'SSE','cal_date':'20241008','is_open':1}])
    save(store,'stock_basic',[{'ts_code':'302132.SZ','list_date':'20100827'},
                             {'ts_code':'920174.BJ','list_date':'20210820'}])
    save(store,'bse_mapping',[{'o_code':'835174.BJ','n_code':'920174.BJ','list_date':'20210820'}])
    for endpoint in ('daily','daily_basic','adj_factor','stk_limit'):
        save(store,endpoint,[{'ts_code':s,'trade_date':'20241008'} for s in ('300114.SZ','835174.BJ')])
    result=reconcile_daily(store,'20241008')
    assert result['complete'] and result['identity_comparison_only']
    assert not result['historical_pit_created']
    assert {r['ts_code'] for r in latest_rows(store,'daily')}=={'300114.SZ','835174.BJ'}


def test_missing_bars_not_fabricated_as_suspensions(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    save(store, 'trade_cal', [{'exchange':'SSE','cal_date':'20261008','is_open':1}])
    save(store, 'stock_basic', [{'ts_code':'a','list_date':'20200101'}, {'ts_code':'b','list_date':'20200101'},
                               {'ts_code':'dead','list_date':'20200101','delist_date':'20241001'}])
    for endpoint in ('daily','daily_basic','adj_factor','stk_limit'):
        save(store, endpoint, [{'ts_code':'a','trade_date':'20261008'}])
    report = reconcile_daily(store, '20261008')
    assert report['observed_quotes_aligned']
    assert not report['complete']
    assert report['unexplained_no_daily_bar'] == ['b']


def test_ipo_filter_uses_completed_sessions_not_calendar_days():
    start = date(2025,1,1)
    sessions = [(start+timedelta(days=i)).strftime('%Y%m%d') for i in range(200)
                if (start+timedelta(days=i)).weekday()<5]
    listings = [{'ts_code':'a','list_date':sessions[0]}]
    assert not listing_eligibility('a', sessions[119], listings, sessions)['eligible']
    assert listing_eligibility('a', sessions[120], listings, sessions)['eligible']
    assert listing_eligibility('a', sessions[120], listings, sessions[1:])['reason'] == 'insufficient_exchange_calendar'


def test_quarter_and_month_jobs_and_multiple_financial_windows(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    save(store, 'stock_basic', [{'ts_code':'a','list_status':'L'}])
    save(store, 'disclosure_date', [{'ts_code':'a','ann_date':'20261001','end_date':'20260930','pre_date':'20261016','actual_date':'20261016'}])
    save(store, 'fund_portfolio', [{'ts_code':'fund','ann_date':'20260420','end_date':'20260331','symbol':'a'}])
    scheduler = ExtensionScheduler(store, lambda *a:None)
    scheduler.plan(datetime(2026,10,16,20,tzinfo=SHANGHAI))
    scheduler.plan(datetime(2026,10,19,20,tzinfo=SHANGHAI))
    with closing(store.connect()) as db:
        jobs = [dict(r) for r in db.execute('SELECT * FROM ts_schedule')]
    monthly = [r for r in jobs if ':monthly:index_weight:' in r['identity']]
    assert len(monthly)==3
    quarterly = [r for r in jobs if ':quarterly:' in r['identity']]
    assert len(quarterly)==2
    assert all(json.loads(r['params'])['period']=='20260930' for r in quarterly)
    assert len([r for r in jobs if ':financial:' in r['identity'] and r['endpoint']=='fina_indicator'])==6
    assert all(json.loads(r['params'])=={'period':'20260930'}
               for r in jobs if r['endpoint']=='fina_indicator')
    assert all(set(json.loads(r['params'])) <= {'actual_date','pre_date','ann_date'}
               for r in jobs if r['endpoint']=='disclosure_date')


def test_backfill_requires_full_calendar_and_estimates_terminal_page(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    save(store, 'stock_basic', [{'ts_code':'a','list_date':'20200101'}])
    with pytest.raises(ValueError, match='calendar'):
        estimate(store, '20261008','20261009')
    save(store, 'trade_cal', [{'exchange':'SSE','cal_date':'20261008','is_open':1},
                            {'exchange':'SSE','cal_date':'20261009','is_open':0}])
    plan = estimate(store, '20261008','20261009')
    assert plan['sessions']==['20261008']
    assert len(plan['jobs'])==4
    assert plan['estimated_requests']==4
    assert plan['plan_hash']==estimate(store, '20261008','20261009')['plan_hash']
    assert not plan['historical_pit_created']


def test_technical_comparison_rejects_missing_reference_and_outliers():
    prices = [10+i/100 for i in range(260)]
    calculated = indicator_series(prices)
    rows = [{'ts_code':'a','trade_date':str(20200000+i),'close_qfq':price,**indicator}
            for i, (price,indicator) in enumerate(zip(prices,calculated))]
    assert compare(rows,evaluation_start='20200250')['status']=='passed'
    rows[-1]['macd']+=1
    report = compare(rows,evaluation_start='20200250')
    assert report['status']=='not_passed'
    assert report['outlier_row_ratio']==0.1
    rows[-1].pop('rsi_6')
    assert compare(rows,evaluation_start='20200250')['missing']
    assert compare([],evaluation_start='20240101')['status']=='not_passed'


def test_one_price_execution_probe_never_submits_account_orders(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    assert audit(store)['status']=='not_run'
    save(store,'daily',[{'ts_code':'000001.SZ','trade_date':'20260928','open':10,'high':10,'low':10,'close':10,'vol':1000,'amount':1000}])
    save(store,'stk_limit',[{'ts_code':'000001.SZ','trade_date':'20260928','up_limit':10,'down_limit':8}])
    result=audit(store)
    assert result['status']=='passed'
    assert not result['production_settings_changed']
    assert not result['strategy_comparison_completed']
