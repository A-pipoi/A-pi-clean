"""Verify a security rollout did not change research, factors, paper or features."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from auth_migration_audit import database_digest, file_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--username", default="pipoi")
    args = parser.parse_args()
    root = args.data_root.resolve()
    report = {
        "databases": {
            name: database_digest(root / "users" / args.username / name)
            for name in ("factors.sqlite3", "paper.sqlite3", "research.sqlite3")
        },
        "features_sha256": file_digest(root / "features.sqlite3"),
    }
    if args.mode == "capture":
        args.baseline.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Business data baseline captured")
    elif report != json.loads(args.baseline.read_text(encoding="utf-8")):
        raise ValueError(
            "Business data changed during security rollout; inspect before declaring success"
        )
    else:
        print("PASS: paper, research, factors and shared features unchanged")


if __name__ == "__main__":
    main()
