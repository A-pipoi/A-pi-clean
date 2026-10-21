from datetime import UTC, datetime

import pytest

from quant.data.extension_derived import derived_values
from quant.data.extension_features import values_as_of
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync


def test_changes_main_flow_and_volume_weighted_same_day_discount(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    def add(endpoint, rows):
        sync(store, endpoint, {}, '', fetch=lambda *args: rows)
    add('margin_detail', [{'ts_code':'a','trade_date':'20240101','rzye':100}, {'ts_code':'a','trade_date':'20240102','rzye':120}])
    add('hk_hold', [{'ts_code':'a','trade_date':'20240101','ratio':1.5}, {'ts_code':'a','trade_date':'20240102','ratio':1.8}])
    add('stk_holdernumber', [{'ts_code':'a','ann_date':'20240101','end_date':'20231201','holder_num':100}, {'ts_code':'a','ann_date':'20240102','end_date':'20231231','holder_num':90}])
    add('moneyflow', [{'ts_code':'a','trade_date':'20240102','buy_lg_amount':10,'buy_elg_amount':20,'sell_lg_amount':3,'sell_elg_amount':2,'net_mf_amount':999}])
    add('daily', [{'ts_code':'a','trade_date':'20240102','close':10}])
    add('block_trade', [{'ts_code':'a','trade_date':'20240102','price':8,'vol':1,'buyer':'x','seller':'y'}, {'ts_code':'a','trade_date':'20240102','price':10,'vol':3,'buyer':'z','seller':'y'}])
    now = datetime(2099,1,1,tzinfo=UTC)
    values, proof = derived_values(store,now)
    assert values['margin_balance_change']['a'] == .2
    assert values['northbound_ratio_change']['a'] == pytest.approx(.3)
    assert values['holder_count_change']['a'] == .1
    assert values['main_moneyflow_net']['a'] == 25
    assert values['main_moneyflow_intensity']['a'] == pytest.approx(25/35)
    assert values['block_discount']['a'] == pytest.approx(.05)
    assert len(proof['evidence']['block_discount']['a']['source_hashes']) == 3
    bound, metadata = values_as_of(store,now)
    assert bound['block_discount'] == values['block_discount']
    assert metadata['derived_features']['missing_values_imputed'] is False
    past, _ = derived_values(store,datetime(2024,2,1,tzinfo=UTC))
    assert not any(past.values())


def test_missing_zero_baseline_and_wrong_date_close_never_imputed(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    sync(store,'margin_detail',{},'',fetch=lambda *args:[{'ts_code':'a','trade_date':'20240101','rzye':0},{'ts_code':'a','trade_date':'20240102','rzye':20}])
    sync(store,'daily',{},'',fetch=lambda *args:[{'ts_code':'a','trade_date':'20240101','close':10}])
    sync(store,'block_trade',{},'',fetch=lambda *args:[{'ts_code':'a','trade_date':'20240102','price':8,'vol':1}])
    sync(store,'moneyflow',{},'',fetch=lambda *args:[{'ts_code':'a','trade_date':'20240102','net_mf_amount':100}])
    result,_=derived_values(store,datetime(2099,1,1,tzinfo=UTC))
    assert not any(result.values())


def test_fund_consensus_same_period_only_and_positive_holdings(tmp_path):
    store = ExtensionStore(tmp_path/'raw.sqlite3'); store.initialize()
    sync(store,'fund_portfolio',{},'',fetch=lambda *args:[
        {'ts_code':'f1','symbol':'a','end_date':'20240630','ann_date':'20240720','amount':10},
        {'ts_code':'f2','symbol':'a','end_date':'20240630','ann_date':'20240720','amount':20},
        {'ts_code':'f3','symbol':'a','end_date':'20240331','ann_date':'20240420','amount':30},
        {'ts_code':'f1','symbol':'b','end_date':'20240630','ann_date':'20240720','amount':0},
    ])
    result,proof=derived_values(store,datetime(2099,1,1,tzinfo=UTC))
    assert result['fund_consensus_count'] == {'a':2}
    assert proof['evidence']['fund_consensus_count']['a']['source_dates'] == ['20240630','20240630']
