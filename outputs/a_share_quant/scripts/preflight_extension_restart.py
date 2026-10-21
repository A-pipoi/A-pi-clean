"""Read-only aggregate check before restarting the locally owned API service."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'data' / 'users'
active_backtests = 0
active_mining = 0
for path in root.glob('*/research.sqlite3'):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        active_backtests += db.execute(
            "SELECT COUNT(*) FROM backtest_jobs WHERE status IN ('queued','running','cancelling')"
        ).fetchone()[0]
        active_mining += db.execute(
            "SELECT COUNT(*) FROM factor_mining_runs WHERE status IN ('queued','running','cancelling')"
        ).fetchone()[0]
print(json.dumps({'active_backtests':active_backtests,'active_mining':active_mining,
                  'restart_safe':active_backtests == 0 and active_mining == 0}))
