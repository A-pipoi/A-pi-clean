import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest

from quant.api.account_management import handle_account_request
from quant.api.auth import Auth, TokenClaims
from quant.api.auth_store import AuthStore


@pytest.fixture
def auth(tmp_path):
    store = AuthStore(tmp_path / "users.sqlite3")
    store.create_user("admin", "admin-long-password")
    store.create_user("alice", "alice-long-password")
    instance = Auth(store, tmp_path / "secret", secret="s" * 32)
    return instance


def claims_for(auth, username, jti="current"):
    user = auth.store.get_by_username(username)
    now = int(time.time())
    return TokenClaims(user.id, user.username, user.role, now + 3600, now, jti, user.session_version)


def call(auth, path, payload=None, claims=None):
    return handle_account_request(auth, path, payload or {}, claims, "127.0.0.1", "test-agent")


def test_registry_persists_and_fails_closed(auth):
    user = auth.store.get_by_username("alice")
    with patch("quant.api.auth_store.time.time", return_value=1000):
        auth.store.register_session("first", user.id, 5000, "ip", "agent")
    restarted = AuthStore(auth.store.path)
    assert restarted.check_session("first", user.id, 1000)
    assert not restarted.check_session("missing", user.id, 1000)
    assert not restarted.check_session("first", 999, 1000)
    assert restarted.check_session("first", user.id, 2799)
    assert not restarted.check_session("first", user.id, 2800, touch=True)
    assert restarted.check_session("first", user.id, 2000, touch=True)
    assert restarted.check_session("first", user.id, 3799)
    assert not restarted.check_session("first", user.id, 5000, idle_seconds=10000)
    with patch("quant.api.auth_store.time.time", return_value=2000):
        rows = restarted.list_sessions(user.id, "first")
        assert rows[0]["current"] is True and rows[0]["ip"] == "ip"
        assert rows[0]["last_seen"] == "1970-01-01T00:33:20+00:00"
        with pytest.raises(sqlite3.IntegrityError):
            restarted.register_session("first", user.id, 5000, "ip", "agent")
        restarted.register_session("expired", user.id, 1999, "ip", "agent")
        assert not restarted.check_session("expired", user.id, 2000)


def test_owned_revoke_and_purge(auth):
    user = auth.store.get_by_username("alice")
    with patch("quant.api.auth_store.time.time", return_value=1000):
        auth.store.register_session("first", user.id, 5000, "ip", "agent")
    assert not auth.store.revoke_owned_session(999, "first")
    assert auth.store.check_session("first", user.id, 1001)
    assert auth.store.revoke_owned_session(user.id, "first")
    assert AuthStore(auth.store.path).session_revoked("first")
    assert not auth.store.check_session("first", user.id, 1001)
    with patch("quant.api.auth_store.time.time", return_value=1001):
        with pytest.raises(ValueError):
            auth.store.register_session("first", user.id, 6000, "ip", "agent")
        auth.store.register_session("second", user.id, 1100, "ip", "agent")
        auth.store.audit(user.id, "test")
    with patch("quant.api.auth_store.time.time", return_value=5000):
        auth.store.purge_security_records()
    with sqlite3.connect(auth.store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM revoked_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM security_audit").fetchone()[0] == 1


def test_session_cap_revokes_oldest_even_on_equal_timestamps(auth):
    alice = claims_for(auth, "alice")
    for index in range(13):
        auth.store.register_session(f"session-{index}", alice.sub, alice.exp, "ip", "agent")
    sessions = auth.store.list_sessions(alice.sub, "session-12")
    assert len(sessions) == 12
    assert auth.store.session_revoked("session-0")
    assert not auth.store.check_session("session-0", alice.sub, time.time())
    assert any(item["current"] for item in sessions)


def test_sensitive_admin_mutation_does_not_require_step_up(auth):
    response = call(auth, "/api/admin/users/disable", {"username": "alice"}, claims_for(auth, "admin"))
    assert response[0] == 200
    assert auth.store.get_by_username("alice").disabled


def test_sensitive_check_ignores_password_and_totp_challenges(auth):
    admin = claims_for(auth, "admin")
    with patch.object(auth.second_factor, "enabled", return_value=True):
        assert auth.check_sensitive(admin, {}, "127.0.0.1") is None
        assert auth.check_sensitive(admin, {"current_password": "wrong", "otp": "bad"}, "127.0.0.1") is None


@pytest.mark.parametrize("operation", ["disable", "role"])
def test_last_admin_guard_and_version(auth, operation):
    with pytest.raises(ValueError, match="last enabled admin"):
        if operation == "disable":
            auth.store.set_disabled("admin", True)
        else:
            auth.store.set_role("admin", "researcher")
    auth.store.set_role("alice", "admin")
    assert auth.store.get_by_username("alice").session_version == 1
    auth.store.set_disabled("admin", True)
    assert auth.store.get_by_username("admin").session_version == 1
    with pytest.raises(ValueError, match="last enabled admin"):
        auth.store.set_role("alice", "researcher")


def test_concurrent_admin_removal_is_atomic(auth):
    auth.store.set_role("alice", "admin")

    def disable(username):
        try:
            AuthStore(auth.store.path).set_disabled(username, True)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(disable, ["admin", "alice"])) == [False, True]
    assert sum(user.role == "admin" and not user.disabled for user in auth.store.list_users()) == 1


@pytest.mark.parametrize("action", ["create", "disable", "enable", "role", "reset-sessions"])
def test_admin_requires_role_but_not_step_up_credentials(auth, action):
    path = "/api/admin/users/" + action
    assert call(auth, path)[0] == 401
    assert call(auth, path, claims=claims_for(auth, "alice"))[0] == 403
    admin = claims_for(auth, "admin")
    if action == "create":
        payload = {"username": "no-step-up", "password": "long-enough-password"}
    else:
        payload = {"username": "alice"}
    assert call(auth, path, payload, admin)[0] != 403
    forged = replace(claims_for(auth, "alice"), role="admin")
    assert call(auth, path, claims=forged)[0] == 401


def test_admin_mutations_and_safe_listing(auth):
    admin = claims_for(auth, "admin")
    credential = {"current_password": "admin-long-password"}
    status, body, _ = call(auth, "/api/admin/users/create", dict(credential, username="carol", password="carol-long-password"), admin)
    assert status == 201 and body["user"]["role"] == "researcher"
    assert call(auth, "/api/admin/users/create", dict(credential, username="carol", password="carol-long-password"), admin)[0] == 409
    for action in ("disable", "enable", "role", "reset-sessions"):
        status, _, _ = call(auth, "/api/admin/users/" + action, dict(credential, username="carol", role="admin"), admin)
        assert status == 200
    assert auth.store.get_by_username("carol").session_version == 4
    status, body, headers = call(auth, "/api/admin/users", claims=admin)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert all('password_hash' not in user and 'password' not in user for user in body['users'])
    assert next(user for user in body['users'] if user['username'] == 'carol')['must_change_password'] is True
    with sqlite3.connect(auth.store.path) as connection:
        assert "password" not in str(connection.execute("SELECT * FROM security_audit").fetchall())


def test_last_admin_endpoint_and_invalid_inputs(auth):
    admin = claims_for(auth, "admin")
    credential = {"current_password": "admin-long-password", "username": "admin"}
    assert call(auth, "/api/admin/users/disable", credential, admin)[0] == 409
    assert call(auth, "/api/admin/users/role", dict(credential, role="researcher"), admin)[0] == 409
    assert call(auth, "/api/admin/users/role", dict(credential, role=[]), admin)[0] == 422
    assert call(auth, "/api/admin/users/disable", dict(credential, username="missing"), admin)[0] == 404
    assert call(auth, "/api/admin/users/nope", credential, admin)[0] == 404


def test_session_endpoints_and_reset_invalidation(auth):
    alice = claims_for(auth, "alice")
    admin = claims_for(auth, "admin", "other")
    auth.store.register_session(alice.jti, alice.sub, alice.exp, "ip", "agent")
    auth.store.register_session(admin.jti, admin.sub, admin.exp, "ip", "agent")
    assert len(call(auth, "/api/auth/sessions", claims=alice)[1]["sessions"]) == 1
    assert call(auth, "/api/auth/sessions/revoke", {"jti": "other"}, alice)[0] == 404
    assert call(auth, "/api/auth/sessions/revoke", {"jti": []}, alice)[0] == 422
    status, _, headers = call(auth, "/api/auth/sessions/revoke", {"jti": "current"}, alice)
    assert status == 200 and "Max-Age=0" in headers["Set-Cookie"]
    alice = claims_for(auth, "alice", "new")
    auth.store.register_session(alice.jti, alice.sub, alice.exp, "ip", "agent")
    assert call(auth, "/api/auth/sessions/revoke-all", claims=alice)[0] == 200
    assert not auth.store.check_session(alice.jti, alice.sub, time.time())
    assert call(auth, "/api/auth/sessions", claims=alice)[0] == 401


@pytest.mark.parametrize("operation", ["password", "role", "disable", "reset"])
def test_account_changes_invalidate_registry(auth, operation):
    alice = claims_for(auth, "alice")
    auth.store.register_session(alice.jti, alice.sub, alice.exp, "ip", "agent")
    if operation == "password":
        auth.store.set_password("alice", "new-long-password")
    elif operation == "role":
        auth.store.set_role("alice", "admin")
    elif operation == "disable":
        auth.store.set_disabled("alice", True)
    else:
        auth.store.reset_sessions("alice")
    assert not auth.store.check_session(alice.jti, alice.sub, time.time())
    assert not auth.store.list_sessions(alice.sub, alice.jti)
