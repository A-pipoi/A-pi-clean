import json
import random
import sqlite3
import ssl
import threading
import time
from dataclasses import asdict, replace
from http.client import HTTPSConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quant.api.arena_service import (
    arena_request,
    participant_identity,
    participant_request,
)
from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.errors import ApiConflictError
from quant.api.factor_panels import (
    factor_panel_correlation,
    factor_values_for_definition,
)
from quant.api.http_handler import HttpApiHandler
from quant.api.http_security import BoundedHTTPServer
from quant.api.mining_acceptance import _accept_factor_mining_run
from quant.api.mining_evolution import evolution_request
from quant.api.mining_portfolio import set_cycle_policy
from quant.api.mining_research import _evaluate_factor_expression_request
from quant.api.user_scope import user_context
from quant.execution import PaperStore
from quant.factors import FactorRegistryStore
from quant.factors.analysis import evaluate_factor_panel
from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.arena import (
    ArenaStore,
    arena_access,
    issue_capability,
    verify_capability,
)
from quant.mining.evolution import EvolutionStore
from quant.research import ResearchLedger
from tests.test_factor_mining import _bars


@pytest.fixture
def arena_box(tmp_path):
    bars = _bars(130, tuple("ABCDEFGHIJKLMNOPQRST"))
    rng = random.Random(414)
    prices = {}
    noisy = []
    for bar in bars:
        price = prices.get(bar.symbol, bar.close) * (1 + rng.uniform(-0.025, 0.03))
        prices[bar.symbol] = price
        noisy.append(
            replace(
                bar,
                open=price,
                close=price,
                high=price * 1.01,
                low=price * 0.99,
                volume=rng.randint(10000, 20000),
            )
        )
    bars = noisy
    dates = sorted({x.date for x in bars})
    train = [x for x in bars if x.date < dates[85]]
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    sealed = ledger.create_sealed_test(
        strategy_version="baseline",
        factor_pipeline_version="test-pipeline",
        data_version="synthetic-arena",
        sealed_start=dates[85].isoformat(),
        sealed_end=dates[-1].isoformat(),
        dataset_hash="fixture-only",
        bars=[{**asdict(x), "date": x.date.isoformat()} for x in bars],
        metadata={},
        parameters={},
    )
    store = AuthStore(tmp_path / "auth.sqlite3")
    store.create_user("alice", "isolated-https-password", role="admin")
    store.create_user("bob", "isolated-https-password")
    auth = Auth(store, tmp_path / "key", secret="isolated-arena-only-secret-1234567890")
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=FactorRegistryStore(tmp_path / "factors.sqlite3"),
        PAPER_STORE=PaperStore(tmp_path / "paper.sqlite3"),
        _factor_mining_dataset=lambda: (sealed, bars, train),
        _validate_factor_mining_universe_request=lambda _: None,
        _as_int=int,
        _as_float=float,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _factor_values_for_definition=factor_values_for_definition,
        _object_mapping=lambda x: x if isinstance(x, dict) else {},
        _object_list=lambda x: x if isinstance(x, list) else [],
        _factor_mining_cross_universe_reference=lambda *_, **__: {
            "status": "unavailable",
            "expressions": {},
        },
        _factor_mining_scope=lambda: ("scope", "test-pipeline"),
        build_factor_panel=build_factor_panel,
        evaluate_expression=evaluate_expression,
        evaluate_factor_panel=evaluate_factor_panel,
        ApiConflictError=ApiConflictError,
        _bars_from_payload=lambda _: bars,
        _factor_panel_correlation=factor_panel_correlation,
    )
    evaluate = lambda p: _evaluate_factor_expression_request(p, deps=deps)
    accept = lambda rid: _accept_factor_mining_run(rid, deps=deps)
    return SimpleNamespace(
        deps=deps,
        auth=auth,
        ledger=ledger,
        sealed=sealed,
        evaluate=evaluate,
        accept=accept,
        path=tmp_path,
    )


def create(box, **overrides):
    with user_context("alice"):
        return arena_request(
            "create",
            "",
            {
                "proposers": ["mock-A", "mock-B"],
                "budget": 2,
                "top_n": 1,
                "interval_seconds": 0,
                **overrides,
            },
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )


def participant(box, credential, action, payload=None, sid=None):
    claims = participant_identity(box.auth, credential["token"])
    with user_context(claims["owner"]):
        return participant_request(
            action,
            sid or credential["session_id"],
            payload or {},
            claims=claims,
            deps=box.deps,
            evaluate=box.evaluate,
        )


def test_two_simulated_proposers_full_real_dsl_season_accept_and_archive(arena_box):
    box = arena_box
    report = create(box)
    credentials = report["participant_credentials"]
    assert (
        participant(box, credentials[0], "brief")["brief"]
        == participant(box, credentials[1], "brief")["brief"]
    )
    for index, credential in enumerate(credentials):
        expressions = (
            ["close", "volume"] if index == 0 else ["close * 2", "ts_mean(close, 2)"]
        )
        for generation, expression in enumerate(expressions, 1):
            result = participant(
                box,
                credential,
                "step",
                {"generation": generation, "expression": expression},
            )
            assert result["sealed_dates_used"] is False
        with pytest.raises(PermissionError):
            participant(
                box, credential, "step", {"generation": 3, "expression": "volume"}
            )
    assert box.ledger.factor_mining_trial_total(box.sealed["id"]) == 4
    store = ArenaStore(box.ledger, read_only=True)
    result = store.report("alice", report["arena_id"])
    assert sum(x["n_tested"] for x in result["participants"]) == 4
    assert all(
        x["locked"] and x["evaluations_used"] == 2 for x in result["participants"]
    )
    assert result["ranking_basis"] == "training_provisional"
    with pytest.raises(PermissionError):
        box.ledger.claim_factor_mining_acceptance(
            result["participants"][0]["training_candidates"][0]["run_id"]
        )
    assert box.ledger.factor_mining_acceptance(box.sealed["id"]) is None
    with user_context("alice"):
        closed = arena_request(
            "close",
            report["arena_id"],
            {},
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )
        assert len(closed["manifest"]["selected"]) == 2
        assert len(closed["manifest"]["peer_correlations"]["pairs"]) > 0
        assert "registry_pairs" in closed["manifest"]["peer_correlations"]
        with pytest.raises(ValueError):
            arena_request(
                "accept",
                report["arena_id"],
                {},
                deps=box.deps,
                auth=box.auth,
                evaluate=box.evaluate,
                accept=box.accept,
            )
        accepted = arena_request(
            "accept",
            report["arena_id"],
            {"confirm_open_sealed": True},
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )
        assert accepted["ranking_basis"] == "sealed_results"
        assert accepted["result"]["result"]["N_tested"] == 4
        assert accepted["result"]["result"]["evaluated_candidates"] <= 2
        assert accepted["result"]["result"]["acceptance_mode"] == "ic_only"
        assert all(
            "portfolio_gate" in x and "ic_passed" in x
            for x in accepted["result"]["result"]["candidates"]
        )
        with pytest.raises(PermissionError):
            arena_request(
                "accept",
                report["arena_id"],
                {"confirm_open_sealed": True},
                deps=box.deps,
                auth=box.auth,
                evaluate=box.evaluate,
                accept=box.accept,
            )
        archived = arena_request(
            "archive",
            report["arena_id"],
            {},
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )
        assert archived["status"] == "archived"
        assert (
            ArenaStore(box.ledger, read_only=True).list("alice")["seasons"][0]["status"]
            == "archived"
        )
    assert not any(
        x.enabled
        for x in box.deps.FACTOR_STORE.definitions(enabled_only=False)
        if x.expression
    )


@pytest.mark.parametrize(
    "mode, expected",
    [("ic_only", "accepted"), ("ic_and_portfolio", "overfitting-suspect")],
)
def test_joint_acceptance_conflict_is_audited_and_legacy_switch_preserved(
    arena_box, mode, expected
):
    box = arena_box
    set_cycle_policy(
        box.ledger.path, box.sealed["id"], {"mode": mode, "slippage_bps": 1000}
    )
    with user_context("alice"):
        run = box.evaluate({"expression": "close"})
        # Force only significance, not prices/portfolio/N, to exercise the conflict branch.
        with (
            patch(
                "quant.api.mining_acceptance.benjamini_hochberg",
                return_value={"adjusted": [0.001], "rejected": [True]},
            ),
            patch.object(box.deps, "_factor_panel_correlation", return_value=None),
        ):
            result = box.accept(run["run_id"])["result"]
    row = result["candidates"][0]
    assert row["status"] == expected
    assert row["ic_passed"] is True
    assert row["portfolio_passed"] is False
    assert row["metric_conflict"] is True
    assert row["portfolio_gate"]["neutral_net_sharpe"] < 0
    assert row["training_portfolio"]["dsr"]["n_tested"] == result["N_tested"] == 1


def test_arena_horizon_is_shared_by_training_and_sealed_selection(arena_box):
    box = arena_box
    with user_context("alice"):
        box.evaluate({"expression": "close"})
    report = create(box, horizon=10, budget=1)
    for credential in report["participant_credentials"]:
        participant(box, credential, "step", {"generation": 1, "expression": "close"})
    runs = box.ledger.factor_mining_runs_for_sealed_test(box.sealed["id"])
    assert sum(r["result"]["candidates"][0]["label_horizon"] == 10 for r in runs) == 2
    with user_context("alice"):
        arena_request(
            "close",
            report["arena_id"],
            {},
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )
        dataset = box.deps._factor_mining_dataset
        box.deps._factor_mining_dataset = Mock(
            side_effect=AssertionError("closed season cannot read data")
        )
        with pytest.raises(PermissionError):
            participant(
                box,
                report["participant_credentials"][0],
                "step",
                {"generation": 2, "expression": "volume"},
            )
        box.deps._factor_mining_dataset = dataset
        accepted = arena_request(
            "accept",
            report["arena_id"],
            {"confirm_open_sealed": True},
            deps=box.deps,
            auth=box.auth,
            evaluate=box.evaluate,
            accept=box.accept,
        )
    assert all(
        x["label_horizon"] == 10 for x in accepted["result"]["result"]["candidates"]
    )


def test_sessions_isolated_even_for_same_owner_and_ordinary_evolution_endpoints(
    arena_box,
):
    box = arena_box
    report = create(box)
    a, b = report["participant_credentials"]
    with pytest.raises(PermissionError):
        participant(box, a, "history", sid=b["session_id"])
    evo = EvolutionStore(
        box.ledger.path.with_name("factor_evolution.sqlite3"), read_only=True
    )
    with pytest.raises(PermissionError):
        evo.history("alice", b["session_id"])
    with arena_access(a["session_id"]), pytest.raises(PermissionError):
        evo.history("alice", b["session_id"])
    with user_context("alice"), pytest.raises(PermissionError):
        evolution_request(
            {"mode": "evolution_history", "session_id": a["session_id"]},
            deps=box.deps,
            evaluate=Mock(),
        )
    with pytest.raises(LookupError):
        ArenaStore(box.ledger, read_only=True).report("bob", report["arena_id"])


def test_failed_dsl_is_counted_once_budget_locked_and_retry_not_refunded(arena_box):
    box = arena_box
    report = create(box)
    a = report["participant_credentials"][0]
    payload = {"generation": 1, "expression": "BAD_UNKNOWN_FIELD"}
    participant(box, a, "step", payload)
    participant(box, a, "step", payload)
    assert box.ledger.factor_mining_trial_total(box.sealed["id"]) == 1
    participant(box, a, "step", {"generation": 2, "expression": "close"})
    with pytest.raises(PermissionError):
        participant(box, a, "step", {"generation": 3, "expression": "close"})
    assert box.ledger.factor_mining_trial_total(box.sealed["id"]) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"proposer_tag": "spoof"},
        {"n_tested": 0},
        {"budget": 1000},
        {"sealed_start": "fake"},
        {"label_horizons": [1]},
    ],
)
def test_participant_cannot_override_rules_or_N(arena_box, override):
    a = create(arena_box)["participant_credentials"][0]
    with pytest.raises(ValueError):
        participant(
            arena_box, a, "step", {"generation": 1, "expression": "close", **override}
        )
    assert arena_box.ledger.factor_mining_trial_total(arena_box.sealed["id"]) == 0


def test_expired_tampered_and_reset_account_capabilities_rejected(arena_box):
    box = arena_box
    report = create(box)
    token = report["participant_credentials"][0]["token"]
    with pytest.raises(PermissionError):
        verify_capability(box.auth.secret, token + "a")
    expired = issue_capability(
        box.auth.secret,
        owner="alice",
        arena_id=report["arena_id"],
        session_id="a" * 32,
        account_version=0,
        expires=int(time.time()) - 1,
    )
    with pytest.raises(PermissionError):
        verify_capability(box.auth.secret, expired)
    box.auth.store.reset_sessions("alice")
    with pytest.raises(PermissionError):
        participant_identity(box.auth, token)


def test_uniform_brief_locks_portfolio_policy_before_first_arena_trial(arena_box):
    create(arena_box)
    assert arena_box.ledger.factor_mining_trial_total(arena_box.sealed["id"]) == 0
    with pytest.raises(PermissionError):
        set_cycle_policy(
            arena_box.ledger.path, arena_box.sealed["id"], {"mode": "ic_and_portfolio"}
        )


def test_uniform_rate_limit_without_sealed_or_trial_increment(arena_box):
    box = arena_box
    report = create(box, interval_seconds=3600)
    for credential in report["participant_credentials"]:
        participant(box, credential, "step", {"generation": 1, "expression": "close"})
        with pytest.raises(PermissionError):
            participant(
                box, credential, "step", {"generation": 2, "expression": "close"}
            )
    assert box.ledger.factor_mining_trial_total(box.sealed["id"]) == 2


def test_batch_freeze_detects_changed_population_before_sealed_claim(arena_box):
    box = arena_box
    report = create(box, budget=1)
    for credential in report["participant_credentials"]:
        participant(box, credential, "step", {"generation": 1, "expression": "close"})
    store = ArenaStore(box.ledger)
    store.close("alice", report["arena_id"])
    with pytest.raises(PermissionError):
        box.ledger.save_factor_mining_run(
            "later",
            sealed_test_id=box.sealed["id"],
            data_version="synthetic-arena",
            n_tested=1,
            result={},
        )
    with sqlite3.connect(box.ledger.path) as db:
        db.execute("UPDATE factor_mining_runs SET n_tested=n_tested+1")
    with pytest.raises(PermissionError):
        store.accept("alice", report["arena_id"], confirm=True, accept=box.accept)
    assert box.ledger.factor_mining_acceptance(box.sealed["id"]) is None


def test_https_participant_cross_session_and_every_sealed_route_forbidden(arena_box):
    box = arena_box
    report = create(box)
    a, b = report["participant_credentials"]

    class Handler(HttpApiHandler):
        def _auth(self):
            return box.auth

        def _participant_identity_callback(self, token):
            return participant_identity(box.auth, token)

        def _services(self):
            return SimpleNamespace(
                _arena_participant_request=lambda action, sid, payload, claims: (
                    participant_request(
                        action,
                        sid,
                        payload,
                        claims=claims,
                        deps=box.deps,
                        evaluate=box.evaluate,
                    )
                )
            )

        def log_message(self, *_):
            pass

    server = BoundedHTTPServer(("127.0.0.1", 0), Handler)
    root = Path(__file__).resolve().parents[1]
    cert = root / "local_tls/localhost.crt.pem"
    server.enable_tls(cert, root / "local_tls/localhost.key.pem")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, token, payload=None):
        client = HTTPSConnection(
            "localhost",
            server.server_port,
            context=ssl.create_default_context(cafile=str(cert)),
        )
        client.request(
            "POST" if payload is not None else "GET",
            path,
            body=json.dumps(payload) if payload is not None else None,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        response = client.getresponse()
        data = json.loads(response.read())
        client.close()
        return response.status, data

    try:
        own = "/api/mining/arena/participant/" + a["session_id"]
        assert request(own + "/brief", a["token"])[0] == 200
        assert (
            request(
                own + "/step", a["token"], {"generation": 1, "expression": "close"}
            )[0]
            == 200
        )
        assert (
            request(
                "/api/mining/arena/participant/" + b["session_id"] + "/history",
                a["token"],
            )[0]
            == 403
        )
        for path in (
            "/api/factor-mining/accept",
            "/api/sealed/run",
            "/api/research/sealed",
            "/api/workspace",
            "/api/health",
            "/api/mining/arena/" + report["arena_id"],
            "/api/mining/evolution/" + b["session_id"] + "/history",
        ):
            assert request(path, a["token"])[0] == 403
            assert request(path, a["token"], {})[0] == 403
        assert request(own + "/history", "invalid")[0] == 403
        assert box.ledger.factor_mining_acceptance(box.sealed["id"]) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
