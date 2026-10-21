"""Isolated browser acceptance fixture. Never uses production data or Tushare."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.api.http_security import BoundedHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--data-root", type=Path, required=True)
parser.add_argument("--site", type=Path, required=True)
parser.add_argument("--port", type=int, default=18081)
parser.add_argument("--initial-password-fixture", action="store_true")
parser.add_argument("--registration-fixture", action="store_true")
parser.add_argument("--ui-hardening-fixture", action="store_true")
parser.add_argument("--extension-report", type=Path)
parser.add_argument("--quality-fixture", action="store_true")
parser.add_argument("--tls-cert", type=Path)
parser.add_argument("--tls-key", type=Path)
args = parser.parse_args()
args.site = args.site.resolve(strict=True)
os.environ["A_PI_DATA_ROOT"] = str(args.data_root)
os.environ["A_PI_DATA_CACHE"] = str(args.data_root / "data_cache")
os.environ["A_PI_FEATURE_DB"] = str(args.data_root / "features.sqlite3")
from quant import api_server

if args.quality_fixture:
    from quant.data.extension_store import ExtensionStore

    quality_store = ExtensionStore(args.data_root / "features.sqlite3")
    quality_store.initialize()
    for phase in (1, 2, 3):
        quality_store.archive_quality(
            phase,
            {
                "kind": f"fixture_phase_{phase}",
                "status": "passed",
                "scope": "isolated_browser_acceptance",
            },
        )
users = api_server.AUTH_STORE
if args.initial_password_fixture and not users.get_by_username("charlie"):
    users.create_user("charlie", "initial-test-password", must_change_password=True)
for name in ("alice", "bob"):
    if not users.get_by_username(name):
        users.create_user(name, "acceptance-only-password")
    stores = api_server.USER_STORES.get(name)
    stores.factor_store.register(
        name + " private factor", "momentum", 19 if name == "alice" else 23
    )
    if not stores.paper_store.order("QA-" + name):
        stores.paper_store.create_order("000001.SZ", "BUY", 100, order_id="QA-" + name)
    if args.ui_hardening_fixture and name == "alice":
        for index in range(125):
            order_id = f"QA-HISTORY-{index:04d}"
            if not stores.paper_store.order(order_id):
                stores.paper_store.create_order(
                    "000001.SZ", "BUY", 100, order_id=order_id
                )
if args.registration_fixture:
    if not users.get_by_username("pipoi"):
        users.create_user("pipoi", "acceptance-only-password")
    private = api_server.USER_STORES.get("pipoi")
    private.factor_store.register("pipoi private factor", "momentum", 17)
    if not private.paper_store.order("QA-pipoi"):
        private.paper_store.create_order("600036.SH", "BUY", 100, order_id="QA-pipoi")
api_server._data_probe_status = lambda: {"status": "fixture", "data_reachable": False}


class Handler(api_server.ApiHandler):
    def __init__(self, *pos, **kw):
        # Keep static root explicit; fixture CLI accepts source or published roots.
        kw["directory"] = str(args.site)
        super().__init__(*pos, **kw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/data/extensions" and args.extension_report:
            if not self._authorize():
                return
            import json

            report = json.loads(args.extension_report.read_text(encoding="utf-8"))
            return self._send_json(200, {"ok": True, "status": "sample_only", **report})
        if args.ui_hardening_fixture and path == "/api/workspaces":
            if not self._authorize():
                return
            return self._send_json(
                404, {"ok": False, "error": "fixture_workspace_not_supported"}
            )
        if (
            not path.startswith("/api/")
            or path.startswith(
                ("/api/auth/", "/api/admin/", "/api/backtest/", "/api/ws/")
            )
            or path
            in {
                "/api/health",
                "/api/strategy-recipes",
                "/api/factors",
                "/api/factors/live-ic",
                "/api/factors/industry-pit",
                "/api/research/ledger",
                "/api/research/data-quality",
                "/api/paper/config",
                "/api/paper/orders",
            }
        ):
            return super().do_GET()
        if not self._authorize():
            return
        if not (self.claims):
            raise AssertionError(
                "Verification gate failed: scripts/auth_acceptance_server.py:102"
            )
        stores = api_server.USER_STORES.get(self.claims.username)
        if path == "/api/paper/state":
            return self._send_json(200, {"ok": True, **stores.paper_store.state()})
        self._send_json(
            200,
            {
                "ok": True,
                "source": "acceptance_fixture",
                "workspaces": [],
                "portfolio": {"metrics": {}, "equity_curve": []},
                "paper": {},
                "research": {},
                "warnings": [],
            },
        )


server = BoundedHTTPServer(
    ("127.0.0.1", args.port),
    lambda *pos, **kw: Handler(*pos, directory=str(args.site), **kw),
)
if args.tls_cert or args.tls_key:
    if not args.tls_cert or not args.tls_key:
        raise ValueError("Both TLS certificate and key are required")
    server.enable_tls(args.tls_cert, args.tls_key)
try:
    print(
        f"Authentication fixture listening on {args.port}; site={args.site}", flush=True
    )
    server.serve_forever()
finally:
    server.server_close()
    api_server.USER_STORES.close()
