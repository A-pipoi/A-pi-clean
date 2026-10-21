"""In-memory/Redis backtest job lifecycle and polling payloads."""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from quant.api.user_scope import CURRENT_USERNAME, user_context

from ..numeric import as_float, as_int
from .resource_limits import (
    Lease,
    ResourceLimitExceeded,
    ResourceLimits,
    get_resource_limits,
    tushare_resource_context,
)

LOGGER = logging.getLogger(__name__)


class BacktestCancelled(Exception):
    """Cancellation is cooperative, never a thread kill or budget refund."""


@dataclass(frozen=True)
class BacktestJobDependencies:
    executor: Executor
    queue_limit: int
    ledger: Callable[[], Any]
    queue_mode: Callable[[], str]
    redis_queue: Callable[[], tuple[Any, Any]]
    redis_counts: Callable[[Any, Any], Mapping[str, int]]
    execute: Callable[[dict[str, Any]], tuple[int, dict[str, object]]]
    remote_worker: Callable[[str, dict[str, object]], None]
    resource_limits: Callable[[], ResourceLimits] = get_resource_limits


@dataclass
class BacktestJobManager:
    """Owns live job state, progress, submission, and persistence coordination."""

    dependencies: BacktestJobDependencies
    jobs: dict[str, dict[str, object]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)
    active_job: contextvars.ContextVar[str | None] = field(
        default_factory=lambda: contextvars.ContextVar(
            "active_backtest_job", default=None
        )
    )

    def view(self, job: Mapping[str, object]) -> dict[str, object]:
        """Return the public poll payload without internal monotonic timestamps."""

        now = time.monotonic()
        started = as_float(
            job.get("started_monotonic") or job.get("created_monotonic") or now
        )
        status = str(job.get("status") or "queued")
        elapsed = max(0.0, now - started) if job.get("started_monotonic") else None
        if status == "queued":
            progress = as_int(job.get("progress", 0) or 0)
            message = str(job.get("message") or "等待 Python 回测任务启动")
        elif status == "running":
            progress = as_int(job.get("progress", 15) or 15)
            message = str(
                job.get("message") or "正在请求 Tushare 实时行情并执行样本外验证"
            )
        elif status == "completed":
            progress = 100
            message = str(job.get("message") or "真实 Tushare 回测已完成")
        else:
            progress = as_int(job.get("progress", 0) or 0)
            message = str(job.get("message") or "真实 Tushare 回测失败")
        payload: dict[str, object] = {
            "ok": True,
            "job_id": job["job_id"],
            "status": status,
            "progress": progress,
            "message": message,
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        }
        if status == "completed":
            payload["result"] = job.get("result")
        if status == "failed":
            payload["error"] = job.get("error") or "回测任务失败"
        return payload

    def update(self, job_id: str, **changes: object) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None:
                job.update(changes)
        try:
            self.dependencies.ledger().update_backtest_job(job_id, **changes)
        except Exception as error:
            # Keep progress available in memory if the audit ledger is transiently unavailable.
            LOGGER.exception("Unable to persist backtest job progress: %s", type(error).__name__, exc_info=None)

    def report_progress(self, progress: int, message: str) -> None:
        job_id = self.active_job.get()
        if job_id:
            self.check_cancelled(job_id)
            self.update(
                job_id, progress=max(0, min(99, int(progress))), message=message
            )

    def run(self, job_id: str, payload: dict[str, object]) -> None:
        owner = payload.get("_auth_owner")
        with user_context(str(owner)) if owner else nullcontext():
            store = self.dependencies.resource_limits() if owner else None
            token = payload.get("_resource_job_lease")
            lease = Lease(str(token), str(owner), job_id) if owner and token else None
            # Owned jobs submitted by this manager always carry a persisted lease.
            # Unowned legacy jobs keep their old mocks and execution contract.
            if owner and lease is None:
                self.update(job_id, status="failed", error="missing backtest resource lease")
                return
            try:
                with (
                    store.running_job(lease) if store and lease else nullcontext()
                ), (
                    tushare_resource_context(str(owner), store)
                    if owner
                    else nullcontext()
                ):
                    self._run_scoped(
                        job_id,
                        {
                            key: value
                            for key, value in payload.items()
                            if key not in {"_auth_owner", "_resource_job_lease"}
                        },
                    )
            except ResourceLimitExceeded:
                # A duplicate delivery must not overwrite the live worker's state.
                return

    def check_cancelled(self, job_id: str) -> None:
        job = self.dependencies.ledger().get_backtest_job(job_id)
        if job and job.get("status") in {"cancelling", "cancelled"}:
            raise BacktestCancelled("用户取消回测；已记录的研究预算不会退还")

    def _run_scoped(self, job_id: str, payload: dict[str, object]) -> None:
        try:
            self.check_cancelled(job_id)
        except BacktestCancelled:
            self.update(job_id, status="cancelled", message="回测已取消", completed_at=datetime.now(UTC).isoformat())
            return
        started_at = datetime.now(UTC).isoformat()
        self.update(
            job_id,
            status="running",
            progress=15,
            message="正在请求 Tushare 实时行情并执行样本外验证",
            started_at=started_at,
            started_monotonic=time.monotonic(),
        )
        context_token = self.active_job.set(job_id)
        try:
            status, result = self.dependencies.execute(payload)
            self.check_cancelled(job_id)
            if status >= 400 or not result.get("ok"):
                error = result.get("error") or "回测服务返回无效结果"
                self.update(
                    job_id,
                    status="failed",
                    progress=0,
                    message="真实 Tushare 回测失败",
                    error=str(error),
                    completed_at=datetime.now(UTC).isoformat(),
                )
                return
            research = result.get("research")
            experiment_id = (
                as_int(research.get("experiment_id"))
                if isinstance(research, Mapping)
                else 0
            )
            if experiment_id:
                self.dependencies.ledger().update_experiment_parameters(
                    experiment_id,
                    {"result_url": f"/api/backtest/{job_id}", "job_id": job_id},
                )
            self.update(
                job_id,
                status="completed",
                progress=100,
                message="真实 Tushare 回测已完成",
                result=result,
                completed_at=datetime.now(UTC).isoformat(),
            )
        except BacktestCancelled as error:
            self.update(job_id, status="cancelled", progress=0, message=str(error), completed_at=datetime.now(UTC).isoformat())
        except (TimeoutError, OSError, json.JSONDecodeError) as error:
            LOGGER.exception("Backtest job connection failed: %s", type(error).__name__, exc_info=None)
            self.update(
                job_id,
                status="failed",
                progress=0,
                message="真实 Tushare 回测失败",
                error=f"回测任务连接失败：{type(error).__name__}",
                completed_at=datetime.now(UTC).isoformat(),
            )
        except Exception as error:  # keep asynchronous failures visible to the UI
            LOGGER.exception("Backtest job failed: %s", type(error).__name__, exc_info=None)
            self.update(
                job_id,
                status="failed",
                progress=0,
                message="真实 Tushare 回测失败",
                error=f"回测任务失败：{type(error).__name__}",
                completed_at=datetime.now(UTC).isoformat(),
            )
        finally:
            self.active_job.reset(context_token)

    def submit(self, payload: dict[str, object]) -> str:
        job_id = f"BT-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        now = time.monotonic()
        mode = self.dependencies.queue_mode()
        owner = CURRENT_USERNAME.get() or payload.get("_auth_owner")
        store = self.dependencies.resource_limits() if owner else None
        lease = None
        if mode != "local":
            queue, connection = self.dependencies.redis_queue()
            counts = self.dependencies.redis_counts(queue, connection)
            active = counts["queued"] + counts["running"]
            if active >= self.dependencies.queue_limit:
                raise RuntimeError("回测队列已满，请等待当前任务完成后再试")
        with self.lock:
            if mode == "local":
                active = sum(
                    item.get("status") in {"queued", "running", "cancelling"}
                    for item in self.jobs.values()
                )
                if active >= self.dependencies.queue_limit:
                    raise RuntimeError("回测队列已满，请等待当前任务完成后再试")
            if mode == "local" and owner and any(
                item.get("owner") == owner and item.get("status") in {"queued", "running", "cancelling"}
                for item in self.jobs.values()
            ):
                raise ResourceLimitExceeded("backtest_already_active")
            if store:
                lease = store.reserve_job(str(owner), job_id)
                payload = {**payload, "_auth_owner": str(owner), "_resource_job_lease": lease.lease_id}
            self.jobs[job_id] = {
                "job_id": job_id,
                "owner": owner,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(),
                "created_monotonic": now,
            }
            completed = [
                key
                for key, item in self.jobs.items()
                if item.get("status") in {"completed", "failed", "cancelled"}
            ]
            for key in sorted(completed)[:-20]:
                self.jobs.pop(key, None)
        try:
            self.dependencies.ledger().create_backtest_job(job_id, payload)
        except Exception:
            with self.lock:
                self.jobs.pop(job_id, None)
            if store and lease:
                store.finish_job(lease)
            raise
        if mode == "local":
            try:
                self.dependencies.executor.submit(contextvars.copy_context().run, self.run, job_id, payload)
            except Exception as error:
                self.update(job_id, status="failed", error=str(error),
                            completed_at=datetime.now(UTC).isoformat())
                if store and lease:
                    store.finish_job(lease)
                raise
        else:
            try:
                queue, _connection = self.dependencies.redis_queue()
                queue.enqueue(
                    self.dependencies.remote_worker,
                    job_id,
                    payload,
                    _job_id=job_id,
                    result_ttl=0,
                    failure_ttl=86400,
                )
            except Exception as error:
                self.update(
                    job_id,
                    status="failed",
                    progress=0,
                    message="分布式回测任务提交失败",
                    error=f"Redis/RQ enqueue failed: {str(error)[:240]}",
                    completed_at=datetime.now(UTC).isoformat(),
                )
                if store and lease:
                    store.finish_job(lease)
                raise RuntimeError(f"分布式队列提交失败：{str(error)[:200]}") from error
        return job_id
