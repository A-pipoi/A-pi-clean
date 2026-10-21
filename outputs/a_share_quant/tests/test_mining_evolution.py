from types import SimpleNamespace

import pytest

from quant.api.mining_evolution import evolution_request, training_feedback
from quant.api.resource_limits import ResourceLimitExceeded
from quant.api.user_scope import user_context
from quant.mining.evolution import EvolutionStore
from quant.research import ResearchLedger


@pytest.mark.parametrize(
    "error", [ResourceLimitExceeded("quota"), PermissionError("denied")]
)
def test_interrupted_counted_attempt_retains_run_id_and_is_not_retried(tmp_path, error):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _factor_mining_dataset=lambda: ({"id": 17, "data_version": "v1"}, [], []),
    )
    calls = []
    error.run_id = "counted-attempt"

    def evaluate(payload, *, deps):
        calls.append(payload)
        raise error

    with user_context("alice"):
        session = evolution_request(
            {"mode": "evolution_create", "budget": 1}, deps=deps, evaluate=evaluate
        )
        request = {
            "mode": "evolution_step",
            "session_id": session["session_id"],
            "generation": 1,
            "expressions": ["close"],
        }
        with pytest.raises(type(error)):
            evolution_request(request, deps=deps, evaluate=evaluate)
        history = evolution_request(
            {"mode": "evolution_history", "session_id": session["session_id"]},
            deps=deps,
            evaluate=evaluate,
        )
        assert history["generations"][0]["status"] == "interrupted"
        assert history["generations"][0]["history"][0]["run_id"] == "counted-attempt"
        evolution_request(request, deps=deps, evaluate=evaluate)
    assert len(calls) == 1


def test_owner_isolation_idempotence_and_budget(tmp_path):
    store = EvolutionStore(tmp_path / "evolution.sqlite3")
    session = store.create("alice", 17, "v1", 2)
    sid = session["session_id"]
    request = {"expressions": ["close"], "proposer_tag": "g1"}
    with pytest.raises(LookupError):
        store.history("bob", sid)
    assert store.begin("alice", sid, 1, request)
    assert not store.begin("alice", sid, 1, request)
    with pytest.raises(ValueError):
        store.begin("alice", sid, 1, {**request, "expressions": ["volume"]})
    with pytest.raises(PermissionError):
        store.begin("alice", sid, 2, request)
    store.checkpoint(sid, 1, [{"run_id": "r1"}], "completed")
    assert store.begin("alice", sid, 2, request)
    store.checkpoint(sid, 2, [], "interrupted")
    with pytest.raises(PermissionError):
        store.begin("alice", sid, 3, request)


def test_two_generations_keep_failed_history_and_never_retry_duplicates(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _factor_mining_dataset=lambda: ({"id": 17, "data_version": "v1"}, [], []),
    )
    calls = []

    def evaluate(payload, *, deps):
        calls.append(payload["expression"])
        if payload["expression"] == "bad":
            raise ValueError("invalid expression")
        return {
            "run_id": str(len(calls)),
            "status": "awaiting_acceptance",
            "result": {"feedback": {"sealed_dates_used": False}},
        }

    with user_context("alice"):
        session = evolution_request(
            {"mode": "evolution_create", "budget": 3}, deps=deps, evaluate=evaluate
        )
        first = {
            "mode": "evolution_step",
            "session_id": session["session_id"],
            "generation": 1,
            "expressions": ["close", "bad"],
            "proposer_tag": "g1",
        }
        history = evolution_request(first, deps=deps, evaluate=evaluate)
        assert history["generations"][0]["history"][1]["status"] == "failed"
        evolution_request(first, deps=deps, evaluate=evaluate)
        assert len(calls) == 2
        second = {
            **first,
            "generation": 2,
            "expressions": ["volume"],
            "proposer_tag": "g2",
        }
        assert (
            len(evolution_request(second, deps=deps, evaluate=evaluate)["generations"])
            == 2
        )
    assert calls == ["close", "bad", "volume"]


def test_real_evaluation_feedback_counts_once_and_never_uses_sealed_dates(tmp_path):
    from quant.api.factor_panels import factor_values_for_definition
    from quant.api.mining_research import _evaluate_factor_expression_request
    from quant.factors import FactorRegistryStore
    from tests.test_factor_mining import _bars

    bars = _bars()
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=FactorRegistryStore(tmp_path / "factors.sqlite3"),
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: (
            {
                "id": 17,
                "data_version": "v1",
                "sealed_start": "2026-01-01",
                "sealed_end": "2026-01-31",
            },
            [],
            bars,
        ),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _factor_values_for_definition=factor_values_for_definition,
        _object_mapping=lambda item: item if isinstance(item, dict) else {},
        _factor_mining_cross_universe_reference=lambda *_, **__: {
            "status": "unavailable",
            "expressions": {},
        },
    )
    result = _evaluate_factor_expression_request(
        {"expression": "close", "label_horizons": [5], "proposer_tag": "test-g1"},
        deps=deps,
    )
    assert result["status"] == "awaiting_acceptance"
    assert result["result"]["feedback"]["n_tested_after"] == 1
    assert list(result["result"]["feedback"]["train_rank_ic"]) == ["5"]
    assert result["result"]["feedback"]["sealed_dates_used"] is False
    assert len(result["result"]["candidates"][0]["evaluation"]["horizons"]) == 20
    matrix = result["result"]["candidates"][0]["evaluation"][
        "registry_correlation_matrix"
    ]
    assert "__candidate__" in matrix["keys"]
    assert matrix["end"] < "2026-01-01"
    assert matrix["sealed_dates_used"] is False
    assert ledger.factor_mining_trial_total(17) == 1


def test_history_read_only_does_not_initialize_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(LookupError):
        EvolutionStore(path, read_only=True)
    assert not path.exists()


def test_feedback_is_bounded_training_only_and_keeps_failures():
    def entry(identity, value, **extra):
        return {
            "expression": identity,
            "run_id": identity,
            "status": "awaiting_acceptance",
            "feedback": {
                "expression_hash": identity,
                "train_icir": {"5": value},
                "sealed_dates_used": False,
                "sealed_result": "DO_NOT_EXPORT",
                **extra,
            },
        }

    session = {
        "session_id": "s1",
        "data_version": "v1",
        "generations": [
            {
                "generation": 1,
                "history": [
                    entry("a", 0.1),
                    entry(
                        "b",
                        0.4,
                        train_icir={
                            "5": 0.4,
                            "10": float("nan"),
                            "secret": "DO_NOT_EXPORT",
                        },
                    ),
                    entry("missing", None),
                    entry("leaky", 1, sealed_dates_used=True),
                    {"status": "failed", "expression": "bad", "code": "ValueError"},
                ],
            },
            {"generation": 2, "history": [entry("a", 0.9), entry("c", float("nan"))]},
        ],
    }
    report = training_feedback(session, top_n=1)
    assert report["top_candidates"][0]["expression"] == "b"
    assert report["eligible_unique"] == 2
    assert report["failure_count"] == 1
    assert report["llm_called"] is False and report["sealed_dates_used"] is False
    assert "DO_NOT_EXPORT" not in str(report)
    assert report["top_candidates"][0]["training_feedback"]["train_icir"]["10"] is None
    for invalid in (True, 0, 21):
        with pytest.raises(ValueError):
            training_feedback(session, top_n=invalid)


def test_feedback_owner_scoped_read_only_and_no_dataset_calls(tmp_path):
    from unittest.mock import Mock

    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    store = EvolutionStore(tmp_path / "factor_evolution.sqlite3")
    session = store.create("alice", 17, "v1", 5)
    dataset = Mock(side_effect=AssertionError("No price/sealed preparation allowed"))
    evaluate = Mock(side_effect=AssertionError("No new evaluation allowed"))
    deps = SimpleNamespace(RESEARCH_LEDGER=ledger, _factor_mining_dataset=dataset)
    payload = {"mode": "evolution_feedback", "session_id": session["session_id"]}
    before = store.path.read_bytes()
    with user_context("alice"):
        assert (
            evolution_request(payload, deps=deps, evaluate=evaluate)["top_candidates"]
            == []
        )
    with user_context("bob"), pytest.raises(LookupError):
        evolution_request(payload, deps=deps, evaluate=evaluate)
    assert store.path.read_bytes() == before
    dataset.assert_not_called()
    evaluate.assert_not_called()


@pytest.mark.parametrize(
    "query,status",
    [("?top_n=3&horizon=10", 200), ("?top_n=bad", 400), ("?top_n=21", 400)],
)
def test_feedback_get_requires_authorization_and_maps_invalid_input(query, status):
    from unittest.mock import Mock

    from quant.api.http_handler import HttpApiHandler

    def evaluate(payload):
        assert payload["mode"] == "evolution_feedback"
        return training_feedback(
            {"session_id": "s1", "data_version": "v1", "generations": []},
            top_n=payload["top_n"],
            horizon=payload["horizon"],
        )

    service = SimpleNamespace(
        _evaluate_factor_expression_request=Mock(side_effect=evaluate)
    )
    handler = SimpleNamespace(
        path="/api/mining/evolution/" + "a" * 32 + "/feedback" + query,
        _request_boundary=lambda: True,
        _authorize=Mock(return_value=True),
        _services=lambda: service,
        _send_json=Mock(),
    )
    HttpApiHandler.do_GET(handler)
    handler._authorize.assert_called_once()
    assert handler._send_json.call_args.args[0] == status
    handler._authorize.reset_mock(return_value=True)
    handler._authorize.return_value = False
    service._evaluate_factor_expression_request.reset_mock()
    HttpApiHandler.do_GET(handler)
    service._evaluate_factor_expression_request.assert_not_called()
