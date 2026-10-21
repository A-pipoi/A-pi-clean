"""User-approved pipoi baseline archival; no seal evaluation or order creation."""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.research import ResearchLedger

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--user-approved", action="store_true")
args = parser.parse_args()
if not args.user_approved:
    raise SystemExit("Explicit user authorization is required.")
root = Path(__file__).resolve().parents[1]
path = root / "data/users/pipoi/research.sqlite3"
report_path = root / "data_cache/data_extension/approved_baseline_archive_20261008.json"
if report_path.exists():
    print(json.dumps({"status": "already_archived", "report": str(report_path)}))
    raise SystemExit(0)


def preserved_seals(db):
    columns = [
        r[1]
        for r in db.execute("PRAGMA table_info(sealed_tests)")
        if r[1] != "archived_at"
    ]
    rows = db.execute(
        "SELECT "
        + ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        + " FROM sealed_tests ORDER BY id"
    ).fetchall()
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
    active = source.execute(
        "SELECT COUNT(*) FROM backtest_jobs WHERE status IN ('queued','running','cancelling')"
    ).fetchone()[0]
    mining = source.execute(
        "SELECT COUNT(*) FROM factor_mining_runs WHERE status IN ('queued','running','cancelling')"
    ).fetchone()[0]
    if active or mining:
        raise SystemExit("Active research exists; archival was not performed.")
    before = preserved_seals(source)
    used = source.execute(
        "SELECT COUNT(*) FROM experiments WHERE strategy_version='baseline' AND archived_at IS NULL"
    ).fetchone()[0]
    if used != 20:
        raise SystemExit(
            "Expected approved 20/20 baseline cycle; state changed, no archival performed."
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = (
        root
        / "data_cache/data_extension/archived_cycles"
        / f"pipoi-baseline-{stamp}.sqlite3"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup) as destination:
        source.backup(destination)

ledger = ResearchLedger(path)
result = ledger.archive(
    "baseline",
    reason="User explicitly authorized preserving and archiving the full baseline cycle and opening a new cycle for remaining acceptance on 2026-10-08.",
)
with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
    after = preserved_seals(source)
if before != after:
    raise RuntimeError(
        "Seal content changed unexpectedly; retain backup and investigate."
    )
report = {
    "status": "archived_new_cycle_ready",
    "archive": result,
    "backup": str(backup),
    "sealed_content_hash_before": before,
    "sealed_content_hash_after": after,
    "budget": ledger.status("baseline"),
    "old_sealed_evaluations_repeated": False,
    "paper_orders_created": False,
    "paper_strategy_parameters_changed": False,
    "new_cycle_sealed_gate_passed": False,
}
report_path.write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "status": report["status"],
            "archived_count": result["archived_count"],
            "sealed_archived_count": result["sealed_archived_count"],
            "budget_remaining": report["budget"]["remaining"]
            if "remaining" in report["budget"]
            else 20 - ledger.count("baseline"),
            "sealed_content_unchanged": before == after,
            "new_cycle_sealed_gate_passed": False,
        }
    )
)
