import logging

import pytest

from quant.api.auth import Auth
from quant.api.auth_routes import auth_response, client_ip
from quant.api.auth_store import AuthStore, password_matches
from tests.conftest import request


def test_salts_roles_and_validation(auth):
    alice, bob = auth.store.list_users()
    assert alice.role == "admin" and bob.role == "researcher"
    assert alice.password_hash.split("$")[3] != bob.password_hash.split("$")[3]
    assert password_matches("alice-long-password", alice.password_hash)
    for username in ("../x", "ALICE", "a/b", "ab", "x\\y", "con", "lpt1"):
        with pytest.raises(ValueError):
            auth.store.create_user(username, "a-long-password")


def test_token_revocation_matrix(auth, tmp_path):
    auth.store.set_role('bob', 'admin')
    user = auth.store.get_by_username("alice")
    token = auth.issue_token(user)
    claims = auth.verify_token(token)
    assert claims.username == "alice"
    assert auth.verify_token(token + "x") is None
    assert auth.verify_token(auth.issue_token(user, ttl=-1)) is None
    auth.revoke(claims.jti)
    assert auth.verify_token(token) is None
    assert Auth(auth.store, tmp_path / "secret.key").verify_token(token) is None
    token = auth.issue_token(user)
    auth.store.set_password("alice", "another-long-password")
    restarted = Auth(auth.store, tmp_path / "secret.key")
    assert restarted.verify_token(token) is None
    token = restarted.issue_token(auth.store.get_by_username("alice"))
    auth.store.set_disabled("alice", True)
    assert Auth(auth.store, tmp_path / "secret.key").verify_token(token) is None
    auth.store.set_disabled("alice", False)
    assert restarted.verify_token(token) is None


def test_uniform_failures_and_dual_lock(auth, monkeypatch):
    logger = logging.getLogger("test-auth")
    responses = []
    for username, password in (("missing", "wrong-password"), ("alice", "wrong-password"), ("bob", "bobby-long-password")):
        if username == "bob":
            auth.store.set_disabled("bob", True)
        responses.append(auth_response(auth, "/api/auth/login", {"username": username, "password": password}, None, username, logger)[:2])
    assert responses[0] == responses[1] == responses[2]
    for index in range(5):
        auth.failed("alice", f"ip-{index}")
    assert auth.locked_seconds("alice", "new-ip") == 0
    assert auth.locked_seconds("charlie", "new-ip") == 0
    for index in range(5):
        auth.failed(f"user-{index}", "shared-ip")
    assert auth.locked_seconds("charlie", "shared-ip") > 0
    assert client_ip("127.0.0.1", "203.0.113.9") == "127.0.0.1"
    monkeypatch.setenv('A_PI_TRUSTED_PROXIES', '127.0.0.1')
    assert client_ip("127.0.0.1", "203.0.113.9") == "203.0.113.9"
    assert client_ip("203.0.113.8", "203.0.113.9") == "203.0.113.8"


def test_change_password_revokes_all_sessions(auth):
    user = auth.store.get_by_username("alice")
    tokens = [auth.issue_token(user), auth.issue_token(user)]
    status, _, headers = auth_response(auth, "/api/auth/change-password", {"new_password": "another-long-password"}, auth.verify_token(tokens[0]), "ip", logging.getLogger("test"))
    assert status == 204 and "Max-Age=0" in headers["Set-Cookie"]
    assert all(auth.verify_token(token) is None for token in tokens)



def test_http_fail_closed_csrf_and_public_health(http_api, auth):
    assert request(http_api, "GET", "/api/health")[2] == {"ok": True}
    for path in ("/api/paper/state", "/api/workspace", "/api/factors", "/api/ws/backtest/secret", "/api/unknown"):
        assert request(http_api, "GET", path)[0] == 401
    status, headers, _ = request(http_api, "POST", "/api/auth/login", {"username": "alice", "password": "alice-long-password", "remember": True})
    assert status == 200
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Max-Age=604800" in cookie
    authenticated = {"Cookie": cookie.split(";", 1)[0]}
    assert request(http_api, "GET", "/api/auth/session", headers=authenticated)[2]["username"] == "alice"
    assert request(http_api, "POST", "/api/auth/logout", headers=authenticated)[0] == 403
    authenticated["X-A-Pi-Request"] = "1"
    assert request(http_api, "POST", "/api/auth/logout", headers=authenticated)[0] == 204
    assert request(http_api, "GET", "/api/auth/session", headers=authenticated)[0] == 401


def test_empty_user_store(tmp_path):
    auth = Auth(AuthStore(tmp_path / "empty.sqlite3"), tmp_path / "secret")
    assert auth_response(auth, "/api/auth/login", {}, None, "ip", logging.getLogger("test"))[0] == 503
