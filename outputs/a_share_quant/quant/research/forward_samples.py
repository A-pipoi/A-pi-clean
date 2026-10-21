"""Unapproved forward training observations, separate from budgets and seals."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import aware, canonical, digest, validate_features


class ForwardSamples:
    def __init__(self, path: Path, *, version: str, now: datetime):
        if now.utcoffset() is None or not version:
            raise ValueError("Aware observation time and fixed version required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS plan(id INTEGER PRIMARY KEY CHECK(id=1),
                    version TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(hash TEXT PRIMARY KEY,blob BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS membership(day TEXT PRIMARY KEY,
                    symbols TEXT NOT NULL,available_at TEXT NOT NULL,source_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(day TEXT,instrument TEXT,
                    features TEXT NOT NULL,price REAL NOT NULL,available_at TEXT NOT NULL,
                    membership_available_at TEXT NOT NULL,source_hash TEXT NOT NULL,
                    PRIMARY KEY(day,instrument));
                CREATE TABLE IF NOT EXISTS market(day TEXT PRIMARY KEY,prices TEXT NOT NULL,
                    available_at TEXT NOT NULL,source_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS labels(day TEXT,instrument TEXT,horizon INTEGER,
                    label_end TEXT NOT NULL,value REAL NOT NULL,available_at TEXT NOT NULL,
                    source_hash TEXT NOT NULL,PRIMARY KEY(day,instrument,horizon));
                CREATE TABLE IF NOT EXISTS calendars(day TEXT PRIMARY KEY,sessions TEXT NOT NULL,
                    available_at TEXT NOT NULL,source_hash TEXT NOT NULL);
            """)
            db.execute(
                "INSERT OR IGNORE INTO plan VALUES(1,?,?)", (version, now.isoformat())
            )
            saved = db.execute("SELECT version FROM plan WHERE id=1").fetchone()[0]
            if saved != version:
                raise PermissionError(
                    "Forward study version changed; preserve original study"
                )

    def _time(self, db: sqlite3.Connection, day: str, now: datetime) -> datetime:
        created = aware(
            db.execute("SELECT created_at FROM plan WHERE id=1").fetchone()[0]
        )
        if (
            now.utcoffset() is None
            or now < created
            or now.astimezone(SHANGHAI).date().isoformat() != day
        ):
            raise PermissionError(
                "Only actual current-day observations after study creation"
            )
        return now.astimezone(SHANGHAI)

    @staticmethod
    def _source(db: sqlite3.Connection, blob: bytes) -> str:
        key = digest(blob)
        db.execute("INSERT OR IGNORE INTO sources VALUES(?,?)", (key, blob))
        return key

    def membership(
        self, day: str, symbols: list[str], blob: bytes, now: datetime
    ) -> None:
        if not symbols or symbols != sorted(set(symbols)):
            raise ValueError("Unique nonempty observed universe required")
        with closing(sqlite3.connect(self.path)) as db, db:
            local = self._time(db, day, now)
            if not 8 <= local.hour < 15:
                raise PermissionError(
                    "Membership must be observed before the decision cutoff"
                )
            key = self._source(db, blob)
            db.execute(
                "INSERT OR IGNORE INTO membership VALUES(?,?,?,?)",
                (day, json.dumps(symbols), now.isoformat(), key),
            )

    def universe(self, day: str) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT symbols FROM membership WHERE day=?", (day,)
            ).fetchone()
        return json.loads(row[0]) if row else []

    def calendar(self, sessions: list[str], blob: bytes, now: datetime) -> None:
        day = now.astimezone(SHANGHAI).date().isoformat()
        if sessions != sorted(set(sessions)) or any(
            session > day for session in sessions
        ):
            raise PermissionError("Actual nonfuture exchange calendar required")
        with closing(sqlite3.connect(self.path)) as db, db:
            self._time(db, day, now)
            key = self._source(db, blob)
            db.execute(
                "INSERT OR IGNORE INTO calendars VALUES(?,?,?,?)",
                (day, json.dumps(sessions), now.isoformat(), key),
            )

    def capture(
        self,
        day: str,
        rows: list[dict],
        prices: dict[str, float],
        blob: bytes,
        now: datetime,
    ) -> None:
        validate_features(rows)
        with closing(sqlite3.connect(self.path)) as db, db:
            if self._time(db, day, now).hour < 17:
                raise PermissionError("Confirmed close observations required")
            if db.execute("SELECT 1 FROM market WHERE day=?", (day,)).fetchone():
                return  # First complete day is immutable, including missing symbols.
            member = db.execute(
                "SELECT symbols,available_at FROM membership WHERE day=?", (day,)
            ).fetchone()
            if not member:
                raise PermissionError("No actual pre-close membership observation")
            symbols = set(json.loads(member[0]))
            if any(
                r["instrument"] not in symbols or r["instrument"] not in prices
                for r in rows
            ):
                raise PermissionError("Observed feature universe mismatch")
            if len(rows) / len(symbols) < 0.9:
                raise ValueError("Forward feature coverage insufficient")
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or v <= 0
                for v in prices.values()
            ):
                raise ValueError("Finite positive observed prices required")
            key = self._source(db, blob)
            db.execute(
                "INSERT OR IGNORE INTO market VALUES(?,?,?,?)",
                (day, json.dumps(prices, sort_keys=True), now.isoformat(), key),
            )
            db.executemany(
                "INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        day,
                        r["instrument"],
                        json.dumps(r["features"]),
                        prices[r["instrument"]],
                        now.isoformat(),
                        member[1],
                        key,
                    )
                    for r in rows
                ],
            )

    def settle(self, sessions: list[str], now: datetime) -> int:
        """Only already observed endpoints, never a provider fetch or a replay."""
        if sessions != sorted(set(sessions)) or now.utcoffset() is None:
            raise ValueError("Ascending real-session calendar and aware time required")
        if any(day > now.astimezone(SHANGHAI).date().isoformat() for day in sessions):
            raise PermissionError("Future trading sessions rejected")
        positions = {day: index for index, day in enumerate(sessions)}
        with closing(sqlite3.connect(self.path)) as db, db:
            before = db.total_changes
            for day, symbol, price in db.execute(
                "SELECT day,instrument,price FROM samples"
            ).fetchall():
                if day not in positions:
                    continue
                for horizon in (5, 10, 20):
                    index = positions[day] + horizon
                    if index >= len(sessions):
                        continue
                    end = sessions[index]
                    market = db.execute(
                        "SELECT prices,available_at,source_hash FROM market WHERE day=?",
                        (end,),
                    ).fetchone()
                    if not market or aware(market[1]) > now:
                        continue
                    endpoint = json.loads(market[0]).get(symbol)
                    if endpoint is None:
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO labels VALUES(?,?,?,?,?,?,?)",
                        (
                            day,
                            symbol,
                            horizon,
                            end,
                            endpoint / price - 1,
                            now.isoformat(),
                            market[2],
                        ),
                    )
            return db.total_changes - before

    def status(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            plan = db.execute(
                "SELECT version,created_at FROM plan WHERE id=1"
            ).fetchone()
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("membership", "samples", "labels")
            }
        return {
            "version": plan[0],
            "created_at": plan[1],
            **counts,
            "approved_for_training": False,
            "formal_acceptance": False,
        }


def receipt_blob(features: dict, market: bytes) -> bytes:
    return canonical({"features": features, "market": json.loads(market)})
