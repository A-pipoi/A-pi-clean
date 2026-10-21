"""Dependency-free validation and immutable research-only deep-factor staging.

This module never loads models, price histories, or sealed price payloads.
Staging is not acceptance or permission to enable historical replay factors.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quant.errors import InputTypeError


def _day(value: Any) -> date:
    if not isinstance(value, str):
        raise InputTypeError("Date must be an ISO date string")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Non-canonical date")
    return result


def validate_scores(
    metadata: dict,
    rows: list[dict],
    *,
    owner: str,
    sealed_start: str,
    production: bool = False,
) -> dict:
    if metadata.get("owner") != owner or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,32}", owner
    ):
        raise ValueError("Artifact owner mismatch")
    model = metadata.get("model_id", "")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model):
        raise ValueError("Invalid model identifier")
    if metadata.get("source") != "external_deep" or metadata.get("schema_version") != 1:
        raise ValueError("Unsupported artifact schema")
    count, attempts = metadata.get("n_tested"), metadata.get("attempts")
    if type(count) is not int or not 1 <= count <= 20000:
        raise ValueError("Invalid cumulative training trial count")
    if not isinstance(attempts, list) or len(attempts) != count:
        raise ValueError("Training trial history mismatch")
    identities = set()
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise InputTypeError("Invalid trial history entry")
        identifier = attempt.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", identifier
        ):
            raise ValueError("Invalid training trial identifier")
        if identifier in identities or attempt.get("status") not in {
            "running",
            "completed",
            "failed",
        }:
            raise ValueError("Duplicate or invalid training trial entry")
        identities.add(identifier)
    if not any(
        item["id"] == model and item["status"] == "completed" for item in attempts
    ):
        raise ValueError("Model trial has not completed")
    horizon = metadata.get("label_horizon")
    if type(horizon) is not int or horizon not in (5, 10, 20):
        raise ValueError("Unsupported label horizon")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 250000:
        raise ValueError("Score row count outside bounds")
    start, end = (
        _day(metadata.get("training_start")),
        _day(metadata.get("training_end")),
    )
    label_end = _day(metadata.get("max_training_label_date"))
    valid_start, valid_end = (
        _day(metadata.get("validation_start")),
        _day(metadata.get("validation_end")),
    )
    cutoff = _day(sealed_start)
    if not start <= end < label_end < valid_start <= valid_end < cutoff:
        raise ValueError("Training-label leakage or sealed overlap")
    timestamps = []
    for field in ("created_at", "trained_at"):
        value = metadata.get(field)
        if not isinstance(value, str):
            raise InputTypeError("Missing actual model creation timestamp")
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() is None or timestamp > datetime.now(UTC):
            raise ValueError("Unzoned or future model timestamp")
        timestamps.append(timestamp)
    if timestamps[1] > timestamps[0]:
        raise ValueError("Model trained after artifact creation")
    seen = set()
    normalized = []
    for row in rows:
        if set(row) != {"date", "instrument", "score", "model_id", "label_horizon"}:
            raise ValueError("Unexpected score columns")
        day = _day(row["date"])
        symbol = row["instrument"]
        score = row["score"]
        if not isinstance(symbol, str) or not re.fullmatch(
            r"(?:SH|SZ|BJ)[0-9]{6}", symbol
        ):
            raise ValueError("Invalid instrument")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError("Non-finite score")
        if (
            row["model_id"] != model
            or type(row["label_horizon"]) is not int
            or row["label_horizon"] != horizon
        ):
            raise ValueError("Score model or horizon mismatch")
        if (
            not valid_start <= day <= valid_end
            or day >= cutoff
            or (day, symbol) in seen
        ):
            raise ValueError("Out-of-range, sealed or duplicate score")
        seen.add((day, symbol))
        normalized.append({**row, "score": float(score)})
    if metadata.get("purpose") != "historical_replay":
        raise ValueError(
            "Only explicitly labelled historical research replay is supported"
        )
    if (
        metadata.get("pit_verified") is not False
        or metadata.get("production_import_allowed") is not False
    ):
        raise ValueError("Replay artifact cannot assert production/PIT readiness")
    if production:
        raise ValueError(
            "Historical replay cannot be enabled: "
            "scores were not available on signal dates"
        )
    normalized.sort(key=lambda row: (row["date"], row["instrument"]))
    payload = json.dumps(
        {"metadata": metadata, "scores": normalized},
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return {
        "version": hashlib.sha256(payload.encode()).hexdigest(),
        "payload": payload,
        "score_count": len(normalized),
        "status": "research_only",
        "enabled": False,
    }


def stage_candidate(
    path: Path, metadata: dict, rows: list[dict], *, owner: str, sealed_start: str
) -> dict:
    validated = validate_scores(metadata, rows, owner=owner, sealed_start=sealed_start)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS external_deep_candidates("
            "owner TEXT NOT NULL, model_id TEXT NOT NULL, version TEXT NOT NULL, "
            "payload TEXT NOT NULL, status TEXT NOT NULL "
            "CHECK(status='research_only'), enabled INTEGER NOT NULL "
            "CHECK(enabled=0), created_at TEXT NOT NULL, "
            "PRIMARY KEY(owner,model_id))"
        )
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT version FROM external_deep_candidates WHERE owner=? AND model_id=?",
            (owner, metadata["model_id"]),
        ).fetchone()
        if existing and existing[0] != validated["version"]:
            raise ValueError("Immutable model version conflict")
        db.execute(
            "INSERT OR IGNORE INTO external_deep_candidates VALUES (?,?,?,?,?,0,?)",
            (
                owner,
                metadata["model_id"],
                validated["version"],
                validated["payload"],
                "research_only",
                datetime.now(UTC).isoformat(),
            ),
        )
        db.commit()
    return {key: value for key, value in validated.items() if key != "payload"}
