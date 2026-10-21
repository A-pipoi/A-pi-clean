"""Retries never replay writes, weaken atomicity, or hide permanent failures."""

import sqlite3
from unittest.mock import patch

import pytest

from quant.api.login_limits import LoginLimits
from quant.api.sqlite_support import _transient, connect_sqlite
from quant.api.two_factor import TwoFactor


def test_security_stores_use_wal_and_busy_timeout(tmp_path):
    for store in (LoginLimits(tmp_path / "limits.db"), TwoFactor(tmp_path / "2fa.db")):
        with store._connect() as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 10000


@pytest.mark.parametrize(
    "message", ["database is locked", "attempt to write a readonly database"]
)
def test_retryable_error_classification(message):
    assert _transient(sqlite3.OperationalError(message))
    assert not _transient(sqlite3.OperationalError("no such table: missing"))


def test_locked_entry_bounded_and_reusable(tmp_path):
    path = tmp_path / "locked.db"
    writer = connect_sqlite(path)
    waiter = connect_sqlite(path)
    writer.execute("BEGIN IMMEDIATE")
    waiter.execute("PRAGMA busy_timeout=1")
    try:
        with patch("quant.api.sqlite_support.time.sleep") as sleep:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                waiter.execute("BEGIN IMMEDIATE")
            assert [call.args[0] for call in sleep.call_args_list] == [0.05, 0.1]
        assert not waiter.in_transaction
        writer.rollback()
        waiter.execute("BEGIN IMMEDIATE")
        assert waiter.in_transaction
        waiter.rollback()
    finally:
        writer.close()
        waiter.close()


def test_non_entry_errors_are_not_retried(tmp_path):
    db = connect_sqlite(tmp_path / "errors.db")
    try:
        with patch("quant.api.sqlite_support.time.sleep") as sleep:
            with pytest.raises(sqlite3.OperationalError):
                db.execute("INSERT INTO missing VALUES(1)")
            sleep.assert_not_called()
    finally:
        db.close()
