import json
from unittest.mock import Mock

import pytest

from deep_factor.llm_arena import (
    ArenaProposer,
    complete_authorized_glm_retry,
    run_training_arena,
)
from tests.test_mining_arena import arena_box as shared_arena_box
from tests.test_mining_arena import create, participant


@pytest.fixture
def arena_box(tmp_path):
    return shared_arena_box.__wrapped__(tmp_path)


@pytest.mark.parametrize("model", ["deepseek-flash", "glm-5.3-flash"])
def test_provider_whitelist_and_parameters(monkeypatch, model):
    calls = []

    def response(opener, url, **kwargs):
        calls.append((url, kwargs))
        return {
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"expressions":["cs_rank(close)"]}'},
                }
            ],
        }

    monkeypatch.setattr("deep_factor.llm_arena.request_json", response)
    proposer = ArenaProposer(model, "test-credential-not-real")
    proposer.propose_one(
        1,
        {
            "dsl_guide": "guide",
            "registry_summary": [{"expression": "volume"}],
            "label_horizons": [5],
            "sealed_dates_used": False,
            "sealed_start": "DO-NOT-EXPORT",
            "token": "DO-NOT-EXPORT",
        },
        {"sealed_dates_used": False, "private": "DO-NOT-EXPORT"},
    )
    url, request = calls[0]
    assert "DO-NOT-EXPORT" not in json.dumps(request["body"])
    assert request["body"]["thinking"]["type"] == (
        "enabled" if model.startswith("glm-") else "disabled"
    )
    assert url.startswith("https://")


def test_four_calls_four_evaluations_and_close_only(arena_box, tmp_path):
    from quant.api.arena_service import arena_request
    from quant.api.user_scope import user_context

    box = arena_box
    created = create(box, proposers=["deepseek-flash", "glm-5.3-flash"])
    proposers = {}
    for model, expressions in zip(
        ["deepseek-flash", "glm-5.3-flash"],
        [
            ["cs_rank(close)", "cs_rank(volume)"],
            ["cs_rank(ts_delta(close, 5))", "cs_rank(ts_mean(volume, 5))"],
        ],
    ):
        proposers[model] = Mock()
        proposers[model].propose_one.side_effect = [
            (expression, {"model": model}) for expression in expressions
        ]

    def organizer(action, identity):
        assert action == "close"
        with user_context("alice"):
            return arena_request(
                action,
                identity,
                {},
                deps=box.deps,
                auth=box.auth,
                evaluate=box.evaluate,
                accept=Mock(side_effect=AssertionError),
            )

    report = run_training_arena(
        created,
        lambda c, a, p: participant(box, c, a, p),
        organizer,
        proposers,
        tmp_path / "completed",
    )
    assert report["provider_attempts"] == report["evaluation_attempts"] == 4
    assert report["leaderboard"]["global_n_tested"] == 4
    assert report["sealed_opened"] is False
    assert box.ledger.factor_mining_acceptance(box.sealed["id"]) is None


def test_unknown_outcome_is_checkpointed_without_retry(tmp_path):
    proposer = Mock()
    proposer.propose_one.side_effect = RuntimeError(
        "network_outcome_unknown_do_not_resubmit"
    )
    created = {
        "arena_id": "test",
        "participant_credentials": [{"proposer_tag": "test"}],
    }
    with pytest.raises(RuntimeError):
        run_training_arena(
            created,
            lambda *_: {"brief": {}, "sealed_dates_used": False},
            Mock(),
            {"test": proposer},
            tmp_path / "failed",
        )
    report = json.loads((tmp_path / "failed/report.json").read_text(encoding="utf-8"))
    assert report["provider_attempts"] == 1
    assert report["evaluation_attempts"] == 0
    assert proposer.propose_one.call_count == 1
    assert report["retry"] is False


def test_explicit_additional_call_preserves_results_and_cannot_repeat(tmp_path):
    path = tmp_path / "report.json"
    original = [{"generation": 1}, {"generation": 1}, {"generation": 2}]
    path.write_text(
        json.dumps(
            {
                "provider_attempts": 4,
                "evaluation_attempts": 3,
                "stage": "provider_outcome_pending:glm-5.3-flash:g2",
                "error_code": "network_outcome_unknown_do_not_resubmit",
                "scope": "real_providers_real_prices_isolated_training_not_production",
                "arena_id": "isolated",
                "rounds": original,
            }
        ),
        encoding="utf-8",
    )
    proposer = Mock()
    proposer.propose_one.return_value = ("close", {"model": "glm-5.3-flash"})
    participant = Mock(return_value={"brief": {}, "sealed_dates_used": False})
    organizer = Mock(return_value={"global_n_tested": 4})
    report = complete_authorized_glm_retry(path, participant, organizer, proposer, {})
    assert report["rounds"][:3] == original
    assert report["provider_attempts"] == 5
    assert report["evaluation_attempts"] == 4
    assert proposer.propose_one.call_count == 1
    organizer.assert_called_once_with("close", "isolated")
    with pytest.raises(PermissionError):
        complete_authorized_glm_retry(path, participant, organizer, proposer, {})
    assert proposer.propose_one.call_count == 1
