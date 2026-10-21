from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from quant.api.paper_routes import handle_paper_cycle
from quant.execution.maintenance import CHINA_TZ, MaintenanceJournal, in_window
from quant.execution.scheduler import PaperScheduler, _next_scheduled_at
from quant.execution.store import PaperStore


def moment(hour=16, minute=0, second=0):
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=CHINA_TZ)


def test_provider_quota_blocks_without_retry_or_partial_completion(tmp_path):
    from quant.api.resource_limits import ResourceLimitExceeded
    store = PaperStore(tmp_path / "paper.sqlite3")
    scheduler = PaperScheduler(store, symbols=["000001.SZ"])
    try:
        with patch("quant.execution.scheduler._china_now", return_value=moment()), patch.object(
            scheduler, "_perform_cycle", side_effect=ResourceLimitExceeded("tushare_hourly_budget", 900)
        ), patch.object(scheduler, "_notify"):
            import pytest
            with pytest.raises(ResourceLimitExceeded):
                scheduler.run_once()
        latest = scheduler.journal.status()["latest"]
        assert latest["status"] == "blocked"
        assert latest["reason_code"] == "tushare_hourly_budget"
        assert latest["result"]["retry_after_seconds"] == 900
        assert latest["result"]["generated_orders"] == 0
        assert scheduler.journal.claim(moment(16, 1), "baseline") is None
    finally:
        store.close()


def test_window_includes_last_second_and_converts_utc():
    assert in_window(moment(16, 10, 59))
    assert not in_window(moment(16, 11))
    assert not in_window(moment(15, 59, 59))
    assert in_window(datetime.fromisoformat("2026-10-09T08:05:00+00:00"))
    assert not in_window(moment() + timedelta(days=1))


def test_account_journal_claim_retry_and_lease_recovery(tmp_path):
    journal = MaintenanceJournal(tmp_path / "paper.sqlite3")
    other = MaintenanceJournal(tmp_path / "paper.sqlite3")
    owner = journal.claim(moment(), "baseline")
    assert owner
    assert other.claim(moment(16, 0, 10), "baseline") is None
    journal.finish("2026-10-09", owner, moment(16, 0, 20), "waiting_data", "data_not_ready", {})
    assert other.claim(moment(16, 0, 25), "baseline") is None
    owner2 = other.claim(moment(16, 1), "baseline")
    assert owner2
    # Simulate abrupt worker death: lease expires, no completion is invented.
    assert journal.claim(moment(16, 2), "baseline") is None
    owner3 = journal.claim(moment(16, 3, 1), "baseline")
    assert owner3
    journal.finish("2026-10-09", owner3, moment(16, 3, 5), "completed", "done", {"generated_orders": 0})
    assert other.claim(moment(16, 4), "baseline") is None
    assert other.status()["latest"]["attempts"] == 3
    assert [entry["status"] for entry in other.status()["attempt_history"]] == ["completed", "interrupted", "waiting_data"]
    assert not other.expire_window(moment(16, 11))


def test_config_change_is_blocked_without_another_execution(tmp_path):
    journal = MaintenanceJournal(tmp_path / "paper.sqlite3")
    owner = journal.claim(moment(), "baseline")
    journal.finish("2026-10-09", owner, moment(), "waiting_data", "wait", {})
    assert journal.claim(moment(16, 1), "momentum") is None
    assert journal.status()["latest"]["status"] == "blocked"


def test_missed_window_persisted_once_without_provider_calls(tmp_path):
    journal = MaintenanceJournal(tmp_path / "paper.sqlite3")
    assert journal.expire_window(moment(16, 11))
    assert not journal.expire_window(moment(16, 12))
    assert not journal.claim(moment(16, 12), "baseline")
    assert journal.status()["latest"]["status"] == "missed_window"


def test_scheduler_deduplicates_before_calendar_and_market_data(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    scheduler = PaperScheduler(store, symbols=["000001.SZ"])
    result = {"cycle_date": "2026-10-09", "generated_orders": 0, "simulated_orders": [], "skipped": False}
    with patch("quant.execution.scheduler._china_now", return_value=moment(16, 5)), patch(
        "quant.execution.scheduler.is_a_share_trading_day", return_value=True
    ) as calendar, patch("quant.execution.scheduler.run_strategy_cycle", return_value=result) as cycle:
        assert _next_scheduled_at(scheduler) == moment(16, 5)
        assert scheduler.run_once() == result
        assert scheduler.run_once()["outcome"] == "maintenance_not_due"
        assert calendar.call_count == cycle.call_count == 1
    restored = PaperScheduler(store, symbols=["000001.SZ"])
    assert restored.status()["last_result"]["cycle_date"] == "2026-10-09"
    store.close()


def test_scheduler_late_start_never_fetches_and_alert_is_deduplicated(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    scheduler = PaperScheduler(store, symbols=["000001.SZ"])
    with patch("quant.execution.scheduler._china_now", return_value=moment(16, 11)), patch(
        "quant.execution.scheduler.is_a_share_trading_day"
    ) as calendar, patch("quant.execution.scheduler.run_strategy_cycle") as cycle:
        scheduler.run_once()
        scheduler.run_once()
        calendar.assert_not_called()
        cycle.assert_not_called()
    from quant.notifications import NotificationOutbox
    assert len(NotificationOutbox(tmp_path / "notifications.sqlite3").status()["events"]) == 1
    store.close()


def test_an_unfinished_cycle_conflict_is_not_reported_as_completed(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    scheduler = PaperScheduler(store, symbols=["000001.SZ"])
    result = {"cycle_date": "2026-10-09", "skipped": True, "generated_orders": 0,
              "simulated_orders": [], "idempotency": {"claimed": False},
              "skip_reason": "其他入口占用"}
    with patch("quant.execution.scheduler._china_now", return_value=moment(16, 5)), patch(
        "quant.execution.scheduler.is_a_share_trading_day", return_value=True
    ), patch("quant.execution.scheduler.run_strategy_cycle", return_value=result):
        scheduler.run_once()
    assert scheduler.status()["maintenance"]["latest"]["status"] == "blocked"
    assert scheduler.status()["maintenance"]["latest"]["reason_code"] == "unfinished_cycle_conflict"
    store.close()


def test_manual_route_uses_configured_scheduler_not_request_defaults():
    scheduler = Mock(strategy="defensive", rebalance="monthly", universe_index="000300.SH")
    scheduler.run_once.return_value = {"skipped": True, "skip_reason": "window", "generated_orders": 0}
    scheduler.status.return_value = {"authority": "backend"}
    deps = Mock()
    deps.scheduler.return_value = scheduler
    deps.risk_limits_payload.return_value = {}
    responses = []
    handle_paper_cycle(lambda status, body: responses.append((status, body)), {}, deps)
    scheduler.run_once.assert_called_once_with()
    assert responses[0][0] == 200
    handle_paper_cycle(lambda status, body: responses.append((status, body)), {"strategy": "baseline"}, deps)
    assert responses[-1][0] == 409
    assert scheduler.run_once.call_count == 1
    handle_paper_cycle(lambda status, body: responses.append((status, body)), {"symbols": ["000001.SZ"]}, deps)
    assert responses[-1][0] == 409
