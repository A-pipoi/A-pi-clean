import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from quant import api_server
from quant.api.data_probe import probe_tushare_data
from quant.data.adapters import _TushareRows


@pytest.mark.parametrize("failed_endpoint", ["daily", "trade_cal"])
def test_probe_failure_redacts_credentials_and_preserves_status(
    failed_endpoint, caplog
):
    today = datetime.now(timezone(timedelta(hours=8))).date()
    calls = []

    def query(_token, api_name, _params, *, fields=""):
        calls.append(api_name)
        if api_name == failed_endpoint:
            try:
                raise ValueError("token=private-cause")
            except ValueError as cause:
                raise RuntimeError("password=private-error") from cause
        return _TushareRows([{"trade_date": today.strftime("%Y%m%d"), "close": 10.0}])

    with patch.dict(os.environ, {"TUSHARE_TOKEN": "offline-test-token"}):
        status = probe_tushare_data(
            query=query, china_timezone=timezone(timedelta(hours=8))
        )
    assert status["status"] == (
        "unreachable" if failed_endpoint == "daily" else "freshness_unknown"
    )
    assert status["data_reachable"] is (failed_endpoint == "trade_cal")
    assert status["data_fresh"] is None
    assert "RuntimeError" in status["error"]
    assert "private-" not in str(status)
    assert "private-" not in caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    assert calls == (
        ["daily"] if failed_endpoint == "daily" else ["daily", "trade_cal"]
    )


class DataProbeTests(unittest.TestCase):
    def test_probe_distinguishes_reachable_from_fresh(self) -> None:
        today = datetime.now(timezone(timedelta(hours=8))).date()
        yesterday = today - timedelta(days=1)

        def query(_token, api_name, _params, *, fields=""):
            if api_name == "daily":
                return _TushareRows(
                    [{"trade_date": today.strftime("%Y%m%d"), "close": 10.0}]
                )
            return _TushareRows(
                [
                    {"cal_date": yesterday.strftime("%Y%m%d"), "is_open": 1},
                    {"cal_date": today.strftime("%Y%m%d"), "is_open": 1},
                ]
            )

        with (
            patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}),
            patch.object(api_server, "_tushare_http_query", side_effect=query),
        ):
            status = api_server._probe_tushare_data()
        self.assertTrue(status["data_reachable"])
        self.assertTrue(status["data_fresh"])

    def test_calendar_failure_is_not_misreported_as_network_failure(self) -> None:
        today = datetime.now(timezone(timedelta(hours=8))).date()

        def query(_token, api_name, _params, *, fields=""):
            if api_name == "daily":
                return _TushareRows(
                    [{"trade_date": today.strftime("%Y%m%d"), "close": 10.0}]
                )
            raise RuntimeError("trade_cal permission denied")

        with (
            patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}),
            patch.object(api_server, "_tushare_http_query", side_effect=query),
        ):
            status = api_server._probe_tushare_data()
        self.assertTrue(status["data_reachable"])
        self.assertIsNone(status["data_fresh"])
        self.assertEqual(status["status"], "freshness_unknown")


if __name__ == "__main__":
    unittest.main()
