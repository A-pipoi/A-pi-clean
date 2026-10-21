from quant.research import ResearchLedger


def test_list_cancel_guard_and_terminal_result_retention(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    ledger.create_backtest_job("queued", {"initial_cash": 250000})
    ledger.create_backtest_job("completed", {})
    ledger.update_backtest_job("completed", status="completed", result={"ok": True})
    assert {row["job_id"] for row in ledger.list_backtest_jobs()} == {"queued", "completed"}
    assert ledger.request_backtest_cancel("queued")
    ledger.update_backtest_job("queued", status="running", progress=35)
    assert ledger.get_backtest_job("queued")["status"] == "cancelling"
    assert not ledger.request_backtest_cancel("completed")
    assert ledger.get_backtest_job("completed")["result"] == {"ok": True}
