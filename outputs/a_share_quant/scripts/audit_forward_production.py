"""Read-only production counts; no receipts, sources, credentials or sealed data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def inspect(root: Path) -> dict:
    path = root / "data/.deep-formal/forward-training/pipoi/samples.sqlite3"
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "approved_for_training": False,
        "formal_acceptance": False,
    }
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            result["plan"] = db.execute(
                "SELECT version,created_at FROM plan WHERE id=1"
            ).fetchone()
            result["counts"] = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("membership", "samples", "labels")
            }
            result["status"] = "initialized"
    except sqlite3.OperationalError:
        result["status"] = "not_confirmed"
    return result


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    payload = json.dumps(inspect(project), ensure_ascii=False, indent=2)
    (project / "reports/forward-production-20261013.json").write_text(
        payload, encoding="utf-8"
    )
    print(payload)
