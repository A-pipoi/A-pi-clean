"""Financial inputs whose raw revision and actual disclosure were already observed.

No network calls and no ann_date-only historical backfill. Missing proof means
ineligible, not an assumed zero or a fallback momentum-only score.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .extension_store import default_store_path

METRICS = ("roe", "netprofit_yoy", "tr_yoy", "debt_to_assets")
SHANGHAI = timezone(timedelta(hours=8))


def _day(value: object) -> date:
    text = str(value).replace("-", "")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def observed_financial_inputs(day: date, symbols: Sequence[str], *, path: Path | None = None) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    cutoff = datetime.combine(day, time(16), SHANGHAI)
    values: dict[str, dict[str, float]] = {}
    evidence: dict[str, Any] = {}
    wanted = sorted(set(symbols))
    location = (path or default_store_path()).resolve()
    report: dict[str, Any] = {"status": "unavailable", "cutoff": cutoff.isoformat(),
        "required_metrics": list(METRICS), "requested_symbols": len(wanted),
        "historical_backfill": False, "availability_basis": "actual_disclosure_and_observed_revision_before_signal_close"}
    if not location.is_file() or not wanted:
        return values, {**report, "reason": "missing_financial_store_or_universe"}
    placeholders = ",".join("?" for _ in wanted)
    boundary = cutoff.astimezone(UTC).isoformat()
    try:
        with closing(sqlite3.connect(location.as_uri()+"?mode=ro", uri=True)) as db:
            disclosures: dict[tuple[str, date], tuple[date, datetime, str]] = {}
            for raw, digest, observed in db.execute(f"SELECT payload_json,source_hash,ingested_at FROM ts_disclosure_date WHERE ts_code IN ({placeholders}) AND ingested_at<=? ORDER BY ingested_at,rowid", (*wanted, boundary)):
                row = json.loads(raw)
                if not row.get("actual_date"):
                    continue
                try:
                    actual, period = _day(row["actual_date"]), _day(row["end_date"])
                    seen = datetime.fromisoformat(observed)
                    if seen.tzinfo is None or actual >= day:
                        continue
                    disclosures[(row["ts_code"], period)] = (actual, seen, digest)
                except (KeyError, ValueError, TypeError):
                    continue
            selected: dict[str, tuple[date, datetime, str, dict[str, float], dict[str, Any]]] = {}
            for raw, digest, observed in db.execute(f"SELECT payload_json,source_hash,ingested_at FROM ts_fina_indicator WHERE ts_code IN ({placeholders}) AND ingested_at<=?", (*wanted, boundary)):
                row = json.loads(raw)
                try:
                    symbol = str(row["ts_code"])
                    period, announced = _day(row["end_date"]), _day(row.get("f_ann_date") or row["ann_date"])
                    disclosure = disclosures.get((symbol, period))
                    seen = datetime.fromisoformat(observed)
                    if disclosure is None or seen.tzinfo is None or announced >= day or period > day or (day-period).days > 550:
                        continue
                    available = max(datetime.combine(max(announced, disclosure[0]), time(16), SHANGHAI), seen, disclosure[1])
                    if available > cutoff:
                        continue
                    numbers = {metric: float(row[metric])/100.0 for metric in METRICS if row.get(metric) is not None and not isinstance(row[metric], bool)}
                    if len(numbers) != len(METRICS) or not all(math.isfinite(number) for number in numbers.values()):
                        continue
                    proof = {"report_date": period.isoformat(), "announcement_date": announced.isoformat(),
                        "actual_disclosure_date": disclosure[0].isoformat(), "available_at": available.isoformat(),
                        "source_hash": digest, "disclosure_hash": disclosure[2], "source": "Tushare:fina_indicator_vip"}
                    key = (period, available, digest)
                    if symbol not in selected or key > selected[symbol][:3]:
                        selected[symbol] = (*key, numbers, proof)
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
            for symbol, entry in selected.items():
                values[symbol], evidence[symbol] = entry[3], entry[4]
    except sqlite3.Error:
        return {}, {**report, "reason": "financial_store_unavailable"}
    coverage = len(values)/len(wanted)
    report.update(status="available" if coverage >= .90 and len(values) >= 5 else "insufficient_coverage",
                  covered_symbols=len(values), coverage=coverage, minimum_coverage=.90,
                  missing_symbols=sorted(set(wanted)-set(values)), evidence=evidence,
                  source_hash=hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest())
    return values, report
