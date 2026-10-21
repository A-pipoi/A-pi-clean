from datetime import date, datetime
from unittest.mock import Mock

import pytest

from quant.api.factor_mining_routes import (
    FactorMiningRouteDependencies,
    handle_factor_mining_request,
)
from quant.api.parsing import bounded_int
from quant.api.resource_limits import ResourceLimitExceeded
from quant.api.serialization import bars_from_payload
from quant.data import adapters, industry_history_probe
from quant.data.live_monitor import LiveMonitorProvider
from quant.errors import InputTypeError, ProviderShapeError
from quant.factors.expression import _numeric_constant
from quant.factors.external_deep import _day
from quant.factors.live_ic import SHANGHAI


@pytest.mark.parametrize(
    "call",
    [
        lambda: bounded_int(None, 1, 10),
        lambda: bounded_int(True, 1, 10),
        lambda: bars_from_payload({}),
        lambda: bars_from_payload([1]),
        lambda: _day(123),
        lambda: _numeric_constant(__import__("ast").Constant(True)),
    ],
)
def test_invalid_input_type_preserves_value_error_compatibility(call):
    with pytest.raises(InputTypeError) as caught:
        call()
    assert isinstance(caught.value, TypeError)
    assert isinstance(caught.value, ValueError)


def test_provider_shape_remains_502_not_bad_request():
    def run(_payload):
        raise ProviderShapeError("invalid_result_shape")

    status, body = handle_factor_mining_request(
        "/api/factor-mining/run",
        {},
        FactorMiningRouteDependencies(
            run=run,
            evaluate=run,
            accept=lambda _: {},
            promote=lambda _: {},
            status=dict,
        ),
    )
    assert status == 502
    assert body["code"] == "data_provider_unavailable"
    assert body["error"] == "ProviderShapeError"


def test_industry_probe_quota_stops_without_retry_or_followup(tmp_path, monkeypatch):
    quota = ResourceLimitExceeded("tushare_hourly_limit", 3600)
    query = Mock(side_effect=quota)
    monkeypatch.setattr(industry_history_probe, "_tushare_http_query", query)
    with pytest.raises(ResourceLimitExceeded) as caught:
        industry_history_probe.probe(tmp_path, "offline-token")
    assert caught.value is quota
    assert caught.value.retry_after == 3600
    assert query.call_count == 1
    assert not (tmp_path / "report.json").exists()


def test_live_industry_quota_prevents_price_requests(tmp_path, monkeypatch):
    now = datetime(2026, 10, 12, 17, 45, tzinfo=SHANGHAI)
    calls = []
    quota = ResourceLimitExceeded("tushare_hourly_limit", 3600)
    provider = LiveMonitorProvider(tmp_path, "offline-token", clock=lambda: now)

    def query(name, params, fields):
        calls.append(name)
        if name == "trade_cal":
            return [{"cal_date": "20261012", "is_open": 1}]
        if name == "index_weight":
            return [
                {"con_code": f"{i:06d}.SZ", "trade_date": "20260930", "weight": 1 / 3}
                for i in range(300)
            ]
        if name == "stock_basic":
            raise quota
        raise AssertionError("Price requests must not follow quota exhaustion")

    monkeypatch.setattr(provider, "query", query)
    with pytest.raises(ResourceLimitExceeded) as caught:
        provider.fetch(now.date(), now)
    assert caught.value is quota
    assert calls == ["trade_cal", "index_weight", "stock_basic"]


def test_limit_error_cache_and_retry_logs_do_not_keep_secret(monkeypatch, caplog):
    error = OSError("synthetic-secret-for-redaction")
    query = Mock(side_effect=error)
    monkeypatch.setattr(adapters, "_tushare_http_query", query)
    monkeypatch.setattr(adapters, "_TUSHARE_LIMIT_ERROR_CACHE", None)
    monkeypatch.setattr(adapters, "_TUSHARE_LIMIT_CACHE", {})
    day = date(2024, 1, 2)
    with pytest.raises(OSError) as caught:
        adapters._fetch_tushare_limits(
            None, "000001.SZ", day, day, 2, 0, "offline-token", True
        )
    assert caught.value is error
    assert query.call_count == 2
    assert adapters._TUSHARE_LIMIT_ERROR_CACHE[1] == "OSError"
    with pytest.raises(RuntimeError, match="^OSError$"):
        adapters._fetch_tushare_limits(
            None, "000001.SZ", day, day, 2, 0, "offline-token"
        )
    assert query.call_count == 2
    assert "synthetic-secret" not in caplog.text
    assert len(caplog.records) == 2
    assert all(record.exc_info is None for record in caplog.records)
