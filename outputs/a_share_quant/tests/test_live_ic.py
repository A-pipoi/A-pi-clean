from datetime import UTC, date, datetime, time, timedelta

import pytest

from quant.data.observed_industry import ObservedIndustryStore
from quant.factors.live_ic import SHANGHAI, LiveICStore


def moment(day):return datetime.combine(day,time.min,SHANGHAI)+timedelta(hours=17)
def values(n=30):return {f'S{i}':float(i) for i in range(n)}
def prices(n=30):return {f'S{i}':100.0 for i in range(n)}

def test_daily_schedule_is_shanghai_weekday_idempotent_and_bounded(tmp_path):
    box=LiveICStore(tmp_path/'ic.sqlite3')
    now=datetime(2026,10,8,9,0,tzinfo=UTC)
    assert box.claim(now)
    assert not box.claim(now+timedelta(seconds=30))
    assert not box.claim(now-timedelta(hours=1))
    for index in range(1,4):assert box.claim(now+timedelta(minutes=15*index))
    assert not box.claim(now+timedelta(hours=1))
    assert not box.claim(datetime(2026,10,10,17,0,tzinfo=SHANGHAI))
    # Deliberately naive: the scheduler must reject timestamps without a timezone.
    with pytest.raises(ValueError):box.claim(datetime(2026,10,8,17))


def test_cache_warmup_can_continue_through_remaining_observation_slots(tmp_path):
    box=LiveICStore(tmp_path/'ic.sqlite3')
    start=datetime(2026,10,8,17,0,tzinfo=SHANGHAI)
    for index in range(12):
        now=start+timedelta(minutes=15*index)
        assert box.claim(now)
        box.finish(now,'warming_cache','warming_market_cache')
    assert not box.claim(start+timedelta(hours=3))
    assert box.report()['attempts'][0]['attempts']==12

def test_future_labels_never_used_and_frozen_signals_never_rewritten(tmp_path):
    box=LiveICStore(tmp_path/'ic.sqlite3')
    day=date(2026,10,8); now=moment(day)
    assert box.freeze(day,'v1','factor',values(),prices(),30,'hash',now=now)
    assert not box.freeze(day,'v1','factor',values(10),prices(),30,'revision',now=now)
    with pytest.raises(ValueError):box.freeze(day-timedelta(days=1),'v1','f',values(),prices(),30,'h',now=now)
    future=day+timedelta(days=1)
    with pytest.raises(ValueError):box.settle([day,future],{future:prices()},now=now)
    box.settle([day,future],{},now=now)
    assert all(not item['observations'] for item in box.report()['monitors'])
    returns={f'S{i}':100+i for i in range(30)}
    box.settle([day,future],{future:returns},now=moment(future))
    one=box.report()['monitors'][0]
    assert one['ic_series'][0]['value']==pytest.approx(1)
    assert one['first_seen']==now.isoformat() and one['source_hash']=='hash'
    box.settle([day,future],{future:{f'S{i}':130-i for i in range(30)}},now=moment(future))
    assert box.report()['monitors'][0]['ic_series']==one['ic_series']

def test_thin_labels_are_explicit_not_zero_or_survivor_only_ic(tmp_path):
    box=LiveICStore(tmp_path/'ic.sqlite3');day=date(2026,10,8);future=date(2026,10,9)
    box.freeze(day,'v','f',values(),prices(),30,'h',now=moment(day))
    box.settle([day,future],{future:prices(10)},now=moment(future))
    one=box.report()['monitors'][0]
    assert one['ic_series']==[] and one['observations'][0]['status']=='insufficient_coverage'
    box.settle([day,future],{future:{f'S{i}':100+i for i in range(30)}},now=moment(future))
    assert len(box.report()['monitors'][0]['ic_series'])==1
    assert not LiveICStore(tmp_path/'other'/'ic.sqlite3').report()['monitors']

def test_horizons_use_exchange_sessions_not_calendar_days(tmp_path):
    day=date(2026,10,8);box=LiveICStore(tmp_path/'ic.sqlite3')
    sessions=[day+timedelta(days=i) for i in range(40) if (day+timedelta(days=i)).weekday()<5]
    box.freeze(day,'v','f',values(),prices(),30,'h',now=moment(day))
    end=sessions[5];box.settle(sessions[:6],{session:{f'S{i}':100+i for i in range(30)} for session in sessions[:6]},now=moment(end))
    five=box.report()['monitors'][1]
    assert five['observations'][0]['maturity']==end.isoformat()
    assert box.report()['monitors'][2]['observations']==[]

def test_industry_observations_cannot_backfill_before_first_seen(tmp_path):
    box=ObservedIndustryStore(tmp_path/'industry.sqlite3');now=moment(date(2026,10,8))
    box.record({'S':'银行'},now,'source')
    assert box.at(now-timedelta(seconds=1),['S'])['coverage']==0
    assert box.at(now,['S'])['status']=='verified'
    box.record({'S':'房地产'},now+timedelta(minutes=1),'revision')
    assert box.at(now+timedelta(minutes=2),['S'])['rows']['S']['industry']=='银行'
    later=now+timedelta(days=1);box.record({'S':'房地产'},later,'new')
    assert box.at(later,['S'])['rows']['S']['industry']=='房地产'


def test_repeated_industry_snapshots_do_not_inflate_coverage(tmp_path):
    from quant.data.pit import materialize_pit_industry_observations
    box=ObservedIndustryStore(tmp_path/'industry.sqlite3');now=moment(date(2026,10,8))
    box.record({'S':'银行'},now,'a')
    box.record({'S':'房地产'},now+timedelta(days=1),'b')
    records=box.records(['S'],date(2026,10,12))
    rows=materialize_pit_industry_observations(records,[date(2026,10,8),date(2026,10,9),date(2026,10,12)])
    assert len(rows)==2
    assert [row.industry for row in rows]==['银行','房地产']
    assert rows[-1].available_at==now+timedelta(days=1)


def test_today_labels_are_not_available_before_close(tmp_path):
    box=LiveICStore(tmp_path/'ic.sqlite3');day=date(2026,10,8);future=date(2026,10,9)
    box.freeze(day,'v','f',values(),prices(),30,'h',now=moment(day))
    with pytest.raises(ValueError):
        box.settle([day,future],{future:prices()},now=moment(future)-timedelta(hours=4))


def test_industry_saved_before_price_warmup_and_after_actual_response(tmp_path,monkeypatch):
    from quant.data.live_monitor import LiveMonitorProvider
    now=moment(date(2026,10,8));observed=now+timedelta(minutes=3)
    provider=LiveMonitorProvider(tmp_path,'test-only',clock=lambda:observed)
    sessions=[now.date()-timedelta(days=i) for i in range(98) if (now.date()-timedelta(days=i)).weekday()<5]
    symbols=[f'{i:06d}.SZ' for i in range(300)]
    def query(name,params,fields):
        if name=='trade_cal':return [{'cal_date':d.strftime('%Y%m%d'),'is_open':1} for d in sessions]
        if name=='index_weight':return [{'con_code':s,'trade_date':'20260930','weight':100/300} for s in symbols]
        if name=='stock_basic':return [{'ts_code':s,'industry':'银行'} for s in symbols]
        if name=='daily':return [{'ts_code':f'{i:06d}.SZ','trade_date':params['trade_date'],'open':100,'high':101,'low':99,'close':100,'vol':1000} for i in range(3000)]
        if name=='adj_factor':return [{'ts_code':f'{i:06d}.SZ','trade_date':params['trade_date'],'adj_factor':1} for i in range(3000)]
        raise AssertionError(name)
    monkeypatch.setattr(provider,'query',query)
    with pytest.raises(RuntimeError,match='warming_market_cache'):provider.fetch(now.date(),now)
    store=ObservedIndustryStore(tmp_path/'industry.sqlite3')
    assert store.at(now,symbols)['coverage']==0
    report=store.at(observed,symbols)
    assert report['coverage']==1 and report['last_capture']['status']=='complete'
    assert report['benchmark']['available_at']==observed.isoformat()
    import json
    raw_files = list((tmp_path/'industry_evidence').glob('*.json'))
    assert len(raw_files) == 1
    evidence = json.loads(raw_files[0].read_text(encoding='utf-8'))
    assert evidence['observed_at'] == observed.isoformat()
    assert evidence['historical_backfill'] is False
    assert len(json.loads(evidence['raw_response_json'])) == 300
    # Partial real cache survives a retry; no historical industry is invented.
    with pytest.raises(RuntimeError,match='warming_market_cache'):provider.fetch(now.date(),now)
    market=provider.fetch(now.date(),now)
    assert market is not None and len(market.symbols)==300
    assert len(market.bars)==300*65
    assert len(store.records(symbols,now.date()))==300
