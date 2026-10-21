from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant.api.backtest_jobs import BacktestJobDependencies, BacktestJobManager
from quant.api.factor_mining_routes import (
    FactorMiningRouteDependencies,
    handle_factor_mining_request,
)

SECRET = "synthetic-secret-not-a-real-key"


class Ledger:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, object]] = {}

    def get_backtest_job(self, job_id: str) -> dict[str, object] | None:
        return self.jobs.get(job_id)

    def update_backtest_job(self, job_id: str, **changes: object) -> None:
        self.jobs.setdefault(job_id, {}).update(changes)


@pytest.mark.parametrize(
    ("error", "prefix"),
    [
        (TimeoutError(SECRET), "回测任务连接失败"),
        (OSError(SECRET), "回测任务连接失败"),
        (json.JSONDecodeError(SECRET, SECRET, 0), "回测任务连接失败"),
        (ArithmeticError(SECRET), "回测任务失败"),
    ],
)
def test_backtest_failure_is_redacted_and_context_reset(error, prefix, caplog):
    ledger = Ledger()

    def execute(_payload):
        raise error from RuntimeError(SECRET)

    with ThreadPoolExecutor(max_workers=1) as executor:
        manager = BacktestJobManager(
            BacktestJobDependencies(
                executor=executor,
                queue_limit=1,
                ledger=lambda: ledger,
                queue_mode=lambda: "local",
                redis_queue=lambda: (None, None),
                redis_counts=lambda _q, _c: {},
                execute=execute,
                remote_worker=lambda _id, _p: None,
            )
        )
        manager.jobs["test-job"] = {"job_id": "test-job", "status": "queued"}
        outer_token = manager.active_job.set("outer-job")
        try:
            manager._run_scoped("test-job", {})
            assert manager.active_job.get() == "outer-job"
        finally:
            manager.active_job.reset(outer_token)
        job = ledger.jobs["test-job"]
        assert job["status"] == "failed"
        assert job["progress"] == 0
        assert job["completed_at"]
        assert job["error"] == f"{prefix}：{type(error).__name__}"
        assert SECRET not in str(manager.view(manager.jobs["test-job"]))
    assert SECRET not in caplog.text
    assert type(error).__name__ in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("error_type", [RuntimeError, ArithmeticError])
@pytest.mark.parametrize("coded", [False, True])
def test_mining_failure_retains_http_and_code_contract(error_type, coded, caplog):
    class CodedError(error_type):
        api_code = "research_evaluation_failed"

    error = CodedError(SECRET) if coded else error_type(SECRET)

    def fail(_payload):
        raise error from RuntimeError(SECRET)

    status, body = handle_factor_mining_request(
        "/api/factor-mining/run",
        {},
        FactorMiningRouteDependencies(
            run=fail,
            evaluate=fail,
            accept=lambda _id: {},
            promote=lambda _id: {},
            status=dict,
        ),
    )
    expected_status = 502 if error_type is RuntimeError else 500
    expected_code = (
        "data_provider_unavailable"
        if error_type is RuntimeError
        else "factor_mining_failed"
    )
    assert status == (409 if coded else expected_status)
    assert body == {
        "ok": False,
        "code": "research_evaluation_failed" if coded else expected_code,
        "error": type(error).__name__,
    }
    assert SECRET not in caplog.text
    assert type(error).__name__ in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_ledger_failure_keeps_in_memory_state_without_leaking(caplog):
    class BrokenLedger(Ledger):
        def update_backtest_job(self, job_id, **changes):
            raise OSError(SECRET) from RuntimeError(SECRET)

    with ThreadPoolExecutor(max_workers=1) as executor:
        manager = BacktestJobManager(
            BacktestJobDependencies(
                executor=executor,
                queue_limit=1,
                ledger=BrokenLedger,
                queue_mode=lambda: "local",
                redis_queue=lambda: (None, None),
                redis_counts=lambda _q, _c: {},
                execute=lambda _p: (200, {"ok": True}),
                remote_worker=lambda _id, _p: None,
            )
        )
        manager.jobs["test-job"] = {}
        manager.update("test-job", status="failed", progress=0)
        assert manager.jobs["test-job"]["status"] == "failed"
    assert "OSError" in caplog.text
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
