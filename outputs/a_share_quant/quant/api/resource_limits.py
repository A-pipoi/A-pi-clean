"""Atomic user budgets and queue ownership, shared by API and workers.

All processes must use the same SQLite path (including Redis workers). Budgets
are rolling UTC seconds, aggregated across HTTP route kinds; ``tushare`` has a
separate budget. Set ``A_PI_TUSHARE_*_LIMIT`` (or constructor args) to ``0`` /
empty / ``none`` / ``unlimited`` to disable that window. Completion consumes usage; explicit release refunds only work
that was never dispatched. Expiration never refunds requests.

Synchronous HTTP operations use a separate table: one per user, four globally,
with 180-second recovery leases renewed every 60 seconds. They neither consume
request budgets nor occupy backtest job slots; async submission can reserve a
job while holding its HTTP operation slot.

Queued jobs expire after at most 24 hours for crash recovery. Running workers
renew that interval every minute, so duration alone cannot free a live job.
Expired queued jobs cannot subsequently start. This is a cooperative lease:
workers must retain database access and renew; it cannot fence an arbitrary
process paused beyond its lease. A remote deployment needs a shared filesystem
with reliable SQLite locking; separate local database copies are not shared.
"""
from __future__ import annotations

import contextvars
import math
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant.errors import InputTypeError

from .sqlite_support import connect_sqlite


class ResourceLimitExceeded(RuntimeError):
    status_code = 429

    def __init__(self, reason: str, retry_after: float = 1) -> None:
        self.reason = reason
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(reason)


@dataclass(frozen=True)
class Lease:
    lease_id: str
    user: str
    kind: str


class ResourceLimits:
    def __init__(
        self, path: str | Path, *, hourly_limit: int | None = None,
        daily_limit: int | None = None, tushare_hourly_limit: int | None = None,
        tushare_daily_limit: int | None = None, job_ttl: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        def limit(value: int | None, name: str, default: int) -> int | None:
            """Positive int enforces a cap; 0 / empty / none / unlimited disables that window."""
            if value is None:
                raw = os.getenv(name)
                if raw is None:
                    result: int | None = int(default)
                else:
                    text = str(raw).strip().lower()
                    if text in {"", "0", "none", "null", "off", "false", "unlimited", "inf"}:
                        return None
                    result = int(text)
            else:
                result = value
            if isinstance(result, bool) or not isinstance(result, int):
                raise InputTypeError(f"{name} must be an integer")
            if result == 0:
                return None
            if result < 0:
                raise ValueError(f"{name} must be >= 0 (0 disables the limit)")
            return result
        self.budgets = {
            "heavy": (limit(hourly_limit, "A_PI_HEAVY_HOURLY_LIMIT", 20),
                      limit(daily_limit, "A_PI_HEAVY_DAILY_LIMIT", 100)),
            "tushare": (limit(tushare_hourly_limit, "A_PI_TUSHARE_HOURLY_LIMIT", 5000),
                        limit(tushare_daily_limit, "A_PI_TUSHARE_DAILY_LIMIT", 10000)),
        }
        self.job_ttl = float(os.getenv("A_PI_JOB_LEASE_SECONDS", "86400")) if job_ttl is None else job_ttl
        if not math.isfinite(self.job_ttl) or not 180 <= self.job_ttl <= 86400:
            raise ValueError("job lease seconds must be between 180 and 86400")
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resource_requests (
                    lease_id TEXT PRIMARY KEY, user TEXT NOT NULL,
                    kind TEXT NOT NULL, bucket TEXT NOT NULL,
                    created REAL NOT NULL, state TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS resource_requests_usage
                    ON resource_requests(user, bucket, created);
                CREATE TABLE IF NOT EXISTS resource_jobs (
                    job_id TEXT PRIMARY KEY, user TEXT NOT NULL UNIQUE,
                    token TEXT NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_operations (
                    token TEXT PRIMARY KEY, user TEXT NOT NULL UNIQUE,
                    expires REAL NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, isolation_level=None)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _identity(user: str, kind: str) -> None:
        if not isinstance(user, str) or not user.strip() or not isinstance(kind, str) or not kind.strip():
            raise ValueError("user and kind are required")

    def reserve(self, user: str, kind: str) -> Lease:
        self._identity(user, kind)
        bucket = "tushare" if kind == "tushare" else "heavy"
        lease = Lease(uuid.uuid4().hex, user, kind)
        with self._transaction() as db:
            now = self.clock()
            db.execute("DELETE FROM resource_requests WHERE created<=?", (now - 86400,))
            for window, maximum in zip((3600, 86400), self.budgets[bucket]):
                if maximum is None:
                    continue  # 0 / empty env => no local budget gate for this window
                count = db.execute(
                    "SELECT COUNT(*) FROM resource_requests "
                    "WHERE user=? AND bucket=? AND created>?",
                    (user, bucket, now - window),
                ).fetchone()[0]
                if count >= maximum:
                    # If configuration lowered a limit below existing usage,
                    # wait until enough reservations (not just one) age out.
                    reset = db.execute(
                        "SELECT created FROM resource_requests WHERE user=? AND bucket=? AND created>? "
                        "ORDER BY created LIMIT 1 OFFSET ?",
                        (user, bucket, now - window, count - maximum),
                    ).fetchone()[0]
                    raise ResourceLimitExceeded(f"{bucket}_{'hourly' if window == 3600 else 'daily'}_budget", reset + window - now)
            db.execute("INSERT INTO resource_requests VALUES (?, ?, ?, ?, ?, 'reserved')",
                       (lease.lease_id, user, kind, bucket, now))
        return lease

    def complete(self, lease: Lease) -> None:
        with self._transaction() as db:
            db.execute("UPDATE resource_requests SET state='completed' WHERE lease_id=? AND user=? AND kind=?",
                       (lease.lease_id, lease.user, lease.kind))

    def release(self, lease: Lease) -> None:
        """Refund an unstarted reservation; idempotent and cannot refund completion."""
        with self._transaction() as db:
            db.execute("DELETE FROM resource_requests WHERE lease_id=? AND user=? AND kind=? AND state='reserved'",
                       (lease.lease_id, lease.user, lease.kind))

    def reserve_job(self, user: str, job_id: str) -> Lease:
        self._identity(user, job_id)
        lease = Lease(uuid.uuid4().hex, user, job_id)
        with self._transaction() as db:
            now = self.clock()
            db.execute("DELETE FROM resource_jobs WHERE expires<=?", (now,))
            existing = db.execute("SELECT expires FROM resource_jobs WHERE user=? OR job_id=?", (user, job_id)).fetchone()
            if existing:
                raise ResourceLimitExceeded("backtest_already_active", existing[0] - now)
            db.execute("INSERT INTO resource_jobs VALUES (?, ?, ?, 'queued', ?)",
                       (job_id, user, lease.lease_id, now + self.job_ttl))
        return lease

    def claim_job(self, lease: Lease) -> bool:
        with self._transaction() as db:
            now = self.clock()
            return bool(db.execute(
                "UPDATE resource_jobs SET state='running', expires=? "
                "WHERE job_id=? AND user=? AND token=? AND state='queued' AND expires>?",
                (now + self.job_ttl, lease.kind, lease.user, lease.lease_id, now),
            ).rowcount)

    def renew_job(self, lease: Lease) -> bool:
        with self._transaction() as db:
            now = self.clock()
            return bool(db.execute(
                "UPDATE resource_jobs SET expires=? WHERE job_id=? AND user=? AND token=? AND state='running' AND expires>?",
                (now + self.job_ttl, lease.kind, lease.user, lease.lease_id, now),
            ).rowcount)

    def finish_job(self, lease: Lease) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM resource_jobs WHERE job_id=? AND user=? AND token=?",
                       (lease.kind, lease.user, lease.lease_id))

    def job_lease_live(self, lease: Lease) -> bool:
        """Read-only ownership check for durable job status/recovery."""
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT 1 FROM resource_jobs WHERE job_id=? AND user=? AND token=? AND expires>?",
                (lease.kind, lease.user, lease.lease_id, self.clock()),
            ).fetchone() is not None

    def reserve_operation(self, user: str) -> Lease:
        """Atomically acquire one synchronous slot; independent of job leases."""
        self._identity(user, "operation")
        lease = Lease(uuid.uuid4().hex, user, "operation")
        with self._transaction() as db:
            now = self.clock()
            db.execute("DELETE FROM resource_operations WHERE expires<=?", (now,))
            existing = db.execute(
                "SELECT expires FROM resource_operations WHERE user=?", (user,),
            ).fetchone()
            if existing:
                raise ResourceLimitExceeded("operation_already_active", existing[0] - now)
            count, earliest = db.execute(
                "SELECT COUNT(*), MIN(expires) FROM resource_operations",
            ).fetchone()
            if count >= 4:
                raise ResourceLimitExceeded("operation_global_capacity", earliest - now)
            db.execute("INSERT INTO resource_operations VALUES (?, ?, ?)",
                       (lease.lease_id, user, now + 180))
        return lease

    def renew_operation(self, lease: Lease) -> bool:
        with self._transaction() as db:
            now = self.clock()
            return bool(db.execute(
                "UPDATE resource_operations SET expires=? WHERE token=? AND user=? AND expires>?",
                (now + 180, lease.lease_id, lease.user, now),
            ).rowcount)

    def finish_operation(self, lease: Lease) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM resource_operations WHERE token=? AND user=?",
                       (lease.lease_id, lease.user))

    @contextmanager
    def running_operation(self, user: str) -> Iterator[None]:
        """Hold an HTTP operation slot until exit, renewing every 60 seconds.

        Call after reserving the heavy budget. If admission fails, release that
        undispatched budget reservation. An unbound identity must be skipped by
        legacy handler fixtures, not turned into a shared anonymous user slot.
        """
        lease = self.reserve_operation(user)
        stop = threading.Event()
        def heartbeat() -> None:
            while not stop.wait(60):
                try:
                    if not self.renew_operation(lease):
                        return
                except sqlite3.Error:
                    continue
        worker = threading.Thread(target=heartbeat, name="http-operation-lease", daemon=True)
        try:
            worker.start()
            yield
        finally:
            stop.set()
            if worker.ident is not None:
                worker.join(timeout=31)
            self.finish_operation(lease)

    @contextmanager
    def running_job(self, lease: Lease) -> Iterator[None]:
        if not self.claim_job(lease):
            raise ResourceLimitExceeded("backtest_lease_expired_or_claimed")
        stop = threading.Event()
        def heartbeat() -> None:
            while not stop.wait(60):
                try:
                    if not self.renew_job(lease):
                        return
                except sqlite3.Error:
                    # Retry while the recovery lease still protects the job.
                    continue
        worker = threading.Thread(target=heartbeat, name="backtest-lease", daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=31)
            self.finish_job(lease)


_default_lock = threading.Lock()
_default_store: ResourceLimits | None = None


def get_resource_limits() -> ResourceLimits:
    global _default_store
    with _default_lock:
        if _default_store is None:
            root = Path(os.getenv("A_PI_DATA_ROOT", str(Path(__file__).resolve().parents[2] / "data")))
            _default_store = ResourceLimits(os.getenv("A_PI_RESOURCE_LIMITS_DB", str(root / "resource_limits.sqlite3")))
        return _default_store


TUSHARE_RESOURCE_CONTEXT: contextvars.ContextVar[tuple[ResourceLimits, str] | None] = contextvars.ContextVar("tushare_resource_context", default=None)


@contextmanager
def tushare_resource_context(user: str, store: ResourceLimits | None = None) -> Iterator[None]:
    """Bind in HTTP handlers; copy_context or explicitly bind in detached work."""
    token = TUSHARE_RESOURCE_CONTEXT.set((store or get_resource_limits(), user))
    try:
        yield
    finally:
        TUSHARE_RESOURCE_CONTEXT.reset(token)


def resolve_tushare_resource_context() -> tuple[ResourceLimits, str] | None:
    """Explicit context wins; scheduler/worker identity is the fallback.

    Lazy import avoids a user-scope/store import cycle. Truly unbound legacy
    calls skip the gate without initializing or writing the default database.
    """
    context = TUSHARE_RESOURCE_CONTEXT.get()
    if context is not None:
        return context
    from .user_scope import CURRENT_USERNAME
    user = CURRENT_USERNAME.get()
    return (get_resource_limits(), user) if user else None


def limited_tushare_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Adapter hook: wrap EACH outbound attempt, outside retry exception handling.

    Explicit context or CURRENT_USERNAME binds a user; no identity preserves
    legacy/internal callers. Failed outbound calls
    consume quota too. SDK pro_bar needs wrapping its underlying query boundary
    to count its multiple HTTP calls rather than just one SDK invocation.
    """
    from quant.data.provider_rate import acquire
    endpoint = str(kwargs.pop("_rate_endpoint", ""))
    if not endpoint:
        request_json = kwargs.get("json")
        endpoint = str(request_json.get("api_name", "")) if isinstance(request_json, dict) else str(kwargs.get("api_name", ""))
    context = resolve_tushare_resource_context()
    rate_store = context[0] if context is not None else get_resource_limits()
    acquire(rate_store.path, endpoint)
    if context is None:
        return function(*args, **kwargs)
    store, user = context
    lease = store.reserve(user, "tushare")
    try:
        return function(*args, **kwargs)
    finally:
        store.complete(lease)
