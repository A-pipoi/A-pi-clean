"""Read-only logical checksums for authentication migration; no secrets or orders."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def database_digest(path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"Integrity check failed: {path.name}")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        result = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            digest = hashlib.sha256()
            count = 0
            for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
                digest.update(repr(row).encode())
                digest.update(b"\n")
                count += 1
            result[table] = {"rows": count, "sha256": digest.hexdigest()}
        return result


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--username", default="pipoi")
    args = parser.parse_args()
    root = args.data_root.resolve()
    names = ("factors.sqlite3", "paper.sqlite3", "research.sqlite3")
    if args.mode == "capture":
        report = {
            "databases": {name: database_digest(root / name) for name in names},
            "shared_features_sha256": file_digest(root / "features.sqlite3"),
        }
        args.baseline.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Captured legacy database counts/checksums and shared feature checksum")
    else:
        report = json.loads(args.baseline.read_text(encoding="utf-8"))
        for name in names:
            actual = database_digest(root / "users" / args.username / name)
            if actual != report["databases"][name]:
                raise ValueError(f"Migrated database differs: {name}")
        if file_digest(root / "features.sqlite3") != report["shared_features_sha256"]:
            raise ValueError("Shared feature file changed")
        print(
            "PASS: all original table counts/content match; shared features unchanged"
        )


if __name__ == "__main__":
    main()
