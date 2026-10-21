from pathlib import Path

import pytest

from quant.data.extension_analytics import diagnostics, implied_volatility, price_option
from quant.data.extension_store import ExtensionStore


@pytest.mark.parametrize('call', [True, False])
def test_implied_volatility_roundtrip(call):
    price = price_option(3, 3.2, 0.25, 0.02, 0.3, call)
    assert implied_volatility(price, 3, 3.2, 0.25, 0.02, call) == pytest.approx(0.3)
    assert implied_volatility(100, 3, 3.2, 0.25, 0.02, call) is None
    assert implied_volatility(-1, 3, 3.2, 0.25, 0.02, call) is None


def test_disclosure_plan_is_not_financial_visibility(tmp_path: Path):
    store = ExtensionStore(tmp_path / 'shared.sqlite3')
    store.initialize()
    for endpoint, row in [('disclosure_date', {'ts_code': 'X', 'end_date': '20260630', 'ann_date': '20260601', 'pre_date': '20260701', 'actual_date': '20260801'}),
        ('income', {'ts_code': 'X', 'end_date': '20260630', 'ann_date': '20260731', 'f_ann_date': '20260802'})]:
        job, offset, _ = store.begin(endpoint, {})
        store.save_page(job, endpoint, offset, [row], complete=True)
    report = diagnostics(store)
    difference = report['financial_disclosure_reconciliation'][0]
    assert difference['status'] == 'mismatch'
    assert not difference['historical_feature_enabled']
    assert not report['historical_industry_pit_usable']
