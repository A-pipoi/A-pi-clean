"""Custom PIT feature adapter; the shared FeatureStore implementation is unchanged."""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, time

from quant.features.store import FEATURE_NAMES, SHANGHAI, FeatureStore

from .auth_store import validate_username
from .sqlite_support import connect_sqlite


class FeatureNamespace:
    def __init__(self, store: FeatureStore, username: str) -> None:
        self.store = store
        self.prefix = "u/" + validate_username(username) + "/"

    def name(self, name: str, *, custom: bool = False) -> str:
        if name.startswith("u/"):
            if not name.startswith(self.prefix):
                raise LookupError("feature not found")
            short = name[len(self.prefix):]
        elif name in FEATURE_NAMES and not custom:
            return name
        else:
            short = name
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", short):
            raise ValueError("invalid feature name")
        return self.prefix + short

    def write_rows(self, rows: Sequence[Mapping[str, object]], *, expression: str) -> int:
        if not expression.strip() or len(expression) > 4096 or len(rows) > 1000:
            raise ValueError("feature expression and at most 1000 observations are required")
        source_hash = hashlib.sha256(expression.strip().encode()).hexdigest()
        values: list[tuple[object, ...]] = []
        for row in rows:
            name = self.name(str(row.get("feature_name") or ""), custom=True)
            version = str(row.get("data_version") or "")
            symbol = str(row.get("symbol") or "").upper()
            day = date.fromisoformat(str(row.get("feature_date")))
            available = datetime.fromisoformat(str(row.get("available_at")))
            if available.tzinfo is None or available.astimezone(SHANGHAI).date() < day:
                raise ValueError("feature availability must be timezone-aware and not precede observation")
            value = float(str(row.get("feature_value")))
            if not math.isfinite(value) or not version or len(version) > 256 or not re.fullmatch(r"[A-Z0-9.]{1,24}", symbol):
                raise ValueError("invalid feature observation")
            if row.get("source_hash") not in {None, "", source_hash}:
                raise ValueError("source_hash does not match expression")
            values.append((version, "custom-pit-v1", symbol, day.isoformat(), name, value, available.astimezone(SHANGHAI).isoformat(), "user_supplied_pit_availability_unverified", source_hash))
        with closing(connect_sqlite(self.store.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = 0
            for record in values:
                sources = connection.execute("SELECT DISTINCT source_hash FROM feature_observations WHERE data_version=? AND feature_name=?", (record[0], record[4])).fetchall()
                if any(source[0] != source_hash for source in sources):
                    raise ValueError("source_hash conflict; use a new data_version")
                existing = connection.execute("SELECT feature_value, available_at, source_hash FROM feature_observations WHERE data_version=? AND schema_version=? AND symbol=? AND feature_date=? AND feature_name=?", record[:5]).fetchone()
                if existing:
                    if tuple(existing) != (record[5], record[6], record[8]):
                        raise ValueError("immutable feature observation conflict")
                    continue
                connection.execute("INSERT INTO feature_observations VALUES(?,?,?,?,?,?,?,?,?)", record)
                inserted += 1
        return inserted

    def read_rows(self, names: Sequence[str], *, data_version: str, before: date) -> list[dict[str, object]]:
        if not names or len(names) > 50:
            raise ValueError("request 1–50 feature names")
        resolved = [self.name(name) for name in names]
        cutoff = datetime.combine(before, time(9, 30), SHANGHAI)
        with closing(sqlite3.connect(self.store.path, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute("SELECT * FROM feature_observations WHERE data_version=? AND feature_name IN (" + ",".join("?" for _ in resolved) + ") ORDER BY feature_date,symbol LIMIT 1000", (data_version, *resolved)).fetchall()
        return [dict(row) for row in records if datetime.fromisoformat(row["available_at"]) < cutoff]
