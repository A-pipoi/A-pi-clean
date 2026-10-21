"""Offline SQLite/threads tests; no Redis, network, or market data required."""
from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from quant.api.backtest_jobs import BacktestJobDependencies, BacktestJobManager
from quant.api.resource_limits import (
    ResourceLimitExceeded,
    ResourceLimits,
    limited_tushare_call,
    tushare_resource_context,
)


class ResourceLimitsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "limits.sqlite3"
        self.now = 100000.0

    def store(self, **kwargs):
        return ResourceLimits(self.path, clock=lambda: self.now, **kwargs)

    def test_hourly_budget_is_shared_across_kinds_and_survives_reopen(self):
        store = self.store(hourly_limit=2)
        store.complete(store.reserve("alice", "mining"))
        store.complete(store.reserve("alice", "backtest"))
        with self.assertRaises(ResourceLimitExceeded) as error:
            self.store(hourly_limit=2).reserve("alice", "validation")
        self.assertEqual(error.exception.retry_after, 3600)
        store.reserve("bob", "mining")
        self.now += 3600
        store.reserve("alice", "mining")

    def test_daily_budget_is_rolling(self):
        store = self.store(hourly_limit=1, daily_limit=2)
        store.reserve("alice", "heavy")
        self.now += 3600
        store.reserve("alice", "heavy")
        self.now += 3600
        with self.assertRaises(ResourceLimitExceeded) as error:
            store.reserve("alice", "heavy")
        self.assertEqual(error.exception.reason, "heavy_daily_budget")
        self.now += 86400 - 7200
        store.reserve("alice", "heavy")

    def test_release_refunds_only_uncompleted_work(self):
        store = self.store(hourly_limit=1)
        lease = store.reserve("alice", "heavy")
        store.release(lease)
        store.release(lease)
        lease = store.reserve("alice", "heavy")
        store.complete(lease)
        store.complete(lease)
        store.release(lease)
        with self.assertRaises(ResourceLimitExceeded):
            store.reserve("alice", "heavy")

    def test_retry_after_accounts_for_lowered_limits(self):
        store = self.store(hourly_limit=3)
        for _ in range(3):
            store.reserve("alice", "heavy")
            self.now += 10
        with self.assertRaises(ResourceLimitExceeded) as error:
            self.store(hourly_limit=1).reserve("alice", "heavy")
        self.assertEqual(error.exception.retry_after, 3590)

    def test_concurrent_budget_reservations_across_connections(self):
        stores = [self.store(hourly_limit=4) for _ in range(16)]
        def reserve(store):
            try:
                store.reserve("alice", "heavy")
                return True
            except ResourceLimitExceeded:
                return False
        with ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(sum(pool.map(reserve, stores)), 4)

    def test_job_concurrency_across_connections(self):
        stores = [self.store() for _ in range(12)]
        def reserve(index):
            try:
                return stores[index].reserve_job("alice", f"job-{index}")
            except ResourceLimitExceeded:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(lease is not None for lease in results), 1)
        winner = next(lease for lease in results if lease)
        stores[0].reserve_job("bob", "bob-job")
        stores[0].finish_job(winner)
        stores[1].reserve_job("alice", "next-job")

    def test_expired_job_cannot_start_or_release_replacement(self):
        store = self.store(job_ttl=180)
        old = store.reserve_job("alice", "old")
        self.now += 180
        replacement = store.reserve_job("alice", "new")
        self.assertFalse(store.claim_job(old))
        self.assertFalse(store.renew_job(old))
        store.finish_job(old)
        with self.assertRaises(ResourceLimitExceeded):
            store.reserve_job("alice", "third")
        self.assertTrue(store.claim_job(replacement))

    def test_live_job_renewal_outlives_original_expiry_and_duplicate_claim(self):
        store = self.store(job_ttl=180)
        lease = store.reserve_job("alice", "job")
        self.assertTrue(store.claim_job(lease))
        self.assertFalse(store.claim_job(lease))
        for _ in range(10):
            self.now += 120
            self.assertTrue(store.renew_job(lease))
            with self.assertRaises(ResourceLimitExceeded):
                self.store(job_ttl=180).reserve_job("alice", "another")

    def test_running_context_finishes_on_exception(self):
        store = self.store()
        lease = store.reserve_job("alice", "job")
        with self.assertRaises(ValueError), store.running_job(lease):
            raise ValueError("execute failed")
        store.reserve_job("alice", "next")

    def test_tushare_context_separate_budget_failures_and_reset(self):
        store = self.store(hourly_limit=1, tushare_hourly_limit=1)
        store.reserve("alice", "heavy")
        calls = []
        def fail():
            calls.append(True)
            raise OSError("fake connection error")
        with tushare_resource_context("alice", store):
            with self.assertRaises(OSError):
                limited_tushare_call(fail)
            with self.assertRaises(ResourceLimitExceeded):
                limited_tushare_call(fail)
            with tushare_resource_context("bob", store):
                self.assertEqual(limited_tushare_call(lambda: 42), 42)
        self.assertEqual(calls, [True])
        self.assertEqual(limited_tushare_call(lambda: 43), 43)

    def test_environment_configuration_and_validation(self):
        with patch.dict("os.environ", {"A_PI_HEAVY_HOURLY_LIMIT": "1", "A_PI_HEAVY_DAILY_LIMIT": "2"}):
            store = self.store()
            self.assertEqual(store.budgets["heavy"], (1, 2))
        with self.assertRaises(ValueError):
            self.store(hourly_limit=-1)
        for ttl in (179, 86401, float("nan")):
            with self.assertRaises(ValueError):
                self.store(job_ttl=ttl)

    def test_zero_or_empty_tushare_limits_disable_budget_gate(self):
        with patch.dict(
            "os.environ",
            {"A_PI_TUSHARE_HOURLY_LIMIT": "0", "A_PI_TUSHARE_DAILY_LIMIT": ""},
            clear=False,
        ):
            store = self.store(tushare_hourly_limit=0, tushare_daily_limit=0)
            self.assertEqual(store.budgets["tushare"], (None, None))
            for _ in range(5):
                store.complete(store.reserve("alice", "tushare"))
        with patch.dict("os.environ", {"A_PI_TUSHARE_HOURLY_LIMIT": "unlimited", "A_PI_TUSHARE_DAILY_LIMIT": "none"}):
            store = self.store()
            self.assertEqual(store.budgets["tushare"], (None, None))
            store.complete(store.reserve("alice", "tushare"))


class FakeLedger:
    def __init__(self):
        self.jobs = {}
        self.broken = False

    def create_backtest_job(self, job_id, payload):
        if self.broken:
            raise OSError("ledger failed")
        self.jobs[job_id] = {"status": "queued", "payload": payload}

    def update_backtest_job(self, job_id, **changes):
        self.jobs.setdefault(job_id, {}).update(changes)

    def get_backtest_job(self, job_id):
        return self.jobs.get(job_id)


class FakeDispatch:
    def __init__(self):
        self.tasks = []
        self.broken = False

    def submit(self, function, *args):
        if self.broken:
            raise OSError("executor failed")
        self.tasks.append((function, args))

    def enqueue(self, function, *args, **kwargs):
        if self.broken:
            raise OSError("enqueue failed")
        self.tasks.append((function, args))


class ManagerLimitsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ResourceLimits(Path(self.directory.name) / "limits.sqlite3")
        self.ledger = FakeLedger()
        self.dispatch = FakeDispatch()

    def manager(self, mode="local", execute=None):
        return BacktestJobManager(BacktestJobDependencies(
            executor=self.dispatch, queue_limit=4, ledger=lambda: self.ledger,
            queue_mode=lambda: mode, redis_queue=lambda: (self.dispatch, None),
            redis_counts=lambda *_: {"queued": 0, "running": 0},
            execute=execute or (lambda _: (200, {"ok": True})),
            remote_worker=lambda *_: None, resource_limits=lambda: self.store,
        ))

    def test_local_and_redis_cross_manager_gate_and_worker_cleanup(self):
        for mode in ("local", "redis"):
            with self.subTest(mode=mode):
                first, second = self.manager(mode), self.manager(mode)
                job = first.submit({"_auth_owner": "alice"})
                with self.assertRaises(ResourceLimitExceeded):
                    second.submit({"_auth_owner": "alice"})
                second.run(job, self.ledger.jobs[job]["payload"])
                self.assertEqual(self.ledger.jobs[job]["status"], "completed")
                next_job = second.submit({"_auth_owner": "alice"})
                second.run(next_job, self.ledger.jobs[next_job]["payload"])

    def test_redis_submitter_cache_does_not_block_after_remote_completion(self):
        submitter, worker = self.manager("redis"), self.manager("redis")
        job = submitter.submit({"_auth_owner": "alice"})
        worker.run(job, self.ledger.jobs[job]["payload"])
        self.assertEqual(submitter.jobs[job]["status"], "queued")
        submitter.submit({"_auth_owner": "alice"})

    def test_simultaneous_submissions_on_one_local_manager(self):
        manager = self.manager()
        def submit(_):
            try:
                return manager.submit({"_auth_owner": "alice"})
            except ResourceLimitExceeded:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(submit, range(8)))
        self.assertEqual(sum(job is not None for job in jobs), 1)
        self.assertEqual(len(self.dispatch.tasks), 1)

    def test_submission_failure_releases_job_for_both_modes(self):
        for mode in ("local", "redis"):
            with self.subTest(mode=mode):
                manager = self.manager(mode)
                self.dispatch.broken = True
                with self.assertRaises((OSError, RuntimeError)):
                    manager.submit({"_auth_owner": "alice"})
                self.dispatch.broken = False
                job = manager.submit({"_auth_owner": "alice"})
                manager.run(job, self.ledger.jobs[job]["payload"])

    def test_ledger_failure_releases_job(self):
        manager = self.manager()
        self.ledger.broken = True
        with self.assertRaises(OSError):
            manager.submit({"_auth_owner": "alice"})
        self.assertEqual(manager.jobs, {})
        self.ledger.broken = False
        manager.submit({"_auth_owner": "alice"})

    def test_context_and_internal_metadata_are_scoped(self):
        from quant.api.user_scope import CURRENT_USERNAME
        seen = []
        def execute(payload):
            seen.append((CURRENT_USERNAME.get(), payload))
            limited_tushare_call(lambda: None)
            return 200, {"ok": True}
        manager = self.manager(execute=execute)
        job = manager.submit({"_auth_owner": "alice", "strategy": "baseline"})
        manager.run(job, self.ledger.jobs[job]["payload"])
        self.assertEqual(seen, [("alice", {"strategy": "baseline"})])
        self.assertIsNone(CURRENT_USERNAME.get())

    def test_execution_failure_and_duplicate_delivery(self):
        calls = []
        def execute(_):
            calls.append(True)
            raise ValueError("fake execution failure")
        manager = self.manager(execute=execute)
        job = manager.submit({"_auth_owner": "alice"})
        payload = self.ledger.jobs[job]["payload"]
        manager.run(job, payload)
        manager.run(job, payload)
        self.assertEqual(calls, [True])
        self.assertEqual(self.ledger.jobs[job]["status"], "failed")
        manager.submit({"_auth_owner": "alice"})

    def test_no_owner_does_not_initialize_resource_store(self):
        dependencies = self.manager().dependencies
        from dataclasses import replace
        def forbidden():
            raise AssertionError("legacy path must skip resource store")
        manager = BacktestJobManager(replace(dependencies, resource_limits=forbidden))
        job = manager.submit({"strategy": "baseline"})
        manager.run(job, self.ledger.jobs[job]["payload"])
        self.assertEqual(manager.jobs[job]["status"], "completed")

    def test_authenticated_context_overrides_payload_owner(self):
        from quant.api.user_scope import user_context
        manager = self.manager()
        with user_context("alice"):
            job = manager.submit({"_auth_owner": "bob"})
        self.assertEqual(self.ledger.jobs[job]["payload"]["_auth_owner"], "alice")
        with self.assertRaises(ResourceLimitExceeded):
            self.manager().submit({"_auth_owner": "alice"})
        self.manager().submit({"_auth_owner": "bob"})


if __name__ == "__main__":
    unittest.main()
