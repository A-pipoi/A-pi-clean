from unittest.mock import Mock, patch

import pytest

from quant.api.paper_routes import handle_paper_cycle
from quant.execution.maintenance import MaintenanceJournal
from quant.execution.scheduler import PaperScheduler
from quant.execution.store import PaperStore
from tests.test_paper_cycle_recovery import cycle_inputs
from tests.test_paper_maintenance import moment


def test_manual_claim_preserves_missed_window_and_is_one_shot(tmp_path):
    journal = MaintenanceJournal(tmp_path / "paper.sqlite3")
    journal.expire_window(moment(21))
    original = journal.status()["latest"]
    owner = journal.claim_manual(moment(21), "config", "用户明确同意补跑")
    assert owner
    assert journal.claim_manual(moment(21, 1), "config", "duplicate") is None
    journal.finish_manual("2026-10-09", owner, moment(21, 2), "blocked", {"gate": "sealed"})
    status = journal.status()
    assert status["latest"] == original
    assert status["manual_catchups"][0]["status"] == "blocked"
    assert status["attempt_history"] == []


def test_manual_cannot_replay_attempted_account_or_run_before_window(tmp_path):
    journal = MaintenanceJournal(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError):
        journal.claim_manual(moment(16), "config", "reason")
    owner = journal.claim(moment(), "config")
    journal.finish("2026-10-09", owner, moment(), "failed", "failure", {})
    journal.expire_window(moment(21))
    assert journal.claim_manual(moment(21), "config", "reason") is None


def test_scheduler_manual_keeps_gates_and_defers_all_fills(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    scheduler = PaperScheduler(store, symbols=["000001.SZ"])
    scheduler.journal.expire_window(moment(21))
    result = {"skipped": True, "outcome": "blocked", "paper_trading_allowed": False}
    with patch("quant.execution.scheduler._china_now", return_value=moment(21)), patch.object(
        scheduler, "_perform_cycle", return_value=result
    ) as cycle:
        actual = scheduler.run_manual_catchup(expected_day="2026-10-09", reason="approved")
        cycle.assert_called_once_with(defer_pending_fills=True)
        assert actual["manual_catchup"]["retroactive_fills"] is False
        assert scheduler.run_manual_catchup(expected_day="2026-10-09", reason="approved")["outcome"] == "manual_catchup_not_due"
        with pytest.raises(ValueError):
            scheduler.run_manual_catchup(expected_day="2026-10-08", reason="approved")
    store.close()


def test_route_requires_explicit_boolean_day_and_reason():
    scheduler = Mock(strategy="baseline", rebalance="weekly", universe_index="000300.SH")
    scheduler.run_manual_catchup.return_value = {"skipped": True, "outcome": "blocked"}
    deps = Mock()
    deps.scheduler.return_value = scheduler
    responses = []
    send = lambda code, body: responses.append((code, body))
    handle_paper_cycle(send, {"manual_catchup": "true"}, deps)
    assert responses[-1][0] == 400
    handle_paper_cycle(send, {"manual_catchup": True}, deps)
    assert responses[-1][0] == 400
    handle_paper_cycle(send, {"manual_catchup": True, "expected_day": "2026-10-09", "reason": "approved"}, deps)
    scheduler.run_manual_catchup.assert_called_once_with(expected_day="2026-10-09", reason="approved")
    scheduler.run_once.assert_not_called()


def test_manual_service_never_fills_old_order_and_keeps_new_order_pending(tmp_path):
    from datetime import date

    from quant.backtest.engine import Order
    from quant.execution.service import run_strategy_cycle

    store = PaperStore(tmp_path / "paper.sqlite3")
    old = store.create_order("000001.SZ", "BUY", 100,
                             signal_date=date(2026, 9, 23), source="strategy:baseline")
    with cycle_inputs(orders=[Order("000002.SZ", "BUY", 100)]), patch.object(
        store, "execute_order", side_effect=AssertionError("manual catchup attempted fill")
    ):
        result = run_strategy_cycle(store, symbols=["000001.SZ", "000002.SZ"], defer_pending_fills=True)
    assert store.order(old["order_id"])["status"] == "NEW"
    assert all(order["status"] == "NEW" for order in result["simulated_orders"])
    assert store.state()["cash"] == 1_000_000
    assert store.state()["positions"] == []
    store.close()
