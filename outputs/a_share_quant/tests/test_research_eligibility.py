from datetime import date, timedelta

import pytest

from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync
from quant.data.research_eligibility import apply_research_eligibility
from quant.data.schema import Bar


def test_policy_filters_120_completed_sessions_and_records_namespace(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    start=date(2024,1,1)
    sessions=[start+timedelta(days=i) for i in range(180) if (start+timedelta(days=i)).weekday()<5]
    sync(store,'trade_cal',{},'',fetch=lambda *a:[{'exchange':'SSE','cal_date':d.strftime('%Y%m%d'),'is_open':1} for d in sessions])
    sync(store,'stock_basic',{},'',fetch=lambda *a:[{'ts_code':'a','list_date':'20240101'}, {'ts_code':'b','list_date':sessions[10].strftime('%Y%m%d')}])
    bars=[Bar(symbol,sessions[120],10,10,10,10,1000) for symbol in ('a','b')]
    output,meta=apply_research_eligibility(bars,{'data_quality_policy':True},store=store)
    assert [b.symbol for b in output]==['a']
    assert meta['excluded_bar_reasons']['new_listing_exclusion']==1
    assert meta['version']
    assert apply_research_eligibility(bars,{},store=store)[0]==bars
    with pytest.raises(ValueError,match='boolean'):
        apply_research_eligibility(bars,{'data_quality_policy':'false'},store=store)
    with pytest.raises(ValueError,match='disclosure proof'):
        apply_research_eligibility(bars,{'data_quality_policy':True,'exclude_nonstandard_audit':True},store=store)
