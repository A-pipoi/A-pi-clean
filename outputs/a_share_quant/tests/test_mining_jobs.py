import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from quant.api.mining_jobs import (
    MiningJobManager,
    mining_requested_range,
    report_mining_progress,
)
from quant.api.resource_limits import ResourceLimitExceeded, ResourceLimits
from quant.api.user_scope import CURRENT_USERNAME


class DeferredExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


def make_manager(tmp_path: Path, executor):
    limits = ResourceLimits(tmp_path / "limits.sqlite3")
    return MiningJobManager(tmp_path / "jobs.sqlite3", executor, limits), limits


def test_duplicate_submission_and_owner_isolation(tmp_path):
    executor = DeferredExecutor()
    manager, limits = make_manager(tmp_path, executor)
    job = manager.submit(
        "alice", "v1", ("2025-01-01", "2026-01-01"), lambda: {"run_id": "r"}
    )
    assert (
        manager.submit("alice", "v1", ("ignored", "ignored"), dict)["job_id"]
        == job["job_id"]
    )
    assert len(executor.calls) == 1
    assert manager.latest("bob", "v1") is None
    assert not manager.cancel("bob", "v1", job["job_id"])
    with pytest.raises(ResourceLimitExceeded):
        limits.reserve_job("alice", "backtest")


def test_worker_reports_progress_then_records_run_and_releases_lease(tmp_path):
    executor = DeferredExecutor()
    manager, limits = make_manager(tmp_path, executor)

    def execute():
        assert CURRENT_USERNAME.get() == "alice"
        assert mining_requested_range() == ("2025-01-01", "2026-01-01")
        report_mining_progress("daily_bars", progress_current=3, progress_total=4)
        assert manager.latest("alice", "v1")["progress_current"] == 3
        return {"run_id": "r1"}

    manager.submit("alice", "v1", ("2025-01-01", "2026-01-01"), execute)
    fn, args = executor.calls[0]
    fn(*args)
    result = manager.latest("alice", "v1")
    assert (result["status"], result["stage"], result["run_id"]) == (
        "completed",
        "completed",
        "r1",
    )
    assert CURRENT_USERNAME.get() is None
    limits.finish_job(limits.reserve_job("alice", "next"))


def test_cancel_before_worker_does_not_seal_or_evaluate(tmp_path):
    executor = DeferredExecutor()
    manager, limits = make_manager(tmp_path, executor)
    invoked = []
    job = manager.submit(
        "alice", "v1", ("2025-01-01", "2026-01-01"), lambda: invoked.append(True)
    )
    assert manager.cancel("alice", "v1", job["job_id"])
    fn, args = executor.calls[0]
    fn(*args)
    assert not invoked
    assert manager.latest("alice", "v1")["status"] == "cancelled"
    limits.finish_job(limits.reserve_job("alice", "next"))


def test_recovery_marks_dead_worker_interrupted_not_sealed(tmp_path):
    executor = DeferredExecutor()
    manager, limits = make_manager(tmp_path, executor)
    job = manager.submit("alice", "v1", ("2025-01-01", "2026-01-01"), dict)
    with sqlite3.connect(manager.path) as db:
        db.execute("UPDATE mining_jobs SET pid=123456 WHERE job_id=?", (job["job_id"],))
    recovered = MiningJobManager(manager.path, executor, limits)
    with patch("quant.api.mining_jobs._process_alive", return_value=False):
        result = recovered.latest("alice", "v1")
    assert result["status"] == "interrupted"
    assert "run_id" not in result
    assert (
        recovered.submit("alice", "v1", ("2025-01-01", "2026-01-01"), dict)["job_id"]
        != job["job_id"]
    )


def test_submit_returns_without_waiting_for_provider(tmp_path):
    entered, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        manager, _ = make_manager(tmp_path, executor)

        def execute():
            entered.set()
            assert release.wait(5)
            return {"run_id": "r1"}

        try:
            job = manager.submit("alice", "v1", ("2025-01-01", "2026-01-01"), execute)
            assert entered.wait(5)
            assert job["stage"] == "dataset_sealing"
            assert manager.latest("alice", "v1")["status"] == "running"
        finally:
            release.set()


def test_global_capacity_and_failure_release(tmp_path):
    executor = DeferredExecutor()
    manager, limits = make_manager(tmp_path, executor)
    for owner in ("alice", "bob", "carol", "dave"):
        manager.submit(owner, "v1", ("a", "b"), dict)
    with pytest.raises(ResourceLimitExceeded, match="operation_global_capacity"):
        manager.submit("eve", "v1", ("a", "b"), dict)
    limits.finish_job(limits.reserve_job("eve", "next"))
