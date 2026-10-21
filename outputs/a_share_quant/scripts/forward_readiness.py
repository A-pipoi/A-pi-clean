"""Audit metadata only; optionally collect current industry with an online backup."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.api.user_scope import user_context
from quant.data.capture_industry import capture
from quant.data.extension_sync import load_token


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def path_state(path):
    """Distinguish absence from ACL denial instead of relying on exists()."""
    try:
        path.stat()
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unreadable"
    except OSError:
        return "unavailable"
    return "present"


def audit(root):
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "historical_backfill": False,
        "sealed_data_read": False,
        "factor_enabled": False,
    }
    industry = root / "data_cache/live_ic/industry.sqlite3"
    if industry.exists():
        with closing(readonly(industry)) as db:
            count, symbols, first, last = db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(available_iso), "
                "MAX(available_iso) FROM industries"
            ).fetchone()
            invalid = db.execute(
                "SELECT COUNT(*) FROM industries WHERE available_iso IS NULL "
                "OR source_hash IS NULL OR source_hash=''"
            ).fetchone()[0]
        report["industry"] = {
            "observations": count,
            "symbols": symbols,
            "first_observed_at": first,
            "latest_observed_at": last,
            "invalid_evidence_rows": invalid,
            "history_before_first_observation_usable": False,
        }
    live = root / "data/users/pipoi/live_ic.sqlite3"
    live_state = path_state(live)
    if live_state == "present":
        with closing(readonly(live)) as db:
            signals = db.execute(
                "SELECT COUNT(*),MIN(day),MAX(day),MIN(first_seen),MAX(first_seen) "
                "FROM signals"
            ).fetchone()
            statuses = db.execute(
                "SELECT horizon,status,COUNT(*) FROM observations "
                "GROUP BY horizon,status"
            ).fetchall()
            attempts = db.execute(
                "SELECT day,status,error,updated FROM attempts "
                "ORDER BY day DESC LIMIT 3"
            ).fetchall()
        report["forward_ic"] = {
            "signals": signals[0],
            "first_signal": signals[1],
            "latest_signal": signals[2],
            "first_seen": signals[3],
            "last_seen": signals[4],
            "observations": [
                {"horizon": h, "status": s, "count": n} for h, s, n in statuses
            ],
            "recent_attempts": [
                {"day": d, "status": s, "error_code": e, "updated": t}
                for d, s, e, t in attempts
            ],
            "labels_fabricated": False,
        }
    else:
        report["forward_ic"] = {
            "status": (
                "waiting_for_first_signal"
                if live_state == "missing" else "metadata_unavailable"
            ),
            "metadata_access": live_state,
            "labels_fabricated": False,
        }
    artifacts = []
    for path in sorted((root / "deep_factor/runs/pipoi").glob("*/metadata.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(
            {
                key: data.get(key)
                for key in (
                    "model_id",
                    "purpose",
                    "created_at",
                    "trained_at",
                    "label_horizon",
                    "pit_verified",
                    "production_ready",
                )
            }
        )
    report["deep_factors"] = {
        "artifacts": artifacts,
        "formal_acceptance_completed": False,
        "formal_budget_registry_and_joint_acceptance_core": "implemented",
        "trusted_training_worker_code_connected": True,
        "worker_runtime_check": "authenticated external-deep formal_worker.running",
        "blocking_conditions": [
            "historical replay is not prospective scoring",
            "strict production data and universe evidence not accepted",
            "no independently approved production training dataset",
        ],
        "action": (
            "keep replay candidates disabled; "
            "predeclare prospective study before future collection"
        ),
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-industry", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "deep_factor/runs" / ("forward-readiness-" + uuid.uuid4().hex)
    output.mkdir(parents=True)
    before = audit(root)
    (output / "before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    capture_result = None
    if args.capture_industry:
        industry = root / "data_cache/live_ic/industry.sqlite3"
        if industry.exists():
            with (
                closing(readonly(industry)) as source,
                closing(sqlite3.connect(output / "industry-before.sqlite3")) as backup,
            ):
                source.backup(backup)
        try:
            with user_context("pipoi"):
                capture_result = capture(industry.parent, load_token())
        except Exception as error:
            logging.getLogger(__name__).exception("scripts.forward_readiness failed: %s", type(error).__name__, exc_info=None)
            capture_result = {"ok": False, "error_type": type(error).__name__}
    report = audit(root)
    report["capture"] = capture_result
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "report_directory": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
