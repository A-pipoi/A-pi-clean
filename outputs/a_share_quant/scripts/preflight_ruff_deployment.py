"""Read-only task counts and non-secret frozen configuration before deployment."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def inspect(root: Path) -> dict:
    accounts = []
    for folder in sorted((root / "data/users").iterdir()):
        if not folder.is_dir():
            continue
        row = {"account": folder.name, "active_tasks": {}}
        path = folder / "research.sqlite3"
        if path.is_file():
            with closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                tables = {
                    item[0]
                    for item in db.execute(
                        "SELECT name FROM sqlite_master WHERE type=?", ("table",)
                    )
                }
                for table in (
                    "backtest_jobs",
                    "factor_mining_runs",
                    "deep_worker_jobs",
                    "deep_worker_scores",
                ):
                    if table in tables:
                        row["active_tasks"][table] = db.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE status IN (?,?,?)",
                            ("queued", "running", "cancelling"),
                        ).fetchone()[0]
        path = folder / "research-paper/configuration.sqlite3"
        if path.is_file():
            with closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                saved = db.execute(
                    "SELECT payload FROM configuration WHERE id=1"
                ).fetchone()
                config = json.loads(saved[0]) if saved else {}
                row["research_configuration"] = {
                    key: config.get(key)
                    for key in (
                        "enabled",
                        "strategy",
                        "rebalance",
                        "universe_index",
                        "code_hash",
                        "config_hash",
                        "created_at",
                    )
                }
        accounts.append(row)
        paper = folder / "research-paper/paper.sqlite3"
        if paper.is_file():
            with closing(
                sqlite3.connect(paper.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                tables = {
                    item[0]
                    for item in db.execute(
                        "SELECT name FROM sqlite_master WHERE type=?", ("table",)
                    )
                }
                row["research_activity"] = {
                    table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "paper_cycles",
                        "paper_orders",
                        "paper_equity",
                        "paper_lots",
                        "paper_dividends",
                    )
                    if table in tables
                }
    return {
        "accounts": accounts,
        "restart_safe": not any(sum(row["active_tasks"].values()) for row in accounts),
    }


if __name__ == "__main__":
    print(json.dumps(inspect(Path(__file__).resolve().parents[1]), ensure_ascii=False))
