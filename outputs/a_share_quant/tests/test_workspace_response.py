import unittest
from concurrent.futures import Future
from unittest.mock import patch

from quant import api_server
from quant.api.runtime_cache import WorkspaceCache
from quant.api.workspace_snapshot import _workspace_seal_or_readonly
from quant.research import ResearchBudgetExceeded, ResearchLedger


class WorkspaceResponseTests(unittest.TestCase):
    def test_exposed_seal_blocks_new_seal_but_readonly_workspace_uses_earliest_cutoff(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(Path(directory) / "research.sqlite3")
            old = ledger.create_sealed_test(
                strategy_version="baseline", factor_pipeline_version="old",
                data_version="real", sealed_start="2026-07-02", sealed_end="2026-09-23",
                dataset_hash="a", bars=[], metadata={}, parameters={},
            )
            ledger.record_sealed_result(old["id"], status="passed", result={"passed": True})
            ledger.archive("baseline", reason="completed")

            def reject():
                raise ResearchBudgetExceeded("overlapping evaluated interval")

            view, blocked = _workspace_seal_or_readonly(ledger, reject)
            self.assertTrue(blocked)
            self.assertEqual(view["sealed_start"], "2026-07-02")
            self.assertEqual(view["status"], "overlap_blocked")
            self.assertIsNone(view["id"])
            self.assertIsNone(ledger.get_sealed_test("baseline", "new"))

    def test_paper_positions_include_tushare_identity_without_changing_store_state(self) -> None:
        state = {
            "equity": 100_000.0,
            "positions": [{"symbol": "000001.SZ", "market_value": 20_000.0, "quote_date": "2026-09-23"}],
        }
        identity = {"000001.SZ": {"name": "平安银行", "industry": "银行"}}
        with patch.object(api_server, "PAPER_STORE") as store, \
             patch.object(api_server, "get_tushare_stock_metadata", return_value=(identity, False)), \
             patch.object(api_server, "_risk_limits_payload", return_value={}):
            store.cycle_summary.return_value = {}
            store.recent_cycles.return_value = []
            enriched = api_server._enrich_paper_state(state)
        self.assertEqual(enriched["positions"][0]["name"], "平安银行")
        self.assertEqual(enriched["positions"][0]["industry"], "银行")
        self.assertEqual(enriched["positions"][0]["quote_date"], "2026-09-23")
        self.assertEqual(enriched["industry_exposure"][0]["industry"], "银行")
        self.assertNotIn("name", state["positions"][0])

    def test_missing_stock_metadata_does_not_invent_an_industry(self) -> None:
        state = {"equity": 100_000.0, "positions": [{"symbol": "000001.SZ", "market_value": 20_000.0}]}
        with patch.object(api_server, "PAPER_STORE") as store, \
             patch.object(api_server, "get_tushare_stock_metadata", return_value=({}, True)), \
             patch.object(api_server, "_risk_limits_payload", return_value={}):
            store.cycle_summary.return_value = {}
            store.recent_cycles.return_value = []
            enriched = api_server._enrich_paper_state(state)
        self.assertEqual(enriched["positions"][0]["industry"], "")
        self.assertEqual(enriched["industry_exposure"][0]["industry"], "未分类")
        self.assertTrue(enriched["industry_metadata_stale"])

    def test_expensive_build_is_reported_as_in_progress(self) -> None:
        future = Future()
        cache = WorkspaceCache()
        self.addCleanup(cache.close)
        cache.future = future
        status, payload = cache.response(lambda: self.fail("Must reuse the active build"))
        self.assertEqual(status, 202)
        self.assertEqual(payload["code"], "workspace_building")

    def test_completed_build_returns_real_snapshot(self) -> None:
        future = Future()
        future.set_result({"ok": True, "source": "tushare", "latest_trade_date": "2026-09-23"})
        cache = WorkspaceCache()
        self.addCleanup(cache.close)
        cache.future = future
        status, payload = cache.response(lambda: self.fail("Must reuse the completed build"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "tushare")
        self.assertEqual(payload["snapshot_age_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
