import pytest

from quant.notifications import NotificationOutbox, queue_decay_alerts, smtp_sender


def test_disabled_delivery_fails_closed(monkeypatch):
    monkeypatch.delenv("A_PI_NOTIFY_ENABLED", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        smtp_sender()


def test_outbox_deduplicates_retries_redacts_error_and_delivers(tmp_path, caplog):
    box = NotificationOutbox(tmp_path / "notify.sqlite3")
    assert box.enqueue("test:1", "测试", "无真实账户数据")
    assert not box.enqueue("test:1", "测试", "重复")

    def fail(*args):
        raise RuntimeError("password=do-not-store")

    assert box.dispatch_one(fail, now=100) == "retry"
    assert box.dispatch_one(fail, now=101) == "empty"
    assert box.status()["events"][0]["error"] == "RuntimeError"
    assert "RuntimeError" in caplog.text
    assert "do-not-store" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    sent = []
    assert box.dispatch_one(lambda *args: sent.append(args), now=131) == "sent"
    assert len(sent) == 1 and box.dispatch_one(fail, now=1000) == "empty"


def test_delivery_failure_stops_after_five_attempts_without_secret_logs(
    tmp_path, caplog
):
    box = NotificationOutbox(tmp_path / "notify.sqlite3")
    box.enqueue("retry-limit", "Generic title", "Generic body")
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError("smtp-password=never-log")

    for index in range(5):
        expected = "retry" if index < 4 else "failed"
        assert box.dispatch_one(fail, now=1000 * (index + 1)) == expected
    assert box.dispatch_one(fail, now=10000) == "empty"
    state = box.status()["events"][0]
    assert state["status"] == "failed"
    assert state["attempts"] == 5
    assert state["error"] == "RuntimeError"
    assert len(calls) == len(caplog.records) == 5
    assert "never-log" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_account_paths_are_separate(tmp_path):
    left, right = (
        NotificationOutbox(tmp_path / "alice" / "notify.sqlite3"),
        NotificationOutbox(tmp_path / "bob" / "notify.sqlite3"),
    )
    left.enqueue("private", "私有事件", "只属于alice")
    assert right.status()["events"] == []


def test_saved_decay_alerts_are_generic_deduplicated_and_never_sent(tmp_path):
    ledger = tmp_path / "research.sqlite3"
    result = {
        "candidates": [
            {
                "factor_key": "secret-factor",
                "evaluation": {
                    "horizons": {"5": {"decay_monitor": {"status": "alert"}}}
                },
            }
        ]
    }
    assert queue_decay_alerts(ledger, "run:1", result) == "queued"
    assert queue_decay_alerts(ledger, "run:1", result) == "queued"
    state = NotificationOutbox(tmp_path / "notifications.sqlite3").status()
    assert len(state["events"]) == 1
    assert "secret-factor" not in str(state)
    assert state["events"][0]["status"] == "pending"
    assert queue_decay_alerts(ledger, "run:2", {}) == "no_alerts"
