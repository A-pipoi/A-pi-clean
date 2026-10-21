"""Local privileged admission of independently audited prospective training data.

This is not a network endpoint and does not turn replay exports into PIT proof.
No approved production dataset is supplied by this command or by the installer.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from quant.research.deep_evidence import HASH, EvidenceAuthority, aware, digest, regular


def approve(
    root: Path,
    dataset: Path,
    audit: Path,
    sources: Path,
    *,
    owner: str,
    data_version: str,
    now: datetime,
) -> str:
    blob = regular(dataset)
    review_blob = regular(audit)
    review = json.loads(review_blob)
    if (
        review.get("policy") != "strict-deep-pit-v1"
        or review.get("status") != "approved"
        or review.get("owner") != owner
        or review.get("data_version") != data_version
        or review.get("dataset_hash") != digest(blob)
        or not isinstance(review.get("reviewer"), str)
        or not review["reviewer"].strip()
        or aware(review["reviewed_at"]) > now
        or set(review.get("checks", {}))
        != {
            "source_availability",
            "revision_vintages",
            "historical_membership",
            "listing_st_delisting",
            "adjustment_availability",
            "feature_contract",
        }
        or any(value is not True for value in review["checks"].values())
        or not isinstance(review.get("source_hashes"), list)
        or not review["source_hashes"]
        or any(
            not isinstance(key, str) or not HASH.fullmatch(key)
            for key in review["source_hashes"]
        )
    ):
        raise PermissionError("Independent source/PIT audit is missing or incomplete")
    retained = {
        key: regular(sources / (key + ".data")) for key in review["source_hashes"]
    }
    retained[digest(review_blob)] = review_blob
    return EvidenceAuthority(root / ".deep-formal/approvals").approve_dataset(
        blob,
        retained,
        owner=owner,
        data_version=data_version,
        audit_reference=digest(review_blob),
        now=now,
        forward_scope=review.get("forward_scope"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--confirm-reviewed-source-pit", action="store_true")
    args = parser.parse_args()
    if not args.confirm_reviewed_source_pit:
        raise SystemExit(
            "Source evidence must be independently reviewed before approval"
        )
    if os.name == "nt":
        import ctypes

        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise SystemExit("Run local dataset admission as administrator")
    elif os.geteuid() != 0:  # type: ignore[attr-defined]  # POSIX-only branch
        raise SystemExit(
            "Run local dataset admission as the privileged service operator"
        )
    root = Path(
        os.getenv("A_PI_DATA_ROOT") or Path(__file__).resolve().parents[2] / "data"
    )
    result = approve(
        root,
        args.dataset,
        args.audit,
        args.sources,
        owner=args.owner,
        data_version=args.data_version,
        now=datetime.now(UTC),
    )
    print(
        json.dumps(
            {
                "dataset_hash": result,
                "approved": True,
                "training_started": False,
                "enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
