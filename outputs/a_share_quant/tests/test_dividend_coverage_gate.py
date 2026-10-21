from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from quant.backtest.dividend_coverage import (
    audit_dividend_coverage,
    require_reconciled_dividend_coverage,
)


def test_missing_coverage_certificate_fails_closed(tmp_path) -> None:
    database = tmp_path / "features.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE ts_quality_artifacts "
            "(digest TEXT, phase INTEGER, created_at TEXT, report_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE ts_dividend "
            "(business_key TEXT,source_hash TEXT,ingested_at TEXT,payload_json TEXT)"
        )
    with pytest.raises(ValueError, match="no complete dividend coverage reconciliation"):
        require_reconciled_dividend_coverage(
            database,
            symbols=["A.SH"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
        )


def test_audit_archives_complete_exact_calendar_scope(tmp_path) -> None:
    database = tmp_path / "features.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE ts_sync "
            "(job TEXT,endpoint TEXT,params_json TEXT,status TEXT,updated_at TEXT,provider_api TEXT,offset INTEGER)"
        )
        connection.execute("CREATE TABLE ts_pages (job TEXT,offset INTEGER,rows INTEGER)")
        connection.execute(
            "CREATE TABLE ts_dividend "
            "(business_key TEXT,source_hash TEXT,ingested_at TEXT,payload_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE ts_quality_artifacts "
            "(digest TEXT PRIMARY KEY,phase INTEGER,created_at TEXT,report_json TEXT)"
        )
        for field in ("ann_date", "imp_ann_date", "record_date", "ex_date"):
            job = f"job-{field}"
            connection.execute(
                "INSERT INTO ts_sync VALUES(?,?,?,?,?,?,?)",
                (job, "dividend", json.dumps({field: "20261008"}), "complete",
                 "2026-10-08T13:20:00+00:00", "dividend", 0),
            )
            connection.execute("INSERT INTO ts_pages VALUES(?,?,?)", (job, 0, 0))

    report = audit_dividend_coverage(
        database,
        start_date=date(2026, 10, 8),
        end_date=date(2026, 10, 8),
    )

    assert report["status"] == "complete"
    assert report["verified_date_field_jobs"] == 4
    assert require_reconciled_dividend_coverage(
        database,
        symbols=["A.SH", "B.SH"],
        start_date=date(2026, 10, 8),
        end_date=date(2026, 10, 8),
    )["coverage_reconciled"] is True
