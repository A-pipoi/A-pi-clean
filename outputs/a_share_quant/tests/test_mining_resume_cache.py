from datetime import date
from unittest.mock import patch

import pytest

from quant.api.resource_limits import ResourceLimitExceeded
from quant.data.fundamentals import (
    _read_completed_job_cache,
    _write_cache,
    fetch_tushare_fundamental_factors,
    fetch_tushare_fundamental_history,
)
from quant.data.pit_sources import (
    fetch_tushare_daily_basic_history,
    fetch_tushare_industry_history,
)
from quant.data.progress import DATA_CACHE_NOT_BEFORE, DATA_PROGRESS


def test_force_refresh_can_only_resume_evidence_from_the_current_job(tmp_path):
    path = tmp_path / "cache.json"
    with patch("quant.data.fundamentals.time.time", return_value=200):
        _write_cache(path, [{"value": 42}])
    assert _read_completed_job_cache(path, 1000) is None
    token = DATA_CACHE_NOT_BEFORE.set(150)
    try:
        with patch("quant.data.fundamentals.time.time", return_value=300):
            assert _read_completed_job_cache(path, 1000) == [{"value": 42}]
        DATA_CACHE_NOT_BEFORE.set(250)
        assert _read_completed_job_cache(path, 1000) is None
    finally:
        DATA_CACHE_NOT_BEFORE.reset(token)


@pytest.mark.parametrize(
    "function,module",
    [
        (fetch_tushare_fundamental_history, "fundamentals"),
        (fetch_tushare_daily_basic_history, "pit_sources"),
        (fetch_tushare_industry_history, "pit_sources"),
    ],
)
def test_provider_quota_is_never_swallowed_as_missing_financial_data(function, module):
    arguments = {"symbols": ["000001.SZ"], "token": "test", "force_refresh": True}
    if function is not fetch_tushare_industry_history:
        arguments.update(start_date=date(2024, 1, 1), end_date=date(2024, 2, 1))
    progress = []
    token = DATA_PROGRESS.set(lambda *args: progress.append(args))
    try:
        with (
            patch(
                f"quant.data.{module}._tushare_http_query",
                side_effect=ResourceLimitExceeded("tushare_hourly_budget", 12),
            ),
            pytest.raises(ResourceLimitExceeded, match="tushare_hourly_budget"),
        ):
            function(**arguments)
        assert progress == [(0, 1, "000001.SZ")]
    finally:
        DATA_PROGRESS.reset(token)


def test_current_financial_quota_stops_before_next_symbol_or_cache_write(tmp_path):
    quota_error = ResourceLimitExceeded("tushare_hourly_budget", 900)
    with (
        patch(
            "quant.data.fundamentals._tushare_http_query", side_effect=quota_error
        ) as query,
        patch("quant.data.fundamentals._write_cache") as write_cache,
        patch("quant.data.fundamentals.time.sleep") as sleep,
        pytest.raises(ResourceLimitExceeded) as raised,
    ):
        fetch_tushare_fundamental_factors(
            ["000001.SZ", "600036.SH"],
            as_of=date(2024, 2, 1),
            token="offline-test-token",
            cache_dir=tmp_path,
            force_refresh=True,
        )
    assert raised.value is quota_error
    assert raised.value.retry_after == 900
    query.assert_called_once()
    write_cache.assert_not_called()
    sleep.assert_not_called()
    assert not list(tmp_path.rglob("*.json"))


def test_current_financial_provider_failure_remains_missing_data():
    with patch(
        "quant.data.fundamentals._tushare_http_query",
        side_effect=RuntimeError("offline-provider-failure"),
    ):
        rows, metadata = fetch_tushare_fundamental_factors(
            ["000001.SZ"],
            token="offline-test-token",
            force_refresh=True,
        )
    assert rows == []
    assert metadata["status"] == "unavailable"
    assert metadata["missing_symbols"] == ["000001.SZ"]
