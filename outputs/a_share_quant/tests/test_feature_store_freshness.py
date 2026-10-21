import sqlite3
from datetime import UTC, datetime

from quant.features.store import FeatureStore


def test_status_freshness_backward_compatible_and_recorded(tmp_path):
    path = tmp_path / "f.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE feature_observations (data_version TEXT NOT NULL, schema_version TEXT NOT NULL, symbol TEXT NOT NULL, feature_date TEXT NOT NULL, feature_name TEXT NOT NULL, feature_value REAL NOT NULL, available_at TEXT NOT NULL, availability_basis TEXT NOT NULL, source_hash TEXT NOT NULL, PRIMARY KEY (data_version, schema_version, symbol, feature_date, feature_name))")
    con.execute("INSERT INTO feature_observations VALUES ('v','daily-price-features-v1','A','2026-09-29','momentum_5',1,'2026-09-29T17:00:00+08:00','b','h')")
    con.commit(); con.close()
    store = FeatureStore(path)  # old DB without meta table / ts_* tables
    status = store.status()
    assert status["rows"] == 1 and status["max_feature_date"] == "2026-09-29"
    assert status["last_materialized_at"] is None and status["latest_trade_date"] is None and status["lag_days"] is None
    stamp = store.record_materialization(data_version="v", written=1, materialized_at=datetime(2026, 10, 15, 1, 0, tzinfo=UTC))
    assert stamp == "2026-10-15T09:00:00+08:00" and store.status()["last_materialized_at"] == stamp

def test_status_trading_day_lag(tmp_path):
    path = tmp_path / "f.sqlite3"
    store = FeatureStore(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO feature_observations VALUES ('v','daily-price-features-v2','A','2026-09-29','momentum_5',1,'2026-09-29T17:00:00+08:00','b','h')")
    con.execute("CREATE TABLE ts_daily (period_date TEXT, payload_json TEXT)")
    con.execute("INSERT INTO ts_daily VALUES ('20261014','{}')")
    con.execute("CREATE TABLE ts_trade_cal (period_date TEXT, payload_json TEXT)")
    for d, open_ in [("20260929",1),("20260930",1),("20261001",0),("20261009",1),("20261014",1)]:
        con.execute("INSERT INTO ts_trade_cal VALUES (NULL, ?)", (f'{{"cal_date":"{d}","exchange":"SSE","is_open":{open_}}}',))
    con.commit(); con.close()
    status = store.status()
    assert status["latest_trade_date"] == "2026-10-14" and status["lag_days"] == 3 and status["lag_unit"] == "trading_days"
