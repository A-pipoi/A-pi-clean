"""SQLite-serialized login reservations survive restarts and parallel requests."""
from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from .sqlite_support import connect_sqlite


class LoginLimits:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS login_attempts(id TEXT, key TEXT, at REAL, state TEXT, PRIMARY KEY(id,key))")
            db.execute("CREATE INDEX IF NOT EXISTS login_attempt_key ON login_attempts(key,at)")
            db.execute('CREATE TABLE IF NOT EXISTS login_locks(key TEXT PRIMARY KEY,until REAL NOT NULL)')
            # Old global username locks must not survive the policy upgrade.
            db.execute("DELETE FROM login_locks WHERE key LIKE 'user:%'")
            db.execute("DELETE FROM login_attempts WHERE key LIKE 'user:%'")

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)

    @staticmethod
    def keys(username: str, ip: str) -> tuple[str, str]:
        pair = hashlib.sha256((username.lower() + '\0' + ip).encode()).hexdigest()
        return "pair:" + pair, "ip:" + ip

    def _cleanup(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM login_attempts WHERE at<=? OR (state='pending' AND at<=?)", (now - 900, now - 60))
        db.execute('DELETE FROM login_locks WHERE until<=?', (now,))

    def reserve(self, username: str, ip: str) -> tuple[str | None, int]:
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            delay = self._delay(db, username, ip, now)
            if delay:
                return None, delay
            identity = uuid.uuid4().hex
            db.executemany("INSERT INTO login_attempts VALUES(?,?,?,'pending')", ((identity, key, now) for key in self.keys(username, ip)))
            return identity, 0

    def _delay(self, db: sqlite3.Connection, username: str, ip: str, now: float) -> int:
        delays = []
        for key in self.keys(username, ip):
            lock = db.execute('SELECT until FROM login_locks WHERE key=?', (key,)).fetchone()
            if lock:
                delays.append(max(1, int(lock[0] - now)))
            count, newest = db.execute("SELECT COUNT(*),MAX(at) FROM login_attempts WHERE key=?", (key,)).fetchone()
            if count >= 5:
                delays.append(max(1, int(900 - (now - newest))))
        return max(delays, default=0)

    def locked_seconds(self, username: str, ip: str) -> int:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            self._cleanup(db, now)
            return self._delay(db, username, ip, now)

    def finish(self, identity: str, *, successful: bool) -> None:
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if successful:
                db.execute("DELETE FROM login_attempts WHERE id=?", (identity,))
            else:
                db.execute("UPDATE login_attempts SET state='failed' WHERE id=?", (identity,))
                keys = db.execute('SELECT key FROM login_attempts WHERE id=?', (identity,)).fetchall()
                now = time.time()
                for (key,) in keys:
                    if db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=? AND state='failed' AND at>?", (key, now - 900)).fetchone()[0] >= 5:
                        db.execute('INSERT OR REPLACE INTO login_locks VALUES(?,?)', (key, now + 900))

    def failed(self, username: str, ip: str) -> None:
        identity, _delay = self.reserve(username, ip)
        if identity:
            self.finish(identity, successful=False)

    def cleanup(self) -> None:
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self._cleanup(db, time.time())
