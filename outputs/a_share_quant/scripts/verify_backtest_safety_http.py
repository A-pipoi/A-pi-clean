"""Isolated fixture only; no production accounts, market data or research runs."""

import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.research.ledger import ResearchLedger

base = "http://127.0.0.1:18086"
root = (
    Path(__file__).resolve().parents[2] / "auth_release/backtest-safety-fixture/users"
)
if not (root.name == "users" and "backtest-safety-fixture" in str(root)):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:18"
    )
alice = ResearchLedger(root / "alice/research.sqlite3")
cancel_id = "QA-CANCEL-" + uuid.uuid4().hex
completed_id = "QA-COMPLETED-" + uuid.uuid4().hex
alice.create_backtest_job(cancel_id, {})
alice.create_backtest_job(completed_id, {})
alice.update_backtest_job(
    completed_id, status="completed", result={"ok": True, "metrics": {"sharpe": 0.5}}
)


def request(client, path, payload=None, header=True):
    headers = {"Content-Type": "application/json", "Origin": base}
    if header:
        headers["X-A-Pi-Request"] = "1"
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    try:
        response = client.open(req)
    except urllib.error.HTTPError as error:
        response = error
    return response.code, json.loads(response.read() or "{}")


clients = []
for name in ("alice", "bob"):
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    if not (
        request(
            client,
            "/api/auth/login",
            {"username": name, "password": "acceptance-only-password"},
        )[0]
        == 200
    ):
        raise AssertionError(
            "Verification gate failed: scripts/verify_backtest_safety_http.py:50"
        )
    clients.append(client)
a, b = clients
if not (request(b, f"/api/backtest/{cancel_id}/cancel", {})[0] == 404):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:60"
    )
if not (request(a, f"/api/backtest/{cancel_id}/cancel", {}, False)[0] == 403):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:61"
    )
if not (request(a, f"/api/backtest/{cancel_id}/cancel", {})[0] == 202):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:62"
    )
if not (request(a, f"/api/backtest/{cancel_id}")[1]["status"] == "cancelling"):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:63"
    )
alice.update_backtest_job(cancel_id, status="running", progress=35)
if not (alice.get_backtest_job(cancel_id)["status"] == "cancelling"):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:65"
    )
if not (request(a, f"/api/backtest/{completed_id}/cancel", {})[0] == 409):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:66"
    )
if not (
    request(a, f"/api/backtest/{completed_id}")[1]["result"]["metrics"]["sharpe"] == 0.5
):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:67"
    )
if not (request(b, f"/api/backtest/{completed_id}")[0] == 404):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:70"
    )
if not (request(a, "/api/backtest/jobs")[1]["jobs"]):
    raise AssertionError(
        "Verification gate failed: scripts/verify_backtest_safety_http.py:71"
    )
print(
    "PASS cancellation authorization/CSRF, persisted job recovery, race guard and completed result retention"
)
