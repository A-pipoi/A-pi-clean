import sqlite3
from contextlib import closing
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.research.deep_evidence import canonical
from quant.research.forward_capture import collect_forward


def run(tmp_path, monkeypatch, hour, calendar):
    now = datetime.fromisoformat(f"2026-10-13T{hour:02d}:00:00+08:00")
    query = Mock(return_value=calendar)
    collector = Mock(provider=SimpleNamespace(query=query))
    factory = Mock(return_value=collector)
    monkeypatch.setattr(
        "quant.research.forward_capture.FormalFeatureCollector", factory
    )
    result = collect_forward(
        tmp_path / "study",
        tmp_path / "cache",
        "alice",
        "fixed",
        "test-only",
        now,
        clock=lambda: now,
        cancelled=lambda: False,
    )
    return result, query, factory, collector


def test_no_market_requests_outside_observation_windows(tmp_path, monkeypatch):
    result, query, factory, _ = run(tmp_path, monkeypatch, 23, [])
    factory.assert_not_called()
    query.assert_not_called()
    assert result["samples"] == 0


def test_closed_day_stops_before_membership_and_features(tmp_path, monkeypatch):
    result, query, _, collector = run(
        tmp_path, monkeypatch, 9, [{"cal_date": "20261013", "is_open": 0}]
    )
    assert query.call_count == 1
    collector.collect.assert_not_called()
    assert result["membership"] == 0


def test_after_close_cannot_backfill_membership(tmp_path, monkeypatch):
    result, query, _, collector = run(
        tmp_path, monkeypatch, 17, [{"cal_date": "20261013", "is_open": 1}]
    )
    assert query.call_count == 1
    collector.collect.assert_not_called()
    assert result["samples"] == 0


@pytest.mark.parametrize("calendar", [[], [{"cal_date": "20261014", "is_open": 1}]])
def test_unknown_or_future_calendar_fails_closed(tmp_path, monkeypatch, calendar):
    with pytest.raises(ValueError, match="calendar"):
        run(tmp_path, monkeypatch, 9, calendar)


def test_preclose_membership_then_actual_close_records_unapproved_samples(
    tmp_path, monkeypatch
):
    clock = [datetime.fromisoformat("2026-10-13T09:00:00+08:00")]
    symbols = [f"{index:06d}.SZ" for index in range(1, 281)]
    calendar = [{"cal_date": "20261013", "is_open": 1}]
    members = [
        {"con_code": symbol, "trade_date": "20260930", "weight": 100 / 280}
        for symbol in symbols
    ]
    query = Mock(side_effect=[calendar, members, calendar])
    collector = Mock(provider=SimpleNamespace(query=query))
    monkeypatch.setattr(
        "quant.research.forward_capture.FormalFeatureCollector",
        Mock(return_value=collector),
    )
    authority = Mock()
    authority.features.return_value = {
        "rows": [{"instrument": symbol, "features": [1.0] * 360} for symbol in symbols]
    }
    monkeypatch.setattr(
        "quant.research.forward_capture.EvidenceAuthority", Mock(return_value=authority)
    )
    cache = tmp_path / "cache"
    args = (tmp_path / "study", cache, "alice", "fixed", "test-only")
    first = collect_forward(
        *args, clock[0], clock=lambda: clock[0], cancelled=lambda: False
    )
    assert first["membership"] == 1
    assert first["samples"] == 0
    collector.collect.assert_not_called()
    clock[0] = datetime.fromisoformat("2026-10-13T17:30:00+08:00")
    payload = {
        "day": "20261013",
        "received_at": clock[0].isoformat(),
        "raw": [
            {"ts_code": symbol, "trade_date": "20261013", "close": 10}
            for symbol in symbols
        ],
        "adjustment": [
            {"ts_code": symbol, "trade_date": "20261013", "adj_factor": 1}
            for symbol in symbols
        ],
    }
    with closing(sqlite3.connect(cache / "deep_raw.sqlite3")) as db, db:
        db.execute("CREATE TABLE observations(day TEXT PRIMARY KEY,payload BLOB)")
        db.execute(
            "INSERT INTO observations VALUES(?,?)", ("20261013", canonical(payload))
        )
    collector.collect.return_value = True
    second = collect_forward(
        *args, clock[0], clock=lambda: clock[0], cancelled=lambda: False
    )
    assert second["samples"] == 280
    assert second["labels"] == 0
    assert second["approved_for_training"] is False
    authority.approve_dataset.assert_not_called()
