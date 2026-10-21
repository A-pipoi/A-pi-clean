from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.external_deep_import import external_deep_request
from quant.api.http_handler import HttpApiHandler
from quant.api.resource_limits import ResourceLimits
from quant.api.user_scope import CURRENT_USERNAME
from quant.factors.external_deep import stage_candidate
from tests.conftest import request
from tests.test_external_deep import fixture


@pytest.fixture
def setup(tmp_path):
    checksums = tmp_path / "checksums.json"
    checksums.write_text('{"test-fixture":"not-production-data"}', encoding="utf-8")
    ledger = tmp_path / "research.sqlite3"
    with closing(sqlite3.connect(ledger)) as db:
        db.execute(
            "CREATE TABLE sealed_tests("
            "sealed_start TEXT, archived_at TEXT, bars_json TEXT)"
        )
        db.executemany(
            "INSERT INTO sealed_tests VALUES (?,?,?)",
            [
                ("2026-07-02", "archived", "DO_NOT_READ_SEALED_PRICES"),
                ("2026-08-01", None, "DO_NOT_READ_SEALED_PRICES"),
            ],
        )
        db.commit()
    metadata, rows = fixture()
    metadata["dataset_hash"] = hashlib.sha256(checksums.read_bytes()).hexdigest()
    inbox = tmp_path / "inbox"
    source = inbox / "alice" / "research_candidates.sqlite3"
    staged = stage_candidate(
        source, metadata, rows, owner="alice", sealed_start="2026-07-02"
    )
    values = {
        "owner": "alice",
        "inbox_root": inbox,
        "destination": tmp_path / "alice-imports.sqlite3",
        "ledger_path": ledger,
        "dataset_checksums": checksums,
    }
    return (
        values,
        {"model_id": metadata["model_id"], "version": staged["version"]},
        source,
    )


def test_readonly_listing_and_idempotent_authenticated_import(setup):
    values, payload, source = setup
    before = source.read_bytes()
    report = external_deep_request("list", {}, **values)
    assert report["imported"] == [] and len(report["available"]) == 1
    assert not values["destination"].exists()
    imported = external_deep_request("import", payload, **values)
    assert imported["enabled"] is False and imported["status"] == "research_only"
    assert imported["formal_budget_recorded"] is False
    assert imported["production_import_allowed"] is False
    assert imported["version"] == payload["version"]
    assert (
        external_deep_request("import", payload, **values)["version"]
        == imported["version"]
    )
    assert len(external_deep_request("list", {}, **values)["imported"]) == 1
    assert source.read_bytes() == before
    with closing(sqlite3.connect(values["destination"])) as db:
        assert db.execute(
            "SELECT enabled,status FROM external_deep_candidates"
        ).fetchall() == [(0, "research_only")]
    with closing(sqlite3.connect(values["ledger_path"])) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("sealed_tests",)]


@pytest.mark.parametrize(
    "extra",
    [{"owner": "bob"}, {"path": "../secret"}, {"enabled": True}, {"production": True}],
)
def test_request_overrides_rejected_without_destination_write(setup, extra):
    values, payload, _ = setup
    with pytest.raises(ValueError):
        external_deep_request("import", {**payload, **extra}, **values)
    assert not values["destination"].exists()


def test_other_owner_cannot_list_or_import_artifact(setup):
    values, payload, _ = setup
    values = {**values, "owner": "bob"}
    assert external_deep_request("list", {}, **values)["available"] == []
    with pytest.raises(LookupError):
        external_deep_request("import", payload, **values)
    assert not values["destination"].exists()


@pytest.mark.parametrize(
    "field,value", [("model_id", "../model"), ("version", "bad"), ("version", "0" * 64)]
)
def test_invalid_or_stale_version_rejected(setup, field, value):
    values, payload, _ = setup
    with pytest.raises(ValueError):
        external_deep_request("import", {**payload, field: value}, **values)
    assert not values["destination"].exists()


def test_canonical_tampering_and_dataset_binding_rejected(setup):
    values, payload, source = setup
    with closing(sqlite3.connect(source)) as db:
        document = json.loads(
            db.execute("SELECT payload FROM external_deep_candidates").fetchone()[0]
        )
        document["scores"][0]["score"] = 99
        db.execute(
            "UPDATE external_deep_candidates SET payload=?", (json.dumps(document),)
        )
        db.commit()
    with pytest.raises(ValueError, match="integrity"):
        external_deep_request("import", payload, **values)
    values["dataset_checksums"].write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="dataset"):
        external_deep_request("import", payload, **values)
    assert not values["destination"].exists()


def test_earliest_boundary_including_archived_is_enforced(setup):
    values, payload, _ = setup
    with closing(sqlite3.connect(values["ledger_path"])) as db:
        db.execute(
            "INSERT INTO sealed_tests VALUES ('2026-02-01','archived','NO_PRICE_READ')"
        )
        db.commit()
    with pytest.raises(ValueError, match="sealed overlap"):
        external_deep_request("import", payload, **values)
    with closing(sqlite3.connect(values["ledger_path"])) as db:
        db.execute("DELETE FROM sealed_tests")
        db.commit()
    with pytest.raises(PermissionError, match="boundary"):
        external_deep_request("import", payload, **values)
    assert not values["destination"].exists()


def test_source_index_cannot_relabel_model_identity(setup):
    values, payload, source = setup
    with closing(sqlite3.connect(source)) as db:
        db.execute("UPDATE external_deep_candidates SET model_id='renamed'")
        db.commit()
    with pytest.raises(ValueError, match="model identity"):
        external_deep_request("import", {**payload, "model_id": "renamed"}, **values)
    assert not values["destination"].exists()


def test_inbox_resolution_cannot_escape_user_directory(setup, monkeypatch):
    from pathlib import Path

    values, payload, source = setup
    original_resolve = Path.resolve

    def resolved(path, *args, **kwargs):
        if path == source:
            return source.parent.parent.parent / "other-owner" / source.name
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(PermissionError, match="escapes owner"):
        external_deep_request("import", payload, **values)
    assert not values["destination"].exists()


def test_http_real_login_csrf_owner_and_disabled_import(setup, tmp_path, monkeypatch):
    values, payload, _ = setup
    auth = Auth(
        AuthStore(tmp_path / "auth.sqlite3"),
        tmp_path / "unused.key",
        secret="isolated-integration-secret-not-production",
    )
    auth.store.create_user("alice", "test-only-alice-password")
    auth.store.create_user("bob", "test-only-bob-password")
    limits = ResourceLimits(tmp_path / "limits.sqlite3", hourly_limit=100)
    monkeypatch.setattr("quant.api.http_handler.get_resource_limits", lambda: limits)
    monkeypatch.delenv("A_PI_PUBLIC_ORIGIN", raising=False)

    def callback(action, body):
        owner = CURRENT_USERNAME.get()
        return external_deep_request(
            action,
            body,
            **{
                **values,
                "owner": owner,
                "destination": tmp_path / f"{owner}-imports.sqlite3",
            },
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
    try:
        port = server.server_port
        route = "/api/mining/external-deep/import"
        assert request(port, "POST", route, payload)[0] == 401
        assert request(port, "GET", "/api/mining/external-deep")[0] == 401
        cookies = {}
        for username in ("alice", "bob"):
            status, headers, _ = request(
                port,
                "POST",
                "/api/auth/login",
                {
                    "username": username,
                    "password": f"test-only-{username}-password",
                },
            )
            assert status == 200
            cookies[username] = headers["Set-Cookie"].split(";", 1)[0]
        assert (
            request(port, "POST", route, payload, {"Cookie": cookies["alice"]})[0]
            == 403
        )
        alice = {"Cookie": cookies["alice"], "X-A-Pi-Request": "1"}
        bob = {"Cookie": cookies["bob"], "X-A-Pi-Request": "1"}
        assert (
            request(
                port,
                "POST",
                route,
                payload,
                {**alice, "Origin": "https://not-authorized.invalid"},
            )[0]
            == 403
        )
        assert request(port, "POST", route, payload, bob)[0] == 404
        assert (
            request(port, "POST", route, {**payload, "owner": "bob"}, alice)[0] == 400
        )
        result = request(port, "POST", route, payload, alice)
        assert result[0] == 200 and result[2]["enabled"] is False
        assert result[2]["formal_budget_recorded"] is False
        assert (
            request(port, "GET", "/api/mining/external-deep", headers=alice)[2][
                "imported"
            ][0]["version"]
            == payload["version"]
        )
        assert (
            request(port, "GET", "/api/mining/external-deep", headers=bob)[2][
                "imported"
            ]
            == []
        )
        claims = auth.verify_token(cookies["alice"].split("=", 1)[1])
        auth.revoke(claims.jti)
        assert request(port, "POST", route, payload, alice)[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
