"""Shard/resume tests for PIT feature materialization."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.feature_materialization import (
    FeatureMaterializationDependencies,
    iter_month_windows,
    materialize_pit_features,
    shard_id_for,
)
from quant.data.schema import Bar
from quant.features.store import FeatureStore


def test_iter_month_windows_clips_to_range():
    windows = iter_month_windows(date(2026, 9, 20), date(2026, 11, 5))
    assert windows == [
        (date(2026, 9, 20), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 5)),
    ]


def test_catch_up_skips_when_already_covered(tmp_path):
    store = FeatureStore(tmp_path / "f.sqlite3")
    # seed one observation so max_feature_date exists via status path — write through API
    store.write_rows(
        [
            {
                "symbol": "600000.SH",
                "feature_name": "momentum_5",
                "feature_value": 0.1,
                "feature_date": date(2026, 10, 14),
                "available_at": __import__("datetime").datetime(
                    2026, 10, 14, 17, 0, tzinfo=__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
                ),
                "data_version": "v",
                "source_hash": "h",
            }
        ]
    )
    # Force max via direct SQL for clarity on catch-up compare with end
    import sqlite3

    con = sqlite3.connect(store.path)
    con.execute("UPDATE feature_observations SET feature_date='2026-10-14'")
    # also fake ts_daily for status latest
    con.execute("CREATE TABLE ts_daily (period_date TEXT)")
    con.execute("INSERT INTO ts_daily VALUES ('20261014')")
    con.commit()
    con.close()

    deps = FeatureMaterializationDependencies(
        today=lambda: date(2026, 10, 14),
        resolve_universe=Mock(),
        filter_universe=Mock(),
        make_adapter=Mock(),
        require_complete_universe=Mock(),
        data_probe=Mock(return_value={"latest_trade_date": "2026-10-14"}),
        scoped_dataset_version=Mock(),
        feature_store=store,
        max_symbols=800,
        index_code="000300.SH",
        cache_dir=tmp_path,
        token="x",
    )
    result = materialize_pit_features(
        {"universe_mode": "pit_index", "start_date": "2024-01-02", "end_date": "2026-10-14", "catch_up": True},
        deps,
    )
    assert result["materialization"]["status"] == "up_to_date"
    deps.resolve_universe.assert_not_called()
    deps.make_adapter.assert_not_called()


def test_month_shard_resume_skips_verified_live_shard(tmp_path):
    store = FeatureStore(tmp_path / "f.sqlite3")
    start, end = date(2026, 10, 1), date(2026, 10, 14)
    sid = shard_id_for("000300.SH", start, end)
    store.upsert_warmup_shard(
        {
            "shard_id": sid,
            "index_code": "000300.SH",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "status": "completed",
            "symbol_count": 1,
            "completed_symbols": ["600000.SH"],
            "rows_written": 7,
            "data_version": "scoped",
            "data_source": "tushare_live",
        }
    )
    deps = FeatureMaterializationDependencies(
        today=lambda: date(2026, 10, 14),
        resolve_universe=Mock(),
        filter_universe=Mock(),
        make_adapter=Mock(),
        require_complete_universe=Mock(),
        data_probe=Mock(return_value={"latest_trade_date": "2026-10-14"}),
        scoped_dataset_version=Mock(),
        feature_store=store,
        max_symbols=800,
        index_code="000300.SH",
        cache_dir=tmp_path,
        token="x",
    )
    result = materialize_pit_features(
        {
            "universe_mode": "pit_index",
            "start_date": "2026-10-01",
            "end_date": "2026-10-14",
            "catch_up": False,
            "resume": True,
        },
        deps,
    )
    assert result["materialization"]["shards_resumed"] == 1
    assert result["materialization"]["shards_fetched"] == 0
    assert result["source"] == "verified_shards"
    deps.make_adapter.assert_not_called()


def test_in_progress_shard_fetches_only_pending_symbols(tmp_path):
    store = FeatureStore(tmp_path / "f.sqlite3")
    start, end = date(2026, 10, 1), date(2026, 10, 14)
    sid = shard_id_for("000300.SH", start, end)
    store.upsert_warmup_shard(
        {
            "shard_id": sid,
            "index_code": "000300.SH",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "status": "in_progress",
            "symbol_count": 2,
            "completed_symbols": ["600000.SH"],
            "rows_written": 3,
            "data_version": "old",
            "data_source": "tushare_live",
        }
    )
    bars = [
        Bar("600001.SH", date(2026, 9, 1), 10, 10, 10, 10, 100),
        Bar("600001.SH", date(2026, 10, 10), 10, 10, 10, 10, 100),
    ]
    snapshot = SimpleNamespace(
        version="tushare:live:hash",
        metadata={"data_source": "tushare_live", "latest_trade_date": "2026-10-14"},
    )
    adapter_instance = Mock()
    adapter_instance.daily_bars.return_value = bars
    adapter_instance.snapshot.return_value = snapshot
    make_adapter = Mock(return_value=adapter_instance)
    store.materialize = Mock(
        return_value={"status": "available", "rows": 7, "written": 7, "feature_start": "2026-10-01", "feature_end": "2026-10-14"}
    )
    universe = {
        "symbols": ["600000.SH", "600001.SH"],
        "version": "membership-v1",
        "metadata": {"mode": "pit_historical_index"},
        "start_date": date(2026, 6, 3),
        "end_date": date(2026, 10, 14),
    }
    deps = FeatureMaterializationDependencies(
        today=lambda: date(2026, 10, 14),
        resolve_universe=Mock(return_value=universe),
        filter_universe=Mock(return_value=bars),
        make_adapter=make_adapter,
        require_complete_universe=Mock(),
        data_probe=Mock(return_value={"latest_trade_date": "2026-10-14"}),
        scoped_dataset_version=Mock(return_value="scoped-v"),
        feature_store=store,
        max_symbols=800,
        index_code="000300.SH",
        cache_dir=tmp_path,
        token="tok",
    )
    result = materialize_pit_features(
        {
            "universe_mode": "pit_index",
            "start_date": "2026-10-01",
            "end_date": "2026-10-14",
            "catch_up": False,
            "resume": True,
        },
        deps,
    )
    make_adapter.assert_called_once()
    assert make_adapter.call_args.kwargs["symbols"] == ["600001.SH"]
    assert make_adapter.call_args.kwargs["force_refresh"] is True
    assert result["materialization"]["shards_fetched"] == 1
    assert result["source"] == "tushare_live"
    shard = store.get_warmup_shard(sid)
    assert shard["status"] == "completed"
    assert set(shard["completed_symbols"]) == {"600000.SH", "600001.SH"}


def test_rejects_non_live_batch(tmp_path):
    store = FeatureStore(tmp_path / "f.sqlite3")
    snapshot = SimpleNamespace(version="v", metadata={"data_source": "cache", "latest_trade_date": "2026-10-14"})
    adapter_instance = Mock()
    adapter_instance.daily_bars.return_value = []
    adapter_instance.snapshot.return_value = snapshot
    universe = {
        "symbols": ["600000.SH"],
        "version": "m",
        "metadata": {},
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 10, 14),
    }
    deps = FeatureMaterializationDependencies(
        today=lambda: date(2026, 10, 14),
        resolve_universe=Mock(return_value=universe),
        filter_universe=Mock(return_value=[]),
        make_adapter=Mock(return_value=adapter_instance),
        require_complete_universe=Mock(),
        data_probe=Mock(return_value={"latest_trade_date": "2026-10-14"}),
        scoped_dataset_version=Mock(return_value="scoped"),
        feature_store=store,
        max_symbols=800,
        index_code="000300.SH",
        cache_dir=tmp_path,
        token="t",
    )
    with pytest.raises(RuntimeError, match="真实 Tushare 拉取"):
        materialize_pit_features(
            {
                "universe_mode": "pit_index",
                "start_date": "2026-10-01",
                "end_date": "2026-10-14",
                "catch_up": False,
                "resume": False,
            },
            deps,
        )


def test_status_exposes_warmup_pending(tmp_path):
    store = FeatureStore(tmp_path / "f.sqlite3")
    import sqlite3

    con = sqlite3.connect(store.path)
    con.execute(
        "INSERT INTO feature_observations VALUES ('v','daily-price-features-v2','A','2026-09-29',"
        "'momentum_5',1,'2026-09-29T17:00:00+08:00','b','h')"
    )
    con.execute("CREATE TABLE ts_daily (period_date TEXT)")
    con.execute("INSERT INTO ts_daily VALUES ('20261014')")
    con.commit()
    con.close()
    status = store.status()
    assert status["max_feature_date"] == "2026-09-29"
    assert status["warmup"]["resume_from"] == "2026-09-30"
    assert status["warmup"]["pending_end"] == "2026-10-14"
    assert "2026-10" in status["warmup"]["pending_months"]
