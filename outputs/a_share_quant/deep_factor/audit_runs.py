"""Audit local training attempts and stage valid, disabled replay candidates."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from import_scores import BASE, inspect_artifact
from prepare_qlib_data import sealed_cutoff

from quant.factors.external_deep import stage_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    # Validate the identifier before using it as a path component.
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.owner):
        parser.error("Invalid owner")
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    dataset_hash = hashlib.sha256(
        (args.dataset / "checksums.json").read_bytes()
    ).hexdigest()
    boundary = sealed_cutoff(Path(manifest["source_sealed_metadata_db"]))
    directory = BASE / "runs" / args.owner
    with closing(
        sqlite3.connect(
            (directory / "training_attempts.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        attempts = db.execute(
            "SELECT id,status FROM attempts WHERE owner=? AND dataset_hash=? "
            "ORDER BY created_at",
            (args.owner, dataset_hash),
        ).fetchall()
    results = []
    for trial, status in attempts:
        if status != "completed":
            results.append({"trial_id": trial, "status": status, "n_tested": 1})
            continue
        metadata, rows, report = inspect_artifact(
            directory / trial,
            owner=args.owner,
            sealed_start=boundary,
            dataset_hash=dataset_hash,
        )
        staged = stage_candidate(
            directory / "research_candidates.sqlite3",
            metadata,
            rows,
            owner=args.owner,
            sealed_start=boundary,
        )
        results.append(
            {
                "trial_id": trial,
                "training_status": status,
                "architecture": metadata["architecture"],
                "horizon": metadata["label_horizon"],
                "icir": metadata["validation"]["icir"],
                "n_tested": 1,
                **report,
                "staged_version": staged["version"],
            }
        )
    summary = {
        "attempts_total": len(attempts),
        "sealed_start": boundary,
        "production_enabled": False,
        "results": results,
    }
    (directory / "verification.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
