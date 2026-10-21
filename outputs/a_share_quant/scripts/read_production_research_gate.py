"""Read-only prerequisites, never creates a research store or archives a cycle."""

import json
import sqlite3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "data/users/pipoi/research.sqlite3"
with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
    used = db.execute(
        "SELECT COUNT(*) FROM experiments WHERE strategy_version='baseline' AND archived_at IS NULL"
    ).fetchone()[0]
    sealed = [
        {"id": r[0], "status": r[1], "already_evaluated": bool(r[2])}
        for r in db.execute(
            "SELECT id,status,result_json FROM sealed_tests WHERE archived_at IS NULL"
        )
    ]
print(
    json.dumps(
        {
            "baseline_budget_used": used,
            "active_sealed": sealed,
            "writes_performed": False,
        }
    )
)
