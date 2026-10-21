from datetime import UTC, datetime

from quant.data.extension_features import values_as_of
from quant.data.extension_financial import financial_status
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync
from quant.factors.expression import compile_expression


def test_audit_and_forecast_revision_fail_closed_before_observation(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    sync(store,'fina_audit',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','audit_result':'标准无保留意见'},
                                                {'ts_code':'b','ann_date':'20240101','end_date':'20231231','audit_result':'带强调事项的无保留意见'}])
    sync(store,'forecast',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','net_profit_min':100,'net_profit_max':120},
                                              {'ts_code':'a','ann_date':'20240120','end_date':'20231231','net_profit_min':120,'net_profit_max':144,'p_change_max':15}])
    past=financial_status(store,datetime(2024,2,1,tzinfo=UTC),['a','b','c'])
    assert not any(r['eligible'] for r in past['audit_eligibility'])
    assert not past['forecast_revisions']
    result=financial_status(store,datetime(2099,1,1,tzinfo=UTC),['a','b','c'])
    assert [r['eligible'] for r in result['audit_eligibility']]==[True,False,False]
    assert result['forecast_revisions'][0]['revision_surprise']==.2
    assert result['forecast_revisions'][0]['baseline_ann_date']=='20240101'
    values,_proof=values_as_of(store,datetime(2099,1,1,tzinfo=UTC))
    assert values['audit_standard']['a']==1
    assert values['forecast_revision_surprise']['a']==.2
    compile_expression('if_else(audit_standard > 0, forecast_revision_surprise, 0)',registered_pit_fields=tuple(values))


def test_same_announcement_revision_is_not_a_new_public_forecast(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    sync(store,'forecast',{},'',fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','net_profit_min':100,'net_profit_max':120}])
    sync(store,'forecast',{},'',refresh=True,fetch=lambda *a:[{'ts_code':'a','ann_date':'20240101','end_date':'20231231','net_profit_min':120,'net_profit_max':144}])
    assert not financial_status(store,datetime(2099,1,1,tzinfo=UTC),['a'])['forecast_revisions']
