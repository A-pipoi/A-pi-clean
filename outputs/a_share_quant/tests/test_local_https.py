"""Real local TLS transport, authentication, and plaintext rejection."""

import json
import logging
import socket
import ssl
import threading
from functools import partial
from http.client import HTTPSConnection
from pathlib import Path

import pytest

from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.http_handler import HttpApiHandler
from quant.api.http_security import BoundedHTTPServer, allowed_request


@pytest.fixture
def https_api(tmp_path):
    certificate = Path(__file__).resolve().parents[1] / "local_tls/localhost.crt.pem"
    private_key = certificate.with_name("localhost.key.pem")
    if not certificate.is_file() or not private_key.is_file():
        pytest.fail("Prepare the local HTTPS certificate before transport acceptance")
    store = AuthStore(tmp_path / "users.sqlite3")
    store.create_user("alice", "isolated-https-password")
    auth = Auth(store, tmp_path / "secret.key")

    class Handler(HttpApiHandler):
        def _auth(self):
            return auth

        def _services(self):
            raise AssertionError("No business calls in authentication tests")

        def log_message(self, *args):
            pass

    server = BoundedHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(tmp_path))
    )
    server.enable_tls(certificate, private_key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(certificate))
    yield server.server_port, context
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def request(api, path, *, payload=None, headers=None, hostname="127.0.0.1"):
    port, context = api
    connection = HTTPSConnection(hostname, port, context=context, timeout=3)
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("X-A-Pi-Request", "1")
    connection.request(
        "POST" if payload is not None else "GET",
        path,
        body=json.dumps(payload) if payload is not None else None,
        headers=request_headers,
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), json.loads(response.read())
    connection.close()
    return result


def test_tls_login_session_and_secure_cookie(https_api):
    port, _ = https_api
    status, _, body = request(https_api, "/api/health", hostname="localhost")
    assert status == 200 and body == {"ok": True}
    status, headers, body = request(
        https_api,
        "/api/auth/login",
        payload={"username": "alice", "password": "isolated-https-password"},
        headers={"Origin": f"https://127.0.0.1:{port}"},
    )
    assert status == 200, body
    cookie = headers["Set-Cookie"]
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Strict"))
    status, _, body = request(
        https_api, "/api/auth/session", headers={"Cookie": cookie.split(";", 1)[0]}
    )
    assert status == 200 and body["username"] == "alice"


def test_tls_origin_and_untrusted_forwarded_headers(https_api):
    port, _ = https_api
    for origin in (f"http://127.0.0.1:{port}", "https://evil.test"):
        assert request(https_api, "/api/health", headers={"Origin": origin})[0] == 403
    assert request(https_api, "/api/health", headers={"Host": "evil.test"})[0] == 403
    assert (
        allowed_request(
            f"127.0.0.1:{port}", f"https://127.0.0.1:{port}", "127.0.0.1", "https"
        )[0]
        is False
    )


def test_plaintext_is_rejected_without_blocking_https(https_api):
    port, _ = https_api
    logging.getLogger("ssl").setLevel(logging.CRITICAL)
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.sendall(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        try:
            assert not connection.recv(4096)
        except ConnectionResetError:
            pass
    assert request(https_api, "/api/health")[0] == 200


def test_idle_tls_client_does_not_block_new_clients(https_api):
    port, _ = https_api
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        assert request(https_api, "/api/health")[0] == 200
