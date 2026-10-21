"""Fail-closed gate for corporate-action replay inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from quant.time_utils import SHANGHAI_TZ as SHANGHAI

DIVIDEND_DATE_FIELDS = ("ann_date", "imp_ann_date", "record_date", "ex_date")


def event_snapshot_digest(connection: sqlite3.Connection) -> str:
    """Hash all append-only source revisions so certificates expire on change."""
    digest = hashlib.sha256()
    for row in connection.execute(
        "SELECT business_key,source_hash,ingested_at,payload_json "
        "FROM ts_dividend ORDER BY business_key,source_hash,ingested_at,payload_json"
    ):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def audit_dividend_coverage(
    database: Path,
    *,
    start_date: date,
    end_date: date,
    symbols: list[str] | None = None,
    archive: bool = True,
) -> dict[str, Any]:
    """Reconcile per-calendar-day provider jobs, durable pages, and PIT timing.

    Date-filter jobs are global (not per symbol); a passed report therefore
    certifies a provider-global date range. ``symbols`` scopes event validation,
    not the upstream download. Incomplete reports are archived as diagnostics
    but never accepted by the backtest gate.
    """
    if start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    if not database.exists():
        raise ValueError("dividend coverage database does not exist")
    requested_symbols = sorted({str(item).strip().upper() for item in (symbols or []) if str(item).strip()})
    expected: dict[tuple[str, str], date] = {}
    day = start_date
    while day <= end_date:
        day_text = day.strftime("%Y%m%d")
        for field in DIVIDEND_DATE_FIELDS:
            expected[(field, day_text)] = day
        day += timedelta(days=1)

    missing: list[dict[str, str]] = []
    late: list[dict[str, str]] = []
    broken_pages: list[dict[str, str]] = []
    matched = 0
    latest_cutoff = datetime.combine(end_date, time.max, tzinfo=SHANGHAI).astimezone(UTC)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"ts_sync", "ts_pages", "ts_dividend"}.issubset(tables):
            raise ValueError("dividend source store is missing sync, page, or event tables")
        query = "SELECT job,params_json,status,updated_at,provider_api,offset FROM ts_sync WHERE endpoint='dividend'"
        jobs = connection.execute(query).fetchall()
        indexed: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for job in jobs:
            try:
                params = json.loads(job["params_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if len(params) == 1:
                field, value = next(iter(params.items()))
                if field in DIVIDEND_DATE_FIELDS and isinstance(value, str):
                    indexed.setdefault((field, value), []).append(job)
        for (field, day_text), target_day in expected.items():
            matching = indexed.get((field, day_text), [])
            valid = []
            for job in matching:
                if job["status"] != "complete" or job["provider_api"] != "dividend":
                    continue
                try:
                    completed_at = datetime.fromisoformat(str(job["updated_at"]))
                except ValueError:
                    continue
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=UTC)
                if completed_at.astimezone(UTC) > datetime.combine(
                    target_day, time.max, tzinfo=SHANGHAI
                ).astimezone(UTC):
                    late.append({"field": field, "date": target_day.isoformat(), "reason": "query_completed_after_date_cutoff"})
                    continue
                pages = connection.execute(
                    "SELECT offset,rows FROM ts_pages WHERE job=? ORDER BY offset", (job["job"],)
                ).fetchall()
                expected_offset = 0
                for page in pages:
                    if int(page["offset"]) != expected_offset:
                        break
                    expected_offset += int(page["rows"])
                if not pages or expected_offset != int(job["offset"]):
                    broken_pages.append({"field": field, "date": target_day.isoformat(), "job": str(job["job"])})
                    continue
                valid.append(job)
            if valid:
                matched += 1
            else:
                missing.append({"field": field, "date": target_day.isoformat()})

        digest = event_snapshot_digest(connection)
        event_count = 0
        invalid_event_count = 0
        if "ts_dividend" in tables:
            latest: dict[str, dict[str, Any]] = {}
            for business_key, payload in connection.execute(
                "SELECT business_key,payload_json FROM ts_dividend WHERE ingested_at<=? "
                "ORDER BY ingested_at,rowid", (latest_cutoff.isoformat(),)
            ):
                try:
                    latest[str(business_key)] = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    invalid_event_count += 1
            for event in latest.values():
                if event.get("div_proc") != "实施":
                    continue
                symbol = str(event.get("ts_code") or "").upper()
                if requested_symbols and symbol not in requested_symbols:
                    continue
                dates = []
                for key in ("record_date", "ex_date"):
                    raw = str(event.get(key) or "").replace("-", "")
                    if len(raw) == 8 and raw.isdigit():
                        try:
                            dates.append(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
                        except ValueError:
                            invalid_event_count += 1
                    else:
                        invalid_event_count += 1
                if not any(start_date <= item <= end_date for item in dates):
                    continue
                event_count += 1
                for key in ("ann_date", "record_date", "ex_date"):
                    if not event.get(key):
                        invalid_event_count += 1
                        break
                if event.get("cash_div") is None or event.get("stk_div") is None:
                    invalid_event_count += 1
                cash_amount = share_amount = math.nan
                try:
                    raw_cash = event.get("cash_div")
                    raw_shares = event.get("stk_div")
                    if raw_cash is None or raw_shares is None:
                        raise ValueError("missing distribution amount")
                    cash_amount = float(raw_cash)
                    share_amount = float(raw_shares)
                    if (
                        not math.isfinite(cash_amount)
                        or not math.isfinite(share_amount)
                        or cash_amount < 0
                        or share_amount < 0
                    ):
                        invalid_event_count += 1
                    if cash_amount > 0 and not event.get("pay_date"):
                        invalid_event_count += 1
                    if share_amount > 0 and not event.get("div_listdate"):
                        invalid_event_count += 1
                except (TypeError, ValueError, OverflowError):
                    invalid_event_count += 1
                for amount, field in ((cash_amount, "pay_date"), (share_amount, "div_listdate")):
                    if amount > 0:
                        raw_date = str(event.get(field) or "").replace("-", "")
                        try:
                            if len(raw_date) != 8 or not raw_date.isdigit():
                                raise ValueError
                            date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
                        except ValueError:
                            invalid_event_count += 1

    coverage_complete = not missing and not late and not broken_pages and invalid_event_count == 0
    report: dict[str, Any] = {
        "kind": "dividend_backtest_coverage_v1",
        "status": "complete" if coverage_complete else "incomplete",
        "coverage_reconciled": coverage_complete,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": requested_symbols,
        "universe_scope": "provider_global_date_queries",
        "date_fields": list(DIVIDEND_DATE_FIELDS),
        "expected_date_field_jobs": len(expected),
        "verified_date_field_jobs": matched,
        "missing_or_invalid_jobs": missing[:100],
        "late_jobs": late[:100],
        "broken_page_ledgers": broken_pages[:100],
        "implemented_events_in_scope": event_count,
        "invalid_implemented_event_rows": invalid_event_count,
        "event_snapshot_digest": digest,
        "point_in_time_basis": "append-only ingested_at; each date query must complete by that Shanghai date end",
        "failure_reasons": [
            reason for condition, reason in (
                (bool(missing), "one or more calendar-date dividend queries are missing or incomplete"),
                (bool(late), "one or more date queries completed after their point-in-time cutoff"),
                (bool(broken_pages), "one or more completed jobs have inconsistent durable page ledgers"),
                (invalid_event_count > 0, "implemented dividend events have invalid or missing dates"),
            ) if condition
        ],
    }
    if archive:
        from quant.data.extension_store import ExtensionStore

        report["artifact_digest"] = ExtensionStore(database).archive_quality(3, report)
    return report


def main() -> None:
    from quant.data.extension_store import default_store_path

    parser = argparse.ArgumentParser(description="Reconcile PIT dividend date-query coverage for P1/P2.")
    parser.add_argument("--start", default="2024-01-02", help="inclusive ISO date")
    parser.add_argument(
        "--end",
        default=(datetime.now(SHANGHAI).date() - timedelta(days=1)).isoformat(),
        help="inclusive ISO date (defaults to yesterday in Shanghai)",
    )
    parser.add_argument("--symbols", default="", help="optional comma-separated uppercase universe")
    parser.add_argument("--db", type=Path, default=default_store_path())
    parser.add_argument("--no-archive", action="store_true", help="run read-only without saving the audit artifact")
    args = parser.parse_args()
    try:
        report = audit_dividend_coverage(
            args.db,
            start_date=date.fromisoformat(args.start),
            end_date=date.fromisoformat(args.end),
            symbols=[item for item in args.symbols.split(",") if item] or None,
            archive=not args.no_archive,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        parser.exit(2, f"coverage audit failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "complete":
        parser.exit(2, "coverage remains incomplete; P1/P2 company-action runs stay blocked\n")


if __name__ == "__main__":
    main()


def require_reconciled_dividend_coverage(
    database: Path,
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Require a stored, exact-range coverage reconciliation before replay.

    Pagination completion alone is insufficient: the extension store currently
    reports ``coverage_reconciled=False``. A future reconciliation job must
    write an immutable quality artifact with the exact contract checked here.
    """
    if not database.exists():
        raise ValueError("corporate_actions requires reconciled dividend coverage; source store is missing")
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ts_quality_artifacts'"
        ).fetchone()
        if not exists:
            raise ValueError("corporate_actions requires reconciled dividend coverage; coverage artifact is missing")
        rows = connection.execute(
            "SELECT report_json FROM ts_quality_artifacts WHERE phase=3 ORDER BY created_at DESC"
        ).fetchall()
    wanted_symbols = sorted({str(symbol).upper() for symbol in symbols})
    for (raw,) in rows:
        try:
            report = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            report.get("kind") == "dividend_backtest_coverage_v1"
            and report.get("status") == "complete"
            and report.get("coverage_reconciled") is True
            and report.get("start_date") == start_date.isoformat()
            and report.get("end_date") == end_date.isoformat()
            and (
                report.get("universe_scope") == "provider_global_date_queries"
                or set(wanted_symbols).issubset(
                    {str(symbol).upper() for symbol in report.get("symbols", [])}
                )
            )
            and report.get("event_snapshot_digest")
        ):
            live = audit_dividend_coverage(
                database,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                archive=False,
            )
            if (
                live.get("status") == "complete"
                and live.get("event_snapshot_digest") == report.get("event_snapshot_digest")
            ):
                return report
    raise ValueError(
        "corporate_actions blocked: no complete dividend coverage reconciliation matches "
        f"{start_date.isoformat()}..{end_date.isoformat()} and the requested universe; "
        "pagination-complete rows are not proof of a complete event set"
    )
