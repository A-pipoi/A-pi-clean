from datetime import datetime
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant.api.user_scheduler import SHANGHAI, UserPaperScheduler
from quant.api.user_scope import CURRENT_USERNAME


def test_multiuser_workers_preserve_identity_and_bound_start_window():
    users = Mock()
    users.list_users.return_value = [SimpleNamespace(username=name, disabled=False) for name in ("alice", "bob")]
    service = UserPaperScheduler(users, Mock(), Mock())
    barrier = Barrier(2)
    calls = []
    def for_user(username):
        assert CURRENT_USERNAME.get() == username
        def run():
            barrier.wait(timeout=3)
            calls.append(CURRENT_USERNAME.get())
        return SimpleNamespace(run_once=run)
    service.for_user = for_user
    with patch("quant.api.user_scheduler.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 9, 16, 5, tzinfo=SHANGHAI)
        service.run_once()
    assert set(calls) == {"alice", "bob"}
    service.stop()


def test_queue_delay_cannot_start_an_account_at_1611():
    service = UserPaperScheduler(Mock(), Mock(), Mock())
    scheduler = Mock()
    service.for_user = lambda username: scheduler
    with patch("quant.api.user_scheduler.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 9, 16, 11, tzinfo=SHANGHAI)
        service._run_user("alice")
    scheduler.run_once.assert_not_called()
    scheduler.check_missed_window.assert_called_once()
    service.stop()


def test_research_and_formal_dispatch_preserve_separate_schedulers():
    users = Mock()
    users.list_users.return_value = [SimpleNamespace(username="alice", disabled=False)]
    service = UserPaperScheduler(users, Mock(), Mock(), Mock())
    formal, research = Mock(), Mock()
    service.for_user = lambda username: formal
    service.for_research_user = lambda username: research
    with patch("quant.api.user_scheduler.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 9, 16, 5, tzinfo=SHANGHAI)
        service.run_once()
    formal.run_once.assert_called_once()
    research.run_once.assert_called_once()
    service.stop()


def test_weekend_does_not_even_enumerate_or_fetch_users():
    users = Mock()
    service = UserPaperScheduler(users, Mock(), Mock())
    with patch("quant.api.user_scheduler.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 10, 16, 5, tzinfo=SHANGHAI)
        service.run_once()
    users.list_users.assert_not_called()
    service.stop()


def test_start_is_idempotent():
    service = UserPaperScheduler(Mock(), Mock(), Mock())
    service.run_once = Mock()
    service.start()
    thread = service.thread
    service.start()
    assert service.thread is thread
    service.stop()


def test_coordinator_survives_account_enumeration_failure():
    service = UserPaperScheduler(Mock(), Mock(), Mock())
    checks = []
    def broken_once():
        checks.append(True)
        if len(checks) == 1:
            raise RuntimeError("temporary account database error")
        service.stop_event.set()
    service.run_once = broken_once
    with patch.object(service.stop_event, "wait", return_value=False):
        service._loop()
    assert len(checks) == 2
    service.stop()
