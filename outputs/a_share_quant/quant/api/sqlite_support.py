"""Bounded retries at transaction entry, never replay a transaction body."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Literal


def _transient(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
    return code in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_READONLY,
    } or any(
        word in str(error).lower()
        for word in ("locked", "busy", "readonly", "read-only")
    )


class RetryConnection(sqlite3.Connection):
    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        entry = sql.strip().rstrip(";").upper() in {
            "BEGIN IMMEDIATE",
            "PRAGMA JOURNAL_MODE=WAL",
        }
        for attempt in range(3):
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as error:
                if (
                    not entry
                    or self.in_transaction
                    or not _transient(error)
                    or attempt == 2
                ):
                    raise
                time.sleep(0.05 * 2**attempt)
        raise AssertionError("Unreachable retry exit")


def connect_sqlite(
    path: str | Path,
    *,
    timeout: float = 10,
    isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = "DEFERRED",
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path, timeout=timeout, isolation_level=isolation_level, factory=RetryConnection
    )
    try:
        connection.execute("PRAGMA busy_timeout=10000")
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise sqlite3.OperationalError("Writable store requires WAL journaling")
        return connection
    except BaseException:
        connection.close()
        raise
