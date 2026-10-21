"""Per-account paper configuration, outside the existing PaperStore internals."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .sqlite_support import connect_sqlite


class UserPaperSettings:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(connect_sqlite(path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS user_paper_settings(id INTEGER PRIMARY KEY CHECK(id=1), strategy TEXT NOT NULL, rebalance TEXT NOT NULL)")

    def get(self) -> dict[str, str]:
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            row = connection.execute("SELECT strategy,rebalance FROM user_paper_settings WHERE id=1").fetchone()
        return {"strategy": row[0], "rebalance": row[1]} if row else {}

    def set(self, strategy: str, rebalance: str) -> None:
        if strategy not in {"baseline", "momentum", "defensive", "inverse_volatility"} or rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("invalid paper configuration; ML research strategies are not allowed")
        with closing(connect_sqlite(self.path)) as connection, connection:
            connection.execute("INSERT INTO user_paper_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET strategy=excluded.strategy,rebalance=excluded.rebalance", (strategy, rebalance))
