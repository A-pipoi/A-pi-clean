"""Preserve industry responses at their real receipt time, never effective time."""

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path


def preserve_response(root: Path, rows: list[dict], observed: datetime):
    if observed.tzinfo is None:
        raise ValueError("timezone-aware observation required")
    raw = json.dumps(rows, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    directory = root / "industry_evidence"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (
        observed.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex + ".json"
    )
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "endpoint": "stock_basic",
                "fields": "ts_code,industry",
                "observed_at": observed.isoformat(),
                "source_hash": digest,
                "historical_backfill": False,
                "raw_response_json": raw,
            },
            stream,
        )
    return digest, path
