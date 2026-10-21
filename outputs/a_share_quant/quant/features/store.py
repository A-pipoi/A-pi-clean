from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from ..data.schema import Bar
from ..errors import InputTypeError
from ..numeric import as_int

SHANGHAI = timezone(timedelta(hours=8))
FEATURE_SCHEMA_VERSION = "daily-price-features-v2"
FEATURE_NAMES = (
    "momentum_5",
    "momentum_20",
    "momentum_60",
    "volatility_20",
    "volatility_60",
    "range_20",
    "volume_ratio_20",
)


class FeatureStore:
    """Append-only SQLite feature warehouse partitioned by data and schema version.

    Feature observations are created after the close and are only returned to a
    decision date when they were available before that session opened. Existing
    rows are immutable: a changed value requires a new ``data_version``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_observations (
                    data_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    feature_date TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    feature_value REAL NOT NULL,
                    available_at TEXT NOT NULL,
                    availability_basis TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    PRIMARY KEY (data_version, schema_version, symbol, feature_date, feature_name)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feature_lookup "
                "ON feature_observations(data_version, schema_version, symbol, feature_date)"
            )
            # Additive, backward-compatible metadata (freshness visibility only).
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_warmup_shards (
                    shard_id TEXT PRIMARY KEY,
                    index_code TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symbol_count INTEGER NOT NULL DEFAULT 0,
                    completed_symbols TEXT NOT NULL DEFAULT '[]',
                    rows_written INTEGER NOT NULL DEFAULT 0,
                    data_version TEXT NOT NULL DEFAULT '',
                    data_source TEXT NOT NULL DEFAULT '',
                    verified_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_warmup_shards_range "
                "ON feature_warmup_shards(index_code, start_date, end_date, status)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def write_rows(self, rows: Iterable[Mapping[str, object]]) -> int:
        materialized: list[tuple[object, ...]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            feature_name = str(row.get("feature_name") or "").strip()
            data_version = str(row.get("data_version") or "").strip()
            feature_day = _parse_day(row.get("feature_date"))
            available_at = _parse_timestamp(row.get("available_at"))
            value = _finite(row.get("feature_value"))
            if not symbol or not feature_name or not data_version or feature_day is None:
                raise ValueError("feature row requires symbol, feature name, data version, and feature date")
            if available_at is None or available_at.date() < feature_day:
                raise ValueError("feature availability must be timezone-aware and not precede its observation date")
            if value is None:
                raise ValueError("feature values must be finite numbers")
            if feature_name not in FEATURE_NAMES:
                raise ValueError(f"unsupported feature name: {feature_name}")
            materialized.append((
                data_version,
                FEATURE_SCHEMA_VERSION,
                symbol,
                feature_day.isoformat(),
                feature_name,
                value,
                available_at.isoformat(),
                str(row.get("availability_basis") or "daily_close_plus_17_00_china"),
                str(row.get("source_hash") or ""),
            ))
        if not materialized:
            return 0
        inserted = 0
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for values in materialized:
                existing = connection.execute(
                    """SELECT feature_value, available_at, source_hash FROM feature_observations
                       WHERE data_version=? AND schema_version=? AND symbol=? AND feature_date=? AND feature_name=?""",
                    values[:5],
                ).fetchone()
                if existing is not None:
                    materialized_value = _finite(values[5])
                    if materialized_value is None or (float(existing[0]), str(existing[1]), str(existing[2])) != (
                        materialized_value, str(values[6]), str(values[8])
                    ):
                        raise ValueError("immutable feature row conflicts; use a new data_version")
                    continue
                connection.execute(
                    """INSERT INTO feature_observations
                       (data_version, schema_version, symbol, feature_date, feature_name,
                        feature_value, available_at, availability_basis, source_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                inserted += 1
        return inserted

    def materialize(
        self,
        bars: Sequence[Bar],
        data_version: str,
        *,
        feature_start: date | None = None,
        feature_end: date | None = None,
    ) -> dict[str, object]:
        if not data_version.strip():
            raise ValueError("data_version is required to materialize features")
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in bars:
            grouped[bar.symbol.upper()].append(bar)
        rows: list[dict[str, object]] = []
        for symbol, history in grouped.items():
            history.sort(key=lambda item: item.date)
            symbol_rows = materialize_price_features(history, data_version=data_version, symbol=symbol)
            if feature_start is not None or feature_end is not None:
                filtered: list[dict[str, object]] = []
                for row in symbol_rows:
                    feature_day = _parse_day(row.get("feature_date"))
                    if feature_day is None:
                        continue
                    if feature_start is not None and feature_day < feature_start:
                        continue
                    if feature_end is not None and feature_day > feature_end:
                        continue
                    filtered.append(row)
                symbol_rows = filtered
            rows.extend(symbol_rows)
        written = self.write_rows(rows)
        return {
            "status": "available" if rows else "insufficient_history",
            "schema_version": FEATURE_SCHEMA_VERSION,
            "data_version": data_version,
            "symbols": len(grouped),
            "rows": len(rows),
            "written": written,
            "feature_start": feature_start.isoformat() if feature_start else None,
            "feature_end": feature_end.isoformat() if feature_end else None,
        }

    def features_for_symbol(self, symbol: str, *, before: date, data_version: str) -> dict[date, dict[str, float]]:
        """Return complete vectors available before the next session's 09:30 cutoff."""
        result = self.features_for_symbols([symbol], before=before, data_version=data_version)
        return result.get(symbol.strip().upper(), {})

    def features_for_symbols(
        self,
        symbols: Sequence[str],
        *,
        before: date,
        data_version: str,
        after: date | None = None,
    ) -> dict[str, dict[date, dict[str, float]]]:
        """Fetch a bounded multi-symbol window with one SQLite connection."""
        normalized_symbols = list(dict.fromkeys(str(item).strip().upper() for item in symbols if str(item).strip()))
        if not normalized_symbols:
            return {}
        cutoff = datetime.combine(before, time(9, 30), tzinfo=SHANGHAI)
        output: dict[str, dict[date, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        with closing(self._connect()) as connection, connection:
            for offset in range(0, len(normalized_symbols), 800):
                batch = normalized_symbols[offset : offset + 800]
                placeholders = ",".join("?" for _ in batch)
                date_filter = " AND feature_date>=?" if after is not None else ""
                parameters: tuple[object, ...] = (
                    data_version, FEATURE_SCHEMA_VERSION, *batch, before.isoformat(),
                    *((after.isoformat(),) if after is not None else ()),
                )
                rows = connection.execute(
                    f"""SELECT symbol, feature_date, feature_name, feature_value, available_at
                        FROM feature_observations
                        WHERE data_version=? AND schema_version=? AND symbol IN ({placeholders})
                          AND feature_date<?{date_filter}
                        ORDER BY symbol, feature_date, feature_name""",
                    parameters,
                ).fetchall()
                for symbol, day_text, name, value, available_text in rows:
                    available_at = _parse_timestamp(available_text)
                    if available_at is None or available_at >= cutoff:
                        continue
                    output[str(symbol)][date.fromisoformat(day_text)][str(name)] = float(value)
        return {
            symbol: {day: vector for day, vector in vectors.items() if all(name in vector for name in FEATURE_NAMES)}
            for symbol, vectors in output.items()
        }

    def record_materialization(
        self,
        *,
        data_version: str,
        written: int,
        materialized_at: datetime | None = None,
    ) -> str:
        """Persist the wall-clock time of a successful PIT warm-up."""
        moment = (materialized_at or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        stamp = moment.isoformat(timespec="seconds")
        values = {
            "last_materialized_at": stamp,
            "last_materialized_data_version": str(data_version),
            "last_materialized_written": str(int(written)),
        }
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS feature_store_meta "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO feature_store_meta(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                [(key, value, stamp) for key, value in values.items()],
            )
        return stamp

    def get_warmup_shard(self, shard_id: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection, connection:
            if not _table_exists(connection, "feature_warmup_shards"):
                return None
            found = connection.execute(
                "SELECT shard_id, index_code, start_date, end_date, status, symbol_count, "
                "completed_symbols, rows_written, data_version, data_source, verified_at, updated_at "
                "FROM feature_warmup_shards WHERE shard_id=?",
                (shard_id,),
            ).fetchone()
        if not found:
            return None
        try:
            completed = json.loads(found[6] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            completed = []
        if not isinstance(completed, list):
            completed = []
        return {
            "shard_id": found[0],
            "index_code": found[1],
            "start_date": found[2],
            "end_date": found[3],
            "status": found[4],
            "symbol_count": int(found[5] or 0),
            "completed_symbols": [str(item).upper() for item in completed],
            "rows_written": int(found[7] or 0),
            "data_version": found[8],
            "data_source": found[9],
            "verified_at": found[10],
            "updated_at": found[11],
        }

    def upsert_warmup_shard(self, shard: Mapping[str, object]) -> None:
        shard_id = str(shard.get("shard_id") or "").strip()
        if not shard_id:
            raise ValueError("shard_id is required")
        completed = shard.get("completed_symbols") or []
        if not isinstance(completed, list):
            raise InputTypeError("completed_symbols must be a list")
        stamp = datetime.now(SHANGHAI).isoformat(timespec="seconds")
        values = (
            shard_id,
            str(shard.get("index_code") or ""),
            str(shard.get("start_date") or ""),
            str(shard.get("end_date") or ""),
            str(shard.get("status") or "in_progress"),
            as_int(shard.get("symbol_count") or 0),
            json.dumps([str(item).upper() for item in completed], ensure_ascii=False),
            as_int(shard.get("rows_written") or 0),
            str(shard.get("data_version") or ""),
            str(shard.get("data_source") or ""),
            str(shard.get("verified_at") or stamp),
            stamp,
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_warmup_shards (
                    shard_id TEXT PRIMARY KEY,
                    index_code TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symbol_count INTEGER NOT NULL DEFAULT 0,
                    completed_symbols TEXT NOT NULL DEFAULT '[]',
                    rows_written INTEGER NOT NULL DEFAULT 0,
                    data_version TEXT NOT NULL DEFAULT '',
                    data_source TEXT NOT NULL DEFAULT '',
                    verified_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO feature_warmup_shards(
                    shard_id, index_code, start_date, end_date, status, symbol_count,
                    completed_symbols, rows_written, data_version, data_source, verified_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shard_id) DO UPDATE SET
                    status=excluded.status,
                    symbol_count=excluded.symbol_count,
                    completed_symbols=excluded.completed_symbols,
                    rows_written=excluded.rows_written,
                    data_version=excluded.data_version,
                    data_source=excluded.data_source,
                    verified_at=excluded.verified_at,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def warmup_progress(
        self, *, index_code: str, resume_from: date | None = None, pending_end: date | None = None
    ) -> dict[str, object]:
        """Summarize verified shards and pending catch-up months (best-effort)."""
        completed = 0
        in_progress = None
        with closing(self._connect()) as connection, connection:
            if _table_exists(connection, "feature_warmup_shards"):
                completed = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM feature_warmup_shards WHERE index_code=? AND status='completed'",
                        (index_code,),
                    ).fetchone()[0]
                )
                row = connection.execute(
                    "SELECT shard_id, start_date, end_date, status, completed_symbols, symbol_count "
                    "FROM feature_warmup_shards WHERE index_code=? AND status='in_progress' "
                    "ORDER BY start_date LIMIT 1",
                    (index_code,),
                ).fetchone()
                if row:
                    try:
                        done = json.loads(row[4] or "[]")
                        done_n = len(done) if isinstance(done, list) else 0
                    except (TypeError, ValueError, json.JSONDecodeError):
                        done_n = 0
                    in_progress = {
                        "shard_id": row[0],
                        "start_date": row[1],
                        "end_date": row[2],
                        "status": row[3],
                        "completed_symbols": done_n,
                        "symbol_count": int(row[5] or 0),
                    }
        pending_months: list[str] = []
        if resume_from is not None and pending_end is not None and resume_from <= pending_end:
            cursor = date(resume_from.year, resume_from.month, 1)
            last = date(pending_end.year, pending_end.month, 1)
            while cursor <= last:
                pending_months.append(f"{cursor.year:04d}-{cursor.month:02d}")
                if cursor.month == 12:
                    cursor = date(cursor.year + 1, 1, 1)
                else:
                    cursor = date(cursor.year, cursor.month + 1, 1)
        return {
            "completed_shards": completed,
            "in_progress_shard": in_progress,
            "resume_from": resume_from.isoformat() if resume_from else None,
            "pending_end": pending_end.isoformat() if pending_end else None,
            "pending_months": pending_months,
        }

    def status(self) -> dict[str, object]:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol), COUNT(DISTINCT data_version) FROM feature_observations"
            ).fetchone()
            freshness = _freshness(connection)
        resume_from = None
        pending_end = None
        max_feature = _parse_day(freshness.get("current_schema_max_feature_date"))
        latest_trade = _parse_day(freshness.get("latest_trade_date"))
        if max_feature is not None:
            resume_from = max_feature + timedelta(days=1)
        if latest_trade is not None:
            pending_end = latest_trade
        warmup = self.warmup_progress(
            index_code="000300.SH", resume_from=resume_from, pending_end=pending_end
        )
        return {
            "status": "available" if int(row[0]) else "empty",
            "schema_version": FEATURE_SCHEMA_VERSION,
            "rows": int(row[0]),
            "symbols": int(row[1]),
            "data_versions": int(row[2]),
            "path": str(self.path),
            **freshness,
            "warmup": warmup,
        }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _freshness(connection: sqlite3.Connection) -> dict[str, object]:
    """Best-effort freshness fields; any failure degrades to None, never raises."""
    result: dict[str, object] = {
        "max_feature_date": None,
        "lagging_feature_date": None,
        "current_schema_max_feature_date": None,
        "last_materialized_at": None,
        "latest_trade_date": None,
        "lag_days": None,
        "lag_unit": None,
    }
    try:
        # Covering-index scan (idx_feature_lookup); a bare MAX(feature_date) is a full scan.
        found = connection.execute(
            "SELECT MAX(m) FROM (SELECT MAX(feature_date) AS m FROM feature_observations "
            "GROUP BY data_version, schema_version, symbol)"
        ).fetchone()
        result["max_feature_date"] = str(found[0])[:10] if found and found[0] else None
        found = connection.execute(
            "SELECT MIN(m), MAX(m) FROM (SELECT MAX(feature_date) AS m FROM feature_observations "
            "WHERE schema_version=? GROUP BY symbol)",
            (FEATURE_SCHEMA_VERSION,),
        ).fetchone()
        if found and found[0]:
            result["lagging_feature_date"] = str(found[0])[:10]
            result["current_schema_max_feature_date"] = str(found[1])[:10]
    except sqlite3.Error:
        pass
    try:
        if _table_exists(connection, "feature_store_meta"):
            found = connection.execute(
                "SELECT value FROM feature_store_meta WHERE key='last_materialized_at'"
            ).fetchone()
            result["last_materialized_at"] = str(found[0]) if found else None
    except sqlite3.Error:
        pass
    try:
        if _table_exists(connection, "ts_daily"):
            found = connection.execute("SELECT MAX(period_date) FROM ts_daily").fetchone()
            text = str(found[0] or "") if found else ""
            if len(text) == 8 and text.isdigit():
                result["latest_trade_date"] = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    except sqlite3.Error:
        pass
    feature_day = _parse_day(result["current_schema_max_feature_date"]) if result["current_schema_max_feature_date"] else None
    trade_day = _parse_day(result["latest_trade_date"]) if result["latest_trade_date"] else None
    if feature_day is not None and trade_day is not None:
        lag: int | None = None
        try:
            if _table_exists(connection, "ts_trade_cal"):
                covered = connection.execute(
                    "SELECT COUNT(*) FROM ts_trade_cal WHERE json_extract(payload_json,'$.exchange')='SSE' "
                    "AND json_extract(payload_json,'$.cal_date')<=?",
                    (trade_day.strftime("%Y%m%d"),),
                ).fetchone()
                if covered and int(covered[0]):
                    found = connection.execute(
                        "SELECT COUNT(*) FROM ts_trade_cal WHERE json_extract(payload_json,'$.exchange')='SSE' "
                        "AND CAST(json_extract(payload_json,'$.is_open') AS INTEGER)=1 "
                        "AND json_extract(payload_json,'$.cal_date')>? AND json_extract(payload_json,'$.cal_date')<=?",
                        (feature_day.strftime("%Y%m%d"), trade_day.strftime("%Y%m%d")),
                    ).fetchone()
                    lag = int(found[0])
                    result["lag_unit"] = "trading_days"
        except sqlite3.Error:
            lag = None
        if lag is None:
            lag = max((trade_day - feature_day).days, 0)
            result["lag_unit"] = "calendar_days"
        result["lag_days"] = lag
    return result


def materialize_price_features(
    bars: Sequence[Bar], *, data_version: str, symbol: str | None = None
) -> list[dict[str, object]]:
    ordered = sorted(bars, key=lambda item: item.date)
    output: list[dict[str, object]] = []
    for index, current in enumerate(ordered):
        values = _feature_vector(ordered, index)
        if values is None:
            continue
        source = {
            "symbol": (symbol or current.symbol).upper(),
            "date": current.date.isoformat(),
            "ohlcv": [current.open, current.high, current.low, current.close, current.volume],
            "schema": FEATURE_SCHEMA_VERSION,
        }
        source_hash = hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        available_at = datetime.combine(current.date, time(17, 0), tzinfo=SHANGHAI)
        for name, value in values.items():
            output.append({
                "symbol": (symbol or current.symbol).upper(),
                "feature_date": current.date,
                "feature_name": name,
                "feature_value": value,
                "available_at": available_at,
                "availability_basis": "daily_close_plus_17_00_china",
                "source_hash": source_hash,
                "data_version": data_version,
            })
    return output


def _signal_price(bar: Bar, raw: float, adjusted: float | None) -> float:
    return float(adjusted if adjusted is not None else raw)


def _signal_volume(bar: Bar) -> float:
    volume = float(bar.volume)
    if bar.signal_close is not None and bar.close > 0 and bar.signal_close > 0:
        return volume * float(bar.close) / float(bar.signal_close)
    return volume


def _feature_vector(bars: Sequence[Bar], index: int) -> dict[str, float] | None:
    current = bars[index]
    if current.close <= 0 or index < 60:
        return None
    window = bars[: index + 1]
    closes = [
        _signal_price(item, item.close, item.signal_close) for item in window
    ]
    highs = [_signal_price(item, item.high, item.signal_high) for item in window]
    lows = [_signal_price(item, item.low, item.signal_low) for item in window]
    volumes = [_signal_volume(item) for item in window]
    if any(value <= 0 for value in closes[-61:]):
        return None
    returns = [right / left - 1.0 for left, right in zip(closes[-61:-1], closes[-60:]) if left > 0]
    if len(returns) < 60:
        return None
    result = {
        "momentum_5": closes[-1] / closes[-6] - 1.0,
        "momentum_20": closes[-1] / closes[-21] - 1.0,
        "momentum_60": closes[-1] / closes[-61] - 1.0,
        "volatility_20": _sample_std(returns[-20:]),
        "volatility_60": _sample_std(returns),
        "range_20": (max(highs[-20:]) - min(lows[-20:])) / closes[-1],
        "volume_ratio_20": volumes[-1] / (sum(volumes[-21:-1]) / 20.0) if sum(volumes[-21:-1]) > 0 else 0.0,
    }
    return result if all(math.isfinite(value) for value in result.values()) else None


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_day(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(SHANGHAI)
