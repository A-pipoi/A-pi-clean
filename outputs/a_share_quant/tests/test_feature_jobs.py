"""Unit tests for async PIT feature materialization jobs."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant.api.feature_jobs import (
    FeatureJobDependencies,
    FeatureMaterializationJobManager,
)
from quant.api.resource_limits import ResourceLimitExceeded
from quant.api.user_scope import user_context


class _FakeLimits:
    def reserve_job(self, owner, job_id):
        from quant.api.resource_limits import Lease

        return Lease(f"lease-{job_id}", owner, job_id)

    def finish_job(self, lease):
        return None

    def running_job(self, lease):
        from contextlib import nullcontext

        return nullcontext()


def test_submit_runs_async_with_progress_and_result():
    started = threading.Event()

    def execute(payload, on_progress, check_cancelled):
        on_progress({"stage": "universe", "progress": 10, "message": "u", "symbol_count": 2})
        started.set()
        check_cancelled()
        on_progress(
            {
                "stage": "bars",
                "progress": 40,
                "message": "b",
                "symbol_count": 2,
                "processed_symbols": 1,
                "current_symbol": "600000.SH",
            }
        )
        on_progress({"stage": "materialize", "progress": 90, "message": "m", "symbol_count": 2})
        return {"materialization": {"written": 3}, "feature_store": {"rows": 3}, "ok_marker": payload.get("x")}

    with ThreadPoolExecutor(max_workers=1) as pool:
        mgr = FeatureMaterializationJobManager(
            FeatureJobDependencies(
                executor=pool,
                queue_limit=2,
                execute=execute,
                resource_limits=lambda: _FakeLimits(),
            )
        )
        with user_context("alice"):
            job = mgr.submit({"x": 1, "_auth_owner": "alice"})
        assert job["job_id"].startswith("FM-") and job["status"] in {"queued", "running", "completed"}
        assert started.wait(2)
        # wait for completion
        for _ in range(50):
            view = mgr.get(job["job_id"], owner="alice")
            if view and view["status"] == "completed":
                break
            time.sleep(0.05)
        assert view["status"] == "completed"
        assert view["progress"] == 100
        assert view["result"]["ok_marker"] == 1
        assert view["stage"] == "completed"


def test_cancel_cooperative_and_owner_isolation():
    gate = threading.Event()

    def execute(payload, on_progress, check_cancelled):
        on_progress({"stage": "bars", "progress": 30, "message": "wait"})
        gate.wait(2)
        check_cancelled()
        return {}

    with ThreadPoolExecutor(max_workers=1) as pool:
        mgr = FeatureMaterializationJobManager(
            FeatureJobDependencies(
                executor=pool,
                queue_limit=2,
                execute=execute,
                resource_limits=lambda: _FakeLimits(),
            )
        )
        with user_context("alice"):
            job = mgr.submit({"_auth_owner": "alice"})
        job_id = job["job_id"]
        # wait until running
        for _ in range(50):
            view = mgr.get(job_id, owner="alice")
            if view and view["status"] == "running":
                break
            time.sleep(0.05)
        assert mgr.cancel(job_id, owner="bob") is False
        assert mgr.get(job_id, owner="bob") is None
        assert mgr.cancel(job_id, owner="alice") is True
        gate.set()
        for _ in range(50):
            view = mgr.get(job_id, owner="alice")
            if view and view["status"] == "cancelled":
                break
            time.sleep(0.05)
        assert view["status"] == "cancelled"
        assert view["code"] == "cancelled"


def test_failure_keeps_classified_code():
    def execute(payload, on_progress, check_cancelled):
        raise RuntimeError("未配置 TUSHARE_TOKEN，请先设置 Tushare Pro Token")

    with ThreadPoolExecutor(max_workers=1) as pool:
        mgr = FeatureMaterializationJobManager(
            FeatureJobDependencies(
                executor=pool,
                queue_limit=2,
                execute=execute,
                resource_limits=lambda: _FakeLimits(),
            )
        )
        with user_context("alice"):
            job = mgr.submit({"_auth_owner": "alice"})
        for _ in range(50):
            view = mgr.get(job["job_id"], owner="alice")
            if view and view["status"] == "failed":
                break
            time.sleep(0.05)
        assert view["code"] == "tushare_token_missing"
        assert "原始错误" in view["error"]


def test_duplicate_active_job_rejected():
    hold = threading.Event()

    def execute(payload, on_progress, check_cancelled):
        hold.wait(2)
        return {}

    with ThreadPoolExecutor(max_workers=1) as pool:
        mgr = FeatureMaterializationJobManager(
            FeatureJobDependencies(
                executor=pool,
                queue_limit=4,
                execute=execute,
                resource_limits=lambda: _FakeLimits(),
            )
        )
        with user_context("alice"):
            mgr.submit({"_auth_owner": "alice"})
            with pytest.raises(ResourceLimitExceeded):
                mgr.submit({"_auth_owner": "alice"})
        hold.set()
