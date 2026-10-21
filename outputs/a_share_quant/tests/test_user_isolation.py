import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date, datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.backtest_jobs import BacktestJobDependencies, BacktestJobManager
from quant.api.feature_namespace import FeatureNamespace
from quant.api.shared_sealed import publish_sealed_data
from quant.api.user_scheduler import SHANGHAI, UserPaperScheduler
from quant.api.user_scope import (
    CURRENT_USERNAME,
    ScopedStoreProxy,
    UserScopedStores,
    user_context,
)
from quant.api.user_settings import UserPaperSettings
from quant.execution import PaperStore
from quant.factors import FactorRegistryStore
from quant.features import FeatureStore
from quant.research import ExperimentRecord, ResearchLedger
from scripts.migrate_shared_data import migrate
from tests.conftest import request


@pytest.fixture
def stores(tmp_path):
    result = UserScopedStores(tmp_path)
    yield result
    result.close()


def test_path_validation_and_context_fail_closed(stores):
    with pytest.raises(RuntimeError):
        stores.current()
    for username in ("../other", "abc/def", "Alice", "x\\y"):
        with pytest.raises(ValueError):
            stores.get(username)
    with user_context("alice"):
        assert stores.current() is stores.get("alice")
    assert CURRENT_USERNAME.get() is None


def test_store_creation_is_serialized(stores):
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: stores.get("alice"), range(20)))
    assert all(value is values[0] for value in values)


def test_custom_feature_namespaces_and_source_conflicts(tmp_path):
    store = FeatureStore(tmp_path / "features.sqlite3")
    alice = FeatureNamespace(store, "alice")
    bob = FeatureNamespace(store, "bob")
    row = {"feature_name": "custom", "data_version": "v1", "symbol": "000001.SZ", "feature_date": "2026-01-01", "available_at": "2026-01-01T17:00:00+08:00", "feature_value": 0.5}
    assert alice.write_rows([row], expression="cs_rank(close)") == 1
    assert bob.write_rows([row], expression="cs_rank(volume)") == 1
    assert alice.read_rows(["custom"], data_version="v1", before=date(2026, 1, 2))[0]["feature_name"] == "u/alice/custom"
    assert not alice.read_rows(["custom"], data_version="v1", before=date(2026, 1, 1))
    with pytest.raises(LookupError):
        bob.read_rows(["u/alice/custom"], data_version="v1", before=date(2026, 1, 2))
    with pytest.raises(LookupError):
        bob.write_rows([{**row, "feature_name": "u/alice/custom"}], expression="cs_rank(close)")
    with pytest.raises(ValueError, match="source_hash conflict"):
        alice.write_rows([{**row, "symbol": "600036.SH"}], expression="cs_rank(volume)")


def test_paper_configuration_is_per_account(stores):
    alice = UserPaperSettings(stores.get("alice").paper_store.path)
    bob = UserPaperSettings(stores.get("bob").paper_store.path)
    alice.set("defensive", "weekly")
    assert alice.get() == {"strategy": "defensive", "rebalance": "weekly"}
    assert bob.get() == {}
    with pytest.raises(ValueError):
        bob.set("lightgbm", "weekly")


def test_sealed_truth_shared_but_does_not_contain_personal_results(tmp_path):
    seal = {"bars": [{"symbol": "000001.SZ"}], "pit_data": {}, "result": {"owner": "alice", "passed": True}}
    first = publish_sealed_data(tmp_path, seal)
    second = publish_sealed_data(tmp_path, {**seal, "result": {"owner": "bob"}})
    assert first == second
    assert "alice" not in first.read_text() and "bob" not in first.read_text()
    first.write_text("tampered")
    with pytest.raises(ValueError, match="modified"):
        publish_sealed_data(tmp_path, seal)


def test_legacy_bars_digest_only_allowed_without_pit_data(tmp_path):
    import hashlib
    import json
    bars = [{"symbol": "000001.SZ", "close": 10}]
    digest = hashlib.sha256(json.dumps(bars, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    seal = {'bars': bars, 'pit_data': {}, 'dataset_hash': digest}
    target = publish_sealed_data(tmp_path, seal)
    assert json.loads(target.read_text()) == {'bars': bars, 'pit_data': {}}
    assert seal['dataset_hash'] == digest
    with pytest.raises(ValueError, match='hash does not match'):
        publish_sealed_data(tmp_path, {**seal, 'pit_data': {'snapshots': [1]}})
    with pytest.raises(ValueError, match='hash does not match'):
        publish_sealed_data(tmp_path, {**seal, 'bars': [{'symbol': '000001.SZ', 'close': 11}]})


def test_budget_factors_paper_and_mining_are_independent(stores):
    alice, bob = stores.get("alice"), stores.get("bob")
    factor = alice.factor_store.register("Alice factor", "momentum", 20)
    assert factor.key not in [item.key for item in bob.factor_store.definitions()]
    for _ in range(20):
        alice.ledger.record(ExperimentRecord(strategy_version="baseline", data_version="test", parameters={}, status="passed"))
    assert not alice.ledger.can_record("baseline")
    assert bob.ledger.status("baseline")["remaining"] == 20
    order = alice.paper_store.create_order("000001.SZ", "BUY", 100)
    assert bob.paper_store.order(order["order_id"]) is None
    alice.ledger.save_factor_mining_run("alice-run", sealed_test_id=1, data_version="test", n_tested=2, result={})
    assert bob.ledger.factor_mining_run("alice-run") is None
    alice.workspace_path.write_text('{"owner":"alice"}')
    assert not bob.workspace_path.exists()
    assert alice.mining_dir != bob.mining_dir


def test_scheduler_isolates_failures_and_skips_disabled(stores, tmp_path):
    users = AuthStore(tmp_path / "auth.sqlite3")
    for name in ("alice", "bob", "disabled"):
        users.create_user(name, "long-test-password")
    users.set_disabled("disabled", True)
    calls = []
    class Scheduler:
        def __init__(self, username):
            self.username = username
        def run_once(self):
            assert CURRENT_USERNAME.get() == self.username
            calls.append(self.username)
            if self.username == "alice":
                raise RuntimeError("isolated failure")
    scheduler = UserPaperScheduler(users, stores, Scheduler)
    try:
        with patch("quant.api.user_scheduler.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 9, 16, 5, tzinfo=SHANGHAI)
            scheduler.run_once()
        assert set(calls) == {"alice", "bob"}
    finally:
        scheduler.stop()


def test_background_queue_captures_owner(stores):
    executor = ThreadPoolExecutor(max_workers=2)
    def execute(_payload):
        return 200, {"ok": True, "owner": CURRENT_USERNAME.get()}
    manager = BacktestJobManager(BacktestJobDependencies(executor=executor, queue_limit=4, ledger=lambda: stores.current().ledger, queue_mode=lambda: "local", redis_queue=lambda: None, redis_counts=lambda *_: {}, execute=execute, remote_worker=lambda *_: None))
    try:
        with user_context("alice"):
            first = manager.submit({"_auth_owner": "alice"})
        with user_context("bob"):
            second = manager.submit({"_auth_owner": "bob"})
        executor.shutdown(wait=True)
        assert manager.jobs[first]["owner"] == "alice"
        assert manager.jobs[second]["owner"] == "bob"
        assert stores.get("alice").ledger.get_backtest_job(second) is None
        assert stores.get("bob").ledger.get_backtest_job(first) is None
        assert manager.jobs[first]["result"]["owner"] == "alice"
        assert manager.jobs[second]["result"]["owner"] == "bob"
    finally:
        executor.shutdown(wait=True)


def test_real_http_idor_matrix(stores, tmp_path):
    from quant import api_server
    users = AuthStore(tmp_path / "auth" / "users.sqlite3")
    alice = users.create_user("alice", "long-test-password")
    bob = users.create_user("bob", "long-test-password")
    auth = Auth(users, tmp_path / "secret")
    alice_store = stores.get("alice")
    job_id = "BT-20261006-ALICE1234"
    alice_store.ledger.create_backtest_job(job_id, {})
    alice_store.ledger.save_factor_mining_run("alice-run", sealed_test_id=1, data_version="test", n_tested=2, result={})
    alice_store.factor_store.register("Alice private factor", "momentum", 30)
    with ExitStack() as stack:
        for name, value in {"AUTH": auth, "AUTH_STORE": users, "USER_STORES": stores, "PAPER_STORE": ScopedStoreProxy(stores, "paper_store"), "FACTOR_STORE": ScopedStoreProxy(stores, "factor_store"), "RESEARCH_LEDGER": ScopedStoreProxy(stores, "ledger")}.items():
            stack.enter_context(patch.object(api_server, name, value))
        stack.enter_context(patch.object(api_server, "BACKTEST_JOBS", {job_id: {"job_id": job_id, "owner": "alice", "status": "completed", "result": {"private": "alice"}}}))
        server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args, **kwargs: api_server.ApiHandler(*args, directory=str(tmp_path), **kwargs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        headers = {"Cookie": "a_pi_session=" + auth.issue_token(bob), "X-A-Pi-Request": "1"}
        try:
            for path in (f"/api/backtest/{job_id}", f"/api/ws/backtest/{job_id}", "/api/factors/alice-private", "/api/workspace/alice", "/api/research/archive/alice"):
                assert request(server.server_port, "GET", path, headers=headers)[0] == 404
            assert request(server.server_port, "POST", "/api/factor-mining/accept", {"run_id": "alice-run"}, headers)[0] == 404
            factors = request(server.server_port, "GET", "/api/factors", headers=headers)[2]["definitions"]
            assert all(item["name"] != "Alice private factor" for item in factors)
            alice_headers = {"Cookie": "a_pi_session=" + auth.issue_token(alice)}
            assert request(server.server_port, "GET", f"/api/backtest/{job_id}", headers=alice_headers)[0] == 200
            # An owned, syntactically valid WS job reaches origin validation;
            # a foreign one must return 404 BEFORE any upgrade is accepted.
            assert request(server.server_port, "GET", f"/api/ws/backtest/{job_id}", headers=alice_headers)[0] == 403
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def test_migration_preserves_legacy_data_and_shared_features(tmp_path):
    AuthStore(tmp_path / "auth" / "users.sqlite3").create_user("pipoi", "long-test-password")
    factor = FactorRegistryStore(tmp_path / "factors.sqlite3").register("My factor", "momentum", 20)
    paper = PaperStore(tmp_path / "paper.sqlite3")
    order = paper.create_order("000001.SZ", "BUY", 100)
    paper.close()
    ResearchLedger(tmp_path / "research.sqlite3").record(ExperimentRecord(strategy_version="baseline", data_version="test", parameters={}, status="passed"))
    features = tmp_path / "features.sqlite3"
    features.write_bytes(b"shared deterministic data")
    recovery = migrate(tmp_path, "pipoi", confirmed_offline=True, port=0)
    stores = UserScopedStores(tmp_path)
    try:
        account = stores.get("pipoi")
        assert factor.key in [item.key for item in account.factor_store.definitions()]
        assert account.paper_store.order(order["order_id"]) is not None
        assert account.ledger.status("baseline")["used"] == 1
        assert features.read_bytes() == b"shared deterministic data"
        assert (recovery / "paper.sqlite3").exists()
        assert not (tmp_path / "paper.sqlite3").exists()
        with pytest.raises(ValueError, match="Target exists"):
            migrate(tmp_path, "pipoi", confirmed_offline=True, port=0)
    finally:
        stores.close()
