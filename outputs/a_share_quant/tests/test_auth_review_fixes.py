"""Security-review regressions, using isolated databases and no market calls."""

import logging
import os
import sqlite3
from unittest.mock import patch

import pytest

from quant.api.auth import Auth
from quant.api.auth_routes import auth_response
from quant.api.auth_store import AuthStore, _protect_database_files, password_hash
from quant.api.login_limits import LoginLimits
from quant.api.private_files import protect_private_path
from tests.conftest import request


def test_login_audit_covers_success_failure_and_rejected_lock(auth):
    logger = logging.getLogger("audit-test")
    login = {"username": "alice", "password": "alice-long-password"}
    assert (
        auth_response(
            auth, "/api/auth/login", login, None, "good-ip", logger, "test-agent"
        )[0]
        == 200
    )
    for _ in range(5):
        assert (
            auth_response(
                auth,
                "/api/auth/login",
                {**login, "password": "wrong-password"},
                None,
                "bad-ip",
                logger,
                "test-agent",
            )[0]
            == 401
        )
    assert (
        auth_response(
            auth, "/api/auth/login", login, None, "bad-ip", logger, "test-agent"
        )[0]
        == 429
    )
    with sqlite3.connect(auth.store.path) as db:
        rows = db.execute(
            "SELECT action,target,ip,user_agent,success FROM security_audit ORDER BY id"
        ).fetchall()
    assert rows[0] == ("login", "alice", "good-ip", "test-agent", 1)
    assert [row[4] for row in rows[1:]] == [0] * 6
    assert rows[-1][0] == "login.rate_limited"
    # Persisted across restarts; never include the supplied password.
    AuthStore(auth.store.path)
    assert "wrong-password" not in repr(rows)


def test_totp_failure_is_audited(auth):
    with patch.object(auth.second_factor, "enabled", return_value=True):
        status = auth_response(
            auth,
            "/api/auth/login",
            {"username": "alice", "password": "alice-long-password"},
            None,
            "ip",
            logging.getLogger("test"),
        )[0]
    assert status == 401
    with sqlite3.connect(auth.store.path) as db:
        assert db.execute("SELECT action,success FROM security_audit").fetchall() == [
            ("login", 0)
        ]


def test_old_global_username_locks_removed_on_upgrade(tmp_path):
    limits = LoginLimits(tmp_path / "limits.sqlite3")
    with sqlite3.connect(limits.path) as db:
        db.execute("INSERT INTO login_locks VALUES('user:alice',99999999999)")
    assert LoginLimits(limits.path).locked_seconds("alice", "new-ip") == 0
    for index in range(5):
        limits.failed("alice", str(index))
    assert LoginLimits(limits.path).locked_seconds("alice", "legitimate-ip") == 0


@pytest.mark.parametrize(
    "password",
    [
        "password123",
        "Password123!",
        "1234567890",
        "aaaaaaaaaa",
        "admin123456",
        "qwertyuiop",
    ],
)
def test_common_passwords_rejected(password):
    with pytest.raises(ValueError, match="password_weak"):
        password_hash(password)


def test_forced_password_change_blocks_every_business_and_admin_path(auth, http_api):
    user = auth.store.create_user(
        "charlie", "initial-long-password", "admin", must_change_password=True
    )
    status, headers, body = request(
        http_api,
        "POST",
        "/api/auth/login",
        {"username": user.username, "password": "initial-long-password"},
    )
    assert status == 200 and body["must_change_password"] is True
    cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-A-Pi-Request": "1"}
    assert request(http_api, "GET", "/api/auth/session", headers=cookie)[2][
        "must_change_password"
    ]
    assert request(http_api, "GET", "/api/health", headers=cookie)[2] == {"ok": True}
    for method, path in [
        ("GET", "/api/workspace"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/auth/sessions"),
        ("GET", "/api/ws/backtest/example"),
        ("POST", "/api/paper/cycle"),
        ("POST", "/api/auth/2fa/setup"),
    ]:
        status, _, body = request(http_api, method, path, {}, cookie)
        assert status == 403 and body["error"] == "password_change_required"
    body = {
        "old_password": "initial-long-password",
        "new_password": "initial-long-password",
    }
    assert (
        request(http_api, "POST", "/api/auth/change-password", body, cookie)[2]["error"]
        == "password_unchanged"
    )
    body["new_password"] = "password123"
    assert (
        request(http_api, "POST", "/api/auth/change-password", body, cookie)[2]["error"]
        == "password_weak"
    )
    assert auth.store.get_by_username("charlie").must_change_password
    body["new_password"] = "a-unique-new-passphrase"
    assert (
        request(http_api, "POST", "/api/auth/change-password", body, cookie)[0] == 204
    )
    assert request(http_api, "GET", "/api/auth/session", headers=cookie)[0] == 401
    assert not auth.store.get_by_username("charlie").must_change_password


def test_admin_created_user_requires_password_change(auth, http_api):
    cookie = {
        "Cookie": "a_pi_session="
        + auth.issue_token(auth.store.get_by_username("alice")),
        "X-A-Pi-Request": "1",
    }
    assert (
        request(
            http_api,
            "POST",
            "/api/admin/users",
            {
                "username": "charlie",
                "password": "initial-long-password",
                "current_password": "alice-long-password",
            },
            cookie,
        )[0]
        == 201
    )
    assert auth.store.get_by_username("charlie").must_change_password
    assert not auth.store.get_by_username("alice").must_change_password
    assert (
        request(
            http_api,
            "POST",
            "/api/admin/users",
            {
                "username": "weakuser",
                "password": "password123",
                "current_password": "alice-long-password",
            },
            cookie,
        )[2]["error"]
        == "password_weak"
    )


def test_private_acl_failure_stops_secret_read(auth, tmp_path, monkeypatch):
    # This test specifically exercises file-backed secrets, independently of
    # the isolated suite's environment-supplied authentication secret.
    monkeypatch.delenv("A_PI_AUTH_SECRET", raising=False)
    with (
        patch(
            "quant.api.auth.protect_private_path",
            side_effect=PermissionError("ACL failed"),
        ),
        pytest.raises(PermissionError),
    ):
        Auth(auth.store, tmp_path / "secret.key")


def test_private_file_permissions(tmp_path):
    path = tmp_path / "private.key"
    path.touch()
    protect_private_path(path)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    else:
        # Real Windows ACL integration; inspect metadata, never secret contents.
        import subprocess

        result = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            check=True,
        )
        assert "(I)" not in result.stdout
        assert "Everyone" not in result.stdout and "BUILTIN\\Users" not in result.stdout


def test_sidecar_disappearance_does_not_mask_main_database_acl_failure(tmp_path):
    main = tmp_path / "auth.sqlite3"
    main.touch()
    sidecar = tmp_path / "auth.sqlite3-wal"
    sidecar.touch()

    def protect(path):
        if path == sidecar:
            raise FileNotFoundError("SQLite removed sidecar")

    with patch("quant.api.auth_store.protect_private_path", side_effect=protect):
        _protect_database_files(main)
    with (
        patch(
            "quant.api.auth_store.protect_private_path",
            side_effect=PermissionError("Denied"),
        ),
        pytest.raises(PermissionError),
    ):
        _protect_database_files(main)
