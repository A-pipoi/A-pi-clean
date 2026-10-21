"""Read-only version audit; never registers or approves a research study."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

FROZEN_FIELDS = (
    "strategy",
    "rebalance",
    "universe_index",
    "factors",
    "initial_cash",
    "execution_mode",
    "code_hash",
)


def audit(root: Path, account: str, current_code_hash: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", account):
        raise ValueError("Invalid account path")
    directory = root / "data/users" / account
    config_path = directory / "research-paper/configuration.sqlite3"
    with closing(
        sqlite3.connect(config_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        saved = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
        if saved is None:
            raise ValueError("Frozen configuration missing")
        config = json.loads(saved[0])
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type=?", ("table",)
            )
        }
        revision = (
            db.execute(
                "SELECT changed_at,old_payload,new_hash FROM configuration_revisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if "configuration_revisions" in tables
            else None
        )
    expected = hashlib.sha256(
        json.dumps(
            {key: config[key] for key in FROZEN_FIELDS},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    cutoff = (
        revision[0]
        if revision and revision[2] == config.get("config_hash")
        else config.get("created_at")
    )
    moment = datetime.fromisoformat(cutoff)
    if moment.utcoffset() is None:
        raise ValueError("Unzoned version cutoff")
    signal_path = directory / "live_ic.sqlite3"
    old_signals = total_signals = 0
    observations = []
    if signal_path.is_file():
        with closing(
            sqlite3.connect(signal_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            timestamps = db.execute("SELECT first_seen FROM signals").fetchall()
            total_signals = len(timestamps)
            for (timestamp,) in timestamps:
                seen = datetime.fromisoformat(timestamp)
                if seen.utcoffset() is None:
                    raise ValueError("Unzoned signal evidence")
                old_signals += seen < moment
            observations = [
                {"horizon": horizon, "status": status, "count": count}
                for horizon, status, count in db.execute(
                    "SELECT horizon,status,COUNT(*) FROM observations GROUP BY horizon,status"
                )
            ]
    models = []
    for path in sorted((root / "deep_factor/runs" / account).glob("*/metadata.json")):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        models.append(
            {
                "model_id": metadata.get("model_id"),
                "purpose": metadata.get("purpose"),
                "pit_verified": metadata.get("pit_verified"),
            }
        )
    return {
        "audit_only": True,
        "code_hash_matches": config.get("code_hash") == current_code_hash,
        "configuration_integrity_valid": config.get("config_hash") == expected,
        "current_code_hash": current_code_hash,
        "configuration_hash": config.get("config_hash"),
        "version_cutoff": cutoff,
        "previous_code_hash": json.loads(revision[1]).get("code_hash")
        if revision
        else None,
        "revision_matches_current_configuration": bool(
            revision and revision[2] == config.get("config_hash")
        ),
        "generic_forward_ic": {
            "signals": total_signals,
            "before_current_version": old_signals,
            "observations": observations,
            "automatically_credited_to_current_acceptance": False,
        },
        "model_artifacts": models,
        "historical_replay_automatically_credited": False,
        "formal_acceptance_granted_by_this_audit": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default="pipoi")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    import sys

    sys.path.insert(0, str(root))
    from quant.api.research_paper import execution_code_hash

    print(
        json.dumps(audit(root, args.account, execution_code_hash()), ensure_ascii=False)
    )
