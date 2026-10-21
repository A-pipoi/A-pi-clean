from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.deep_formal_trials import formal_trial_request
from quant.api.errors import ApiConflictError
from quant.api.http_handler import HttpApiHandler
from quant.api.mining_acceptance import (
    MiningAcceptanceDependencies,
    _accept_factor_mining_run,
)
from quant.api.resource_limits import ResourceLimits
from quant.api.user_scope import CURRENT_USERNAME
from quant.factors.expression import FactorPanel
from quant.mining.batch import require_supported_acceptance_candidates
from quant.research.deep_formal import (
    DeepFormalTrials,
    deep_candidate_panel,
    deep_candidates_ready,
)
from quant.research.ledger import ResearchBudgetExceeded, ResearchLedger
from tests.conftest import request

CONFIG = {
    "architecture": "GRU",
    "dataset_hash": "a" * 64,
    "feature_version": "Alpha360-v1",
    "label_horizon": 5,
    "hyperparameters": {"seed": 7, "epochs": 1},
}
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


@pytest.fixture
def setup(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3", budget=3)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(
            "INSERT INTO sealed_tests(strategy_version,factor_pipeline_version,"
            "data_version,sealed_start,sealed_end,dataset_hash,bars_json,"
            "metadata_json,parameters_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "baseline",
                "future-fixture",
                "fixture-market-version",
                "2099-10-12",
                "2099-12-01",
                "b" * 64,
                "DO_NOT_READ_PRICES",
                json.dumps({"symbols": ["000001.SZ", "600000.SH"]}),
                "{}",
                NOW.isoformat(),
            ),
        )
    return ledger, DeepFormalTrials(ledger, "alice", clock=lambda: NOW)


def reserve(store, key="request_001"):
    return store.reserve(1, key, CONFIG)


def trusted(context):
    assert context["owner"] == "alice" and context["started_at"]
    assert context["config"] == CONFIG
    return {
        "purpose": "prospective",
        "weights_sha256": "c" * 64,
        "dataset_hash": CONFIG["dataset_hash"],
        "feature_version": CONFIG["feature_version"],
        "start_permit_verified": True,
        "pit_evidence_verified": True,
        "metadata_hash": "d" * 64,
    }


def test_atomic_reservation_failure_idempotence_and_budget(setup):
    ledger, store = setup
    first = reserve(store)
    assert first["status"] == "reserved" and first["enabled"] is False
    assert reserve(store) == first
    assert ledger.factor_mining_trial_total(1) == 1
    with pytest.raises(ValueError, match="mismatch"):
        store.reserve(1, "request_001", {**CONFIG, "hyperparameters": {"seed": 8}})
    store.start(first["attempt_id"])
    with pytest.raises(PermissionError, match="permit"):
        store.start(first["attempt_id"])
    store.fail(first["attempt_id"])
    store.fail(first["attempt_id"])
    assert ledger.factor_mining_trial_total(1) == 1
    assert reserve(store)["status"] == "failed"
    reserve(store, "request_002")
    reserve(store, "request_003")
    with pytest.raises(ResearchBudgetExceeded):
        reserve(store, "request_004")
    assert ledger.factor_mining_trial_total(1) == 3
    assert len(ledger.factor_mining_runs_for_sealed_test(1)) == 3


def test_model_registry_is_separate_immutable_disabled_and_blocks_old_evaluator(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    with pytest.raises(PermissionError, match="started"):
        store.register_verified(attempt, verify=trusted)
    store.start(attempt)
    model = store.register_verified(attempt, verify=trusted)
    assert model["enabled"] is False and model["status"] == "awaiting_acceptance"
    assert (
        store.register_verified(
            attempt, verify=lambda _: pytest.fail("must not reverify")
        )["version"]
        == model["version"]
    )
    assert ledger.factor_mining_trial_total(1) == 1
    with pytest.raises(PermissionError, match="erased"):
        store.fail(attempt)
    runs = ledger.factor_mining_runs_for_sealed_test(1)
    assert runs[0]["data_version"] == "fixture-market-version"
    assert "expression" not in runs[0]["result"]["candidates"][0]
    with pytest.raises(PermissionError, match="adapter"):
        require_supported_acceptance_candidates(runs)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT enabled FROM deep_formal_models").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM factor_mining_acceptances").fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT bars_json FROM sealed_tests").fetchone()[0]
            == "DO_NOT_READ_PRICES"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE deep_formal_models SET enabled=1")


def test_trusted_score_receipt_is_forward_only_immutable_and_builds_panel(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    model = store.register_verified(attempt, verify=trusted)
    received = datetime(2099, 10, 12, 9, 30, tzinfo=UTC)
    generated = received - timedelta(minutes=10)
    available = generated - timedelta(minutes=5)
    rows = [
        {"instrument": "000001.SZ", "score": 0.2},
        {"instrument": "600000.SH", "score": -0.1},
    ]
    receipt = store.receive_verified_scores(
        model["model_id"],
        rows,
        generated_at=generated,
        feature_available_at=available,
        received_at=received,
    )
    assert receipt["rows"] == 2 and receipt["enabled"] is False
    assert (
        store.receive_verified_scores(
            model["model_id"],
            list(reversed(rows)),
            generated_at=generated,
            feature_available_at=available,
            received_at=received,
        )["payload_hash"]
        == receipt["payload_hash"]
    )
    with pytest.raises(ValueError, match="Immutable"):
        store.receive_verified_scores(
            model["model_id"],
            [{**rows[0], "score": 99}, rows[1]],
            generated_at=generated,
            feature_available_at=available,
            received_at=received,
        )
    runs = ledger.factor_mining_runs_for_sealed_test(1)
    deep_candidates_ready(ledger, runs)
    assert ledger.claim_factor_mining_acceptance(attempt)["allowed"] is True
    with pytest.raises(PermissionError, match="closed"):
        store.receive_verified_scores(
            model["model_id"],
            rows,
            generated_at=generated,
            feature_available_at=available,
            received_at=received,
        )
    panel = FactorPanel(
        dates=(date(2099, 10, 12), date(2099, 10, 13)),
        symbols=("000001.SZ", "600000.SH"),
        fields={},
        industries=((None, None), (None, None)),
        data_version="fixture-market-version",
    )
    assert deep_candidate_panel(ledger, panel, model) == [[0.2, -0.1], [None, None]]


def test_deep_scores_join_whole_cycle_acceptance_and_remain_disabled(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    model = store.register_verified(attempt, verify=trusted)
    received = datetime(2099, 10, 12, 9, 30, tzinfo=UTC)
    store.receive_verified_scores(
        model["model_id"],
        [
            {"instrument": "000001.SZ", "score": 0.2},
            {"instrument": "600000.SH", "score": -0.1},
        ],
        generated_at=received - timedelta(minutes=10),
        feature_available_at=received - timedelta(minutes=15),
        received_at=received,
    )
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE sealed_tests SET bars_json='[]' WHERE id=1")
    panel = FactorPanel(
        dates=(date(2099, 10, 12), date(2099, 10, 13)),
        symbols=("000001.SZ", "600000.SH"),
        fields={},
        industries=((None, None), (None, None)),
        data_version="fixture-market-version",
    )
    factor_store = Mock()
    factor_store.definitions.return_value = []
    factor_store.expression_library.return_value = []
    deps = MiningAcceptanceDependencies(
        build_factor_panel=lambda *args, **kwargs: panel,
        evaluate_expression=lambda expression, panel: pytest.fail(
            "deep model is not DSL"
        ),
        evaluate_factor_panel=lambda *args, **kwargs: {
            "coverage": 1.0,
            "horizons": {"5": {"raw_p_value": 0.01, "dsr_p_value": 0.01}},
        },
        ApiConflictError=ApiConflictError,
        FACTOR_STORE=factor_store,
        PAPER_STORE=Mock(),
        RESEARCH_LEDGER=ledger,
        _as_float=float,
        _as_int=int,
        _bars_from_payload=lambda payload: [],
        _factor_mining_scope=lambda: ("scope", "future-fixture"),
        _factor_panel_correlation=lambda *args: None,
        _factor_registry_status=lambda count: {},
        _factor_values_for_definition=lambda *args: None,
        _object_list=lambda value: value if isinstance(value, list) else [],
        _object_mapping=lambda value: value if isinstance(value, dict) else {},
        _pit_observations_for_bars=lambda *args: ([], [], {}, {}),
        _record_research_experiment=lambda *args: 1,
    )
    accepted = _accept_factor_mining_run(attempt, deps=deps)
    assert accepted["status"] == "passed"
    candidate = accepted["result"]["candidates"][0]
    assert candidate["model_id"] == model["model_id"]
    assert candidate["status"] == "accepted"
    assert accepted["result"]["N_tested"] == 1
    assert accepted["result"]["evaluated_candidates"] == 1
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute(
            "SELECT status,enabled FROM deep_formal_models WHERE model_id=?",
            (model["model_id"],),
        ).fetchone() == ("accepted", 0)


@pytest.mark.parametrize(
    "received_delta,generated_delta,available_delta",
    [
        (timedelta(hours=-1), timedelta(minutes=-10), timedelta(minutes=-15)),
        (timedelta(), timedelta(minutes=1), timedelta(minutes=-5)),
        (timedelta(), timedelta(minutes=-10), timedelta(minutes=-5)),
    ],
)
def test_score_receipt_rejects_early_future_or_unavailable_evidence(
    setup, received_delta, generated_delta, available_delta
):
    _, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    model = store.register_verified(attempt, verify=trusted)
    received = datetime(2099, 10, 12, 9, 30, tzinfo=UTC) + received_delta
    generated = received + generated_delta
    available = received + available_delta
    with pytest.raises(ValueError):
        store.receive_verified_scores(
            model["model_id"],
            [{"instrument": "000001.SZ", "score": 1.0}],
            generated_at=generated,
            feature_available_at=available,
            received_at=received,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "historical_replay"),
        ("pit_evidence_verified", False),
        ("start_permit_verified", False),
        ("weights_sha256", "bad"),
        ("dataset_hash", "0" * 64),
        ("feature_version", "other"),
    ],
)
def test_unverified_registration_rolls_back_without_erasing_count(setup, field, value):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    with pytest.raises(PermissionError):
        store.register_verified(
            attempt, verify=lambda ctx: {**trusted(ctx), field: value}
        )
    assert store.get(attempt)["status"] == "running"
    assert ledger.factor_mining_trial_total(1) == 1
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM deep_formal_models").fetchone()[0] == 0
    assert store.register_verified(attempt, verify=trusted)["enabled"] is False


def test_worker_crash_and_sql_crash_leave_no_half_registered_model(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    with pytest.raises(RuntimeError, match="worker"):
        store.register_verified(
            attempt, verify=lambda _: (_ for _ in ()).throw(RuntimeError("worker"))
        )
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(
            "CREATE TRIGGER fixture_abort BEFORE UPDATE ON deep_formal_trials "
            "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture crash"):
        store.register_verified(attempt, verify=trusted)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        assert db.execute("SELECT COUNT(*) FROM deep_formal_models").fetchone()[0] == 0
        assert (
            json.loads(
                db.execute("SELECT result_json FROM factor_mining_runs").fetchone()[0]
            )["candidates"]
            == []
        )
        db.execute("DROP TRIGGER fixture_abort")
    assert store.get(attempt)["status"] == "running"


def test_concurrent_idempotency_and_shared_expression_cap(setup):
    ledger, store = setup
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: reserve(store), range(6)))
    assert len({row["attempt_id"] for row in results}) == 1
    ledger.save_factor_mining_run(
        run_id="gp-other",
        sealed_test_id=1,
        data_version="fixture-market-version",
        n_tested=1,
        result={},
        status="evaluation_in_progress",
    )
    reserve(store, "request_002")
    with pytest.raises(ResearchBudgetExceeded):
        ledger.record_factor_mining_attempts("gp-other", 2)
    with pytest.raises(ResearchBudgetExceeded):
        ledger.save_factor_mining_run(
            run_id="gp-bypass",
            sealed_test_id=1,
            data_version="fixture-market-version",
            n_tested=1,
            result={},
        )
    assert ledger.factor_mining_trial_total(1) == 3
    assert ledger.factor_mining_run("gp-bypass") is None


def test_concurrent_different_reservations_do_not_overdraw(setup):
    ledger, store = setup

    def one(index):
        try:
            return reserve(store, f"request_{index:03}")["attempt_id"]
        except ResearchBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(8)))
    assert sum(value is not None for value in results) == 3
    assert ledger.factor_mining_trial_total(1) == 3


@pytest.mark.parametrize("change", ["history", "archive", "opened", "claimed"])
def test_old_or_opened_cycle_rejected_before_count_or_schema(setup, change):
    ledger, store = setup
    with closing(sqlite3.connect(ledger.path)) as db, db:
        if change == "history":
            db.execute("UPDATE sealed_tests SET sealed_start='2026-10-09'")
        elif change == "archive":
            db.execute("UPDATE sealed_tests SET archived_at='fixture'")
        elif change == "opened":
            db.execute("UPDATE sealed_tests SET result_json='{}'")
        else:
            db.execute(
                "INSERT INTO factor_mining_acceptances "
                "VALUES(1,'fixture','claimed',NULL,'fixture',NULL)"
            )
    with pytest.raises(PermissionError, match="future"):
        reserve(store)
    assert ledger.factor_mining_trial_total(1) == 0
    with closing(sqlite3.connect(ledger.path)) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'deep_formal_%'"
            ).fetchone()[0]
            == 0
        )


def test_owner_time_and_http_payload_override_boundaries(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    other = DeepFormalTrials(ledger, "bob", clock=lambda: NOW)
    for action in (other.get, other.start, other.fail):
        with pytest.raises(LookupError):
            action(attempt)
    with pytest.raises(ValueError, match="aware"):
        reserve(
            DeepFormalTrials(ledger, "alice", clock=lambda: NOW.replace(tzinfo=None))
        )
    with pytest.raises(ValueError):
        formal_trial_request(
            "trial-reserve",
            {
                "sealed_test_id": 1,
                "request_key": "request_002",
                "config": CONFIG,
                "owner": "bob",
            },
            owner="alice",
            ledger=ledger,
        )


def test_finish_requires_owned_claim_and_cannot_overwrite_result(setup):
    ledger, store = setup
    first = reserve(store)["attempt_id"]
    store.fail(first)
    second = reserve(store, "request_002")["attempt_id"]
    store.fail(second)
    with pytest.raises(PermissionError, match="claimed"):
        ledger.finish_factor_mining_acceptance(first, status="passed", result={})
    assert ledger.claim_factor_mining_acceptance(first)["allowed"]
    with pytest.raises(PermissionError, match="claimed"):
        ledger.finish_factor_mining_acceptance(second, status="passed", result={})
    saved = ledger.finish_factor_mining_acceptance(
        first, status="needs_review", result={"passed": False}
    )
    with pytest.raises(PermissionError, match="once"):
        ledger.finish_factor_mining_acceptance(first, status="passed", result={})
    assert saved["result"] == {"passed": False}
    with closing(sqlite3.connect(ledger.path)) as db:
        assert (
            db.execute("SELECT status FROM factor_mining_acceptances").fetchone()[0]
            == "needs_review"
        )


def test_direct_claim_rechecks_missing_receipts_under_transaction(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    store.register_verified(attempt, verify=trusted)
    with pytest.raises(PermissionError, match="receipt"):
        ledger.claim_factor_mining_acceptance(attempt)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM factor_mining_acceptances").fetchone()[0]
            == 0
        )


def test_panel_preparation_failure_closes_claim_without_allowing_replay(setup):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.fail(attempt)
    price_reads = []
    original = ledger.get_sealed_test

    def read_snapshot(*args, **kwargs):
        if kwargs.get("include_bars"):
            with closing(sqlite3.connect(ledger.path)) as db:
                assert (
                    db.execute(
                        "SELECT status FROM factor_mining_acceptances"
                    ).fetchone()[0]
                    == "claimed"
                )
            price_reads.append(True)
        return original(*args, **kwargs)

    ledger.get_sealed_test = read_snapshot
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _as_int=int,
        _factor_mining_scope=lambda: ("scope", "future-fixture"),
        ApiConflictError=ApiConflictError,
        _bars_from_payload=lambda _: (_ for _ in ()).throw(
            ValueError("fixture preparation failure")
        ),
    )
    with pytest.raises(ValueError, match="preparation"):
        _accept_factor_mining_run(attempt, deps=deps)
    assert price_reads == [True]
    with closing(sqlite3.connect(ledger.path)) as db:
        assert (
            db.execute("SELECT status FROM factor_mining_acceptances").fetchone()[0]
            == "failed"
        )
    with pytest.raises(ApiConflictError):
        _accept_factor_mining_run(attempt, deps=deps)
    assert price_reads == [True]


@pytest.mark.parametrize("change", ["score", "receipt_time", "model", "count"])
def test_claimed_manifest_rejects_model_receipt_or_count_tampering(setup, change):
    ledger, store = setup
    attempt = reserve(store)["attempt_id"]
    store.start(attempt)
    model = store.register_verified(attempt, verify=trusted)
    received = datetime(2099, 10, 12, 9, 30, tzinfo=UTC)
    store.receive_verified_scores(
        model["model_id"],
        [{"instrument": "000001.SZ", "score": 1.0}],
        generated_at=received - timedelta(minutes=10),
        feature_available_at=received - timedelta(minutes=15),
        received_at=received,
    )
    assert ledger.claim_factor_mining_acceptance(attempt)["allowed"]
    panel = FactorPanel(
        dates=(date(2099, 10, 12),),
        symbols=("000001.SZ",),
        fields={},
        industries=((None,),),
        data_version="fixture-market-version",
    )
    assert deep_candidate_panel(ledger, panel, model) == [[1.0]]
    with closing(sqlite3.connect(ledger.path)) as db, db:
        if change == "score":
            db.execute("UPDATE deep_formal_score_batches SET scores_json='[]'")
        elif change == "receipt_time":
            db.execute("UPDATE deep_formal_score_batches SET received_at='tampered'")
        elif change == "model":
            db.execute("UPDATE deep_formal_models SET metadata_json='{}'")
        else:
            db.execute("UPDATE factor_mining_runs SET n_tested=99")
    with pytest.raises(ValueError, match="manifest"):
        deep_candidate_panel(ledger, panel, model)


def test_formal_http_login_csrf_owner_budget_and_no_registration_upload(
    setup, auth, tmp_path, monkeypatch
):
    ledger, _ = setup
    limits = ResourceLimits(tmp_path / "limits.sqlite3", hourly_limit=100)
    monkeypatch.setattr("quant.api.http_handler.get_resource_limits", lambda: limits)

    def callback(action, payload):
        return formal_trial_request(
            action, payload, owner=CURRENT_USERNAME.get(), ledger=ledger
        )

    class Handler(HttpApiHandler):
        def _auth(self):
            return auth

        def _services(self):
            return SimpleNamespace(_external_deep_request=callback)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    route = "/api/mining/external-deep/trials/"
    payload = {"sealed_test_id": 1, "request_key": "http_req_001", "config": CONFIG}
    try:
        port = server.server_port
        assert request(port, "POST", route + "reserve", payload)[0] == 401
        headers = {}
        for user, password in (
            ("alice", "alice-long-password"),
            ("bob", "bobby-long-password"),
        ):
            status, response, _ = request(
                port,
                "POST",
                "/api/auth/login",
                {"username": user, "password": password},
            )
            assert status == 200
            headers[user] = {
                "Cookie": response["Set-Cookie"].split(";", 1)[0],
                "X-A-Pi-Request": "1",
            }
        assert (
            request(
                port,
                "POST",
                route + "reserve",
                payload,
                {"Cookie": headers["alice"]["Cookie"]},
            )[0]
            == 403
        )
        assert (
            request(
                port,
                "POST",
                route + "reserve",
                {**payload, "owner": "bob"},
                headers["alice"],
            )[0]
            == 400
        )
        status, _, result = request(
            port, "POST", route + "reserve", payload, headers["alice"]
        )
        assert status == 200 and result["enabled"] is False
        attempt = {"attempt_id": result["attempt_id"]}
        assert request(port, "POST", route + "get", attempt, headers["bob"])[0] == 404
        assert request(port, "POST", route + "start", attempt, headers["bob"])[0] == 404
        assert (
            request(port, "POST", route + "start", attempt, headers["alice"])[0] == 200
        )
        assert (
            request(port, "POST", route + "start", attempt, headers["alice"])[0] == 409
        )
        assert (
            request(port, "POST", route + "fail", attempt, headers["alice"])[0] == 200
        )
        assert (
            request(port, "POST", route + "reserve", payload, headers["alice"])[2][
                "status"
            ]
            == "failed"
        )
        for index in (2, 3):
            assert (
                request(
                    port,
                    "POST",
                    route + "reserve",
                    {**payload, "request_key": f"http_req_{index:03}"},
                    headers["alice"],
                )[0]
                == 200
            )
        exhausted = request(
            port,
            "POST",
            route + "reserve",
            {**payload, "request_key": "http_req_004"},
            headers["alice"],
        )
        assert (
            exhausted[0] == 409 and exhausted[2]["code"] == "research_budget_exceeded"
        )
        assert (
            request(
                port,
                "POST",
                route + "register",
                {**attempt, "pit_verified": True},
                headers["alice"],
            )[0]
            == 404
        )
        assert ledger.factor_mining_trial_total(1) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(ValueError):
        formal_trial_request(
            "trial-register",
            {"attempt_id": attempt, "pit_evidence_verified": True},
            owner="alice",
            ledger=ledger,
        )
