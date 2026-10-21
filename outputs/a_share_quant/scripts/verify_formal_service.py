"""Real HTTPS, isolated account and automatic formal-worker integration proof.

Uses explicitly synthetic numeric fixtures in a unique data root. Never creates
production approvals, reads production credentials/prices or opens sealed data.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def require(condition: object, message: str) -> None:
    """Acceptance gates must remain active with python -O."""
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    from quant.api.auth_store import AuthStore
    from quant.api.deep_scheduler import independent_runtime
    from quant.api.user_scope import UserScopedStores
    from quant.research.deep_evidence import EvidenceAuthority, canonical
    from tests.test_deep_worker import OPTIONS, SYMBOLS, training_data

    run = PROJECT / "deep_factor/runs" / ("formal-service-proof-" + uuid.uuid4().hex)
    run.mkdir(parents=True)
    root = run / "isolated-data"
    password = secrets.token_urlsafe(24)
    AuthStore(root / "auth/users.sqlite3").create_user("alice", password)
    scoped = UserScopedStores(root).get("alice")
    now = datetime.now(UTC)
    future = now.astimezone().date() + timedelta(days=1)
    with closing(sqlite3.connect(scoped.ledger.path)) as db, db:
        db.execute(
            "INSERT INTO sealed_tests(strategy_version,factor_pipeline_version,"
            "data_version,sealed_start,sealed_end,dataset_hash,bars_json,"
            "metadata_json,parameters_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "baseline",
                "isolated-future",
                "isolated-v1",
                future.isoformat(),
                (future + timedelta(days=20)).isoformat(),
                "b" * 64,
                "DO_NOT_READ_SYNTHETIC_SEALED",
                json.dumps({"symbols": SYMBOLS}),
                "{}",
                now.isoformat(),
            ),
        )
    data, sources = training_data()
    data_hash = EvidenceAuthority(root / ".deep-formal/approvals").approve_dataset(
        canonical(data),
        sources,
        owner="alice",
        data_version="isolated-v1",
        audit_reference="ISOLATED SYNTHETIC INTEGRATION FIXTURE",
        now=now,
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    environment.update(
        {
            "A_PI_DATA_ROOT": str(root),
            "A_PI_DATA_CACHE": str(run / "cache"),
            "A_PI_PAPER_DB": str(run / "paper.sqlite3"),
            "A_PI_FACTOR_DB": str(run / "factors.sqlite3"),
            "A_PI_RESEARCH_DB": str(run / "research.sqlite3"),
            "A_PI_FEATURE_DB": str(run / "features.sqlite3"),
            "A_PI_AUTH_SECRET": secrets.token_hex(32),
            "A_PI_IC_AUTO": "0",
            "A_PI_DATA_AUTO": "0",
            "A_PI_PAPER_AUTO": "0",
            "A_PI_DEEP_AUTO": "1",
            "A_PI_DEEP_PYTHON": str(independent_runtime(PROJECT)),
        }
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    context = ssl.create_default_context(
        cafile=str(PROJECT / "local_tls/localhost.crt.pem")
    )
    opener = build_opener(
        HTTPCookieProcessor(http.cookiejar.CookieJar()), HTTPSHandler(context=context)
    )
    url = f"https://127.0.0.1:{port}"
    checks = {}

    def call(path, payload=None):
        request = Request(
            url + path,
            data=canonical(payload) if payload is not None else None,
            headers={"Content-Type": "application/json", "X-A-Pi-Request": "1"},
        )
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    with (
        (run / "stdout.log").open("wb") as stdout,
        (run / "stderr.log").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "quant.api_server",
                "--port",
                str(port),
                "--no-paper-scheduler",
            ],
            cwd=PROJECT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError(
                        "Isolated server exited; inspect retained stderr"
                    )
                try:
                    if call("/api/health")[0] == 200:
                        break
                except (URLError, TimeoutError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Isolated HTTPS readiness timeout")
                time.sleep(0.25)
            checks["https_validated"] = True
            checks["unauthenticated_worker_status"] = call("/api/mining/external-deep")[
                0
            ]
            require(
                checks["unauthenticated_worker_status"] == 401,
                "Anonymous worker access was not rejected",
            )
            require(
                call("/api/auth/login", {"username": "alice", "password": password})[0]
                == 200,
                "Isolated login failed",
            )
            status, summary = call("/api/mining/external-deep")
            require(
                status == 200 and summary["formal_worker"]["running"] is True,
                "Formal supervisor is not running",
            )
            checks["authenticated_supervisor_running"] = True
            config = {
                "architecture": "Linear",
                "dataset_hash": data_hash,
                "feature_version": "Alpha360-v1",
                "label_horizon": 5,
                "hyperparameters": OPTIONS,
            }
            status, reserved = call(
                "/api/mining/external-deep/trials/reserve",
                {
                    "sealed_test_id": 1,
                    "request_key": "isolated_https_001",
                    "config": config,
                },
            )
            require(status == 200, "Isolated trial reservation failed")
            payload = {"attempt_id": reserved["attempt_id"]}
            status, queued = call("/api/mining/external-deep/trials/execute", payload)
            require(
                status == 200 and queued["worker_status"] == "queued",
                "Isolated trial was not queued",
            )
            deadline = time.monotonic() + 90
            while True:
                status, job = call(
                    "/api/mining/external-deep/trials/" + reserved["attempt_id"]
                )
                require(status == 200, "Isolated trial status failed")
                if job["worker_status"] not in {"queued", "running"}:
                    require(
                        job["worker_status"] == "completed",
                        "Isolated trial did not complete",
                    )
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Formal service training timeout")
                time.sleep(0.5)
            checks["automatic_cpu_training_and_registration"] = True
            with closing(sqlite3.connect(scoped.ledger.path)) as db:
                require(
                    db.execute(
                        "SELECT COUNT(*) FROM deep_formal_models WHERE enabled=0"
                    ).fetchone()[0]
                    == 1,
                    "Expected one disabled model",
                )
                require(
                    db.execute(
                        "SELECT SUM(n_tested) FROM factor_mining_runs"
                    ).fetchone()[0]
                    == 1,
                    "Expected one isolated budget entry",
                )
                require(
                    db.execute(
                        "SELECT COUNT(*) FROM factor_mining_acceptances"
                    ).fetchone()[0]
                    == 0,
                    "Isolated test unexpectedly created an acceptance",
                )
                require(
                    db.execute("SELECT bars_json FROM sealed_tests").fetchone()[0]
                    == "DO_NOT_READ_SYNTHETIC_SEALED",
                    "Synthetic seal content changed",
                )
            checks.update(
                {
                    "formal_n": 1,
                    "enabled": False,
                    "sealed_opened": False,
                    "production_database_used": False,
                    "data": "synthetic fixture only",
                }
            )
        finally:
            # Only this subprocess handle is stopped; never a port-selected process.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    report = {"checked_at": datetime.now(UTC).isoformat(), "checks": checks}
    (run / "verification.json").write_bytes(canonical(report))
    print(
        json.dumps(
            {"report": str(run / "verification.json"), **checks}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
