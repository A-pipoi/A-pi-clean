from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from quant.data.extension_catalog import ENDPOINTS, SCHEDULE
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import sync
from quant.data.extension_transport import ProviderError, query
from quant.data.provider_rate import reserve


def test_schema_uses_existing_single_file(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'features.sqlite3')
    store.initialize()
    store.initialize()
    assert len(store.report()['counts']) == len(ENDPOINTS)
    assert 'cb_daily' not in ENDPOINTS
    assert all(name in ENDPOINTS for group in SCHEDULE.values() for name in group)


def test_quality_archive_is_readable_by_digest_without_budget_mutation(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'features.sqlite3')
    store.initialize()
    digest = store.archive_quality(2, {'kind':'technical_indicator_comparison','status':'passed','source':'real'})
    listed = store.quality_artifacts()
    assert listed[0]['digest'] == digest
    assert (listed[0]['phase'], listed[0]['kind'], listed[0]['status']) == (2,'technical_indicator_comparison','passed')
    saved = store.quality_artifact(digest)
    assert saved is not None and saved['report']['source'] == 'real'
    assert saved['research_budget_used'] is False
    assert store.quality_artifact(digest[:-1]) is None
    assert store.quality_artifact('0'*64) is None
    phase_three = store.archive_quality(3, {'kind':'fund_historical_layers','status':'partial'})
    for index in range(51):
        store.archive_quality(1, {'kind':'market_probe','index':index})
    assert phase_three in {item['digest'] for item in store.quality_artifacts()}
    from quant.api.research_routes import handle_research_get
    with patch('quant.data.extension_store.default_store_path', return_value=store.path):
        status, payload = handle_research_get('/api/research/data-quality', {'digest':[digest]}, None)  # type: ignore[arg-type]
        assert status == 200 and payload['report']['kind'] == 'technical_indicator_comparison'
        missing, _ = handle_research_get('/api/research/data-quality', {'digest':['invalid']}, None)  # type: ignore[arg-type]
        assert missing == 404


def test_restart_checkpoint_and_immutable_revisions(tmp_path: Path) -> None:
    path = tmp_path / 'shared.sqlite3'
    store = ExtensionStore(path)
    store.initialize()
    first = [{'ts_code': str(i), 'trade_date': '20260930', 'pe': i} for i in range(1000)]
    result = sync(store, 'daily_basic', {'trade_date': '20260930'}, 'secret', max_pages=1, fetch=lambda *args: first)
    assert result['status'] == 'partial'
    offsets = []
    def fetch(token, endpoint, params):
        offsets.append(params['offset'])
        return [{'ts_code': '1001', 'trade_date': '20260930', 'pe': 12}]
    restarted = ExtensionStore(path)
    result = sync(restarted, 'daily_basic', {'trade_date': '20260930'}, 'secret', fetch=fetch)
    assert offsets == [1000]
    assert result['status'] == 'complete'
    assert restarted.report()['counts']['daily_basic'] == 1001
    assert sync(restarted, 'daily_basic', {'trade_date': '20260930'}, 'secret', fetch=fetch)['requests'] == 0
    with sqlite3.connect(path) as db:
        assert all('secret' not in str(row) for row in db.execute('SELECT * FROM ts_sync'))


def test_larger_batch_resumes_exact_existing_offset(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'raw.sqlite3')
    store.initialize()
    rows = [{'ts_code':str(i), 'trade_date':'20241008'} for i in range(1000)]
    assert sync(store, 'daily', {'trade_date':'20241008'}, '', max_pages=1,
                fetch=lambda *args:rows)['status'] == 'partial'
    calls = []
    def fetch(token, endpoint, params):
        calls.append((params['offset'], params['limit']))
        return [{'ts_code':'1000', 'trade_date':'20241008'}]
    result = sync(store, 'daily', {'trade_date':'20241008'}, '', page_size=5000, fetch=fetch)
    assert calls == [(1000, 5000)]
    assert result['status'] == 'complete' and result['rows'] == 1001
    with pytest.raises(ValueError):
        sync(store, 'stk_limit', {'trade_date':'20241009'}, '', page_size=6000, fetch=fetch)


def test_repeated_full_page_does_not_loop(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'shared.sqlite3')
    store.initialize()
    rows = [{'ts_code': str(i)} for i in range(1000)]
    result = sync(store, 'stock_basic', {}, 'secret', fetch=lambda *args: rows)
    assert result['status'] == 'failed'
    assert store.report()['counts']['stock_basic'] == 1000
    assert store.report()['jobs'][0]['offset'] == 1000


def test_permission_failure_checkpoint_retained(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'shared.sqlite3')
    store.initialize()
    def denied(*args):
        raise ProviderError('permission_denied')
    result = sync(store, 'fund_portfolio', {}, 'secret', fetch=denied)
    assert result['error'] == 'permission_denied'
    assert store.report()['jobs'][0]['offset'] == 0


def test_rate_limits_atomic_and_endpoint_specific(tmp_path: Path) -> None:
    path = tmp_path / 'limits.sqlite3'
    for _ in range(100):
        assert reserve(path, 'stk_factor', 10) == 0
    assert reserve(path, 'stk_factor', 10) == 60
    for _ in range(400):
        assert reserve(path, 'daily', 10) == 0
    assert reserve(path, 'daily', 10) == 60
    assert reserve(path, 'stk_factor', 70) == 0


def test_no_retries_or_error_text_retention_for_permission() -> None:
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size=-1): return b'{"code":-1,"msg":"permission denied secret-token"}'
    with patch('quant.data.extension_transport._open_provider', return_value=Response()) as request, patch('quant.data.extension_transport.limited_tushare_call', side_effect=lambda f, **kw: f()):
        with pytest.raises(ProviderError, match='permission_denied') as caught:
            query('secret-token', 'daily', {})
        assert request.call_count == 1
        assert 'secret-token' not in str(caught.value)


def test_fund_portfolio_5000_tier_has_200_per_minute_cap(tmp_path: Path) -> None:
    path = tmp_path / 'limits.sqlite3'
    for _ in range(200):
        assert reserve(path, 'fund_portfolio', 10) == 0
    assert reserve(path, 'fund_portfolio', 10) == 60
    for _ in range(300):
        assert reserve(path, 'daily', 10) == 0
    assert reserve(path, 'daily', 10) == 60
    assert reserve(path, 'fund_portfolio', 70) == 0


def test_reserved_parameters_and_unknown_endpoint_rejected(tmp_path: Path) -> None:
    store = ExtensionStore(tmp_path / 'shared.sqlite3')
    store.initialize()
    for endpoint, params in [('rt_k', {}), ('daily', {'token': 'secret'})]:
        with pytest.raises(ValueError):
            store.begin(endpoint, params)


def test_shared_root_override_is_identical_for_cli_and_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv('A_PI_FEATURE_DB', raising=False)
    monkeypatch.setenv('A_PI_DATA_ROOT', str(tmp_path))
    assert default_store_path() == tmp_path / 'features.sqlite3'


def test_financial_indicator_respects_official_100_row_cap() -> None:
    assert ENDPOINTS['fina_indicator'].page_size == 100
