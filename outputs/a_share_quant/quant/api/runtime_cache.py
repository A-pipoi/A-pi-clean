"""Per-application asynchronous caches with owned state and executor lifetimes."""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import RLock
from time import monotonic

Payload = dict[str, object]
LOGGER = logging.getLogger(__name__)


class DataProbeCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="a-pi-data-probe"
        )
        self.lock = RLock()
        self.future: Future[Payload] | None = None
        self.snapshot: Payload = {"status": "pending", "checked_at": None}

    def status(self, probe: Callable[[], Payload]) -> Payload:
        with self.lock:
            if self.future is not None and self.future.done():
                try:
                    self.snapshot = self.future.result()
                except Exception as error:
                    # Never return or log raw provider messages or traceback chains.
                    LOGGER.exception(
                        "Data probe cache failed: %s",
                        type(error).__name__,
                        exc_info=None,
                    )
                    self.snapshot = {
                        "status": "unreachable",
                        "data_reachable": False,
                        "data_fresh": None,
                        "error": type(error).__name__,
                        "checked_at": datetime.now(UTC).isoformat(),
                    }
                self.future = None
            checked = self.snapshot.get("checked_at")
            expired = (
                not checked
                or (
                    datetime.now(UTC) - datetime.fromisoformat(str(checked))
                ).total_seconds()
                >= self.ttl_seconds
            )
            if expired and self.future is None:
                self.future = self.executor.submit(probe)
            return dict(self.snapshot)

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class WorkspaceCache:
    def __init__(self, ttl_seconds: int = 600, failure_ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self.failure_ttl_seconds = failure_ttl_seconds
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="a-pi-workspace"
        )
        self.lock = RLock()
        self.future: Future[Payload] | None = None
        self.snapshot: Payload | None = None
        self.success_at = 0.0
        self.failure: str | None = None
        self.failure_at = 0.0

    def response(self, build: Callable[[], Payload]) -> tuple[int, Payload]:
        with self.lock:
            now = monotonic()
            if self.future is not None and self.future.done():
                try:
                    self.snapshot = self.future.result()
                    self.success_at = now
                    self.failure = None
                except Exception as error:
                    LOGGER.exception(
                        "Workspace cache failed: %s",
                        type(error).__name__,
                        exc_info=None,
                    )
                    self.failure = type(error).__name__
                    self.failure_at = now
                self.future = None
            if self.snapshot is not None and now - self.success_at < self.ttl_seconds:
                age = int(now - self.success_at)
                return 200, {
                    **self.snapshot,
                    "snapshot_age_seconds": age,
                    "snapshot_cached": age > 0,
                }
            if (
                self.future is None
                and self.failure
                and now - self.failure_at < self.failure_ttl_seconds
            ):
                return 502, {
                    "ok": False,
                    "code": "workspace_build_failed",
                    "error": self.failure,
                }
            if self.future is None:
                self.future = self.executor.submit(
                    contextvars.copy_context().run, build
                )
            return 202, {
                "ok": False,
                "code": "workspace_building",
                "status": "running",
                "message": "正在拉取真实 Tushare 行情并生成工作区快照",
            }

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


class RuntimeCaches:
    def __init__(self) -> None:
        self.probe = DataProbeCache()
        self.workspace = WorkspaceCache()

    def close(self) -> None:
        self.probe.close()
        self.workspace.close()
