import unittest
from concurrent.futures import Future
from datetime import UTC, datetime

import pytest

from quant.api.runtime_cache import DataProbeCache, WorkspaceCache


@pytest.mark.parametrize("cache_class", [DataProbeCache, WorkspaceCache])
def test_cache_failures_redact_payload_and_logs_without_retry(cache_class, caplog):
    cache = cache_class()
    try:
        future = Future()
        cause = RuntimeError("token=private-cause")
        error = ValueError("password=private-error https://private-provider")
        error.__cause__ = cause
        future.set_exception(error)
        cache.future = future

        def forbidden_retry():
            raise AssertionError("must not retry within the failure TTL")

        if isinstance(cache, DataProbeCache):
            payload = cache.status(forbidden_retry)
            assert payload["status"] == "unreachable"
            assert cache.status(forbidden_retry) == payload
        else:
            code, payload = cache.response(forbidden_retry)
            assert code == 502
            assert cache.response(forbidden_retry) == (code, payload)
        assert payload["error"] == "ValueError"
        assert "private-" not in str(payload)
        assert "private-" not in caplog.text
        assert len(caplog.records) == 1
        assert caplog.records[0].exc_info is None
    finally:
        cache.close()


class RuntimeCacheTests(unittest.TestCase):
    def test_probe_failure_is_reported_and_cached_without_retry_storm(self):
        cache = DataProbeCache()
        self.addCleanup(cache.close)
        future = Future()
        future.set_exception(ValueError("provider unavailable"))
        cache.future = future
        result = cache.status(lambda: self.fail("must not retry during TTL"))
        self.assertEqual(result["status"], "unreachable")
        self.assertFalse(result["data_reachable"])
        self.assertIsNone(cache.future)

    def test_probe_returns_snapshot_copy(self):
        cache = DataProbeCache()
        self.addCleanup(cache.close)
        cache.snapshot = {
            "status": "available",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        result = cache.status(lambda: self.fail("fresh probe must be reused"))
        result["status"] = "changed"
        self.assertEqual(cache.snapshot["status"], "available")

    def test_workspace_failure_is_explicit_and_throttled(self):
        cache = WorkspaceCache()
        self.addCleanup(cache.close)
        future = Future()
        future.set_exception(ValueError("data unavailable"))
        cache.future = future
        code, payload = cache.response(
            lambda: self.fail("must not retry during failure TTL")
        )
        self.assertEqual(code, 502)
        self.assertEqual(payload["code"], "workspace_build_failed")
        self.assertIsNone(cache.future)

    def test_close_prevents_new_background_jobs(self):
        cache = WorkspaceCache()
        cache.close()
        with self.assertRaises(RuntimeError):
            cache.response(dict)
