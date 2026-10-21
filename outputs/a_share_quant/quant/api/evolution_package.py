"""Owner-scoped training packages handed to the existing whole-cycle acceptance."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from quant.mining.evolution import EvolutionStore

from .mining_evolution import training_feedback
from .sqlite_support import connect_sqlite


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _cycle(path: Path, session: dict) -> dict:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT data_version,status,archived_at,strategy_version,"
            "factor_pipeline_version "
            "FROM sealed_tests WHERE id=?",
            (session["sealed_id"],),
        ).fetchone()
        if not row or row[0] != session["data_version"] or row[2] is not None:
            raise PermissionError("Evolution snapshot is unavailable or archived")
        if db.execute(
            "SELECT 1 FROM sealed_tests WHERE strategy_version=? "
            "AND factor_pipeline_version=? "
            "AND archived_at IS NULL AND id>?",
            (row[3], row[4], session["sealed_id"]),
        ).fetchone():
            raise PermissionError("Evolution snapshot is no longer the current cycle")
        if (
            row[1] != "sealed"
            or db.execute(
                "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
                (session["sealed_id"],),
            ).fetchone()
        ):
            raise PermissionError("Sealed acceptance has already been claimed")
        rows = db.execute(
            "SELECT run_id,data_version,status,n_tested,result_json "
            "FROM factor_mining_runs WHERE sealed_test_id=? ORDER BY run_id",
            (session["sealed_id"],),
        ).fetchmany(20001)
    if not rows or len(rows) > 20000:
        raise PermissionError("Cycle run population is empty or exceeds limits")
    runs = []
    for identity, version, status, count, result in rows:
        if (
            version != session["data_version"]
            or status
            not in {
                "awaiting_acceptance",
                "evaluation_failed",
            }
            or count < 0
        ):
            raise PermissionError("Cycle contains unfinished or incompatible research")
        runs.append(
            {
                "run_id": identity,
                "status": status,
                "n_tested": count,
                "result_hash": hashlib.sha256(result.encode()).hexdigest(),
            }
        )
    return {"runs": runs, "n_tested": sum(item["n_tested"] for item in runs)}


def package_request(
    action: str,
    payload: dict,
    *,
    owner: str,
    session_id: str,
    ledger_path: Path,
    accept: Callable[[str], dict],
) -> dict:
    if not owner or not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise PermissionError("Authenticated owner and session required")
    store = EvolutionStore(
        ledger_path.with_name("factor_evolution.sqlite3"), read_only=True
    )
    session = store.history(owner, session_id)
    if action == "list":
        if payload:
            raise ValueError("Package listing does not accept overrides")
        with store.connection() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='evolution_packages'"
            ).fetchone()
            rows = (
                db.execute(
                    "SELECT manifest FROM evolution_packages WHERE owner=? "
                    "AND session_id=? "
                    "ORDER BY rowid DESC LIMIT 100",
                    (owner, session_id),
                ).fetchall()
                if exists
                else []
            )
        return {
            "packages": [json.loads(row[0]) for row in rows],
            "sealed_dates_used": False,
        }
    if action == "accept":
        if (
            set(payload) != {"package_id", "confirm_open_sealed"}
            or payload["confirm_open_sealed"] is not True
        ):
            raise ValueError(
                "Explicit confirm_open_sealed=true and package_id required"
            )
        identity = payload["package_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("Invalid package identifier")
        with store.connection() as db:
            row = db.execute(
                "SELECT manifest FROM evolution_packages WHERE owner=? "
                "AND session_id=? AND package_id=?",
                (owner, session_id, identity),
            ).fetchone()
        if not row:
            raise LookupError("Evolution package not found")
        manifest = json.loads(row[0])
        document = {
            key: value for key, value in manifest.items() if key != "package_id"
        }
        if hashlib.sha256(_canonical(document).encode()).hexdigest() != identity:
            raise ValueError("Immutable package integrity mismatch")
        if _cycle(ledger_path, session) != manifest["cycle"]:
            raise PermissionError(
                "Research population changed; create and review a new package"
            )
        # The existing accept service atomically claims the sealed opportunity
        # and aggregates ALL cycle candidates/N, including outside this session.
        return {
            "package_id": identity,
            "acceptance": accept(manifest["selected"][0]["run_id"]),
        }
    if action != "create" or set(payload) - {"top_n", "horizon"}:
        raise ValueError("Only top_n and horizon may be supplied")
    if not session["generations"] or any(
        step["status"] == "running" for step in session["generations"]
    ):
        raise PermissionError("Finish the evolution generation before packaging")
    feedback = training_feedback(
        session, top_n=payload.get("top_n", 5), horizon=payload.get("horizon", 5)
    )
    selected = [
        {
            "run_id": item["run_id"],
            "expression": item["expression"],
            "training_icir": item["score"],
        }
        for item in feedback["top_candidates"]
    ]
    if not selected:
        raise PermissionError("No eligible training candidates to package")
    cycle = _cycle(ledger_path, session)
    eligible = {
        item["run_id"]
        for item in cycle["runs"]
        if item["status"] == "awaiting_acceptance" and item["n_tested"] > 0
    }
    if any(item["run_id"] not in eligible for item in selected):
        raise PermissionError(
            "Selected candidate is absent from the current research cycle"
        )
    document = {
        "owner": owner,
        "session_id": session_id,
        "sealed_id": session["sealed_id"],
        "data_version": session["data_version"],
        "horizon": feedback["horizon"],
        "top_n": payload.get("top_n", 5),
        "selected": selected,
        "cycle": cycle,
        "acceptance_scope": "all_cycle_candidates_and_all_trials",
        "sealed_dates_used": False,
        "sealed_opened": False,
        "confirmation_required": True,
    }
    identity = hashlib.sha256(_canonical(document).encode()).hexdigest()
    manifest = {**document, "package_id": identity}
    with closing(connect_sqlite(store.path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS evolution_packages("
            "owner TEXT NOT NULL,session_id TEXT NOT NULL,package_id TEXT NOT NULL,"
            "manifest TEXT NOT NULL,PRIMARY KEY(owner,session_id,package_id))"
        )
        db.execute(
            "INSERT OR IGNORE INTO evolution_packages VALUES(?,?,?,?)",
            (owner, session_id, identity, _canonical(manifest)),
        )
    return manifest
