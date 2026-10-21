"""Cross-process sliding-minute reservation; shared SQLite, no in-memory loophole."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


def reserve(path: Path, endpoint: str, now: float | None = None) -> float:
    """Return wait seconds, or atomically reserve one outbound attempt."""
    moment = time.time() if now is None else now
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS provider_minute_calls (at REAL NOT NULL, endpoint TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM provider_minute_calls WHERE at<=?', (moment - 60,))
        for target, maximum in (('', 500), ('stk_factor', 100), ('fund_portfolio', 200)):
            if target and endpoint != target:
                continue
            rows = db.execute('SELECT at FROM provider_minute_calls WHERE (?=\'\' OR endpoint=?) ORDER BY at', (target, target)).fetchall()
            if len(rows) >= maximum:
                return max(0.01, float(rows[len(rows) - maximum][0]) + 60 - moment)
        db.execute('INSERT INTO provider_minute_calls VALUES(?,?)', (moment, endpoint))
    return 0.0


def acquire(path: Path, endpoint: str) -> None:
    while True:
        delay = reserve(path, endpoint)
        if not delay:
            return
        time.sleep(min(delay, 1))
