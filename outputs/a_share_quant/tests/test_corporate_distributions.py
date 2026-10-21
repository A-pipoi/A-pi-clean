from datetime import UTC, date

import pytest

from quant.backtest.costs import CostModel
from quant.data.schema import Bar
from quant.execution.store import PaperStore

ZERO = CostModel(commission_rate=0,min_commission=0,stamp_tax_rate=0,transfer_fee_rate=0,fixed_slippage_bps=0,impact_bps=0)


def quote(day):
    return Bar('000001.SZ',date(2024,1,day),10,10,10,10,100000)


def action(**changes):
    return {'ts_code':'000001.SZ','end_date':'20231231','ann_date':'20240101','imp_ann_date':'20240102',
            'div_proc':'实施','record_date':'20240103','ex_date':'20240104','pay_date':'20240105',
            'div_listdate':'20240105','cash_div':0.5,'cash_div_tax':0.6,'stk_div':0.2,**changes}


def account(path):
    store=PaperStore(path,initial_cash=10000)
    order=store.create_order('000001.SZ','BUY',100)
    store.execute_order(order['order_id'],quote(2),ZERO)
    return store


def test_record_date_quantity_survives_sell_and_restart(tmp_path):
    path=tmp_path/'paper.sqlite3'
    store=account(path)
    store.record_entitlements(date(2024,1,3))
    order=store.create_order('000001.SZ','SELL',100)
    store.execute_order(order['order_id'],quote(4),ZERO)
    before=store.state()['cash']
    store.close()
    store=PaperStore(path)
    result=store.settle_distributions(date(2024,1,5),[action()])
    assert result['events'][0]['quantity_at_record_close']==100
    assert store.state()['cash']==before+50
    assert store.state()['positions'][0]['quantity']==20
    store.settle_distributions(date(2024,1,5),[action()])
    assert store.state()['cash']==before+50
    assert store.state()['positions'][0]['quantity']==20
    store.close()


def test_current_holdings_cannot_fabricate_past_entitlement(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    before=store.state()['cash']
    result=store.settle_distributions(date(2024,1,5),[action()])
    assert result['events'][0]['status']=='blocked_missing_record_date_snapshot'
    assert store.state()['cash']==before
    store.close()


def test_only_pay_and_listing_dates_credit_and_source_revision_blocks(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    store.record_entitlements(date(2024,1,3))
    before=store.state()['cash']
    store.settle_distributions(date(2024,1,4),[action()])
    assert store.state()['cash']==before
    result=store.settle_distributions(date(2024,1,5),[action(cash_div=5)])
    assert result['events'][0]['status']=='blocked_source_revision_requires_review'
    assert store.state()['cash']==before
    store.close()


def test_bad_event_rolls_back_entire_posting_transaction(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    store.record_entitlements(date(2024,1,3))
    before=store.state()['cash']
    with pytest.raises(ValueError):
        store.settle_distributions(date(2024,1,5),[action(),action(ts_code='bad',stk_div=float('nan'))])
    assert store.state()['cash']==before
    assert store.state()['positions'][0]['quantity']==100
    store.close()


def test_unknown_net_tax_and_fractional_stock_do_not_get_guessed(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    store.record_entitlements(date(2024,1,3))
    before=store.state()['cash']
    result=store.settle_distributions(date(2024,1,5),[action(cash_div=None,stk_div=.005)])
    assert result['events'][0]['cash_status']=='blocked_missing_net_cash_dividend'
    assert result['events'][0]['share_status']=='blocked_fractional_share_allocation_unproven'
    assert store.state()['cash']==before
    store.close()


def test_reset_removes_distribution_idempotency_and_snapshots(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    store.record_entitlements(date(2024,1,3))
    store.settle_distributions(date(2024,1,5),[action()])
    store.reset()
    result=store.settle_distributions(date(2024,1,5),[action()])
    assert result['events'][0]['status']=='blocked_missing_record_date_snapshot'
    assert store.state()['cash']==10000
    store.close()


def test_invalid_provider_rows_are_quarantined_not_credited(tmp_path,monkeypatch):
    from datetime import datetime

    from quant.data.extension_store import ExtensionStore
    from quant.data.extension_sync import sync
    from quant.execution.corporate_actions import observed_events
    raw=ExtensionStore(tmp_path/'raw.sqlite3');raw.initialize()
    sync(raw,'dividend',{},'',fetch=lambda *a:[action(ann_date=None)])
    monkeypatch.setenv('A_PI_FEATURE_DB',str(raw.path))
    events=observed_events(datetime(2099,1,1,tzinfo=UTC))
    store=account(tmp_path/'paper.sqlite3')
    before=store.state()['cash']
    assert store.settle_distributions(date(2024,1,5),events)['events'][0]['status']=='blocked_invalid_provider_distribution'
    assert store.state()['cash']==before
    store.close()


def test_missing_payment_evidence_can_be_completed_without_changing_entitlement(tmp_path):
    store=account(tmp_path/'paper.sqlite3')
    store.record_entitlements(date(2024,1,3))
    before=store.state()['cash']
    store.settle_distributions(date(2024,1,4),[action(pay_date=None,div_listdate=None)])
    store.settle_distributions(date(2024,1,5),[action()])
    assert store.state()['cash']==before+50
    assert store.state()['positions'][0]['quantity']==120
    store.close()
