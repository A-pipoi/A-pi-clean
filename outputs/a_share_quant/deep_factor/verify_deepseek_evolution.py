"""Real provider + real pre-sealed prices, isolated authenticated HTTPS API."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import threading
import uuid
from datetime import date
from functools import partial
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deep_factor.deepseek_evolution import DeepSeek, Workbench, best_effort_logout, run
from quant.api.auth import Auth
from quant.api.auth_store import AuthStore
from quant.api.evolution_package import package_request
from quant.api.factor_panels import factor_values_for_definition
from quant.api.http_handler import HttpApiHandler
from quant.api.http_security import BoundedHTTPServer
from quant.api.mining_evolution import evolution_request
from quant.api.mining_research import _evaluate_factor_expression_request
from quant.api.user_scope import CURRENT_USERNAME
from quant.data.schema import Bar
from quant.factors import FactorRegistryStore
from quant.research import ResearchLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # Help/invalid arguments must exit before credentials or writes.
    root = Path(__file__).resolve().parents[1]
    output = (
        root / "deep_factor/runs" / ("deepseek-https-acceptance-" + uuid.uuid4().hex)
    )
    output.mkdir(parents=True)
    os.environ["A_PI_RESOURCE_LIMITS_DB"] = str(output / "limits.sqlite3")
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key and os.name == "nt":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            key = winreg.QueryValueEx(handle, "DEEPSEEK_API_KEY")[0]
    dataset = root / "deep_factor/datasets/csi300-price-20261011"
    checksums = json.loads((dataset / "checksums.json").read_text(encoding="utf-8"))
    bars, evidence = [], []
    for file in sorted((dataset / "csv").glob("*.csv"))[:35]:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        key_name = "csv\\" + file.name
        if checksums.get(key_name) != digest:
            raise ValueError("Dataset hash mismatch")
        with file.open(encoding="utf-8", newline="") as stream:
            rows = [
                row
                for row in csv.DictReader(stream)
                if "2025-01-03" <= row["date"] <= "2025-12-19"
            ][-120:]
        for row in rows:
            bars.append(
                Bar(
                    row["symbol"],
                    date.fromisoformat(row["date"]),
                    *(
                        float(row[field])
                        for field in ("open", "high", "low", "close", "volume")
                    ),
                )
            )
        evidence.append(
            {"file": file.name, "sha256": digest, "training_rows": len(rows)}
        )
    if len(bars) < 3000 or max(bar.date for bar in bars) >= date(2026, 7, 2):
        raise ValueError("Insufficient or sealed-overlapping training subset")
    source_hash = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode()
    ).hexdigest()
    users = AuthStore(output / "users.sqlite3")
    users.create_user("alice", "isolated-evolution-verification-password")
    auth = Auth(users, output / "secret.key")
    ledger = ResearchLedger(output / "research.sqlite3")
    sealed = ledger.create_sealed_test(
        strategy_version="isolated",
        factor_pipeline_version="isolated-price-demo",
        data_version=source_hash,
        sealed_start="2026-07-02",
        sealed_end="2026-07-30",
        dataset_hash=source_hash,
        bars=[],
        metadata={"scope": "isolated_empty_sealed_no_price_payload"},
        parameters={},
    )
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=FactorRegistryStore(output / "factors.sqlite3"),
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: (sealed, [], bars),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _factor_values_for_definition=factor_values_for_definition,
        _object_mapping=lambda item: item if isinstance(item, dict) else {},
        _factor_mining_cross_universe_reference=lambda *_, **__: {
            "status": "unavailable",
            "expressions": {},
        },
    )

    def forbidden(*_):
        raise PermissionError("No sealed acceptance or enablement in this verification")

    def evaluate(payload):
        return evolution_request(
            payload, deps=deps, evaluate=_evaluate_factor_expression_request
        )

    def package(action, session_id, payload):
        return package_request(
            action,
            payload,
            owner=CURRENT_USERNAME.get(),
            session_id=session_id,
            ledger_path=ledger.path,
            accept=forbidden,
        )

    services = SimpleNamespace(
        _evaluate_factor_expression_request=evaluate,
        _run_factor_mining_request=forbidden,
        _accept_factor_mining_run=forbidden,
        _promote_mined_factor=forbidden,
        _factor_mining_status=dict,
        _cancel_factor_mining_job=None,
        _evolution_package_request=package,
    )

    class Handler(HttpApiHandler):
        def _auth(self):
            return auth

        def _services(self):
            return services

        def log_message(self, *_):
            pass

    server = BoundedHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(output))
    )
    certificate = root / "local_tls/localhost.crt.pem"
    server.enable_tls(certificate, root / "local_tls/localhost.key.pem")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Workbench(f"https://127.0.0.1:{server.server_port}", certificate)
    try:
        client.call(
            "/api/auth/login",
            {
                "username": "alice",
                "password": "isolated-evolution-verification-password",
            },
        )
        report = run(client, DeepSeek(key), output / "evolution", count=2)
        if not (ledger.factor_mining_acceptance(sealed["id"]) is None):
            raise AssertionError(
                "Verification gate failed: deep_factor/verify_deepseek_evolution.py:169"
            )
        if not (report["n_tested"] == 4):
            raise AssertionError(
                "Verification gate failed: deep_factor/verify_deepseek_evolution.py:170"
            )
        proof = {
            "ok": True,
            "scope": "real_external_ai_real_price_subset_isolated_https_not_production",
            "price_rows": len(bars),
            "symbols": len(evidence),
            "source_hash": source_hash,
            "price_start": min(bar.date for bar in bars).isoformat(),
            "price_end": max(bar.date for bar in bars).isoformat(),
            "provider_attempts": report["provider_attempts"],
            "completed_generations": len(report["generations"]),
            "n_tested": report["n_tested"],
            "package_id": report["package_id"],
            "sealed_opened": False,
            "factor_enabled": False,
            "strict_production_pit_accepted": False,
        }
        (output / "verification.json").write_text(
            json.dumps(proof, indent=2), encoding="utf-8"
        )
        print(json.dumps({**proof, "report_directory": str(output)}))
    finally:
        best_effort_logout(client)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.getLogger(__name__).exception(
            "deep_factor.verify_deepseek_evolution failed: %s",
            type(error).__name__,
            exc_info=None,
        )
        print(
            json.dumps(
                {"ok": False, "error_type": type(error).__name__, "retry": False}
            )
        )
        raise SystemExit(1)
