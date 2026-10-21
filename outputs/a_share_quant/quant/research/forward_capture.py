"""Opt-in acquisition only; never approves, trains, opens seals or spends budget."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from quant.data.deep_features import FormalFeatureCollector
from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import EvidenceAuthority, aware, canonical
from quant.research.forward_samples import ForwardSamples, receipt_blob


def collect_forward(
    root: Path,
    cache: Path,
    owner: str,
    version: str,
    token: str,
    now: datetime,
    *,
    clock: Callable[[], datetime],
    cancelled: Callable[[], bool],
) -> dict:
    local = now.astimezone(SHANGHAI)
    store = ForwardSamples(root / "samples.sqlite3", version=version, now=now)
    if not (8 <= local.hour < 15 or 17 <= local.hour < 20):
        return store.status()
    cache.mkdir(parents=True, exist_ok=True)
    authority = EvidenceAuthority(root / "receipts")
    collector = FormalFeatureCollector(
        cache, token, authority, clock=clock, cancelled=cancelled
    )
    day = local.date().isoformat()
    calendar = collector.provider.query(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": (local.date() - timedelta(days=120)).strftime("%Y%m%d"),
            "end_date": local.strftime("%Y%m%d"),
        },
        "cal_date,is_open",
    )
    sessions = sorted(
        {
            date.fromisoformat(str(row["cal_date"])).isoformat()
            for row in calendar
            if int(row["is_open"]) == 1
        }
    )
    if not calendar or any(session > day for session in sessions):
        raise ValueError("Actual nonfuture exchange calendar required")
    if day not in sessions:
        return store.status()
    observed = clock()
    store.calendar(
        sessions,
        canonical(
            {"api": "trade_cal", "rows": calendar, "received_at": observed.isoformat()}
        ),
        observed,
    )
    symbols = store.universe(day)
    if local.hour < 15:
        if not symbols:
            members = collector.provider.query(
                "index_weight",
                {
                    "index_code": "000300.SH",
                    "start_date": (local.date() - timedelta(days=62)).strftime(
                        "%Y%m%d"
                    ),
                    "end_date": local.strftime("%Y%m%d"),
                },
                "con_code,trade_date,weight",
            )
            eligible = [
                r for r in members if str(r["trade_date"]) <= local.strftime("%Y%m%d")
            ]
            if not eligible or len(members) >= 6000:
                raise ValueError("Observed membership unavailable or truncated")
            latest = max(str(r["trade_date"]) for r in eligible)
            snapshot = [r for r in eligible if str(r["trade_date"]) == latest]
            symbols = sorted({str(r["con_code"]) for r in snapshot})
            total = sum(float(r["weight"]) for r in snapshot)
            if not 280 <= len(symbols) <= 350 or not 98 <= total <= 102:
                raise ValueError("Complete observed CSI300 membership required")
            received = clock()
            blob = canonical(
                {
                    "api": "index_weight",
                    "rows": members,
                    "calendar": calendar,
                    "received_at": received.isoformat(),
                }
            )
            store.membership(day, symbols, blob, received)
        return store.status()
    if not symbols:
        return store.status()  # Never backdate after-close membership to 15:00.
    if not collector.collect(owner, symbols, now):
        return store.status()
    features = authority.features(owner, day)
    with closing(sqlite3.connect(cache / "deep_raw.sqlite3")) as db:
        record = db.execute(
            "SELECT payload FROM observations WHERE day=?", (local.strftime("%Y%m%d"),)
        ).fetchone()
    if not record:
        raise ValueError("Actual endpoint market receipt missing")
    blob = bytes(record[0])
    raw = json.loads(blob)
    received = clock()
    if raw["day"] != local.strftime("%Y%m%d") or aware(raw["received_at"]) > received:
        raise PermissionError("Invalid endpoint receipt")
    factors = {
        r["ts_code"]: float(r["adj_factor"])
        for r in raw["adjustment"]
        if str(r["trade_date"]) == raw["day"]
    }
    prices = {
        r["ts_code"]: float(r["close"]) * factors[r["ts_code"]]
        for r in raw["raw"]
        if r["ts_code"] in factors and str(r["trade_date"]) == raw["day"]
    }
    rows = [
        {"instrument": r["instrument"], "features": r["features"]}
        for r in features["rows"]
    ]
    store.capture(day, rows, prices, receipt_blob(features, blob), received)
    store.settle(sessions, received)
    return store.status()
