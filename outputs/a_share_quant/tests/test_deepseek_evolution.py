import json
from unittest.mock import Mock

import pytest

from deep_factor.deepseek_evolution import (
    DeepSeek,
    Workbench,
    best_effort_logout,
    run,
    safe_feedback,
)


def test_logout_failure_is_logged_once_without_credentials_or_traceback(caplog):
    client = Mock()
    client.call.side_effect = RuntimeError("password=never-log token=never-log")
    best_effort_logout(client)
    client.call.assert_called_once_with("/api/auth/logout", {})
    assert "RuntimeError" in caplog.text
    assert "never-log" not in caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None


def test_logout_does_not_mask_original_failure(caplog):
    client = Mock()
    client.call.side_effect = OSError("private-provider-url")
    original = ValueError("original-research-failure")
    with pytest.raises(ValueError) as raised:
        try:
            raise original
        finally:
            best_effort_logout(client)
    assert raised.value is original
    assert "private-provider-url" not in caplog.text


def test_logout_success_is_silent_and_interrupts_are_not_swallowed(caplog):
    client = Mock()
    best_effort_logout(client)
    client.call.assert_called_once_with("/api/auth/logout", {})
    assert not caplog.records
    client.call.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        best_effort_logout(client)


def test_feedback_whitelist_excludes_sealed_and_credentials():
    feedback = {
        "sealed_dates_used": False,
        "secret": "never_send",
        "sealed_result": {"p": 0},
        "top_candidates": [
            {
                "expression": "close",
                "training_feedback": {
                    "coverage": 0.9,
                    "train_icir": {"5": 0.1},
                    "password": "never_send",
                    "sealed_ic": 1,
                },
            }
        ],
    }
    safe = json.dumps(safe_feedback(feedback))
    assert (
        "never_send" not in safe
        and "sealed_ic" not in safe
        and "sealed_result" not in safe
    )
    assert safe_feedback(feedback)["candidates"][0]["train_icir"]["10"] is None
    with pytest.raises(ValueError):
        safe_feedback({"sealed_dates_used": True})


@pytest.mark.parametrize(
    "base",
    ["http://127.0.0.1:8000", "https://example.com", "https://alice@localhost:8000"],
)
def test_workbench_rejects_untrusted_destinations(base, tmp_path):
    with pytest.raises(ValueError):
        Workbench(base, tmp_path / "missing.pem")


def test_client_cannot_call_accept_or_promote():
    client = object.__new__(Workbench)
    for path in (
        "/api/factor-mining/accept",
        "/api/factor-mining/promote",
        "/api/mining/evolution/" + "a" * 32 + "/package/accept",
    ):
        with pytest.raises(ValueError):
            client.call(path, {})


def test_unknown_post_outcome_stops_without_second_provider_or_replay(tmp_path):
    client, proposer = Mock(), Mock()
    client.call.side_effect = [
        {"run": {"session_id": "a" * 32}},
        RuntimeError("unknown"),
    ]
    proposer.propose.return_value = ["close"]
    output = tmp_path / "run"
    with pytest.raises(RuntimeError):
        run(client, proposer, output, count=1)
    assert proposer.propose.call_count == 1 and client.call.call_count == 2
    report = json.loads((output / "report.json").read_text())
    assert report["sealed_opened"] is False
    assert report["generations"][0]["expressions"] == ["close"]
    assert report["stage"] == "stopped_no_retry"


def test_provider_invalid_json_never_retries(monkeypatch):
    call = Mock(
        return_value={
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
        }
    )
    monkeypatch.setattr("deep_factor.deepseek_evolution.request_json", call)
    with pytest.raises(RuntimeError):
        DeepSeek("test-key").propose(1, 1, {"sealed_dates_used": False})
    assert call.call_count == 1


@pytest.mark.parametrize("model", [None, "unexpected-model"])
def test_provider_model_mismatch_is_rejected_without_retry(monkeypatch, model):
    call = Mock(
        return_value={
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"expressions":["close"]}'},
                }
            ],
        }
    )
    monkeypatch.setattr("deep_factor.deepseek_evolution.request_json", call)
    with pytest.raises(RuntimeError, match="invalid_proposal"):
        DeepSeek("test-key").propose(1, 1, {"sealed_dates_used": False})
    assert call.call_count == 1
