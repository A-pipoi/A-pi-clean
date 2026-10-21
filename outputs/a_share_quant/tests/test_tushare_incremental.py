"""All provider responses are synthetic; these tests never contact Tushare."""

import json
from datetime import UTC, date, timedelta
from unittest.mock import patch

import pytest

from quant.data import adapters as module
from quant.data.raw_incremental import FULL_REFRESH_SECONDS, _path
from quant.execution.service import _data_freshness, _limits_data_quality

START = date(2025, 10, 8)
END = date(2026, 10, 8)
CODE = "000001.SZ"


def test_paper_retry_reuses_only_recent_complete_current_close(tmp_path, provider):
    calls, _state = provider
    first = adapter(tmp_path)
    first.daily_bars(START, END)
    count = len(calls)
    second = adapter(tmp_path, resume_completed_refresh=True)
    second.daily_bars(START, END)
    assert len(calls) == count
    meta = second.snapshot().metadata
    assert meta["resumed_completed_symbols"] == [CODE]
    assert meta["data_source"] == "tushare_verified_close_resume"
    with patch("quant.execution.service._china_today", return_value=END):
        assert _data_freshness(meta, END)["fresh"]


def test_resume_cannot_extend_fetch_time_or_use_expired_or_incomplete_cache(
    tmp_path, provider
):
    import time
    from datetime import datetime

    from quant.data.adapters import _disk_cache_path
    from quant.data.raw_incremental import load_completed_refresh

    adapter(tmp_path).daily_bars(START, END)
    path = _disk_cache_path(
        tmp_path, (CODE, START.isoformat(), END.isoformat(), ""), prefix="tushare"
    )
    original = path.read_bytes()
    assert load_completed_refresh(tmp_path, CODE, START, END)
    assert path.read_bytes() == original
    payload = json.loads(original)
    payload["fetched_at"] = datetime.fromtimestamp(time.time() - 601, UTC).isoformat()
    path.write_text(json.dumps(payload))
    assert load_completed_refresh(tmp_path, CODE, START, END) is None
    payload = json.loads(original)
    payload["bars"][-1]["limit_up"] = None
    path.write_text(json.dumps(payload))
    assert load_completed_refresh(tmp_path, CODE, START, END) is None


@pytest.mark.parametrize("endpoint", ["daily", "stk_limit"])
def test_quota_error_is_not_partial_coverage_or_retried(tmp_path, provider, endpoint):
    from quant.api.resource_limits import ResourceLimitExceeded

    original = module._tushare_http_query
    calls = []

    def query(token, name, params, **kwargs):
        calls.append(name)
        if name == endpoint:
            raise ResourceLimitExceeded("tushare_hourly_budget", 900)
        return original(token, name, params, **kwargs)

    current = module.TushareDailyAdapter(
        [CODE, "600036.SH"],
        token="offline-token",
        adjust="",
        force_refresh=True,
        cache_dir=tmp_path,
        retry_count=3,
        retry_backoff_seconds=0,
    )
    with (
        patch.object(module, "_tushare_http_query", side_effect=query),
        pytest.raises(ResourceLimitExceeded, match="tushare_hourly_budget"),
    ):
        current.daily_bars(START, END)
    assert calls.count(endpoint) == 1


@pytest.mark.parametrize(
    "kind,timing,proof_day,fresh",
    [
        ("S", None, "20261008", True),
        ("S", "09:30-10:00", "20261008", False),
        ("R", None, "20261008", False),
        ("S", None, "20261007", False),
    ],
)
def test_only_current_full_day_suspension_closes_freshness_gap(
    tmp_path, provider, kind, timing, proof_day, fresh
):
    original = module._tushare_http_query
    paused = "601059.SH"

    def query(token, name, params, **kwargs):
        if name == "suspend_d":
            return module._TushareRows(
                [
                    {
                        "ts_code": paused,
                        "trade_date": proof_day,
                        "suspend_type": kind,
                        "suspend_timing": timing,
                    }
                ]
            )
        result = original(token, name, params, **kwargs).to_dict()
        if params.get("ts_code") == paused:
            result = [
                {**row, "ts_code": paused}
                for row in result
                if row["trade_date"] <= "20260914" or name != "daily"
            ]
        return module._TushareRows(result)

    current = module.TushareDailyAdapter(
        [CODE, paused],
        token="offline-token",
        adjust="",
        force_refresh=True,
        resume_completed_refresh=True,
        cache_dir=tmp_path,
    )
    with (
        patch.object(module, "_tushare_http_query", side_effect=query),
        patch.object(module.time, "sleep"),
    ):
        bars = current.daily_bars(START, END)
    meta = current.snapshot().metadata
    assert meta["coverage_ratio"] == 1
    assert max(bar.date for bar in bars if bar.symbol == paused) == date(2026, 9, 14)
    assert meta["proven_full_day_suspensions"] == ([paused] if fresh else [])
    with patch("quant.execution.service._china_today", return_value=END):
        assert _data_freshness(meta, END)["fresh"] is fresh


@pytest.fixture
def provider():
    calls = []
    state = {
        "close": 10.0,
        "empty": False,
        "fail": False,
        "limits": "available",
        "latest": None,
        "omit": None,
    }

    def query(token, endpoint, params, *, fields=""):
        assert token == "offline-token"
        calls.append((endpoint, dict(params)))
        if endpoint == "stock_basic":
            return module._TushareRows([])
        start = date.fromisoformat(
            f"{params['start_date'][:4]}-{params['start_date'][4:6]}-{params['start_date'][6:]}"
        )
        end = date.fromisoformat(
            f"{params['end_date'][:4]}-{params['end_date'][4:6]}-{params['end_date'][6:]}"
        )
        if endpoint == "daily" and state["fail"]:
            raise RuntimeError("offline failure")
        if endpoint == "stk_limit" and state["limits"] == "fail":
            raise RuntimeError("no permission")
        rows = []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if endpoint == "daily":
                if (
                    state["empty"]
                    or (state["latest"] and day > state["latest"])
                    or day == state["omit"]
                ):
                    continue
                rows.append(
                    {
                        "ts_code": CODE,
                        "trade_date": day.strftime("%Y%m%d"),
                        "open": 10,
                        "high": 12,
                        "low": 9,
                        "close": state["close"],
                        "vol": 100,
                    }
                )
            elif state["limits"] != "empty":
                if state["limits"] == "partial" and day == end:
                    continue
                rows.append(
                    {
                        "trade_date": day.strftime("%Y%m%d"),
                        "up_limit": 11,
                        "down_limit": 9,
                    }
                )
        return module._TushareRows(rows)

    with (
        patch.dict(module._TUSHARE_BAR_CACHE, {}, clear=True),
        patch.dict(module._TUSHARE_LIMIT_CACHE, {}, clear=True),
        patch.object(module, "_TUSHARE_LIMIT_ERROR_CACHE", None),
        patch.object(module, "_load_tushare", return_value=(object(), object())),
        patch.object(module, "_tushare_http_query", side_effect=query),
        patch("requests.post", side_effect=AssertionError("real network forbidden")),
    ):
        yield calls, state


def adapter(directory, **kwargs):
    return module.TushareDailyAdapter(
        [CODE],
        token="offline-token",
        adjust="",
        cache_dir=directory,
        force_refresh=True,
        retry_count=1,
        retry_backoff_seconds=0,
        **kwargs,
    )


@pytest.mark.parametrize("proof,valid", [
    ({"ts_code": "601059.SH", "list_status": "D", "delist_date": "20261008"}, True),
    ({"ts_code": "601059.SH", "list_status": "D", "delist_date": "20261009"}, False),
    ({"ts_code": "601059.SH", "list_status": "L", "delist_date": "20261008"}, False),
    ({"ts_code": "601059.SH", "list_status": "D", "delist_date": None}, False),
    ({"ts_code": "600000.SH", "list_status": "D", "delist_date": "20261008"}, False),
    (None, False),
])
def test_verified_delisting_is_not_a_stale_tradable_quote(tmp_path, provider, proof, valid):
    original = module._tushare_http_query
    retired = "601059.SH"

    def query(token, name, params, **kwargs):
        if name == "suspend_d":
            return module._TushareRows([])
        if name == "stock_basic":
            assert params == {"ts_code": retired, "list_status": "D"}
            return module._TushareRows([proof] if proof is not None else [])
        rows = original(token, name, params, **kwargs).to_dict()
        if params.get("ts_code") == retired:
            rows = [{**row, "ts_code": retired} for row in rows
                    if name != "daily" or row["trade_date"] <= "20260914"]
        return module._TushareRows(rows)

    current = module.TushareDailyAdapter(
        [CODE, retired], token="offline-token", adjust="", force_refresh=True,
        resume_completed_refresh=True, cache_dir=tmp_path,
    )
    with patch.object(module, "_tushare_http_query", side_effect=query), patch.object(module.time, "sleep"):
        bars = current.daily_bars(START, END)
    meta = current.snapshot().metadata
    assert retired in meta["available_symbols"]  # Original PIT membership/history remains.
    assert max(bar.date for bar in bars if bar.symbol == retired) == date(2026, 9, 14)
    assert meta["proven_delistings"] == ({retired: END.isoformat()} if valid else {})
    with patch("quant.execution.service._china_today", return_value=END):
        assert _data_freshness(meta, END)["fresh"] is valid


@pytest.mark.parametrize("exposure", ["none", "position", "order"])
def test_delisting_never_liquidates_or_bypasses_exposure_gate(exposure):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from quant.data.schema import Bar
    from quant.execution.service import run_strategy_cycle

    retired = "601059.SH"
    bars = [Bar(CODE, START + timedelta(days=i), 10, 11, 9, 10, 1000)
            for i in range((END - START).days + 1)]
    meta = {"proven_delistings": {retired: END.isoformat()}, "limits_available": True,
            "data_source": "tushare_live", "latest_trade_date": END.isoformat()}
    adapter_stub = Mock()
    adapter_stub.daily_bars.return_value = bars
    adapter_stub.snapshot.return_value = SimpleNamespace(metadata=meta, version="offline")
    store = Mock()
    store.state.return_value = {"positions": [{"symbol": retired}] if exposure == "position" else []}
    store.active_order_symbols.return_value = [retired] if exposure == "order" else []
    with (
        patch("quant.execution.service._china_today", return_value=END),
        patch("quant.execution.service.is_a_share_trading_day", return_value=True),
        patch("quant.execution.service.TushareDailyAdapter", return_value=adapter_stub),
        patch("quant.execution.service._blocked_maintenance", return_value={
            "skipped": True, "generated_orders": 0, "simulated_orders": [],
        }) as maintenance,
        patch("quant.execution.service.validate_strategy") as validate,
    ):
        result = run_strategy_cycle(store, symbols=[CODE, retired], sealed_test={"passed": False})
    assert result["generated_orders"] == 0
    assert result["simulated_orders"] == []
    validate.assert_not_called()
    if exposure == "none":
        maintenance.assert_called_once()  # Valuation-only maintenance; sealed gate remains closed.
    else:
        maintenance.assert_not_called()
        assert not result["data_freshness"]["fresh"]
        assert retired in result["skip_reason"]


def test_rolling_365_day_window_refreshes_tail_and_all_limits(tmp_path, provider):
    calls, state = provider
    adapter(tmp_path).daily_bars(START, END)
    # Simulate restart: no in-process cache may be required.
    module._TUSHARE_BAR_CACHE.clear()
    state["close"] = 10.5
    current = adapter(tmp_path)
    bars = current.daily_bars(START + timedelta(days=1), END + timedelta(days=1))
    tail = END - timedelta(days=7)
    assert calls[-2] == (
        "daily",
        {
            "ts_code": CODE,
            "start_date": tail.strftime("%Y%m%d"),
            "end_date": "20261009",
        },
    )
    assert calls[-1] == (
        "stk_limit",
        {"ts_code": CODE, "start_date": "20251009", "end_date": "20261009"},
    )
    assert len(bars) == 366
    assert len({bar.date for bar in bars}) == len(bars)
    assert bars[0].date == START + timedelta(days=1)
    assert bars[0].close == 10
    assert bars[-1].close == 10.5
    metadata = current.snapshot().metadata
    assert metadata["historical_bars_reused"] == 357
    assert metadata["incremental_fetches"] == 1
    assert metadata["cache_used"]
    assert metadata["data_source"] == "tushare_live"
    assert metadata["latest_trade_date"] == "2026-10-09"
    assert _limits_data_quality(metadata)["limits_available"]
    with patch(
        "quant.execution.service._china_today", return_value=END + timedelta(days=1)
    ):
        assert _data_freshness(metadata, bars[-1].date)["fresh"]
    assert "offline-token" not in _path(tmp_path, CODE).read_text()


@pytest.mark.parametrize("failure", ["empty", "fail"])
def test_failed_tail_never_returns_or_persists_old_bars(tmp_path, provider, failure):
    _, state = provider
    adapter(tmp_path).daily_bars(START, END)
    before = _path(tmp_path, CODE).read_bytes()
    state[failure] = True
    with pytest.raises(RuntimeError, match="未返回可用历史行情"):
        adapter(tmp_path).daily_bars(START, END + timedelta(days=1))
    assert _path(tmp_path, CODE).read_bytes() == before


def test_previous_day_response_is_not_fresh_and_overlap_deletions_are_preserved(
    tmp_path, provider
):
    _, state = provider
    adapter(tmp_path).daily_bars(START, END)
    state["latest"] = END
    state["omit"] = END - timedelta(days=2)
    current = adapter(tmp_path)
    bars = current.daily_bars(START, END + timedelta(days=1))
    assert state["omit"] not in {bar.date for bar in bars}
    assert bars[-1].date == END
    with patch(
        "quant.execution.service._china_today", return_value=END + timedelta(days=1)
    ):
        assert not _data_freshness(current.snapshot().metadata, bars[-1].date)["fresh"]


@pytest.mark.parametrize("limits", ["fail", "empty", "partial"])
def test_incremental_refresh_cannot_reuse_old_limit_prices(tmp_path, provider, limits):
    _, state = provider
    adapter(tmp_path).daily_bars(START, END)
    state["limits"] = limits
    current = adapter(tmp_path)
    bars = current.daily_bars(START, END + timedelta(days=1))
    assert not current.snapshot().metadata["limits_available"]
    assert not _limits_data_quality(current.snapshot().metadata)["limits_available"]
    if limits in {"fail", "empty"}:
        assert all(bar.limit_up is None and bar.limit_down is None for bar in bars)
    else:
        assert bars[-1].limit_up is None


@pytest.mark.parametrize(
    "invalid", ["corrupt", "expired", "wrong_symbol", "earlier_start", "earlier_end"]
)
def test_unsafe_history_requires_full_fetch(tmp_path, provider, invalid):
    calls, _ = provider
    adapter(tmp_path).daily_bars(START, END)
    path = _path(tmp_path, CODE)
    payload = json.loads(path.read_text())
    start, end = START, END + timedelta(days=1)
    if invalid == "corrupt":
        path.write_text("{}")
    elif invalid == "expired":
        payload["full_fetched_at"] -= FULL_REFRESH_SECONDS
        path.write_text(json.dumps(payload))
    elif invalid == "wrong_symbol":
        payload["bars"][0]["symbol"] = "600000.SH"
        path.write_text(json.dumps(payload))
    elif invalid == "earlier_start":
        start -= timedelta(days=1)
    else:
        end = END - timedelta(days=1)
    current = adapter(tmp_path)
    current.daily_bars(start, end)
    assert calls[-2][1]["start_date"] == start.strftime("%Y%m%d")
    assert current.snapshot().metadata["incremental_fetches"] == 0


@pytest.mark.parametrize("adjust", ["qfq", "hfq"])
def test_adjusted_prices_always_fetch_full_window(tmp_path, provider, adjust):
    adapter(tmp_path).daily_bars(START, END)
    with patch.object(
        module,
        "_fetch_tushare_frame",
        return_value=module._TushareRows(
            [
                {
                    "ts_code": CODE,
                    "trade_date": "20261009",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "vol": 1,
                },
            ]
        ),
    ) as fetch:
        current = module.TushareDailyAdapter(
            [CODE],
            token="offline-token",
            adjust=adjust,
            cache_dir=tmp_path,
            force_refresh=True,
        )
        current.daily_bars(START, END + timedelta(days=1))
        assert fetch.call_args.args[3] == START
        assert current.snapshot().metadata["incremental_fetches"] == 0


def test_same_window_still_requeries_tail(tmp_path, provider):
    calls, _ = provider
    adapter(tmp_path).daily_bars(START, END)
    current = adapter(tmp_path)
    current.daily_bars(START, END)
    assert len(calls) == 4
    assert current.snapshot().metadata["cache_hits"] == 0


def test_read_only_incremental_cache_does_not_break_live_data(tmp_path, provider):
    with patch(
        "quant.data.raw_incremental.os.replace", side_effect=OSError("read only")
    ):
        assert len(adapter(tmp_path).daily_bars(START, END)) == 366
    assert not list(tmp_path.glob(".tushare_incremental_*.tmp"))


def test_one_current_symbol_cannot_hide_another_stale_symbol(tmp_path, provider):
    other = "600000.SH"

    def frame(tushare, pro, code, start, end, *args):
        day = end if code == CODE else end - timedelta(days=1)
        return module._TushareRows(
            [
                {
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "vol": 1,
                }
            ]
        )

    with (
        patch.object(module, "_fetch_tushare_frame", side_effect=frame),
        patch.object(module.time, "sleep"),
    ):
        current = module.TushareDailyAdapter(
            [CODE, other],
            token="offline-token",
            adjust="",
            cache_dir=tmp_path,
            force_refresh=True,
        )
        bars = current.daily_bars(START, END)
    assert current.snapshot().metadata["latest_trade_date"] == "2026-10-07"
    with patch("quant.execution.service._china_today", return_value=END):
        assert not _data_freshness(
            current.snapshot().metadata, max(bar.date for bar in bars)
        )["fresh"]
