"""Forward-only Alpha360 collector for registered formal models.

Actual response receipt, raw amount/volume and adjustment bytes are retained.
Historical prices may be used as today's trailing features, never backdated
into historical signal receipts. No industry or sealed price payload is read.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from quant.data.live_monitor import LiveMonitorProvider
from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import (
    EvidenceAuthority,
    aware,
    canonical,
    digest,
)


def alpha360(window: list[dict]) -> list[float]:
    if len(window) != 60:
        raise ValueError("60 complete real trading sessions required")
    normalized = []
    for row in window:
        factor = float(row["adj_factor"])
        shares = float(row["vol"]) * 100
        amount = float(row["amount"]) * 1000
        price = [float(row[key]) * factor for key in ("close", "open", "high", "low")]
        if not math.isfinite(factor) or factor <= 0 or shares <= 0 or amount <= 0:
            raise ValueError("Missing actual volume, amount or adjustment")
        price.extend([amount / shares * factor, shares / factor])
        if any(not math.isfinite(value) or value <= 0 for value in price):
            raise ValueError("Incomplete market observation")
        normalized.append(price)
    close, volume = normalized[-1][0], normalized[-1][5]
    # Official Alpha360 order: close/open/high/low/vwap/volume, lag 59 -> 0.
    return [
        row[group] / (close if group < 5 else volume + 1e-12)
        for group in range(6)
        for row in normalized
    ]


class FormalFeatureCollector:
    def __init__(
        self,
        cache: Path,
        token: str,
        authority: EvidenceAuthority,
        *,
        clock: Callable[[], datetime],
        cancelled: Callable[[], bool],
    ):
        self.cache, self.authority, self.clock = cache, authority, clock
        self.provider = LiveMonitorProvider(cache, token, clock, cancelled)
        self.cancelled = cancelled
        with closing(sqlite3.connect(cache / "deep_raw.sqlite3")) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS observations ("
                "day TEXT PRIMARY KEY,payload BLOB NOT NULL,first_seen TEXT NOT NULL)"
            )

    def collect(self, owner: str, symbols: list[str], now: datetime) -> bool:
        day = now.astimezone(SHANGHAI).date()
        if now.astimezone(SHANGHAI).hour < 17 or not symbols:
            return False
        try:
            self.authority.features(owner, day.isoformat())
            return True
        except FileNotFoundError:
            pass
        calendar = self.provider.query(
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": (day - timedelta(days=120)).strftime("%Y%m%d"),
                "end_date": day.strftime("%Y%m%d"),
            },
            "cal_date,is_open",
        )
        sessions = sorted(
            {str(row["cal_date"]) for row in calendar if int(row["is_open"]) == 1}
        )
        today = day.strftime("%Y%m%d")
        if any(len(session) != 8 or session > today for session in sessions):
            raise PermissionError("Invalid or future trading calendar")
        if today not in sessions:
            return False
        if len(sessions) < 60:
            raise RuntimeError("calendar_coverage_insufficient")
        sources = {}
        calendar_blob = canonical(
            {
                "api": "trade_cal",
                "rows": calendar,
                "received_at": self.clock().isoformat(),
            }
        )
        sources[digest(calendar_blob)] = calendar_blob
        history: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
        requests = 0
        with closing(sqlite3.connect(self.cache / "deep_raw.sqlite3")) as db:
            for session in sessions[-60:]:
                if self.cancelled():
                    raise RuntimeError("collector_cancelled")
                cached = db.execute(
                    "SELECT payload,first_seen FROM observations WHERE day=?",
                    (session,),
                ).fetchone()
                if cached:
                    blob = bytes(cached[0])
                    if aware(cached[1]) > self.clock():
                        raise PermissionError("Future raw observation")
                    payload = json.loads(blob)
                else:
                    if requests >= 48:
                        return False  # Bound warm-up, resume raw bytes on a later tick.
                    raw = self.provider.query(
                        "daily",
                        {"trade_date": session},
                        "ts_code,trade_date,open,high,low,close,vol,amount",
                    )
                    adjustment = self.provider.query(
                        "adj_factor",
                        {"trade_date": session},
                        "ts_code,trade_date,adj_factor",
                    )
                    requests += 2
                    if len(raw) < 3000 or len(raw) >= 6000 or not adjustment:
                        raise RuntimeError("market_source_coverage_insufficient")
                    observed = self.clock()
                    payload = {
                        "day": session,
                        "raw": raw,
                        "adjustment": adjustment,
                        "received_at": observed.isoformat(),
                    }
                    blob = canonical(payload)
                    with db:
                        db.execute(
                            "INSERT OR IGNORE INTO observations VALUES(?,?,?)",
                            (session, blob, observed.isoformat()),
                        )
                    # A concurrent collector's first bytes are canonical.
                    blob = bytes(
                        db.execute(
                            "SELECT payload FROM observations WHERE day=?", (session,)
                        ).fetchone()[0]
                    )
                    payload = json.loads(blob)
                if (
                    payload["day"] != session
                    or aware(payload["received_at"]) > self.clock()
                ):
                    raise PermissionError("Raw market receipt mismatch")
                sources[digest(blob)] = blob
                factors = {
                    row["ts_code"]: row["adj_factor"]
                    for row in payload["adjustment"]
                    if str(row["trade_date"]) == session
                }
                records = {
                    row["ts_code"]: row
                    for row in payload["raw"]
                    if str(row["trade_date"]) == session
                }
                for symbol in symbols:
                    if symbol in records and symbol in factors:
                        history[symbol].append(
                            {**records[symbol], "adj_factor": factors[symbol]}
                        )
        rows = []
        for symbol, window in history.items():
            try:
                rows.append({"instrument": symbol, "features": alpha360(window)})
            except (ValueError, TypeError, KeyError):
                continue  # No imputation of suspension/absent trading days.
        if len(rows) / len(symbols) < 0.9:
            raise RuntimeError("prospective_feature_coverage_insufficient")
        received = self.clock()
        self.authority.publish_features(
            owner, day.isoformat(), rows, sources, now=received
        )
        return True
