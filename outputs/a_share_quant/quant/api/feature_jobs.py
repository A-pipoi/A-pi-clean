"""In-memory PIT feature materialization jobs: submit, poll, cancel, progress."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime

from quant.api.user_scope import CURRENT_USERNAME, user_context
from quant.data.progress import DATA_PROGRESS
from quant.numeric import as_float, as_int

from .feature_materialization import classify_materialization_error
from .resource_limits import (
    Lease,
    ResourceLimitExceeded,
    ResourceLimits,
    get_resource_limits,
    tushare_resource_context,
)

LOGGER = logging.getLogger(__name__)


class FeatureMaterializeCancelled(Exception):
    """Cooperative cancel at a warm-up checkpoint; never a thread kill."""


@dataclass(frozen=True)
class FeatureJobDependencies:
    executor: Executor
    queue_limit: int
    execute: Callable[[dict[str, object], Callable[[dict[str, object]], None], Callable[[], None]], dict[str, object]]
    resource_limits: Callable[[], ResourceLimits] = get_resource_limits


@dataclass
class FeatureMaterializationJobManager:
    """Local async warm-up jobs with stage/progress polling (no Redis)."""

    dependencies: FeatureJobDependencies
    jobs: dict[str, dict[str, object]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)
    active_job: contextvars.ContextVar[str | None] = field(
        default_factory=lambda: contextvars.ContextVar("active_feature_job", default=None)
    )

    def view(self, job: Mapping[str, object]) -> dict[str, object]:
        now = time.monotonic()
        started = as_float(job.get("started_monotonic") or job.get("created_monotonic") or now)
        status = str(job.get("status") or "queued")
        elapsed = max(0.0, now - started) if job.get("started_monotonic") else None
        if status == "queued":
            progress, message = as_int(job.get("progress", 0) or 0), str(job.get("message") or "等待 PIT 特征预热启动")
        elif status == "running":
            progress = as_int(job.get("progress", 5) or 5)
            message = str(job.get("message") or "正在预热 PIT 特征")
        elif status == "completed":
            progress, message = 100, str(job.get("message") or "PIT 特征预热已完成")
        elif status == "cancelled":
            progress, message = as_int(job.get("progress", 0) or 0), str(job.get("message") or "预热已取消")
        else:
            progress, message = as_int(job.get("progress", 0) or 0), str(job.get("message") or "PIT 特征预热失败")
        payload: dict[str, object] = {
            "ok": True,
            "job_id": job["job_id"],
            "status": status,
            "progress": progress,
            "message": message,
            "stage": job.get("stage") or ("queued" if status == "queued" else status),
            "symbol_count": job.get("symbol_count"),
            "processed_symbols": job.get("processed_symbols"),
            "current_symbol": job.get("current_symbol"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        }
        if status == "completed":
            payload["result"] = job.get("result")
        if status == "failed":
            for key in ("error", "code", "hint", "detail", "error_type"):
                if job.get(key) is not None:
                    payload[key] = job[key]
            payload.setdefault("error", "特征预热失败")
        if status == "cancelled":
            payload["error"] = job.get("error") or message
            payload["code"] = "cancelled"
        return payload

    def get(self, job_id: str, *, owner: str | None) -> dict[str, object] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if owner and job.get("owner") and job.get("owner") != owner:
                return None
            return self.view(job)

    def update(self, job_id: str, **changes: object) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None:
                job.update(changes)

    def report_progress(self, **fields: object) -> None:
        job_id = self.active_job.get()
        if not job_id:
            return
        self.check_cancelled(job_id)
        if "progress" in fields:
            fields["progress"] = max(0, min(99, as_int(fields["progress"])))
        self.update(job_id, **fields)

    def check_cancelled(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            status = str(job.get("status") or "") if job else ""
        if status in {"cancelling", "cancelled"}:
            raise FeatureMaterializeCancelled("用户取消预热；已拉取的行情不会回滚")

    def cancel(self, job_id: str, *, owner: str | None) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return False
            if owner and job.get("owner") and job.get("owner") != owner:
                return False
            if job.get("status") not in {"queued", "running", "cancelling"}:
                return False
            job.update(status="cancelling", message="已请求取消，等待安全检查点")
            return True

    def submit(self, payload: dict[str, object]) -> dict[str, object]:
        job_id = f"FM-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        now = time.monotonic()
        owner = CURRENT_USERNAME.get() or payload.get("_auth_owner")
        store = self.dependencies.resource_limits() if owner else None
        lease = None
        with self.lock:
            active = sum(
                1
                for item in self.jobs.values()
                if item.get("status") in {"queued", "running", "cancelling"}
            )
            if active >= self.dependencies.queue_limit:
                raise RuntimeError("特征预热队列已满，请等待当前任务完成后再试")
            if owner and any(
                item.get("owner") == owner
                and item.get("status") in {"queued", "running", "cancelling"}
                for item in self.jobs.values()
            ):
                raise ResourceLimitExceeded("feature_materialize_already_active", 30)
            if store:
                lease = store.reserve_job(str(owner), job_id)
                payload = {
                    **payload,
                    "_auth_owner": str(owner),
                    "_resource_job_lease": lease.lease_id,
                }
            self.jobs[job_id] = {
                "job_id": job_id,
                "owner": owner,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "等待 PIT 特征预热启动",
                "created_at": datetime.now(UTC).isoformat(),
                "created_monotonic": now,
            }
            finished = [
                key
                for key, item in self.jobs.items()
                if item.get("status") in {"completed", "failed", "cancelled"}
            ]
            for key in sorted(finished)[:-20]:
                self.jobs.pop(key, None)
        try:
            self.dependencies.executor.submit(
                contextvars.copy_context().run, self.run, job_id, payload
            )
        except Exception as error:
            self.update(
                job_id,
                status="failed",
                stage="failed",
                error=str(error),
                code="unknown",
                completed_at=datetime.now(UTC).isoformat(),
            )
            if store and lease:
                store.finish_job(lease)
            raise
        with self.lock:
            return self.view(self.jobs[job_id])

    def run(self, job_id: str, payload: dict[str, object]) -> None:
        owner = payload.get("_auth_owner")
        with user_context(str(owner)) if owner else nullcontext():
            store = self.dependencies.resource_limits() if owner else None
            token = payload.get("_resource_job_lease")
            lease = Lease(str(token), str(owner), job_id) if owner and token else None
            if owner and lease is None:
                self.update(job_id, status="failed", error="missing feature resource lease", code="unknown")
                return
            try:
                with (
                    store.running_job(lease) if store and lease else nullcontext(),
                    tushare_resource_context(str(owner), store) if owner else nullcontext(),
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
                return

    def _run_scoped(self, job_id: str, payload: dict[str, object]) -> None:
        try:
            self.check_cancelled(job_id)
        except FeatureMaterializeCancelled as error:
            self.update(
                job_id,
                status="cancelled",
                stage="cancelled",
                message=str(error),
                error=str(error),
                completed_at=datetime.now(UTC).isoformat(),
            )
            return
        self.update(
            job_id,
            status="running",
            stage="universe",
            progress=5,
            message="正在解析沪深300历史成分",
            started_at=datetime.now(UTC).isoformat(),
            started_monotonic=time.monotonic(),
        )
        context_token = self.active_job.set(job_id)

        def on_progress(fields: dict[str, object]) -> None:
            self.report_progress(**fields)

        def check_cancelled() -> None:
            self.check_cancelled(job_id)

        def data_progress(current: int, total: int, symbol: str) -> None:
            total = max(int(total), 1)
            current = max(0, min(int(current), total))
            # bars stage occupies roughly 25%–85%
            pct = 25 + int(60 * current / total)
            on_progress(
                {
                    "stage": "bars",
                    "progress": pct,
                    "message": f"正在拉取真实行情 {current}/{total}",
                    "symbol_count": total,
                    "processed_symbols": current,
                    "current_symbol": symbol,
                }
            )

        progress_token = DATA_PROGRESS.set(data_progress)
        try:
            result = self.dependencies.execute(payload, on_progress, check_cancelled)
            self.check_cancelled(job_id)
            self.update(
                job_id,
                status="completed",
                stage="completed",
                progress=100,
                message="PIT 特征预热已完成",
                result=result,
                completed_at=datetime.now(UTC).isoformat(),
            )
        except FeatureMaterializeCancelled as error:
            self.update(
                job_id,
                status="cancelled",
                stage="cancelled",
                progress=0,
                message=str(error),
                error=str(error),
                code="cancelled",
                completed_at=datetime.now(UTC).isoformat(),
            )
        except Exception as error:
            LOGGER.exception("Feature materialization job failed: %s", type(error).__name__, exc_info=None)
            failure = classify_materialization_error(error)
            self.update(
                job_id,
                status="failed",
                stage="failed",
                progress=0,
                message=str(failure.get("hint") or "PIT 特征预热失败"),
                error=failure.get("error"),
                code=failure.get("code"),
                hint=failure.get("hint"),
                detail=failure.get("detail"),
                error_type=failure.get("error_type"),
                completed_at=datetime.now(UTC).isoformat(),
            )
        finally:
            DATA_PROGRESS.reset(progress_token)
            self.active_job.reset(context_token)
