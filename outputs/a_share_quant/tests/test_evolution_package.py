import json
import sqlite3
import ssl
import threading
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.evolution_package import package_request
from quant.api.http_handler import HttpApiHandler
from quant.api.http_security import BoundedHTTPServer
from quant.api.user_scope import CURRENT_USERNAME
from quant.mining.evolution import EvolutionStore
from quant.research import ResearchLedger


@pytest.fixture
def package_cycle(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    sealed = ledger.create_sealed_test(
        strategy_version="baseline",
        factor_pipeline_version="test-pipeline",
        data_version="v1",
        sealed_start="2026-07-02",
        sealed_end="2026-07-30",
        dataset_hash="snapshot",
        bars=[],
        metadata={},
        parameters={},
    )
    sid = sealed["id"]
    store = EvolutionStore(tmp_path / "factor_evolution.sqlite3")
    session = store.create("alice", sid, "v1", 10)
    identity = session["session_id"]
    for generation, (expression, score) in enumerate(
        [("close", 0.1), ("volume", 0.2)], 1
    ):
        store.begin(
            "alice",
            identity,
            generation,
            {"expressions": [expression], "proposer_tag": "test"},
        )
        run = "run-" + str(generation)
        feedback = {
            "expression_hash": expression,
            "train_icir": {"5": score},
            "sealed_dates_used": False,
        }
        ledger.save_factor_mining_run(
            run,
            sealed_test_id=sid,
            data_version="v1",
            n_tested=1,
            result={"candidates": [{"expression": expression}], "feedback": feedback},
        )
        store.checkpoint(
            identity,
            generation,
            [
                {
                    "run_id": run,
                    "expression": expression,
                    "status": "awaiting_acceptance",
                    "feedback": feedback,
                }
            ],
            "completed",
        )
    ledger.save_factor_mining_run(
        "failed-outside-session",
        sealed_test_id=sid,
        data_version="v1",
        n_tested=3,
        result={"candidates": []},
        status="evaluation_failed",
    )
    with sqlite3.connect(ledger.path) as db:
        db.execute(
            "UPDATE sealed_tests SET bars_json='DO_NOT_READ_OR_PARSE_SEALED_DATA'"
        )
    accept = Mock(side_effect=ledger.claim_factor_mining_acceptance)
    args = {
        "owner": "alice", "session_id": identity, "ledger_path": ledger.path, "accept": accept
    }
    return SimpleNamespace(
        ledger=ledger,
        store=store,
        session=identity,
        sealed=sid,
        accept=accept,
        args=args,
    )


def test_top_n_package_keeps_all_trials_idempotent_and_does_not_open_sealed(
    package_cycle,
):
    box = package_cycle
    before = box.ledger.path.read_bytes()
    package = package_request("create", {"top_n": 1}, **box.args)
    assert package["selected"][0]["run_id"] == "run-2"
    assert len(package["selected"]) == 1
    assert package["cycle"]["n_tested"] == 5
    assert len(package["cycle"]["runs"]) == 3
    assert (
        package["sealed_opened"] is False and package["confirmation_required"] is True
    )
    assert package_request("create", {"top_n": 1}, **box.args) == package
    assert len(package_request("list", {}, **box.args)["packages"]) == 1
    assert box.ledger.path.read_bytes() == before
    box.accept.assert_not_called()


def test_confirmation_passes_original_run_to_existing_once_only_acceptance(
    package_cycle,
):
    box = package_cycle
    package = package_request("create", {"top_n": 1}, **box.args)
    with pytest.raises(ValueError):
        package_request("accept", {"package_id": package["package_id"]}, **box.args)
    box.accept.assert_not_called()
    payload = {"package_id": package["package_id"], "confirm_open_sealed": True}
    result = package_request("accept", payload, **box.args)
    assert result["acceptance"]["allowed"] is True
    box.accept.assert_called_once_with("run-2")
    assert box.ledger.factor_mining_trial_total(box.sealed) == 5
    with pytest.raises(PermissionError):
        package_request("accept", payload, **box.args)
    assert box.accept.call_count == 1


def test_changed_population_and_tampered_manifest_rejected(package_cycle):
    box = package_cycle
    package = package_request("create", {"top_n": 1}, **box.args)
    payload = {"package_id": package["package_id"], "confirm_open_sealed": True}
    box.ledger.save_factor_mining_run(
        "new-run",
        sealed_test_id=box.sealed,
        data_version="v1",
        n_tested=1,
        result={"candidates": []},
    )
    with pytest.raises(PermissionError):
        package_request("accept", payload, **box.args)
    with sqlite3.connect(box.store.path) as db:
        document = {**package, "cycle": {"n_tested": 1, "runs": []}}
        db.execute("UPDATE evolution_packages SET manifest=?", (json.dumps(document),))
    with pytest.raises(ValueError):
        package_request("accept", payload, **box.args)
    box.accept.assert_not_called()


@pytest.mark.parametrize("change", ["running", "archived", "missing", "opened"])
def test_ineligible_cycle_never_packages(package_cycle, change):
    box = package_cycle
    with sqlite3.connect(box.ledger.path) as db:
        if change == "running":
            db.execute(
                "UPDATE factor_mining_runs SET status='evaluation_in_progress' "
                "WHERE run_id='run-1'"
            )
        elif change == "archived":
            db.execute("UPDATE sealed_tests SET archived_at='2026-10-11'")
        elif change == "missing":
            db.execute("DELETE FROM factor_mining_runs WHERE run_id='run-2'")
        else:
            db.execute("UPDATE sealed_tests SET status='evaluated'")
    with pytest.raises(PermissionError):
        package_request("create", {"top_n": 1}, **box.args)
    box.accept.assert_not_called()


def test_owner_and_field_boundaries(package_cycle):
    box = package_cycle
    with pytest.raises(LookupError):
        package_request("create", {}, **{**box.args, "owner": "bob"})
    for overrides in (
        {"owner": "bob"},
        {"n_tested": 1},
        {"top_n": True},
        {"horizon": 1},
    ):
        with pytest.raises(ValueError):
            package_request("create", overrides, **box.args)
    box.accept.assert_not_called()


def test_real_https_package_authentication_csrf_and_cross_owner(
    package_cycle, tmp_path
):
    from tests.test_local_https import request

    box = package_cycle
    users = AuthStore(tmp_path / "http-users.sqlite3")
    users.create_user("alice", "isolated-https-password")
    users.create_user("bob", "isolated-https-password")
    auth = Auth(users, tmp_path / "http-secret.key")

    def callback(action, session_id, payload):
        return package_request(
            action,
            payload,
            **{
                **box.args,
                "owner": CURRENT_USERNAME.get(),
                "session_id": session_id,
            },
        )

    class Handler(HttpApiHandler):
        def _auth(self):
            return auth

        def _services(self):
            return SimpleNamespace(_evolution_package_request=callback)

        def log_message(self, *args):
            pass

    root = Path(__file__).resolve().parents[1]
    certificate = root / "local_tls/localhost.crt.pem"
    server = BoundedHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(tmp_path))
    )
    server.enable_tls(certificate, root / "local_tls/localhost.key.pem")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = server.server_port, ssl.create_default_context(cafile=str(certificate))
    path = "/api/mining/evolution/" + box.session + "/package"
    try:
        assert request(api, path, payload={})[0] == 401
        cookies = {}
        for owner in ("alice", "bob"):
            status, headers, _ = request(
                api,
                "/api/auth/login",
                payload={
                    "username": owner,
                    "password": "isolated-https-password",
                },
            )
            assert status == 200
            cookies[owner] = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        assert (
            request(
                api,
                path,
                payload={},
                headers={
                    **cookies["alice"],
                    "X-A-Pi-Request": "0",
                },
            )[0]
            == 403
        )
        assert request(api, path, payload={}, headers=cookies["bob"])[0] == 404
        status, _, package = request(
            api, path, payload={"top_n": 1}, headers=cookies["alice"]
        )
        assert status == 201 and package["cycle"]["n_tested"] == 5
        listing = path.removesuffix("/package") + "/packages"
        assert (
            request(api, listing, headers=cookies["alice"])[2]["packages"][0][
                "package_id"
            ]
            == package["package_id"]
        )
        assert request(api, listing, headers=cookies["bob"])[0] == 404
        assert (
            request(
                api,
                path + "/accept",
                payload={"package_id": package["package_id"]},
                headers=cookies["alice"],
            )[0]
            == 400
        )
        box.accept.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_two_generations_use_actual_dsl_training_feedback_then_package(tmp_path):
    import random
    from datetime import date, timedelta

    from quant.api.factor_panels import factor_values_for_definition
    from quant.api.mining_evolution import evolution_request
    from quant.api.mining_research import _evaluate_factor_expression_request
    from quant.api.user_scope import user_context
    from quant.data.schema import Bar
    from quant.factors import FactorRegistryStore

    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    sealed = ledger.create_sealed_test(
        strategy_version="baseline",
        factor_pipeline_version="test-pipeline",
        data_version="isolated-training",
        sealed_start="2026-07-02",
        sealed_end="2026-07-30",
        dataset_hash="snapshot",
        bars=[],
        metadata={},
        parameters={},
    )
    rng = random.Random(731)
    bars = []
    for symbol in range(35):
        price = 20 + symbol
        for offset in range(85):
            price *= 1 + rng.uniform(-0.03, 0.035)
            bars.append(
                Bar(
                    str(symbol),
                    date(2024, 1, 2) + timedelta(days=offset),
                    price,
                    price * 1.01,
                    price * 0.99,
                    price,
                    10000 + rng.randrange(10000),
                )
            )
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=FactorRegistryStore(tmp_path / "factors.sqlite3"),
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: (sealed, [], bars),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _factor_values_for_definition=factor_values_for_definition,
        _object_mapping=lambda item: item if isinstance(item, dict) else {},
        _factor_mining_cross_universe_reference=lambda *_, **__: {
            "status": "unavailable",
            "expressions": {},
        },
    )
    with user_context("alice"):
        session = evolution_request(
            {"mode": "evolution_create", "budget": 2},
            deps=deps,
            evaluate=_evaluate_factor_expression_request,
        )
        for generation, expression in enumerate(("close", "volume"), 1):
            history = evolution_request(
                {
                    "mode": "evolution_step",
                    "session_id": session["session_id"],
                    "generation": generation,
                    "expressions": [expression],
                    "proposer_tag": f"isolated-g{generation}",
                },
                deps=deps,
                evaluate=_evaluate_factor_expression_request,
            )
            feedback = history["generations"][-1]["history"][0]["feedback"]
            assert feedback["sealed_dates_used"] is False
            assert feedback["n_tested_after"] == generation
            assert set(feedback["train_icir"]) == {"5", "10", "20"}
        package = package_request(
            "create",
            {"top_n": 1},
            owner="alice",
            session_id=session["session_id"],
            ledger_path=ledger.path,
            accept=Mock(side_effect=AssertionError("No sealed opening")),
        )
    assert len(history["generations"]) == 2
    assert package["cycle"]["n_tested"] == 2
    assert len(package["selected"]) == 1
    assert ledger.factor_mining_acceptance(sealed["id"]) is None
