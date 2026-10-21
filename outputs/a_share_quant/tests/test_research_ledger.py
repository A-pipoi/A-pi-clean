import tempfile
import unittest

from quant.research.ledger import ExperimentRecord, ResearchLedger


class ResearchLedgerTests(unittest.TestCase):
    def test_budget_debits_include_failures_and_saved_result_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3", budget=2)
            experiment_id = ledger.record(ExperimentRecord(
                strategy_version="baseline", data_version="tushare:test",
                parameters={"kind": "backtest"}, status="started", kind="backtest",
            ))
            ledger.update_result(experiment_id, status="failed", notes="execution failed")
            ledger.create_backtest_job("BT-test", {"strategy": "baseline"})
            ledger.update_backtest_job("BT-test", status="completed", result={
                "research": {"experiment_id": experiment_id},
            })
            entry = ledger.experiments("baseline")[0]
            self.assertEqual(entry["status"], "failed")
            self.assertEqual(entry["reason"], "execution failed")
            self.assertEqual(entry["result_url"], "/api/backtest/BT-test")
            self.assertEqual(ledger.status("baseline")["used"], 1)

    def test_kind_counts_and_archive_release_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3", budget=2)
            for kind in ("backtest", "sensitivity"):
                ledger.record(
                    ExperimentRecord(
                        strategy_version="baseline",
                        data_version="tushare:test",
                        parameters={"kind": kind},
                        status="passed",
                        kind=kind,
                    )
                )
            status = ledger.status("baseline")
            self.assertEqual(status["used"], 2)
            self.assertEqual(status["kind_counts"], {"backtest": 1, "sensitivity": 1})
            self.assertFalse(status["can_record"])

            archived = ledger.archive("baseline", reason="开始新的验证周期")
            self.assertEqual(archived["archived_count"], 2)
            self.assertTrue(ledger.status("baseline")["can_record"])
            self.assertEqual(ledger.status("baseline")["archived_count"], 2)

    def test_backtest_job_survives_process_memory_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3")
            ledger.create_backtest_job("BT-test", {"strategy": "baseline"})
            ledger.update_backtest_job(
                "BT-test",
                status="failed",
                progress=0,
                message="真实 Tushare 回测失败",
                error="provider unavailable",
            )
            restored = ledger.get_backtest_job("BT-test")
            self.assertEqual(restored["status"], "failed")
            self.assertEqual(restored["error"], "provider unavailable")

    def test_experiment_parameters_merge_derived_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ResearchLedger(f"{directory}/research.sqlite3")
            experiment_id = ledger.record(
                ExperimentRecord(
                    strategy_version="baseline",
                    data_version="tushare:test",
                    parameters={"kind": "backtest", "lookback": 20},
                    status="started",
                    kind="backtest",
                )
            )
            merged = ledger.update_experiment_parameters(
                experiment_id,
                {"monthly_returns": [{"month": "2026-01", "return": 0.01}]},
            )
            self.assertEqual(merged["lookback"], 20)
            self.assertEqual(merged["monthly_returns"][0]["month"], "2026-01")
            self.assertEqual(ledger.latest("baseline")["parameters"]["monthly_returns"][0]["return"], 0.01)


if __name__ == "__main__":
    unittest.main()
