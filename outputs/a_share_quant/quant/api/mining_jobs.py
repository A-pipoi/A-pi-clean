"""Durable, user-isolated initial sealing jobs; never persist partial seals."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Executor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.data.progress import DATA_CACHE_NOT_BEFORE, DATA_PROGRESS

from .resource_limits import (
    Lease,
    ResourceLimitExceeded,
    ResourceLimits,
    tushare_resource_context,
)
from .sqlite_support import connect_sqlite
from .user_scope import user_context

LOGGER = logging.getLogger(__name__)

MINING_JOB: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mining_job", default=None
)


class MiningCancelled(Exception):
    """A stage boundary cancellation, not a thread kill or quota refund."""


def report_mining_progress(stage: str, **details: object) -> None:
    context = MINING_JOB.get()
    if context:
        context["manager"].progress(context["job_id"], stage, **details)


def mining_requested_range() -> tuple[str, str] | None:
    context = MINING_JOB.get()
    return context["requested_range"] if context else None


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied is not proof that another worker is dead.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            )
            return (
                bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                and code.value == 259
            )
        finally:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class MiningJobManager:
    def __init__(self, path: Path, executor: Executor, limits: ResourceLimits) -> None:
        self.path, self.executor, self.limits = path, executor, limits
        self.lock = threading.RLock()
        self.live: dict[str, dict[str, object]] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS mining_jobs (
                job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, pipeline TEXT NOT NULL,
                pid INTEGER NOT NULL, lease TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, isolation_level=None)

    def _view(self, row: tuple) -> dict[str, Any]:
        data = json.loads(row[6])
        data.update(self.live.get(row[0], {}))
        return data

    def latest(self, owner: str, pipeline: str) -> dict[str, Any] | None:
        with self.lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM mining_jobs WHERE owner=? AND pipeline=? ORDER BY rowid DESC LIMIT 1",
                (owner, pipeline),
            ).fetchone()
            if (
                row
                and row[5] in {"queued", "running", "cancelling"}
                and (
                    not _process_alive(row[3])
                    or not self.limits.job_lease_live(Lease(row[4], owner, row[0]))
                )
            ):
                self.limits.finish_job(Lease(row[4], owner, row[0]))
                data = self._view(row)
                data.update(
                    status="interrupted",
                    stage="interrupted",
                    completed_at=datetime.now(UTC).isoformat(),
                    error="worker_interrupted; retry resumes verified cache, never a partial seal",
                )
                db.execute(
                    "UPDATE mining_jobs SET state='interrupted', payload=? WHERE job_id=?",
                    (json.dumps(data), row[0]),
                )
                self.live.pop(row[0], None)
                return data
            return self._view(row) if row else None

    def submit(
        self,
        owner: str,
        pipeline: str,
        requested_range: tuple[str, str],
        execute: Callable[[], dict[str, object]],
    ) -> dict[str, Any]:
        with self.lock:
            with closing(self._connect()) as db:
                stale_owners = db.execute(
                    "SELECT DISTINCT owner, pipeline FROM mining_jobs WHERE state IN ('queued','running','cancelling')"
                ).fetchall()
            for previous_owner, previous_pipeline in stale_owners:
                self.latest(previous_owner, previous_pipeline)
            previous = self.latest(owner, pipeline)
            if previous and previous["status"] in {"queued", "running", "cancelling"}:
                return previous
            cache_not_before = datetime.now(UTC).timestamp()
            if previous and previous["status"] in {"interrupted", "failed"}:
                requested_range = (
                    previous["requested_start"],
                    previous["requested_end"],
                )
                cache_not_before = previous.get("cache_not_before", cache_not_before)
            job_id = uuid.uuid4().hex
            lease = self.limits.reserve_job(owner, job_id)
            data = {
                "job_id": job_id,
                "status": "queued",
                "stage": "dataset_sealing",
                "requested_start": requested_range[0],
                "requested_end": requested_range[1],
                "created_at": datetime.now(UTC).isoformat(),
                "progress_current": 0,
                "progress_total": None,
                "cache_not_before": cache_not_before,
            }
            try:
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    if (
                        db.execute(
                            "SELECT COUNT(*) FROM mining_jobs WHERE state IN ('queued','running','cancelling')"
                        ).fetchone()[0]
                        >= 4
                    ):
                        raise ResourceLimitExceeded("operation_global_capacity", 60)
                    db.execute(
                        "INSERT INTO mining_jobs VALUES (?,?,?,?,?,'queued',?)",
                        (
                            job_id,
                            owner,
                            pipeline,
                            os.getpid(),
                            lease.lease_id,
                            json.dumps(data),
                        ),
                    )
                    db.commit()
                self.executor.submit(
                    self._run, owner, lease, requested_range, execute, cache_not_before
                )
            except BaseException:
                self.limits.finish_job(lease)
                self._update(job_id, status="failed", error="worker_submission_failed")
                raise
            return data

    def _update(self, job_id: str, **changes: object) -> None:
        with self.lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM mining_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row:
                data = self._view(row)
                data.update(changes)
                if changes.get("status") in {"completed", "failed", "cancelled"}:
                    self.live.pop(job_id, None)
                db.execute(
                    "UPDATE mining_jobs SET state=?, payload=? WHERE job_id=?",
                    (data["status"], json.dumps(data, ensure_ascii=False), job_id),
                )

    def progress(self, job_id: str, stage: str, **details: object) -> None:
        with self.lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT state FROM mining_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row and row[0] in {"cancelling", "cancelled", "interrupted"}:
                raise MiningCancelled("研究任务在安全检查点取消；已消耗配额不退回")
            previous = self.live.get(job_id, {}).get("stage")
            self.live[job_id] = {"stage": stage, **details}
            # Persist stage boundaries only, not every symbol/page.
            if stage != previous:
                self._update(job_id, stage=stage, **details)

    def cancel(self, owner: str, pipeline: str, job_id: str) -> bool:
        with self.lock:
            data = self.latest(owner, pipeline)
            if (
                not data
                or data["job_id"] != job_id
                or data["status"] not in {"queued", "running", "cancelling"}
            ):
                return False
            self._update(job_id, status="cancelling")
            return True

    def _run(
        self,
        owner: str,
        lease: Any,
        requested_range: tuple[str, str],
        execute: Callable[[], dict[str, object]],
        cache_not_before: float,
    ) -> None:
        token = MINING_JOB.set(
            {"manager": self, "job_id": lease.kind, "requested_range": requested_range}
        )
        cache_token = DATA_CACHE_NOT_BEFORE.set(cache_not_before)
        progress_token = DATA_PROGRESS.set(
            lambda current, total, symbol: self.progress(
                lease.kind,
                str(self.live.get(lease.kind, {}).get("stage") or "daily_bars"),
                progress_current=current,
                progress_total=total,
                current_symbol=symbol,
            )
        )
        try:
            with (
                user_context(owner),
                self.limits.running_job(lease),
                tushare_resource_context(owner, self.limits),
            ):
                self.progress(lease.kind, "members")
                self._update(
                    lease.kind,
                    status="running",
                    started_at=datetime.now(UTC).isoformat(),
                )
                result = execute()
                self._update(
                    lease.kind,
                    status="completed",
                    stage="completed",
                    run_id=result.get("run_id"),
                    session_id=result.get("session_id"),
                    completed_at=datetime.now(UTC).isoformat(),
                )
        except MiningCancelled as error:
            self._update(
                lease.kind,
                status="cancelled",
                stage="cancelled",
                error=str(error),
                completed_at=datetime.now(UTC).isoformat(),
            )
        except Exception as error:
            logging.getLogger(__name__).exception("quant.api.mining_jobs failed: %s", type(error).__name__, exc_info=None)
            self._update(
                lease.kind,
                status="failed",
                stage="failed",
                error=type(error).__name__,
                code=getattr(error, "api_code", "factor_mining_failed"),
                completed_at=datetime.now(UTC).isoformat(),
            )
        finally:
            self.live.pop(lease.kind, None)
            DATA_PROGRESS.reset(progress_token)
            DATA_CACHE_NOT_BEFORE.reset(cache_token)
            MINING_JOB.reset(token)
