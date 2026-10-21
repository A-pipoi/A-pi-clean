from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync
from quant.data.historical_experiments import (
    disclosure_ic,
    etf_allocation_diagnostic,
    fund_layers,
    rank_ic,
)


def ingest(store, endpoint, rows):
    sync(store,endpoint,{},'',fetch=lambda *args:rows)


def test_rank_ic_uses_average_ties_and_requires_sample():
    assert rank_ic([1]*20,[2]*20) is None
    assert rank_ic(list(range(20)),list(reversed(range(20)))) == -1
    assert rank_ic(list(range(19)),list(range(19))) is None
    assert rank_ic([float('nan')]*20,list(range(20))) is None


def test_actual_disclosure_can_delay_financial_announcement(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    symbols=[f'{i:06d}.SZ' for i in range(20)]
    ingest(store,'fina_indicator',[{'ts_code':s,'end_date':'20240331','ann_date':'20240401','netprofit_yoy':i} for i,s in enumerate(symbols)])
    ingest(store,'disclosure_date',[{'ts_code':s,'end_date':'20240331','ann_date':'20240301','actual_date':'20240501'} for s in symbols])
    ingest(store,'daily',[{'ts_code':s,'trade_date':d,'close':100+i*(d=='20240403')} for i,s in enumerate(symbols) for d in ('20240402','20240403')])
    ingest(store,'adj_factor',[{'ts_code':s,'trade_date':d,'adj_factor':1} for s in symbols for d in ('20240402','20240403')])
    result=disclosure_ic(store,[('20240402','20240403')])['series'][0]
    assert result['common_symbols']==0 and result['ann_date_ic'] is None
    assert result['end_date_only_unannounced_symbols']==20


def test_alignment_curves_share_cohort_and_mark_lookahead(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    symbols=[f'{i:06d}.SZ' for i in range(25)]
    ingest(store,'fina_indicator',[{'ts_code':s,'end_date':'20240331','ann_date':'20240401' if i<20 else '20240501','netprofit_yoy':i} for i,s in enumerate(symbols)])
    ingest(store,'daily',[{'ts_code':s,'trade_date':d,'close':100+i*(d=='20240403')} for i,s in enumerate(symbols) for d in ('20240402','20240403')])
    ingest(store,'adj_factor',[{'ts_code':s,'trade_date':d,'adj_factor':1} for s in symbols for d in ('20240402','20240403')])
    result=disclosure_ic(store,[('20240402','20240403')])
    point=result['series'][0]
    assert point['common_symbols']==20 and point['end_date_only_unannounced_symbols']==5
    assert point['ann_date_ic']==point['end_date_ic']==1
    assert not result['sealed_approval_usable'] and not result['production_feature_registered']


def test_fund_deduplicates_codes_excludes_foreign_and_never_fills_missing_price(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    ingest(store,'fund_portfolio',[{'ts_code':'f','end_date':'20190331','ann_date':a,'symbol':s,'amount':100} for a,s in [('20190420','000001.SZ'),('20190421','000001.SZ'),('20190420','000002.SZ'),('20190420','00700.HK')]])
    ingest(store,'daily',[{'ts_code':'000001.SZ','trade_date':d,'close':p} for d,p in [('20190531',10),('20190830',11)]])
    ingest(store,'adj_factor',[{'ts_code':'000001.SZ','trade_date':d,'adj_factor':1} for d in ('20190531','20190830')])
    report=fund_layers(store,[('20190331','20190531','20190830')])
    period=report['periods'][0]
    assert period['observed_funds']==1 and period['excluded_non_a_share_rows']==1
    assert period['missing_price_symbols']==['000002.SZ'] and not report['all_periods_priced']
    assert sum(g['symbols'] for g in period['layers'])==1
    assert not report['costs_included'] and not report['sealed_approval_usable']


def test_etf_history_refuses_missing_adjustment_or_quote(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    ingest(store,'fund_daily',[{'ts_code':'A.SH','trade_date':'20200102','close':10},
                               {'ts_code':'B.SH','trade_date':'20200102','close':20}])
    ingest(store,'fund_adj',[{'ts_code':'A.SH','trade_date':'20200102','adj_factor':1}])
    report=etf_allocation_diagnostic(store,('A.SH','B.SH'),['20200102','20200103'])
    assert report['status']=='insufficient_real_history'
    assert not report['complete_price_coverage'] and report['curve']==[]
    assert {'date':'20200102','symbol':'B.SH','missing':'fund_adj'} in report['missing_observations']


def test_etf_history_uses_adjusted_close_and_monthly_turnover_cost(tmp_path):
    store=ExtensionStore(tmp_path/'raw.sqlite3');store.initialize()
    days=('20200130','20200131','20200203')
    ingest(store,'fund_daily',[{'ts_code':symbol,'trade_date':day,'close':price}
               for symbol,values in [('A.SH',(10,12,12)),('B.SH',(10,10,10))]
               for day,price in zip(days,values)])
    ingest(store,'fund_adj',[{'ts_code':symbol,'trade_date':day,'adj_factor':1}
               for symbol in ('A.SH','B.SH') for day in days])
    report=etf_allocation_diagnostic(store,('A.SH','B.SH'),list(days),cost_bps=5)
    assert report['status']=='computed_research_only'
    assert report['complete_price_coverage'] and len(report['curve'])==3
    assert report['rebalance_dates'][0]['date']=='20200203'
    assert 0.09 < report['modeled_total_return'] < 0.1
    assert not report['sealed_approval_usable'] and not report['execution_price_verified']
