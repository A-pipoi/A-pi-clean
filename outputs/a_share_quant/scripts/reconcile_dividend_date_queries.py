"""Resumable reconciliation of historical Tushare dividend date queries."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import ProviderError, query

FIELDS = ("ann_date", "imp_ann_date", "record_date", "ex_date")


def pending_jobs(database: Path, start: date, end: date) -> list[tuple[str, str]]:
    complete: set[tuple[str, str]] = set()
    with sqlite3.connect(database) as connection:
        jobs = connection.execute(
            "SELECT job,params_json,status,offset FROM ts_sync WHERE endpoint='dividend'"
        )
        for job, params_json, status, total_rows in jobs:
            try:
                params = json.loads(params_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if status != "complete" or len(params) != 1:
                continue
            field, day = next(iter(params.items()))
            if field not in FIELDS or not isinstance(day, str):
                continue
            pages = connection.execute(
                "SELECT offset,rows FROM ts_pages WHERE job=? ORDER BY offset", (job,)
            ).fetchall()
            position = 0
            for offset, row_count in pages:
                if offset != position:
                    break
                position += row_count
            if pages and position == total_rows:
                complete.add((field, day))

    missing: list[tuple[str, str]] = []
    current = start
    while current <= end:
        day = current.strftime("%Y%m%d")
        missing.extend((field, day) for field in FIELDS if (field, day) not in complete)
        current += timedelta(days=1)
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 2))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 9))
    parser.add_argument("--db", type=Path, default=default_store_path())
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1 or args.start > args.end:
        parser.error("limit must be positive and start must not exceed end")
    token = load_token()
    if not token:
        parser.exit(2, "TUSHARE_TOKEN is unavailable; no jobs were started\n")

    store = ExtensionStore(args.db)
    batch = pending_jobs(args.db, args.start, args.end)[: args.limit]
    completed = failed = 0
    for index, (field, day) in enumerate(batch, start=1):
        try:
            result: dict[str, Any] = sync(
                store, "dividend", {field: day}, token, max_pages=100, fetch=query
            )
        except (ProviderError, OSError, sqlite3.Error) as error:
            print(json.dumps({"index": index, "field": field, "date": day,
                              "error": getattr(error, "category", type(error).__name__)}, flush=True))
            failed += 1
            break
        status = result.get("status")
        completed += status == "complete"
        failed += status not in ("complete", "busy")
        if status not in ("complete", "busy"):
            print(json.dumps({"index": index, "field": field, "date": day,
                              "status": status, "error": result.get("error"),
                              "reason": result.get("reason")}, flush=True))
            if result.get("error") in ("permission_denied", "rate_limited"):
                break
    print(json.dumps({
        "batch_requested": len(batch),
        "batch_complete": completed,
        "batch_failures": failed,
        "pending_after": len(pending_jobs(args.db, args.start, args.end)),
        "historical_queries_are_pit_late": True,
    }), flush=True)


if __name__ == "__main__":
    main()
