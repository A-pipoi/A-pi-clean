"""Verify a Parquet artifact, then stage it in a separate research-only registry.

No authenticated service stores or enabled production factors are modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / ".vendor"))
sys.path.insert(0, str(BASE.parent))

from quant.factors.external_deep import stage_candidate, validate_scores


def inspect_artifact(artifact, *, owner, sealed_start, dataset_hash=None):
    import pyarrow.parquet as pq

    metadata_path = artifact / "metadata.json"
    score_path = artifact / "factor_scores.parquet"
    if (
        metadata_path.stat().st_size > 4 * 1024 * 1024
        or score_path.stat().st_size > 64 * 1024 * 1024
    ):
        raise ValueError("Artifact file size limit exceeded")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if dataset_hash is not None and metadata.get("dataset_hash") != dataset_hash:
        raise ValueError("Artifact belongs to a different dataset snapshot")
    if hashlib.sha256(score_path.read_bytes()).hexdigest() != metadata.get(
        "parquet_sha256"
    ):
        raise ValueError("Parquet integrity check failed")
    file = pq.ParquetFile(score_path)
    if not 1 <= file.metadata.num_rows <= 250000 or file.schema_arrow.names != [
        "date",
        "instrument",
        "score",
        "model_id",
        "label_horizon",
    ]:
        raise ValueError("Unexpected Parquet shape")
    rows = file.read().to_pylist()
    validated = validate_scores(metadata, rows, owner=owner, sealed_start=sealed_start)
    return (
        metadata,
        rows,
        {key: value for key, value in validated.items() if key != "payload"},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Write only to an isolated research registry",
    )
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    from prepare_qlib_data import sealed_cutoff

    boundary = sealed_cutoff(Path(manifest["source_sealed_metadata_db"]))
    metadata, rows, report = inspect_artifact(
        args.artifact,
        owner=args.owner,
        sealed_start=boundary,
        dataset_hash=hashlib.sha256(
            (args.dataset / "checksums.json").read_bytes()
        ).hexdigest(),
    )
    if args.stage:
        report = stage_candidate(
            BASE / "runs" / args.owner / "research_candidates.sqlite3",
            metadata,
            rows,
            owner=args.owner,
            sealed_start=boundary,
        )
    print(
        json.dumps(
            {**report, "staged": args.stage, "production_import_allowed": False},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
