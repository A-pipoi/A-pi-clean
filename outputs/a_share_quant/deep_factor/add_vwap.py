"""Add actual-turnover VWAP to an already verified generated dataset only."""

import csv
import hashlib
import json
import math
import struct
import sys
from pathlib import Path


def main(root):
    root = Path(root).resolve()
    hashes = json.loads((root / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        source = (root / name).resolve()
        if not source.is_relative_to(root):
            raise ValueError("checksum path escapes dataset")
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("checksum mismatch: " + name)
    calendar = (root / "qlib_data/calendars/day.txt").read_text().splitlines()
    for file in sorted((root / "csv").glob("*.csv")):
        with file.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = list(reader.fieldnames)
            rows = list(reader)
        if "vwap" not in fields:
            fields.append("vwap")
        for row in rows:
            volume = float(row["volume"])
            money = float(row["money"])
            if not (math.isnan(volume) or (math.isfinite(volume) and math.isfinite(money) and volume > 0 and money > 0)):
                raise ValueError("VWAP requires finite positive volume and turnover")
            row["vwap"] = money / volume if volume > 0 else math.nan
        temporary = file.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(file)
        values = {row["date"]: float(row["vwap"]) for row in rows}
        lo = calendar.index(min(values))
        hi = calendar.index(max(values))
        array = [float(lo)] + [
            values.get(day, math.nan) for day in calendar[lo : hi + 1]
        ]
        path = root / f"qlib_data/features/{file.stem.lower()}/vwap.day.bin"
        path.write_bytes(struct.pack("<" + "f" * len(array), *array))
    provider_files = list((root / "qlib_data").rglob("*")) + list(
        (root / "csv").glob("*.csv")
    )
    hashes = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(provider_files)
        if p.is_file()
    }
    (root / "checksums.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print("Actual-turnover VWAP added; CSV and binary checksums updated.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
