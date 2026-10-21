from datetime import date
from unittest.mock import patch

import pytest

from quant.api.resource_limits import ResourceLimitExceeded
from quant.data.fundamentals import (
    fetch_tushare_fundamental_factors,
    fetch_tushare_fundamental_history,
)
from quant.data.pit_sources import (
    fetch_tushare_daily_basic_history,
    fetch_tushare_industry_history,
)
from quant.data.statements import fetch_tushare_statements


@pytest.mark.parametrize("statement", ["all", "income", "balancesheet", "cashflow"])
def test_statement_quota_stops_all_remaining_requests_and_keeps_retry_time(
    tmp_path, statement, caplog
):
    error = ResourceLimitExceeded("tushare_hourly_budget", 900)
    with (
        patch("quant.data.statements._tushare_http_query", side_effect=error) as query,
        patch("quant.data.statements._write_cache") as write_cache,
        patch("quant.data.statements.time.sleep") as sleep,
        pytest.raises(ResourceLimitExceeded) as raised,
    ):
        fetch_tushare_statements(
            ["000001.SZ", "600036.SH"],
            statement=statement,
            token="offline-test-token",
            as_of=date(2024, 2, 1),
            cache_dir=tmp_path,
            force_refresh=True,
        )
    assert raised.value is error
    assert raised.value.retry_after == 900
    query.assert_called_once()
    write_cache.assert_not_called()
    sleep.assert_not_called()
    assert not list(tmp_path.rglob("*.json"))
    assert not caplog.records  # Quota is propagated, not logged as provider failure.


@pytest.mark.parametrize(
    "function,module,extra",
    [
        (
            fetch_tushare_fundamental_factors,
            "fundamentals",
            {"as_of": date(2024, 2, 1)},
        ),
        (
            fetch_tushare_fundamental_history,
            "fundamentals",
            {"start_date": date(2024, 1, 1), "end_date": date(2024, 2, 1)},
        ),
        (
            fetch_tushare_daily_basic_history,
            "pit_sources",
            {"start_date": date(2024, 1, 1), "end_date": date(2024, 2, 1)},
        ),
        (fetch_tushare_industry_history, "pit_sources", {}),
        (
            fetch_tushare_statements,
            "statements",
            {"statement": "income", "as_of": date(2024, 2, 1)},
        ),
    ],
)
def test_financial_provider_failure_redacts_metadata_logs_and_cache(
    tmp_path, caplog, function, module, extra
):
    error = RuntimeError("password=private-error provider=https://private-provider")
    error.__cause__ = ValueError("token=private-cause")
    with patch(f"quant.data.{module}._tushare_http_query", side_effect=error) as query:
        _, metadata = function(
            ["000001.SZ"],
            token="offline-test-token",
            cache_dir=tmp_path,
            force_refresh=True,
            **extra,
        )
    query.assert_called_once()
    assert metadata["status"] == "unavailable"
    assert metadata["missing_symbols"] == ["000001.SZ"]
    assert metadata["errors"] and "RuntimeError" in metadata["errors"][0]
    assert "private-" not in str(metadata)
    assert "private-" not in caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    assert not list(tmp_path.rglob("*.json"))
