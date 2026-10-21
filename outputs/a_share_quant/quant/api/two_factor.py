"""Opt-in TOTP; atomic step/recovery consumption prevents replay."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import struct
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote, urlencode

from .sqlite_support import connect_sqlite


def totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
    return f'{value:06d}'


class TwoFactor:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS second_factor(user_id INTEGER PRIMARY KEY,secret TEXT,enabled INTEGER NOT NULL DEFAULT 0,pending TEXT,pending_at REAL,last_step INTEGER NOT NULL DEFAULT -1)')
            db.execute('CREATE TABLE IF NOT EXISTS recovery_codes(user_id INTEGER,hash TEXT,PRIMARY KEY(user_id,hash))')

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)

    def enabled(self, user_id: int) -> bool:
        with closing(self._connect()) as db:
            row = db.execute('SELECT enabled FROM second_factor WHERE user_id=?', (user_id,)).fetchone()
            return bool(row and row[0])

    def setup(self, user_id: int, username: str) -> dict[str, str]:
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
        with closing(self._connect()) as db, db:
            db.execute('INSERT INTO second_factor(user_id,pending,pending_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET pending=excluded.pending,pending_at=excluded.pending_at', (user_id, secret, time.time()))
        return {'secret': secret, 'otpauth_uri': 'otpauth://totp/' + quote('A-pi:' + username, safe='') + '?' + urlencode({'secret': secret, 'issuer': 'A-pi', 'algorithm': 'SHA1', 'digits': 6, 'period': 30})}

    @staticmethod
    def _step(secret: str, code: str, last_step: int = -1) -> int | None:
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            return None
        current = int(time.time()) // 30
        for step in (current, current - 1, current + 1):
            if step > last_step and hmac.compare_digest(totp(secret, step), code):
                return step
        return None

    def enable(self, user_id: int, code: str) -> list[str] | None:
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT pending,pending_at FROM second_factor WHERE user_id=?', (user_id,)).fetchone()
            if not row or not row[0] or row[1] < time.time() - 600:
                return None
            step = self._step(row[0], code)
            if step is None:
                return None
            codes = [secrets.token_hex(8) for _ in range(8)]
            db.execute('UPDATE second_factor SET secret=pending,pending=NULL,pending_at=NULL,enabled=1,last_step=? WHERE user_id=?', (step, user_id))
            db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))
            db.executemany('INSERT INTO recovery_codes VALUES(?,?)', ((user_id, hashlib.sha256(value.encode()).hexdigest()) for value in codes))
            return codes

    def verify(self, user_id: int, code: str) -> bool:
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT secret,enabled,last_step FROM second_factor WHERE user_id=?', (user_id,)).fetchone()
            if not row or not row[1]:
                return True
            step = self._step(row[0], code, row[2])
            if step is not None:
                db.execute('UPDATE second_factor SET last_step=? WHERE user_id=?', (step, user_id))
                return True
            digest = hashlib.sha256(code.encode()).hexdigest()
            return db.execute('DELETE FROM recovery_codes WHERE user_id=? AND hash=?', (user_id, digest)).rowcount == 1

    def disable(self, user_id: int) -> None:
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM second_factor WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))

    def cleanup(self) -> None:
        with closing(self._connect()) as db, db:
            db.execute('UPDATE second_factor SET pending=NULL,pending_at=NULL WHERE pending_at<?', (time.time()-600,))
