from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quant.api.research_paper import research_paper_request
from quant.api.user_scope import UserScopedStores, user_context
from quant.data.schema import Bar
from quant.execution.research_account import ResearchPaperAccount
from quant.execution.service import run_strategy_cycle
from quant.execution.store import PaperStore
from quant.research.ledger import ResearchBudgetExceeded, ResearchLedger


def enable(account):
    return account.enable(strategy="baseline", rebalance="weekly", index="000300.SH", factors=[], code_hash="test")


def test_account_is_empty_separate_and_frozen(tmp_path):
    formal = PaperStore(tmp_path / "paper.sqlite3")
    account = ResearchPaperAccount(formal.path)
    try:
        formal.create_order("000001.SZ", "BUY", 100)
        config = enable(account)
        assert not account.store.state()["orders"]
        assert account.store.path.parent != formal.path.parent
        assert config["sealed_passed"] is False and config["conclusion_ready"] is False
        assert enable(account) == config
        with pytest.raises(ValueError, match="冻结"):
            account.enable(strategy="momentum", rebalance="weekly", index="000300.SH", factors=[], code_hash="test")
        account.pause()
        assert ResearchPaperAccount.verify_store(account.store) is False
        enable(account)
        assert ResearchPaperAccount.verify_store(account.store) is True
        assert len(formal.state()["orders"]) == 1
    finally:
        formal.close()
        account.store.close()


def test_research_flag_cannot_be_applied_to_formal_store(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    try:
        with patch("quant.execution.service.TushareDailyAdapter") as data:
            with pytest.raises(ValueError, match="独立研究账户"):
                run_strategy_cycle(store, symbols=["000001.SZ"], execution_mode="research", research_observer=Mock())
            data.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("fresh,limits", [(False, True), (True, False), (True, True)])
def test_research_keeps_data_gates_but_does_not_claim_validation(tmp_path, fresh, limits):
    account = ResearchPaperAccount(tmp_path / "paper.sqlite3")
    enable(account)
    today = date(2026, 10, 9)
    bars = [Bar(symbol="000001.SZ", date=today-timedelta(days=30-i), open=10, high=11, low=9, close=10, volume=100000) for i in range(31)]
    snapshot = SimpleNamespace(version="real-test", metadata={"limits_available": limits,
        "data_source": "tushare_live", "latest_trade_date": today.isoformat() if fresh else "2026-10-08"})
    adapter = SimpleNamespace(daily_bars=lambda **kw: bars, snapshot=lambda: snapshot)
    observer = Mock()
    try:
        with patch("quant.execution.service._china_today", return_value=today), patch("quant.execution.service.is_a_share_trading_day", return_value=True), patch("quant.execution.service.TushareDailyAdapter", return_value=adapter), patch("quant.execution.service.validate_strategy") as validate, patch("quant.execution.service.get_tushare_stock_metadata", return_value=({}, False)), patch("quant.execution.corporate_actions.observed_events", return_value=[]), patch("quant.execution.service._execute_prepared_cycle", return_value={"skipped": False, "generated_orders": 0}) as execute:
            result = run_strategy_cycle(account.store, symbols=["000001.SZ"], execution_mode="research", research_observer=observer, universe_snapshot={"mode": "pit_index_members"}, sealed_test={"passed": False})
        validate.assert_not_called()
        assert result["sealed_passed"] is False and result["conclusion_ready"] is False
        if fresh and limits:
            execute.assert_called_once()
            observer.assert_called_once_with(today.isoformat(), "real-test")
            assert execute.call_args.kwargs["validation_payload"]["passed"] is False
        else:
            execute.assert_not_called()
            observer.assert_not_called()
            assert result["generated_orders"] == 0 and result["skipped"] is True
    finally:
        account.store.close()


def test_exposure_is_permanent_and_does_not_consume_budget(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    ledger.record_paper_exposure("2026-10-09", "frozen", "real")
    ledger.record_paper_exposure("2026-10-09", "frozen", "real")
    ledger.archive("baseline", reason="not a reset of exposure")
    with pytest.raises(ResearchBudgetExceeded, match="已观察"):
        ledger.create_sealed_test(strategy_version="baseline", factor_pipeline_version="pit", data_version="real", sealed_start="2026-10-01", sealed_end="2026-10-09", dataset_hash="digest", bars=[], metadata={}, parameters={})
    with pytest.raises(ResearchBudgetExceeded):
        ledger.assert_unexposed_interval("2026-10-01", "2026-10-09")
    ledger.assert_unexposed_interval("2026-10-10", "2026-12-31")
    assert ledger.count("baseline") == 0


def test_account_scope_and_authorization(tmp_path):
    stores = UserScopedStores(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="authenticated"):
            research_paper_request("enable", stores, Mock())
        with user_context("alice"):
            assert research_paper_request("status", stores, None)["enabled"] is False
            enable(stores.research_account("alice"))
        with user_context("bob"):
            assert research_paper_request("status", stores, None)["enabled"] is False
    finally:
        stores.close()
