"""Shared isolated auth fixtures; never use production credentials."""

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.http_handler import HttpApiHandler


@pytest.fixture
def auth(tmp_path):
    store = AuthStore(tmp_path / "users.sqlite3")
    store.create_user("alice", "alice-long-password")
    store.create_user("bob", "bobby-long-password")
    return Auth(store, tmp_path / "secret.key")


@pytest.fixture
def http_api(auth, tmp_path):
    class Handler(HttpApiHandler):
        def _auth(self):
            return auth

        def _services(self):
            raise AssertionError("No business dependencies before authorization")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: Handler(*args, directory=str(tmp_path), **kwargs),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def request(port, method, path, payload=None, headers=None):
    headers = dict(headers or {})
    if path == "/api/auth/login" and method == "POST":
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-A-Pi-Request", "1")
    connection = HTTPConnection("127.0.0.1", port)
    connection.request(
        method,
        path,
        body=json.dumps(payload or {}) if method == "POST" else None,
        headers=headers or {},
    )
    response = connection.getresponse()
    data = response.read()
    result = (
        response.status,
        dict(response.getheaders()),
        (
            json.loads(data)
            if "application/json" in response.getheader("Content-Type", "")
            else data.decode()
        )
        if data
        else None,
    )
    connection.close()
    return result
