"""Verify checksums, calendar, Qlib binary/CSV roundtrip and temporal isolation."""

import argparse
import csv
import hashlib
import json
import math
import struct
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((root / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        source = (root / name).resolve()
        require(source.is_relative_to(root), "checksum path escapes dataset")
        require(hashlib.sha256(source.read_bytes()).hexdigest() == expected, "checksum mismatch: " + name)
    calendar = (root / "qlib_data/calendars/day.txt").read_text().splitlines()
    require(calendar == sorted(set(calendar)), "calendar must be sorted and unique")
    require(all(day < manifest["protected_sealed_start"] for day in calendar), "calendar overlaps protected sealed interval")
    index = {day: i for i, day in enumerate(calendar)}
    segments = manifest["segments"]
    require(index[segments["train"][0]] >= 60, "insufficient feature warmup")
    require(index[segments["train"][1]] + 20 < index[segments["valid"][0]], "training validation purge is insufficient")
    require(index[segments["valid"][1]] + 20 < len(calendar), "validation label purge is insufficient")
    count = 0
    missing = 0
    for file in sorted((root / "csv").glob("*.csv")):
        with file.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        values = {row["date"]: row for row in rows}
        for field in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "factor",
            "money",
            "vwap",
        ):
            content = (
                root / f"qlib_data/features/{file.stem.lower()}/{field}.day.bin"
            ).read_bytes()
            numbers = struct.unpack("<" + "f" * (len(content) // 4), content)
            start = int(numbers[0])
            require(start == numbers[0] and start >= 0, "invalid binary calendar offset")
            require(start + len(numbers) - 1 <= len(calendar), "binary exceeds calendar")
            for offset, actual in enumerate(numbers[1:]):
                day = calendar[start + offset]
                expected = float(values[day][field]) if day in values else math.nan
                require((math.isnan(actual) and math.isnan(expected)) or math.isclose(
                    actual, expected, rel_tol=1e-6, abs_tol=1e-7
                ), f"binary CSV mismatch: {file.name}/{field}/{day}")
                if field == "close" and math.isnan(actual):
                    missing += 1
        for row in rows:
            require(row["date"] < manifest["protected_sealed_start"], "CSV overlaps protected sealed interval")
            if not math.isnan(float(row["close"])):
                require(float(row["factor"]) > 0, "invalid adjustment factor")
        count += len(rows)
    require(count == manifest["source_rows"], "source row count mismatch")
    result = {
        "passed": True,
        "files_checksum_verified": len(hashes),
        "rows_roundtrip_verified": count,
        "nan_close_slots": missing,
        "sealed_rows_exported": 0,
        "label_purge_sessions": 20,
    }
    (root / "verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    verify(parser.parse_args().root)
