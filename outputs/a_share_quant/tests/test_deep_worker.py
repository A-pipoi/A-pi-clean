from __future__ import annotations

import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.deep_formal_trials import formal_trial_request
from quant.api.deep_scheduler import DeepFormalScheduler, independent_runtime
from quant.api.http_handler import HttpApiHandler
from quant.api.resource_limits import ResourceLimits
from quant.api.user_scope import CURRENT_USERNAME
from quant.data.deep_features import FormalFeatureCollector, alpha360
from quant.research.deep_evidence import EvidenceAuthority, canonical, digest
from quant.research.deep_operator import approve
from quant.research.deep_worker import DeepFormalWorker
from quant.research.ledger import ResearchLedger
from tests.conftest import request

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = independent_runtime(PROJECT)
NOW = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
SYMBOLS = ["000001.SZ", "600000.SH"]
OPTIONS = {
    "epochs": 1,
    "batch_size": 128,
    "hidden": 8,
    "learning_rate": 0.001,
    "seed": 7,
}


def training_data():
    calendar = []
    day = date(2024, 1, 1)
    while len(calendar) < 505:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    source = b"isolated audited numeric test fixture; NOT production evidence"
    source_hash = digest(source)
    rows = []
    for i, day in enumerate(calendar[:500]):
        for j, instrument in enumerate(SYMBOLS):
            label_end = calendar[i + 5]
            rows.append(
                {
                    "date": day,
                    "instrument": instrument,
                    "features": [1 + ((i + j + k) % 11) / 10 for k in range(360)],
                    "label": (i % 7 - 3) / 100,
                    "label_end": label_end,
                    "feature_available_at": day + "T17:30:00+08:00",
                    "membership_available_at": day + "T14:00:00+08:00",
                    "label_available_at": label_end + "T18:00:00+08:00",
                    "source_hashes": [source_hash],
                }
            )
    return {
        "schema_version": 1,
        "purpose": "prospective",
        "feature_version": "Alpha360-v1",
        "label_horizon": 5,
        "calendar": calendar,
        "rows": rows,
    }, {source_hash: source}


@pytest.fixture
def fixture(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3", budget=4)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(
            "INSERT INTO sealed_tests(strategy_version,factor_pipeline_version,"
            "data_version,sealed_start,sealed_end,dataset_hash,bars_json,"
            "metadata_json,parameters_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "baseline",
                "future-fixture",
                "fixture-v1",
                "2026-10-13",
                "2026-10-30",
                "b" * 64,
                "DO_NOT_READ_SEALED_PRICES",
                json.dumps({"symbols": SYMBOLS}),
                "{}",
                NOW.isoformat(),
            ),
        )
    authority = EvidenceAuthority(tmp_path / "approvals")
    data, sources = training_data()
    dataset_hash = authority.approve_dataset(
        canonical(data),
        sources,
        owner="alice",
        data_version="fixture-v1",
        audit_reference="ISOLATED TEST ONLY",
        now=NOW,
    )
    clock = [NOW]
    config = {
        "architecture": "Linear",
        "dataset_hash": dataset_hash,
        "feature_version": "Alpha360-v1",
        "label_horizon": 5,
        "hyperparameters": dict(OPTIONS),
    }
    worker = DeepFormalWorker(
        ledger,
        "alice",
        authority=authority,
        artifacts=tmp_path / "artifacts",
        runtime=RUNTIME,
        script=PROJECT / "deep_factor/formal_runtime.py",
        clock=lambda: clock[0],
    )
    return ledger, worker, config, clock


def reserve(fixture):
    ledger, worker, config, _ = fixture
    trial = worker.trials.reserve(1, "request-001", config)
    worker.enqueue(trial["attempt_id"])
    assert ledger.factor_mining_trial_total(1) == 1
    return trial["attempt_id"]


def fake_train(worker, monkeypatch):
    def execute(mode, directory, payload):
        assert mode == "train"
        (directory / "input.json").write_bytes(canonical(payload))
        (directory / "model.pt").write_bytes(b"isolated numeric artifact fixture")
        result = {
            "config": payload["config"],
            "train_samples": len(payload["rows"]),
            "losses": [0.1],
        }
        (directory / "result.json").write_bytes(canonical(result))
        return result

    monkeypatch.setattr(worker, "_execute", execute)


def publish(worker, clock, *, rows=None):
    clock[0] = NOW + timedelta(days=1)
    rows = rows or [
        {"instrument": symbol, "features": [1.0] * 360} for symbol in SYMBOLS
    ]
    blob = b"isolated current-day source fixture"
    worker.authority.publish_features(
        "alice", "2026-10-13", rows, {digest(blob): blob}, now=clock[0]
    )


def test_archived_protected_boundary_still_blocks_later_training_labels(fixture):
    ledger, worker, config, _ = fixture
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute(
            "INSERT INTO sealed_tests(strategy_version,factor_pipeline_version,"
            "data_version,sealed_start,sealed_end,dataset_hash,bars_json,"
            "metadata_json,parameters_json,created_at,archived_at) "
            "SELECT strategy_version,factor_pipeline_version,data_version,"
            "'2024-01-01','2024-01-31',dataset_hash,bars_json,metadata_json,"
            "parameters_json,created_at,'2026-10-01T00:00:00+00:00' "
            "FROM sealed_tests WHERE id=1"
        )
    attempt = worker.trials.reserve(1, "archived-boundary", config)["attempt_id"]
    with pytest.raises(PermissionError, match="Protected boundary"):
        worker.enqueue(attempt)
    assert worker.job(attempt)["worker_status"] == "not_queued"
    assert worker.trials.get(attempt)["status"] == "reserved"
    assert ledger.factor_mining_trial_total(1) == 1


def test_missing_approval_and_replay_cannot_be_admitted(fixture):
    ledger, worker, config, _ = fixture
    modified = {**config, "dataset_hash": "a" * 64}
    attempt = worker.trials.reserve(1, "request-001", modified)["attempt_id"]
    with pytest.raises(PermissionError, match="approval missing"):
        worker.enqueue(attempt)
    assert worker.trials.get(attempt)["status"] == "reserved"
    assert ledger.factor_mining_trial_total(1) == 1
    data, sources = training_data()
    data["purpose"] = "historical_replay"
    with pytest.raises(PermissionError, match="prospective"):
        worker.authority.approve_dataset(
            canonical(data),
            sources,
            owner="alice",
            data_version="fixture-v1",
            audit_reference="fixture",
            now=NOW,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("feature_available_at", "2099-01-01T18:00:00+08:00"),
        ("feature_available_at", "2024-01-01T14:00:00+08:00"),
        ("membership_available_at", "2099-01-01T14:00:00+08:00"),
        ("label_available_at", "2099-01-01T18:00:00+08:00"),
        ("label_available_at", "2024-01-08T10:00:00+08:00"),
        ("label_end", "2024-01-01"),
        ("label", float("nan")),
    ],
)
def test_source_admission_rejects_invalid_temporal_evidence(tmp_path, field, value):
    authority = EvidenceAuthority(tmp_path / "approvals")
    data, sources = training_data()
    data["rows"][0][field] = value
    with pytest.raises((ValueError, PermissionError)):
        authority.approve_dataset(
            json.dumps(data).encode(),
            sources,
            owner="alice",
            data_version="fixture-v1",
            audit_reference="fixture",
            now=NOW,
        )


def test_owned_admission_bounds_and_immutable_signed_approval(fixture):
    ledger, worker, config, _ = fixture
    attempt = reserve(fixture)
    assert worker.enqueue(attempt)["worker_status"] == "queued"
    foreign = DeepFormalWorker(
        ledger,
        "bob",
        authority=worker.authority,
        artifacts=worker.artifacts,
        runtime=RUNTIME,
        script=worker.script,
    )
    with pytest.raises(LookupError):
        foreign.enqueue(attempt)
    path = worker.authority.root / (config["dataset_hash"] + ".approval")
    value = json.loads(path.read_bytes())
    value["payload"]["owner"] = "bob"
    path.write_bytes(canonical(value))
    with pytest.raises(PermissionError, match="Unsigned"):
        worker.enqueue(attempt)


def test_counted_actual_training_receipt_and_weight_verifier(fixture, monkeypatch):
    ledger, worker, _, _ = fixture
    attempt = reserve(fixture)
    fake_train(worker, monkeypatch)
    assert worker.train_one()
    assert worker.job(attempt)["worker_status"] == "completed"
    assert worker.trials.get(attempt)["status"] == "completed"
    assert not worker.train_one()
    assert ledger.factor_mining_trial_total(1) == 1
    receipt = worker.authority.load(attempt + ".receipt")
    permit = {
        "attempt_id": attempt,
        "owner": "alice",
        "config": receipt["config"],
        "started_at": receipt["started_at"],
        "registered_at": NOW.isoformat(),
    }
    assert worker.verify(permit)["pit_evidence_verified"]
    (worker.artifacts / attempt / "model.pt").write_bytes(b"tampered")
    with pytest.raises(PermissionError, match="artifact mismatch"):
        worker.verify(permit)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute("SELECT enabled FROM deep_formal_models").fetchone()[0] == 0
        assert (
            db.execute("SELECT bars_json FROM sealed_tests").fetchone()[0]
            == "DO_NOT_READ_SEALED_PRICES"
        )
        assert (
            db.execute("SELECT COUNT(*) FROM factor_mining_acceptances").fetchone()[0]
            == 0
        )


def test_crash_recovers_without_training_replay_or_budget_refund(fixture):
    ledger, worker, _, _ = fixture
    attempt = reserve(fixture)
    worker.trials.start(attempt)
    with closing(sqlite3.connect(ledger.path)) as db, db:
        db.execute("UPDATE deep_worker_jobs SET status='running'")
    worker.recover()
    assert worker.job(attempt)["worker_status"] == "interrupted"
    assert worker.trials.get(attempt)["status"] == "failed"
    assert ledger.factor_mining_trial_total(1) == 1
    assert not worker.train_one()


def test_failed_subprocess_is_counted_once(fixture, monkeypatch):
    ledger, worker, _, _ = fixture
    attempt = reserve(fixture)
    monkeypatch.setattr(worker, "_execute", Mock(side_effect=RuntimeError("secret")))
    worker.train_one()
    assert worker.job(attempt)["error_code"] == "RuntimeError"
    assert worker.job(attempt)["worker_status"] == "failed"
    assert worker.trials.get(attempt)["status"] == "failed"
    assert ledger.factor_mining_trial_total(1) == 1
    assert not worker.train_one()


def test_concurrent_training_claim_is_unique(fixture, monkeypatch):
    ledger, worker, _, _ = fixture
    reserve(fixture)
    fake_train(worker, monkeypatch)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: worker.train_one(), range(2)))
    assert sorted(results) == [False, True]
    assert ledger.factor_mining_trial_total(1) == 1


def test_forward_receipt_generation_and_immutable_daily_inference(fixture, monkeypatch):
    ledger, worker, _, clock = fixture
    reserve(fixture)
    fake_train(worker, monkeypatch)
    worker.train_one()
    assert worker.score_today() == 0
    publish(worker, clock)

    def infer(mode, directory, payload):
        assert mode == "score"
        return {
            "rows": [
                {"instrument": r["instrument"], "score": 0.1} for r in payload["rows"]
            ]
        }

    monkeypatch.setattr(worker, "_execute", infer)
    assert worker.score_today() == 1
    assert worker.score_today() == 0
    with closing(sqlite3.connect(ledger.path)) as db:
        row = db.execute(
            "SELECT score_date,received_at FROM deep_formal_score_batches"
        ).fetchone()
        assert row == ("2026-10-13", clock[0].isoformat())
        assert db.execute("SELECT enabled FROM deep_formal_models").fetchone()[0] == 0


@pytest.mark.parametrize("tamper", ["weight", "coverage", "symbols", "nan"])
def test_invalid_forward_inference_not_received(fixture, monkeypatch, tamper):
    ledger, worker, _, clock = fixture
    attempt = reserve(fixture)
    fake_train(worker, monkeypatch)
    worker.train_one()
    rows = [{"instrument": symbol, "features": [1.0] * 360} for symbol in SYMBOLS]
    publish(worker, clock, rows=rows[:1] if tamper == "coverage" else rows)
    if tamper == "weight":
        (worker.artifacts / attempt / "model.pt").write_bytes(b"tampered")

    def infer(*args):
        symbol = "000002.SZ" if tamper == "symbols" else SYMBOLS[0]
        return {
            "rows": [
                {
                    "instrument": symbol,
                    "score": float("nan") if tamper == "nan" else 0.1,
                },
                {"instrument": SYMBOLS[1], "score": 0.2},
            ]
        }

    monkeypatch.setattr(worker, "_execute", infer)
    assert worker.score_today() == 0
    assert worker.score_today() == 0
    with closing(sqlite3.connect(ledger.path)) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM deep_formal_score_batches").fetchone()[0]
            == 0
        )
        assert (
            db.execute("SELECT status FROM deep_worker_scores").fetchone()[0]
            == "failed"
        )


def test_past_feature_receipt_cannot_be_published(fixture):
    _, worker, _, clock = fixture
    with pytest.raises(PermissionError, match="current-day"):
        worker.authority.publish_features(
            "alice",
            "2024-01-01",
            [{"instrument": SYMBOLS[0], "features": [1.0] * 360}],
            {digest(b"source"): b"source"},
            now=clock[0],
        )


def test_exact_worker_action_and_no_client_path_or_pit_overrides(fixture):
    ledger, worker, _, _ = fixture
    trial = worker.trials.reserve(1, "request-001", fixture[2])
    execute = Mock(return_value={"worker_status": "queued"})
    formal_trial_request(
        "trial-execute",
        {"attempt_id": trial["attempt_id"]},
        owner="alice",
        ledger=ledger,
        execute=execute,
    )
    execute.assert_called_once()
    with pytest.raises(ValueError):
        formal_trial_request(
            "trial-execute",
            {
                "attempt_id": trial["attempt_id"],
                "path": "model.pt",
                "pit_verified": True,
            },
            owner="alice",
            ledger=ledger,
            execute=execute,
        )


def test_alpha360_uses_amount_adjusted_volume_and_exact_order():
    window = [
        {
            "close": 10,
            "open": 9,
            "high": 11,
            "low": 8,
            "vol": 100,
            "amount": 100,
            "adj_factor": 2,
        }
        for _ in range(60)
    ]
    window[0]["adj_factor"] = 1
    values = alpha360(window)
    assert len(values) == 360
    assert values[0] == 0.5 and values[59] == 1
    assert values[60] == 0.45 and values[119] == 0.9
    assert values[240] == 0.5 and values[299] == 1
    assert values[300] == pytest.approx(2)
    with pytest.raises(ValueError):
        alpha360(window[:-1])
    window[0]["amount"] = 0
    with pytest.raises(ValueError):
        alpha360(window)


@pytest.mark.parametrize("architecture", ["Linear", "MLP", "GRU"])
def test_real_independent_cpu_training_registration_and_inference(
    fixture, architecture
):
    if not RUNTIME.is_file():
        pytest.skip("Independent CPU runtime not installed on this test host")
    ledger, worker, config, clock = fixture
    config["architecture"] = architecture
    attempt = reserve(fixture)
    assert "torch" not in sys.modules
    worker.train_one()
    assert worker.job(attempt)["worker_status"] == "completed"
    receipt = worker.authority.load(attempt + ".receipt")
    assert receipt["weights_sha256"] == digest(
        (worker.artifacts / attempt / "model.pt").read_bytes()
    )
    publish(worker, clock)
    assert worker.score_today() == 1
    assert worker.score_today() == 0
    assert "torch" not in sys.modules
    assert ledger.factor_mining_trial_total(1) == 1


def test_scheduler_lease_is_exclusive_and_shutdown_releases(tmp_path, monkeypatch):
    stores = Mock()
    stores.root = tmp_path / "users"
    users = Mock()
    users.list_users.return_value = []
    first = DeepFormalScheduler(users, stores, tmp_path / "cache")
    second = DeepFormalScheduler(users, stores, tmp_path / "cache")
    monkeypatch.delenv("A_PI_DEEP_AUTO", raising=False)
    first.start()
    try:
        second.start()
        assert first.lease is not None
        assert second.lease is None
    finally:
        first.stop()
        second.stop()
    assert first.lease is None


def test_collector_does_not_publish_on_closed_day(tmp_path):
    authority = EvidenceAuthority(tmp_path / "approvals")
    collector = FormalFeatureCollector(
        tmp_path / "cache",
        "unused-test-token",
        authority,
        clock=lambda: NOW,
        cancelled=lambda: False,
    )
    collector.provider.query = Mock(
        return_value=[{"cal_date": "20261012", "is_open": 0}]
    )
    assert not collector.collect("alice", SYMBOLS, NOW)
    assert not authority.root.exists()


def test_scheduler_account_failure_does_not_starve_other_accounts(
    tmp_path, monkeypatch
):
    stores = Mock(root=tmp_path / "users")
    users = Mock()
    users.list_users.return_value = [
        SimpleNamespace(username="alice", disabled=False),
        SimpleNamespace(username="bob", disabled=False),
        SimpleNamespace(username="carol", disabled=True),
    ]
    scheduler = DeepFormalScheduler(users, stores, tmp_path / "cache")
    callback = Mock(side_effect=[RuntimeError("isolated failure"), None])
    monkeypatch.setattr(scheduler, "_run_user", callback)
    scheduler.run_once(NOW)
    assert [call.args[0] for call in callback.call_args_list] == ["alice", "bob"]


def test_collector_cold_cache_is_bounded_then_freezes_real_day_features(tmp_path):
    clock = [NOW]
    authority = EvidenceAuthority(tmp_path / "approvals")
    collector = FormalFeatureCollector(
        tmp_path / "cache",
        "unused-test-token",
        authority,
        clock=lambda: clock[0],
        cancelled=lambda: False,
    )
    calendar = []
    day = NOW.astimezone().date()
    # Fixture calendar, explicitly not production supplier evidence.
    while len(calendar) < 60:
        if day.weekday() < 5:
            calendar.append(day.strftime("%Y%m%d"))
        day -= timedelta(days=1)
    calendar.sort()
    calls = []

    def query(name, parameters, fields):
        calls.append(name)
        if name == "trade_cal":
            return [{"cal_date": day, "is_open": 1} for day in calendar]
        day = parameters["trade_date"]
        symbols = [*SYMBOLS, *(f"{i:06d}.SZ" for i in range(100000, 102998))]
        if name == "adj_factor":
            return [
                {"ts_code": symbol, "trade_date": day, "adj_factor": 2}
                for symbol in symbols
            ]
        return [
            {
                "ts_code": symbol,
                "trade_date": day,
                "open": 9,
                "high": 11,
                "low": 8,
                "close": 10,
                "vol": 100,
                "amount": 100,
            }
            for symbol in symbols
        ]

    collector.provider.query = query
    assert not collector.collect("alice", SYMBOLS, NOW)
    assert (
        len(calls) == 49
    )  # Calendar + at most 48 market calls, no eager full warm-up.
    assert not list(authority.root.glob("*.features"))
    assert not collector.collect("alice", SYMBOLS, NOW)
    assert collector.collect("alice", SYMBOLS, NOW)
    batch = authority.features("alice", "2026-10-12")
    assert batch["feature_available_at"] == NOW.isoformat()
    assert len(batch["rows"]) == 2
    assert all(len(row["features"]) == 360 for row in batch["rows"])
    count = len(calls)
    assert collector.collect("alice", SYMBOLS, NOW)
    assert len(calls) == count  # Frozen day's collector is idempotent.


def test_collector_rejects_future_calendar(tmp_path):
    authority = EvidenceAuthority(tmp_path / "approvals")
    collector = FormalFeatureCollector(
        tmp_path / "cache",
        "unused-test-token",
        authority,
        clock=lambda: NOW,
        cancelled=lambda: False,
    )
    collector.provider.query = Mock(
        return_value=[
            {"cal_date": "20261012", "is_open": 1},
            {"cal_date": "20990101", "is_open": 1},
        ]
    )
    with pytest.raises(PermissionError, match="future"):
        collector.collect("alice", SYMBOLS, NOW)


def test_http_worker_admission_auth_csrf_owner_and_override_guards(
    fixture, auth, tmp_path, monkeypatch
):
    ledger, worker, config, _ = fixture
    limits = ResourceLimits(tmp_path / "limits.sqlite3", hourly_limit=100)
    monkeypatch.setattr("quant.api.http_handler.get_resource_limits", lambda: limits)

    def execute(action, payload, owner):
        assert owner == "alice"
        return (
            worker.enqueue(payload["attempt_id"])
            if action == "trial-execute"
            else worker.job(payload["attempt_id"])
        )

    def callback(action, payload):
        return formal_trial_request(
            action,
            payload,
            owner=CURRENT_USERNAME.get(),
            ledger=ledger,
            execute=execute,
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
    trial = worker.trials.reserve(1, "request-001", config)
    payload = {"attempt_id": trial["attempt_id"]}
    try:
        port = server.server_port
        assert request(port, "POST", route + "execute", payload)[0] == 401
        headers = {}
        for user, password in [
            ("alice", "alice-long-password"),
            ("bob", "bobby-long-password"),
        ]:
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
                route + "execute",
                payload,
                {"Cookie": headers["alice"]["Cookie"]},
            )[0]
            == 403
        )
        assert (
            request(port, "POST", route + "execute", payload, headers["bob"])[0] == 404
        )
        assert (
            request(
                port,
                "POST",
                route + "execute",
                {**payload, "weights": "some-file"},
                headers["alice"],
            )[0]
            == 400
        )
        status, _, result = request(
            port, "POST", route + "execute", payload, headers["alice"]
        )
        assert status == 200 and result["worker_status"] == "queued"
        assert (
            request(port, "POST", route + "status", payload, headers["bob"])[0] == 404
        )
        assert (
            request(port, "POST", route + "status", payload, headers["alice"])[2][
                "worker_status"
            ]
            == "queued"
        )
        with closing(sqlite3.connect(limits.path)) as db:
            before = db.execute("SELECT COUNT(*) FROM resource_requests").fetchone()[0]
        for _ in range(25):
            assert (
                request(port, "POST", route + "status", payload, headers["alice"])[0]
                == 200
            )
        get_route = route + trial["attempt_id"]
        assert request(port, "GET", get_route)[0] == 401
        assert request(port, "GET", get_route, headers=headers["bob"])[0] == 404
        assert (
            request(port, "GET", get_route, headers=headers["alice"])[2][
                "worker_status"
            ]
            == "queued"
        )
        assert (
            request(
                port,
                "POST",
                route + "status",
                {**payload, "mode": "train"},
                headers["alice"],
            )[0]
            == 400
        )
        with closing(sqlite3.connect(limits.path)) as db:
            assert (
                db.execute("SELECT COUNT(*) FROM resource_requests").fetchone()[0]
                == before
            )
        assert ledger.factor_mining_trial_total(1) == 1
        assert worker.trials.get(trial["attempt_id"])["status"] == "reserved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "missing",
    [
        "source_availability",
        "revision_vintages",
        "historical_membership",
        "adjustment_availability",
    ],
)
def test_operator_cannot_approve_incomplete_source_audit(tmp_path, missing):
    data, _ = training_data()
    dataset = tmp_path / "data.json"
    dataset.write_bytes(canonical(data))
    review = {
        "policy": "strict-deep-pit-v1",
        "status": "approved",
        "owner": "alice",
        "data_version": "fixture-v1",
        "dataset_hash": digest(dataset.read_bytes()),
        "reviewer": "isolated-test-reviewer",
        "reviewed_at": NOW.isoformat(),
        "source_hashes": ["a" * 64],
        "checks": dict.fromkeys(
            [
                "source_availability",
                "revision_vintages",
                "historical_membership",
                "listing_st_delisting",
                "adjustment_availability",
                "feature_contract",
            ],
            True,
        ),
    }
    review["checks"][missing] = False
    audit = tmp_path / "audit.json"
    audit.write_bytes(canonical(review))
    with pytest.raises(PermissionError, match="incomplete"):
        approve(
            tmp_path / "operator",
            dataset,
            audit,
            tmp_path / "sources",
            owner="alice",
            data_version="fixture-v1",
            now=NOW,
        )
    assert not (tmp_path / "operator").exists()
