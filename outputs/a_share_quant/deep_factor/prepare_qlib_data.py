"""Export training-only Qlib files using stdlib; no main-service/heavy imports.

Reads only sealed metadata, never bars_json/pit_data_json or sealed features.
Refuses overwrite, conflicting revisions and missing adjustment observations.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import sqlite3
import struct
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

FIELDS = ("open", "high", "low", "close", "volume", "factor", "money", "vwap")


def read_only(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


def sealed_cutoff(research: Path) -> str:
    with read_only(research) as db:
        # Archived/evaluated windows remain protected. Do not select payloads.
        row = db.execute("SELECT MIN(sealed_start) FROM sealed_tests").fetchone()
    if not row or not row[0]:
        raise ValueError(
            "No registered sealed boundary; explicit governance is required"
        )
    return str(row[0])


def unique_rows(db, table, symbol, start, end):
    if table not in ("ts_daily", "ts_adj_factor"):
        raise ValueError("Unsupported source table")
    records = {}
    for (payload,) in db.execute(
        f"SELECT payload_json FROM {table} "
        "WHERE ts_code=? AND period_date BETWEEN ? AND ?",
        (symbol, start.replace("-", ""), end.replace("-", "")),
    ):
        row = json.loads(payload)
        day = str(row["trade_date"])
        if day in records and records[day] != row:
            raise ValueError(f"Conflicting revisions: {table}/{symbol}/{day}")
        records[day] = row
    return records


def adjusted_row(raw, adjustment: float, anchor: float):
    factor = adjustment / anchor
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Invalid adjustment factor")
    values = {k: float(raw[k]) * factor for k in ("open", "high", "low", "close")}
    volume = float(raw["vol"]) * 100  # Tushare daily volume is lots (100 shares).
    money = float(raw["amount"]) * 1000  # Tushare daily amount is thousands CNY.
    if volume <= 0:
        return {key: math.nan for key in FIELDS}
    if (
        any(not math.isfinite(v) or v <= 0 for v in values.values())
        or not math.isfinite(volume)
        or not math.isfinite(money)
        or money < 0
    ):
        raise ValueError("Invalid OHLCV/amount")
    if values["high"] < max(values["open"], values["close"]) or values["low"] > min(
        values["open"], values["close"]
    ):
        raise ValueError("OHLC invariant violated")
    if money <= 0:
        raise ValueError(
            "Positive trading volume requires actual positive turnover for VWAP"
        )
    return {
        **values,
        "volume": volume / factor,
        "factor": factor,
        "money": money,
        "vwap": money / volume * factor,
    }


def contiguous_intervals(indices):
    result = []
    for index in sorted(set(indices)):
        if result and index == result[-1][1] + 1:
            result[-1][1] = index
        else:
            result.append([index, index])
    return result


def prepare(args):
    cutoff = sealed_cutoff(args.research_db)
    if not args.start <= args.end < cutoff:
        raise ValueError(f"Export must end strictly before {cutoff}")
    if args.output.exists():
        raise FileExistsError("Output already exists; no overwrite is permitted")
    # Only dated membership snapshots strictly before the protected boundary are
    # used; no latest/current membership is backfilled into earlier dates.
    history = json.loads(args.membership.read_text(encoding="utf-8"))
    history = history.get("result", history.get("payload", history))
    snapshots = sorted(
        (
            s
            for s in history["snapshots"]
            if s["trade_date"] < cutoff and s["trade_date"] <= args.end
        ),
        key=lambda s: s["trade_date"],
    )
    if not snapshots or snapshots[0]["trade_date"] >= args.start:
        raise ValueError("Missing a membership snapshot preceding the start date")
    dates = [s["trade_date"] for s in snapshots]
    member_sets = [
        {str(m.get("ts_code") or m.get("con_code")) for m in s["members"]}
        for s in snapshots
    ]
    symbols = sorted(set().union(*member_sets))
    stats = {
        "source_rows": 0,
        "suspended_rows": 0,
        "membership_sample_rows": 0,
        "missing_daily_member_sessions": 0,
    }
    with read_only(args.feature_db) as db:
        calendar = set()
        for (payload,) in db.execute(
            "SELECT payload_json FROM ts_trade_cal "
            "WHERE json_extract(payload_json,'$.cal_date') BETWEEN ? AND ?",
            (args.start.replace("-", ""), args.end.replace("-", "")),
        ):
            row = json.loads(payload)
            if row.get("exchange") == "SSE" and int(row["is_open"]) == 1:
                text = str(row["cal_date"])
                calendar.add(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        calendar = sorted(calendar)
        if len(calendar) < 160:
            raise ValueError("At least 160 verified trading sessions are required")
        membership = defaultdict(list)
        for i, day in enumerate(calendar):
            pos = (
                bisect.bisect_left(dates, day) - 1
            )  # close-date snapshots apply next session
            if pos < 0:
                raise ValueError("Membership does not cover calendar")
            for symbol in member_sets[pos]:
                membership[symbol].append(i)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=args.output.name + "-building-", dir=args.output.parent
            )
        )
        provider = staging / "qlib_data"
        (provider / "calendars").mkdir(parents=True)
        (provider / "instruments").mkdir()
        (staging / "csv").mkdir()
        (provider / "calendars/day.txt").write_text(
            "\n".join(calendar) + "\n", encoding="utf-8"
        )
        all_intervals = []
        research_intervals = []
        emitted = 0
        for symbol in symbols:
            raw = unique_rows(db, "ts_daily", symbol, args.start, args.end)
            adjustments = unique_rows(db, "ts_adj_factor", symbol, args.start, args.end)
            if not raw:
                if membership[symbol]:
                    raise ValueError(
                        f"Missing all prices for historical member {symbol}"
                    )
                continue
            first = min(
                day
                for day, row in raw.items()
                if float(row["close"]) > 0 and float(row["vol"]) > 0
            )
            if first not in adjustments:
                raise ValueError(f"Missing initial adjustment {symbol}/{first}")
            anchor = float(raw[first]["close"]) * float(
                adjustments[first]["adj_factor"]
            )
            code = symbol.split(".")[1] + symbol.split(".")[0]
            values_by_day = {}
            for day, row in raw.items():
                formatted = f"{day[:4]}-{day[4:6]}-{day[6:]}"
                if formatted not in calendar:
                    raise ValueError(f"Price outside verified calendar {symbol}/{day}")
                if day not in adjustments:
                    raise ValueError(f"Missing adjustment {symbol}/{day}")
                values_by_day[formatted] = adjusted_row(
                    row, float(adjustments[day]["adj_factor"]), anchor
                )
                stats["source_rows"] += 1
                stats["suspended_rows"] += int(
                    math.isnan(values_by_day[formatted]["close"])
                )
            feature_dir = provider / "features" / code.lower()
            feature_dir.mkdir(parents=True)
            lo = calendar.index(min(values_by_day))
            hi = calendar.index(max(values_by_day))
            for field in FIELDS:
                floats = [float(lo)] + [
                    values_by_day.get(day, {}).get(field, math.nan)
                    for day in calendar[lo : hi + 1]
                ]
                (feature_dir / f"{field}.day.bin").write_bytes(
                    struct.pack("<" + "f" * len(floats), *floats)
                )
            with (staging / "csv" / f"{code}.csv").open(
                "w", newline="", encoding="utf-8"
            ) as stream:
                writer = csv.writer(stream)
                writer.writerow(("symbol", "date", *FIELDS))
                for day, values in sorted(values_by_day.items()):
                    writer.writerow((code, day, *(values[key] for key in FIELDS)))
            all_intervals.append(f"{code}\t{calendar[lo]}\t{calendar[hi]}")
            for lo, hi in contiguous_intervals(membership[symbol]):
                research_intervals.append(f"{code}\t{calendar[lo]}\t{calendar[hi]}")
            for i in membership[symbol]:
                if calendar[i] not in values_by_day:
                    stats["missing_daily_member_sessions"] += 1
                elif not math.isnan(values_by_day[calendar[i]]["close"]):
                    stats["membership_sample_rows"] += 1
            emitted += 1
            if emitted % 50 == 0:
                print(
                    json.dumps(
                        {"symbols_exported": emitted, "rows": stats["source_rows"]}
                    ),
                    flush=True,
                )
        (provider / "instruments/all.txt").write_text(
            "\n".join(all_intervals) + "\n", encoding="utf-8"
        )
        (provider / "instruments/csi300.txt").write_text(
            "\n".join(sorted(research_intervals)) + "\n", encoding="utf-8"
        )
    # 60-session feature warm-up and 20-session purge protect every horizon.
    split = int(len(calendar) * 0.75)
    segments = {
        "train": [calendar[60], calendar[split - 21]],
        "valid": [calendar[split], calendar[-21]],
    }
    if segments["train"][0] > segments["train"][1]:
        raise ValueError("Insufficient training history after warm-up/purge")
    labels = {str(h): f"Ref($close, -{h}) / Ref($open, -1) - 1" for h in (5, 10, 20)}
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "price_research_data_ready_not_strict_pit_accepted",
        "protected_sealed_start": cutoff,
        "data_start": calendar[0],
        "data_end": calendar[-1],
        "symbols": emitted,
        "sessions": len(calendar),
        **stats,
        "provider_uri": "qlib_data",
        "instruments": "csi300",
        "feature_set": "Alpha360",
        "segments": segments,
        "label_expressions": labels,
        "purge_sessions": 20,
        "decision_timing": (
            "Close features at t can only be used from next trading session; "
            "labels enter at next open."
        ),
        "normalization": (
            "First available positive-volume close per symbol = 1; "
            "factor=adj_factor/(initial_close*initial_adj_factor); "
            "adjusted_volume=raw_shares/factor."
        ),
        "missing_data": (
            "NaN; no forward-fill; missing price is not assumed to prove suspension."
        ),
        "industry_pit_available": False,
        "membership_publication_timestamp_verified": False,
        "historical_revision_availability_verified": False,
        "qlib_runtime_verified": False,
        "limitations": [
            "Monthly effective-dated membership is not publication-time proof.",
            "Missing member sessions may be suspensions or unavailable data.",
            (
                "Listing age/ST/delisting filters are not verified; "
                "this is not the accepted production research universe."
            ),
            (
                "No industry/fundamental features, no test/sealed segment, "
                "no training or score import performed."
            ),
        ],
        "source_membership": str(args.membership.resolve()),
        "source_feature_db": str(args.feature_db.resolve()),
        "source_sealed_metadata_db": str(args.research_db.resolve()),
    }
    checksums = {
        str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(
            list(provider.rglob("*")) + list((staging / "csv").glob("*.csv"))
        )
        if p.is_file()
    }
    (staging / "checksums.json").write_text(
        json.dumps(checksums, indent=2), encoding="utf-8"
    )
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    staging.rename(args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-db", type=Path, required=True)
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--membership", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    date.fromisoformat(args.start)
    date.fromisoformat(args.end)
    prepare(args)


if __name__ == "__main__":
    main()
