import tempfile
import types
import unittest
from datetime import date
from unittest.mock import patch

from quant.data import (
    TushareDailyAdapter,
    fetch_tushare_index,
    fetch_tushare_indices,
    fetch_tushare_market_breadth,
    fetch_tushare_realtime_indices,
)
from quant.data import adapters as adapters_module


class _FakeFrame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        return self.rows


class TushareAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        with adapters_module._TUSHARE_INDEX_CACHE_LOCK:
            adapters_module._TUSHARE_INDEX_CACHE.clear()

    def test_fetches_latest_index_daily_bar(self) -> None:
        with patch.object(
            adapters_module,
            "_tushare_http_query",
            return_value=adapters_module._TushareRows(
                [
                    {
                        "ts_code": "000001.SH",
                        "trade_date": "20240102",
                        "close": 2962.28,
                        "pre_close": 2954.35,
                        "pct_chg": 0.2685,
                    },
                    {
                        "ts_code": "000001.SH",
                        "trade_date": "20240103",
                        "close": 2967.25,
                        "pre_close": 2962.28,
                        "pct_chg": 0.1678,
                    },
                ]
            ),
        ):
            result = fetch_tushare_index(
                "000001.SH", token="test-token", end=date(2024, 1, 3)
            )

        self.assertEqual(result["trade_date"], "2024-01-03")
        self.assertEqual(result["close"], 2967.25)
        self.assertEqual(result["change_pct"], 0.1678)

    def test_fetches_unique_live_index_quotes_without_using_cache(self) -> None:
        self.assertGreaterEqual(len(adapters_module.TUSHARE_REALTIME_INDEX_NAMES), 20)
        request = {}

        def query(_token, api_name, params, *, fields=""):
            request.update(api_name=api_name, params=params, fields=fields)
            return adapters_module._TushareRows(
                [
                    {
                        "ts_code": "399006.SZ",
                        "trade_time": "2026-09-24 10:15:00",
                        "close": 2200,
                        "pre_close": 2000,
                        "pct_chg": 9.99,
                    },
                    {
                        "ts_code": "399006.SZ",
                        "trade_time": "2026-09-24 10:15:00",
                        "close": 2200,
                        "pre_close": 2000,
                        "pct_chg": 9.99,
                    },
                    {
                        "ts_code": "000300.SH",
                        "trade_time": "2026-09-24 10:15:00",
                        "close": 4100,
                        "pre_close": 4000,
                        "pct_chg": 9.99,
                    },
                ]
            )

        with patch.object(
            adapters_module, "_tushare_http_query", side_effect=query
        ) as mocked_query:
            quotes = fetch_tushare_realtime_indices(token="test-token")

        self.assertEqual(mocked_query.call_count, 1)
        self.assertEqual(request["api_name"], "rt_idx_k")
        self.assertEqual(
            request["params"]["ts_code"],
            ",".join(adapters_module.TUSHARE_REALTIME_INDEX_NAMES),
        )
        self.assertEqual(len(quotes), 2)
        self.assertEqual(len({quote["ts_code"] for quote in quotes}), len(quotes))
        self.assertAlmostEqual(quotes[0]["change_pct"], 10.0)
        self.assertEqual(quotes[0]["source"], "tushare_rt_idx_k")

    def test_live_index_quotes_fail_closed_when_no_real_data_is_returned(self) -> None:
        with (
            patch.object(
                adapters_module,
                "_tushare_http_query",
                return_value=adapters_module._TushareRows([]),
            ),
            self.assertRaisesRegex(RuntimeError, "rt_idx_k"),
        ):
            fetch_tushare_realtime_indices(token="test-token")

    def test_fetches_supported_multiple_indices(self) -> None:
        request = {}

        def query(_token, _api_name, params, *, fields=""):
            request.update(params)
            return adapters_module._TushareRows(
                [
                    {
                        "ts_code": "000001.SH",
                        "trade_date": "20240103",
                        "close": 3000,
                        "pre_close": 2990,
                        "pct_chg": 0.1,
                    },
                    {
                        "ts_code": "399001.SZ",
                        "trade_date": "20240103",
                        "close": 9000,
                        "pre_close": 8990,
                        "pct_chg": 0.1,
                    },
                    {
                        "ts_code": "399006.SZ",
                        "trade_date": "20240103",
                        "close": 1800,
                        "pre_close": 1790,
                        "pct_chg": 0.1,
                    },
                ]
            )

        with patch.object(adapters_module, "_tushare_http_query", side_effect=query):
            results, errors = fetch_tushare_indices(
                ["000001.SH", "399001.SZ", "399006.SZ"],
                token="test-token",
                end=date(2024, 1, 3),
                cache_ttl_seconds=1,
            )

        self.assertEqual(
            [item["ts_code"] for item in results],
            ["000001.SH", "399001.SZ", "399006.SZ"],
        )
        self.assertEqual(errors, [])
        self.assertEqual(request["ts_code"], "")
        self.assertEqual(request["trade_date"], "20240103")

    def test_falls_back_to_per_index_queries_when_market_wide_query_fails(self) -> None:
        requests = []

        def query(_token, _api_name, params, *, fields=""):
            requests.append(dict(params))
            if not params.get("ts_code"):
                raise RuntimeError("market-wide query is unavailable")
            code = params["ts_code"]
            return adapters_module._TushareRows(
                [
                    {
                        "ts_code": code,
                        "trade_date": "20240103",
                        "close": 1000,
                        "pre_close": 990,
                        "pct_chg": 1.01,
                    }
                ]
            )

        with patch.object(adapters_module, "_tushare_http_query", side_effect=query):
            results, errors = fetch_tushare_indices(
                ["000001.SH", "399001.SZ", "399006.SZ"],
                token="test-token",
                end=date(2024, 1, 3),
                force_refresh=True,
            )

        self.assertEqual(len(requests), 4)
        self.assertEqual(
            {item["ts_code"] for item in results},
            {"000001.SH", "399001.SZ", "399006.SZ"},
        )
        self.assertTrue(
            all(item["source"] == "tushare" and item["fallback"] for item in results)
        )
        self.assertEqual(errors, [])

    def test_falls_back_to_latest_available_trade_date_before_market_open(self) -> None:
        requests = []

        def query(_token, _api_name, params, *, fields=""):
            requests.append(dict(params))
            if params.get("trade_date") == "20240104":
                return adapters_module._TushareRows([])
            if "cal_date" in fields:
                return adapters_module._TushareRows(
                    [
                        {"cal_date": "20240103", "is_open": 1},
                    ]
                )
            return adapters_module._TushareRows(
                [
                    {
                        "ts_code": "000001.SH",
                        "trade_date": "20240103",
                        "close": 3000,
                        "pre_close": 2990,
                        "pct_chg": 0.1,
                    },
                    {
                        "ts_code": "399001.SZ",
                        "trade_date": "20240103",
                        "close": 9000,
                        "pre_close": 8990,
                        "pct_chg": 0.1,
                    },
                    {
                        "ts_code": "399006.SZ",
                        "trade_date": "20240103",
                        "close": 1800,
                        "pre_close": 1790,
                        "pct_chg": 0.1,
                    },
                ]
            )

        with patch.object(adapters_module, "_tushare_http_query", side_effect=query):
            results, errors = fetch_tushare_indices(
                ["000001.SH", "399001.SZ", "399006.SZ"],
                token="test-token",
                end=date(2024, 1, 4),
                lookback_days=14,
                cache_ttl_seconds=1,
            )

        self.assertEqual([item["trade_date"] for item in results], ["2024-01-03"] * 3)
        self.assertEqual(errors, [])
        self.assertEqual(requests[0]["trade_date"], "20240104")
        self.assertEqual(requests[1]["exchange"], "SSE")
        self.assertEqual(requests[1]["is_open"], "1")
        self.assertEqual(requests[2]["trade_date"], "20240103")

    def test_summarizes_real_market_breadth(self) -> None:
        with patch.object(
            adapters_module,
            "_tushare_http_query",
            return_value=adapters_module._TushareRows(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240103",
                        "close": 10.5,
                        "pre_close": 10,
                        "pct_chg": 5,
                        "amount": 100000,
                    },
                    {
                        "ts_code": "600000.SH",
                        "trade_date": "20240103",
                        "close": 9.5,
                        "pre_close": 10,
                        "pct_chg": -5,
                        "amount": 200000,
                    },
                    {
                        "ts_code": "300001.SZ",
                        "trade_date": "20240103",
                        "close": 12,
                        "pre_close": 12,
                        "pct_chg": 0,
                        "amount": 300000,
                    },
                ]
            ),
        ):
            result = fetch_tushare_market_breadth(date(2024, 1, 3), token="test-token")

        self.assertEqual(result["up"], 1)
        self.assertEqual(result["down"], 1)
        self.assertEqual(result["flat"], 1)
        self.assertEqual(result["turnover_billion"], 6.0)

    def test_converts_tushare_columns_and_limit_prices(self) -> None:
        def pro_bar(**kwargs):
            return _FakeFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240103",
                        "open": 10,
                        "high": 11,
                        "low": 9.5,
                        "close": 10.5,
                        "vol": 120,
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240102",
                        "open": 9,
                        "high": 10,
                        "low": 8.5,
                        "close": 9.5,
                        "vol": 100,
                    },
                ]
            )

        fake_tushare = types.SimpleNamespace(pro_bar=pro_bar)
        fake_pro = types.SimpleNamespace(
            stk_limit=lambda **kwargs: _FakeFrame(
                [
                    {"trade_date": "20240102", "up_limit": 10.45, "down_limit": 8.55},
                    {"trade_date": "20240103", "up_limit": 11.55, "down_limit": 9.45},
                ]
            )
        )
        daily_frame = _FakeFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240103",
                    "open": 10,
                    "high": 11,
                    "low": 9.5,
                    "close": 10.5,
                    "vol": 120,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": "20240102",
                    "open": 9,
                    "high": 10,
                    "low": 8.5,
                    "close": 9.5,
                    "vol": 100,
                },
            ]
        )

        def http_query(_token, api_name, _params, *, fields=""):
            return (
                daily_frame
                if api_name == "daily"
                else _FakeFrame(
                    [
                        {
                            "trade_date": "20240102",
                            "up_limit": 10.45,
                            "down_limit": 8.55,
                        },
                        {
                            "trade_date": "20240103",
                            "up_limit": 11.55,
                            "down_limit": 9.45,
                        },
                    ]
                )
            )

        with (
            tempfile.TemporaryDirectory() as cache_dir,
            patch.object(
                adapters_module, "_load_tushare", return_value=(fake_tushare, fake_pro)
            ),
            patch.object(
                adapters_module, "_tushare_http_query", side_effect=http_query
            ),
        ):
            bars = TushareDailyAdapter(
                ["000001"],
                token="test-token",
                cache_dir=cache_dir,
                adjust="",
                retry_backoff_seconds=0,
            ).daily_bars(start=date(2024, 1, 2), end=date(2024, 1, 3))

        self.assertEqual(
            [bar.date for bar in bars], [date(2024, 1, 2), date(2024, 1, 3)]
        )
        self.assertEqual(bars[0].symbol, "000001.SZ")
        self.assertEqual(bars[0].volume, 10_000)
        self.assertEqual(bars[0].limit_up, 10.45)

    def test_requires_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            TushareDailyAdapter(["600000"]).daily_bars(
                start=date(2024, 1, 2), end=date(2024, 1, 3)
            )

    def test_raw_daily_path_marks_limit_data_as_not_requested(self) -> None:
        fake_tushare = types.SimpleNamespace()
        fake_pro = types.SimpleNamespace()
        frame = _FakeFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20240102",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.2,
                    "vol": 80,
                }
            ]
        )
        with (
            patch.object(
                adapters_module, "_load_tushare", return_value=(fake_tushare, fake_pro)
            ),
            patch.object(adapters_module, "_tushare_http_query", return_value=frame),
        ):
            adapter = TushareDailyAdapter(
                ["600000"],
                token="test-token",
                adjust="",
                fetch_limits=False,
                retry_backoff_seconds=0,
            )
            bars = adapter.daily_bars(start=date(2024, 1, 2), end=date(2024, 1, 2))
        self.assertEqual(len(bars), 1)
        self.assertFalse(adapter.snapshot().metadata["limits_available"])
        self.assertEqual(
            adapter.snapshot().metadata["limit_data_status"], "not_requested"
        )
        self.assertEqual(adapter.snapshot().metadata["coverage_ratio"], 1.0)
        self.assertEqual(adapter.snapshot().metadata["missing_symbols"], [])

    def test_signal_adjust_option_keeps_execution_raw_and_versions_snapshot(
        self,
    ) -> None:
        daily = _FakeFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20240102",
                    "open": 99,
                    "high": 101,
                    "low": 98,
                    "close": 100,
                    "vol": 100,
                },
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20240103",
                    "open": 49.5,
                    "high": 50.5,
                    "low": 49,
                    "close": 50,
                    "vol": 200,
                },
            ]
        )

        def query(_token, api_name, _params, *, fields=""):
            if api_name == "daily":
                return daily
            if api_name == "adj_factor":
                return _FakeFrame(
                    [
                        {"trade_date": "20240102", "adj_factor": 1.0},
                        {"trade_date": "20240103", "adj_factor": 2.0},
                    ]
                )
            raise AssertionError(api_name)

        with (
            patch.object(
                adapters_module,
                "_load_tushare",
                return_value=(types.SimpleNamespace(), types.SimpleNamespace()),
            ),
            patch.object(adapters_module, "_tushare_http_query", side_effect=query),
        ):
            adapter = TushareDailyAdapter(
                ["600000.SH"],
                token="test-token",
                adjust="",
                fetch_limits=False,
                signal_adjust="adj",
                retry_backoff_seconds=0,
            )
            bars = adapter.daily_bars(start=date(2024, 1, 2), end=date(2024, 1, 3))

        self.assertEqual((bars[1].open, bars[1].close), (49.5, 50))
        self.assertEqual((bars[0].signal_close, bars[1].signal_close), (100, 100))
        self.assertIn(":signal-adj:", adapter.snapshot().version)
        self.assertEqual(
            adapter.snapshot().metadata["signal_adjustment_coverage_status"], "complete"
        )

    def test_signal_adjust_option_rejects_incomplete_factor_coverage(self) -> None:
        daily = _FakeFrame(
            [
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20240102",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "vol": 100,
                },
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20240103",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "vol": 100,
                },
            ]
        )

        def query(_token, api_name, _params, *, fields=""):
            return (
                daily
                if api_name == "daily"
                else _FakeFrame([{"trade_date": "20240102", "adj_factor": 1.0}])
            )

        with (
            patch.object(
                adapters_module,
                "_load_tushare",
                return_value=(types.SimpleNamespace(), types.SimpleNamespace()),
            ),
            patch.object(adapters_module, "_tushare_http_query", side_effect=query),
        ):
            adapter = TushareDailyAdapter(
                ["600000.SH"],
                token="test-token",
                adjust="",
                fetch_limits=False,
                signal_adjust="adj",
                retry_backoff_seconds=0,
            )
            with self.assertRaisesRegex(RuntimeError, "coverage incomplete"):
                adapter.daily_bars(start=date(2024, 1, 2), end=date(2024, 1, 3))


if __name__ == "__main__":
    unittest.main()
