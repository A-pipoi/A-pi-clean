"""Read-only mature dataset draft. No operator approval or budget reservation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from quant.research.deep_evidence import HASH, aware, digest, regular


def build_draft(path: Path, horizon: int, training_end: str, now: datetime) -> dict:
    if date.fromisoformat(training_end).isoformat() != training_end:
        raise ValueError("Canonical training cutoff required")
    if (
        type(horizon) is not int
        or horizon not in (5, 10, 20)
        or now.utcoffset() is None
    ):
        raise ValueError("Bounded horizon and aware audit time required")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT s.day,s.instrument,s.features,s.available_at,"
            "s.membership_available_at,s.source_hash,m.source_hash,l.value,"
            "l.label_end,l.available_at,l.source_hash FROM samples s "
            "JOIN membership m ON m.day=s.day JOIN labels l "
            "ON l.day=s.day AND l.instrument=s.instrument "
            "WHERE l.horizon=? AND l.label_end<=? ORDER BY s.day,s.instrument LIMIT 100001",
            (horizon, training_end),
        ).fetchall()
        if len(rows) < 1000:
            raise PermissionError(
                "At least 1000 actual mature rows required; no replay supplementation"
            )
        if len(rows) > 100000:
            raise PermissionError("Bounded training window required")
        calendars = db.execute("SELECT sessions,source_hash FROM calendars").fetchall()
        if not calendars:
            raise PermissionError("Retained exchange-calendar evidence required")
        sessions = sorted({day for row in calendars for day in json.loads(row[0])})
        position = {day: i for i, day in enumerate(sessions)}
        calendar_hashes = {row[1] for row in calendars}
        dataset_rows = []
        for (
            day,
            symbol,
            features,
            available,
            member_available,
            sample_hash,
            member_hash,
            label,
            end,
            label_available,
            end_hash,
        ) in rows:
            if (
                aware(available) > now
                or aware(label_available) > now
                or day not in position
                or end not in position
                or position[end] - position[day] != horizon
            ):
                raise PermissionError("Immature or inconsistent real-session evidence")
            dataset_rows.append(
                {
                    "date": day,
                    "instrument": symbol,
                    "features": json.loads(features),
                    "feature_available_at": available,
                    "membership_available_at": member_available,
                    "label": label,
                    "label_end": end,
                    "label_available_at": label_available,
                    "source_hashes": sorted(
                        {sample_hash, member_hash, end_hash, *calendar_hashes}
                    ),
                }
            )
        keys = sorted({key for row in dataset_rows for key in row["source_hashes"]})
        sources = {}
        for key in keys:
            source = db.execute(
                "SELECT blob FROM sources WHERE hash=?", (key,)
            ).fetchone()
            if source is None or digest(bytes(source[0])) != key:
                raise PermissionError("Missing or altered retained source")
            sources[key] = bytes(source[0])
        # Retain underlying 60-day feature source bytes, not only their hashes.
        dependencies = set()
        for blob in sources.values():
            try:
                value = json.loads(blob)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            features = value.get("features") if isinstance(value, dict) else None
            if isinstance(features, dict):
                dependencies.update(features.get("source_hashes", []))
        for key in dependencies:
            if not isinstance(key, str) or not HASH.fullmatch(key):
                raise PermissionError("Invalid underlying source identity")
            blob = regular(path.parent / "receipts" / (key + ".data"))
            if digest(blob) != key:
                raise PermissionError("Underlying feature source altered")
            sources[key] = blob
    # Draft is intentionally incompatible with production approval until reviewed.
    return {
        "dataset": {
            "schema_version": 1,
            "purpose": "forward_training_candidate",
            "feature_version": "Alpha360-v1",
            "label_horizon": horizon,
            "calendar": sessions,
            "rows": dataset_rows,
        },
        "sources": sources,
        "approved_for_training": False,
        "formal_acceptance": False,
    }
