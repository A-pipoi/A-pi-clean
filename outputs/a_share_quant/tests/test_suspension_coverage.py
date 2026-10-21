from quant.data.extension_coverage import reconcile_daily
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync


def test_only_proven_full_day_suspensions_close_missing_bar_gap(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    def add(name,rows):
        sync(store,name,{},'',fetch=lambda *args:rows)
    add('stock_basic',[{'ts_code':s,'list_date':'20000101'} for s in ('a','b','c','d')])
    add('trade_cal',[{'exchange':'SSE','cal_date':'20261008','is_open':1}])
    for name in ('daily','daily_basic','adj_factor','stk_limit'):
        add(name,[{'ts_code':'a','trade_date':'20261008'}])
    add('suspend_d',[{'ts_code':'b','trade_date':'20261008','suspend_type':'S','suspend_timing':None},
                     {'ts_code':'c','trade_date':'20261008','suspend_type':'S','suspend_timing':'09:30-10:00'},
                     {'ts_code':'d','trade_date':'20261008','suspend_type':'R','suspend_timing':None}])
    report=reconcile_daily(store,'20261008')
    assert report['proven_full_day_suspensions']==['b']
    assert report['unexplained_no_daily_bar']==['c','d']
    assert not report['complete']


def test_missing_tables_need_initialization_not_fake_proof(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    sync(store,'trade_cal',{},'',fetch=lambda *args:[{'exchange':'SSE','cal_date':'20261008','is_open':0}])
    assert reconcile_daily(store,'20261008')['status']=='market_closed'
