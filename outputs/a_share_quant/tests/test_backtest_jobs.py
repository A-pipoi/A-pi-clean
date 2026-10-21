from __future__ import annotations

from typing import Any

from quant.api.backtest_jobs import BacktestJobDependencies, BacktestJobManager


class _Ledger:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, object]] = {}
        self.experiment_updates: list[tuple[int, dict[str, object]]] = []

    def create_backtest_job(self, job_id: str, payload: dict[str, object]) -> None:
        self.jobs[job_id] = {"job_id": job_id, "status": "queued", "payload": payload}

    def update_backtest_job(self, job_id: str, **changes: object) -> None:
        self.jobs.setdefault(job_id, {}).update(changes)

    def get_backtest_job(self, job_id: str) -> dict[str, object] | None:
        return self.jobs.get(job_id)

    def update_experiment_parameters(self, experiment_id: int, changes: dict[str, object]) -> None:
        self.experiment_updates.append((experiment_id, changes))


class _Executor:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def submit(self, function: Any, *args: Any, **_kwargs: Any) -> None:
        self.tasks.append((function, args))


def test_local_backtest_job_progress_and_completion_are_auditable() -> None:
    ledger = _Ledger()
    executor = _Executor()
    manager_ref: dict[str, BacktestJobManager] = {}

    def execute(_payload: dict[str, Any]) -> tuple[int, dict[str, object]]:
        manager_ref["manager"].report_progress(63, "回测计算完成")
        return 200, {"ok": True, "research": {"experiment_id": 12}, "metrics": {"sharpe": 0.5}}

    manager = BacktestJobManager(BacktestJobDependencies(
        executor=executor,  # type: ignore[arg-type]
        queue_limit=4,
        ledger=lambda: ledger,
        queue_mode=lambda: "local",
        redis_queue=lambda: (None, None),
        redis_counts=lambda _queue, _connection: {"queued": 0, "running": 0},
        execute=execute,
        remote_worker=lambda _job_id, _payload: None,
    ))
    manager_ref["manager"] = manager

    job_id = manager.submit({"strategy": "baseline"})
    assert manager.view(manager.jobs[job_id])["status"] == "queued"
    assert len(executor.tasks) == 1
    worker, args = executor.tasks[0]
    worker(*args)

    completed = manager.view(manager.jobs[job_id])
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["result"] == {"ok": True, "research": {"experiment_id": 12}, "metrics": {"sharpe": 0.5}}
    assert ledger.experiment_updates[0][0] == 12
    assert ledger.jobs[job_id]["status"] == "completed"


def test_backtest_job_submit_rolls_back_memory_when_ledger_creation_fails() -> None:
    class BrokenLedger(_Ledger):
        def create_backtest_job(self, _job_id: str, _payload: dict[str, object]) -> None:
            raise OSError("ledger unavailable")

    manager = BacktestJobManager(BacktestJobDependencies(
        executor=_Executor(),  # type: ignore[arg-type]
        queue_limit=4,
        ledger=BrokenLedger,
        queue_mode=lambda: "local",
        redis_queue=lambda: (None, None),
        redis_counts=lambda _queue, _connection: {"queued": 0, "running": 0},
        execute=lambda _payload: (200, {"ok": True}),
        remote_worker=lambda _job_id, _payload: None,
    ))

    try:
        manager.submit({"strategy": "baseline"})
    except OSError as error:
        assert str(error) == "ledger unavailable"
    else:
        raise AssertionError("ledger failure should abort job submission")

    assert manager.jobs == {}


def test_queued_cancellation_never_executes_research() -> None:
    ledger, executor = _Ledger(), _Executor()
    executed: list[bool] = []
    manager = BacktestJobManager(BacktestJobDependencies(
        executor=executor,  # type: ignore[arg-type]
        queue_limit=4, ledger=lambda: ledger, queue_mode=lambda: "local",
        redis_queue=lambda: (None, None), redis_counts=lambda q, c: {},
        execute=lambda p: (executed.append(True) or 200, {"ok": True}),
        remote_worker=lambda j, p: None,
    ))
    job_id = manager.submit({"strategy": "baseline"})
    ledger.update_backtest_job(job_id, status="cancelling")
    worker, args = executor.tasks[0]
    worker(*args)
    assert not executed
    assert manager.jobs[job_id]["status"] == "cancelled"


def test_running_cancellation_is_observed_at_progress_checkpoint() -> None:
    ledger, executor = _Ledger(), _Executor()
    manager: BacktestJobManager
    def execute(payload: dict[str, Any]) -> tuple[int, dict[str, object]]:
        job_id = manager.active_job.get()
        assert job_id
        ledger.update_backtest_job(job_id, status="cancelling")
        manager.report_progress(35, "checkpoint")
        raise AssertionError("must stop before execution continues")
    manager = BacktestJobManager(BacktestJobDependencies(
        executor=executor,  # type: ignore[arg-type]
        queue_limit=4, ledger=lambda: ledger, queue_mode=lambda: "local",
        redis_queue=lambda: (None, None), redis_counts=lambda q, c: {},
        execute=execute, remote_worker=lambda j, p: None,
    ))
    job_id = manager.submit({"strategy": "baseline"})
    worker, args = executor.tasks[0]
    worker(*args)
    assert ledger.jobs[job_id]["status"] == "cancelled"
