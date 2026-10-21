import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from quant.backtest.engine import EventDrivenBacktester, Order, OrderRejectReason
from quant.data.extension_features import TEMPLATES, values_as_of
from quant.data.extension_scheduler import SHANGHAI, ExtensionScheduler
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync
from quant.data.schema import Bar
from quant.factors.expression import compile_expression


def test_revision_refresh_preserves_source_and_busy_lease(tmp_path: Path):
    store=ExtensionStore(tmp_path/'features.sqlite3'); store.initialize()
    params={'trade_date':'20260930'}
    job,_,_=store.begin('daily_basic',params)
    assert store.claim(job,'other')
    assert sync(store,'daily_basic',params,'',fetch=lambda *a:[])['status']=='busy'
    store.release(job,'other')
    sync(store,'daily_basic',params,'',fetch=lambda *a:[{'ts_code':'a','trade_date':'20260930','pe':1}])
    sync(store,'daily_basic',params,'',refresh=True,fetch=lambda *a:[{'ts_code':'a','trade_date':'20260930','pe':2}])
    assert store.report()['counts']['daily_basic']==2


def test_ingestion_does_not_backfill_historical_features(tmp_path: Path):
    store=ExtensionStore(tmp_path/'features.sqlite3');store.initialize()
    sync(store,'fina_indicator',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','roa':1}])
    sync(store,'disclosure_date',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20231201','end_date':'20231231','actual_date':'20240101'}])
    values,_=values_as_of(store,datetime(2024,2,1,tzinfo=UTC))
    assert values['quality_roa']=={}
    values,_=values_as_of(store,datetime(2099,2,1,tzinfo=UTC))
    assert values['quality_roa']=={'a':1}
    for template in TEMPLATES:
        compile_expression(template['expression'],registered_pit_fields=tuple(values))


def test_contributing_provenance_includes_actual_disclosure_source(tmp_path):
    store=ExtensionStore(tmp_path/'features.sqlite3');store.initialize()
    sync(store,'fina_indicator',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','roa':1}])
    sync(store,'disclosure_date',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20231201','end_date':'20231231','actual_date':'20240102'}])
    cutoff=datetime(2099,2,1,tzinfo=UTC)
    before,proof=values_as_of(store,cutoff)
    sync(store,'disclosure_date',{},'',refresh=True,fetch=lambda *a:[{'ts_code':'a','ann_date':'20231201','end_date':'20231231','actual_date':'20240103'}])
    after,new_proof=values_as_of(store,cutoff)
    assert before['quality_roa']==after['quality_roa']=={'a':1}
    assert proof['source_hash']!=new_proof['source_hash']
    assert proof['provenance_scope']=='latest_contributing_field_observations_v2'
    assert not new_proof['historical_backfill_enabled']


def test_schedule_closed_day_and_weekend_no_quote_requests(tmp_path: Path):
    store=ExtensionStore(tmp_path/'features.sqlite3');store.initialize()
    scheduler=ExtensionScheduler(store,lambda *a:None)
    sync(store,'trade_cal',{},'',fetch=lambda *a:[{'exchange':'SSE','cal_date':'20261008','is_open':0}])
    with patch('quant.data.extension_scheduler.load_token',return_value='test'),patch('quant.data.extension_scheduler.sync',return_value={'status':'complete','rows':1}) as fetch:
        scheduler.run_once(datetime(2026,10,8,20,tzinfo=SHANGHAI))
        assert all(call.args[1] in {'trade_cal','stock_basic'} for call in fetch.call_args_list)
    with sqlite3.connect(store.path) as db:
        count=db.execute('SELECT COUNT(*) FROM ts_schedule').fetchone()[0]
    scheduler.plan(datetime(2026,10,10,20,tzinfo=SHANGHAI))
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM ts_schedule').fetchone()[0]==count


def test_amount_constraint_uses_real_notional_not_share_volume():
    class Buy:
        def orders_for_date(self,*args):return [Order('a','BUY',100)]
    bar=Bar('a',date(2026,9,30),10,10,10,10,1000000,amount=1000)
    result=EventDrivenBacktester().run([bar],Buy())
    assert result.rejected[0].reason==OrderRejectReason.AMOUNT_LIMIT
    assert result.fills==[]


def test_real_process_kill_then_checkpoint_recovery(tmp_path: Path):
    import subprocess
    import sys
    import time
    path=tmp_path/'crash.sqlite3'
    worker=subprocess.Popen([sys.executable,str(Path(__file__).with_name('extension_crash_worker.py')),str(path)],stdout=subprocess.PIPE,text=True)
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline().strip()=='PAGE_COMMITTED'
        worker.kill();worker.wait(timeout=10)
    finally:
        if worker.poll() is None:worker.kill();worker.wait(timeout=10)
    store=ExtensionStore(path)
    offsets=[]
    def fetch(token,endpoint,params):offsets.append(params['offset']);return []
    # Advance only the lease clock, not provider data or availability timestamps.
    with patch('time.time',return_value=time.time()+181):
        assert sync(store,'daily_basic',{'trade_date':'20260930'},'',fetch=fetch)['status']=='complete'
    assert offsets==[1000]
    assert store.report()['counts']['daily_basic']==1000
