"""Durable account-local maintenance attempts; no broker or research writes."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHINA_TZ = timezone(timedelta(hours=8))
TERMINAL = {"completed", "blocked", "market_closed", "missed_window"}
MAX_ATTEMPTS = 5


def in_window(now: datetime) -> bool:
    local = now.astimezone(CHINA_TZ)
    return local.weekday() < 5 and (16, 0) <= (local.hour, local.minute) <= (16, 10)


class MaintenanceJournal:
    def __init__(self, account_path: Path) -> None:
        self.path = account_path.with_name("paper-maintenance.sqlite3")
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS maintenance_runs (
                run_date TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                owner TEXT, lease_until REAL NOT NULL DEFAULT 0, config TEXT NOT NULL DEFAULT '',
                started_at TEXT, finished_at TEXT, next_attempt_at REAL NOT NULL DEFAULT 0,
                reason_code TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}')""")
            db.execute("""CREATE TABLE IF NOT EXISTS maintenance_attempts (
                run_date TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
                reason_code TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL,
                finished_at TEXT, result TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_date,attempt))""")
            db.execute("""CREATE TABLE IF NOT EXISTS manual_catchups (
                run_date TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT NOT NULL, config TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, result TEXT NOT NULL DEFAULT '{}')""")

    def claim_manual(self, now: datetime, config: str, reason: str) -> str | None:
        """One explicit same-day recovery; never rewrite the missed-window audit."""
        local = now.astimezone(CHINA_TZ)
        if local.weekday() >= 5 or (local.hour, local.minute) < (16, 11):
            raise ValueError("人工补跑只允许当日窗口结束后执行")
        if not reason.strip() or len(reason) > 200:
            raise ValueError("人工补跑需要不超过200字的原因")
        day, owner = local.date().isoformat(), uuid.uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM maintenance_runs WHERE run_date=?", (day,)).fetchone()
            if not previous or previous["status"] != "missed_window" or previous["attempts"] != 0:
                return None
            if previous["lease_until"] > now.timestamp() or (previous["config"] and previous["config"] != config):
                return None
            if db.execute("SELECT 1 FROM manual_catchups WHERE run_date=?", (day,)).fetchone():
                return None
            db.execute("INSERT INTO manual_catchups(run_date,owner,status,reason,config,started_at) VALUES(?,?,'running',?,?,?)",
                       (day, owner, reason.strip(), config, local.isoformat()))
        return owner

    def finish_manual(self, day: str, owner: str, now: datetime, status: str, result: dict[str, Any]) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE manual_catchups SET status=?,finished_at=?,result=? WHERE run_date=? AND owner=? AND status='running'",
                       (status, now.isoformat(), json.dumps(result, ensure_ascii=False), day, owner))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def claim(self, now: datetime, config: str) -> str | None:
        if not in_window(now):
            return None
        day, stamp = now.astimezone(CHINA_TZ).date().isoformat(), now.timestamp()
        owner = uuid.uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM maintenance_runs WHERE run_date=?", (day,)).fetchone()
            if row and (row["status"] in TERMINAL or row["attempts"] >= MAX_ATTEMPTS
                        or row["lease_until"] > stamp or row["next_attempt_at"] > stamp):
                return None
            if row and row["config"] != config:
                db.execute("UPDATE maintenance_runs SET status='blocked',reason_code='config_changed',finished_at=? WHERE run_date=?", (now.isoformat(), day))
                return None
            if row and row["status"] == "running":
                db.execute("UPDATE maintenance_attempts SET status='interrupted',reason_code='expired_lease',finished_at=? WHERE run_date=? AND attempt=? AND status='running'", (now.isoformat(), day, row["attempts"]))
            db.execute("""INSERT INTO maintenance_runs(run_date,status,attempts,owner,lease_until,config,started_at)
                VALUES(?,'running',1,?,?,?,?) ON CONFLICT(run_date) DO UPDATE SET
                status='running',attempts=attempts+1,owner=excluded.owner,lease_until=excluded.lease_until,
                started_at=excluded.started_at,finished_at=NULL,result='{}',reason_code='recovering_or_retrying'""",
                (day, owner, stamp + 120, config, now.isoformat()))
            db.execute("INSERT INTO maintenance_attempts(run_date,attempt,status,started_at) VALUES(?,?,'running',?)", (day, (row["attempts"] if row else 0) + 1, now.isoformat()))
        return owner

    def renew(self, day: str, owner: str, now: datetime) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE maintenance_runs SET lease_until=? WHERE run_date=? AND owner=? AND status='running'", (now.timestamp() + 120, day, owner))

    def finish(self, day: str, owner: str, now: datetime, status: str,
               reason: str, result: dict[str, Any]) -> None:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT attempts FROM maintenance_runs WHERE run_date=? AND owner=?", (day, owner)).fetchone()
            if not row:
                return
            retry = status in {"waiting_data", "failed"} and row["attempts"] < MAX_ATTEMPTS
            next_attempt = now.timestamp() + min(120, 30 * 2 ** (row["attempts"] - 1)) if retry else 0
            db.execute("""UPDATE maintenance_runs SET status=?,reason_code=?,result=?,finished_at=?,
                next_attempt_at=?,lease_until=0,owner=NULL WHERE run_date=? AND owner=?""",
                (status, reason, json.dumps(result, ensure_ascii=False, default=str), now.isoformat(), next_attempt, day, owner))
            db.execute("UPDATE maintenance_attempts SET status=?,reason_code=?,finished_at=?,result=? WHERE run_date=? AND attempt=?",
                       (status, reason, now.isoformat(), json.dumps(result, ensure_ascii=False, default=str), day, row["attempts"]))

    def expire_window(self, now: datetime) -> bool:
        local = now.astimezone(CHINA_TZ)
        if local.weekday() >= 5 or (local.hour, local.minute) < (16, 11):
            return False
        day = local.date().isoformat()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM maintenance_runs WHERE run_date=?", (day,)).fetchone()
            if row and (
                row["status"] in TERMINAL | {"failed", "waiting_data", "running"}
                or row["lease_until"] > now.timestamp()
            ):
                return False
            db.execute("""INSERT INTO maintenance_runs(run_date,status,finished_at,reason_code)
                VALUES(?,'missed_window',?,'window_expired') ON CONFLICT(run_date) DO UPDATE SET
                status='missed_window',finished_at=excluded.finished_at,reason_code='window_expired',
                lease_until=0,next_attempt_at=0,owner=NULL""", (day, now.isoformat()))
        return True

    def status(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM maintenance_runs ORDER BY run_date DESC LIMIT 20").fetchall()
            attempts = db.execute("SELECT * FROM maintenance_attempts ORDER BY run_date DESC,attempt DESC LIMIT 100").fetchall()
            manual = db.execute("SELECT run_date,status,reason,started_at,finished_at,result FROM manual_catchups ORDER BY run_date DESC LIMIT 20").fetchall()
        history = []
        for row in rows:
            entry = dict(row)
            entry.pop("owner", None)
            entry["result"] = json.loads(entry["result"])
            for key in ("next_attempt_at", "lease_until"):
                value = entry[key]
                entry[key] = datetime.fromtimestamp(value, CHINA_TZ).isoformat() if value else None
            history.append(entry)
        attempt_history = [{**dict(row), "result": json.loads(row["result"])} for row in attempts]
        return {"latest": history[0] if history else None, "history": history, "attempt_history": attempt_history,
                "manual_catchups": [{**dict(row), "result": json.loads(row["result"])} for row in manual],
                "max_attempts": MAX_ATTEMPTS, "missed_window": bool(history and history[0]["status"] == "missed_window")}
