"""Bounded genuine full-day suspension coverage; no orders or inferred halts."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_coverage import reconcile_daily
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync

root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
days = ("20260928", "20260929", "20260930", "20261008")
plan = {
    "dates": days,
    "endpoint": "suspend_d",
    "maximum_requests": 20,
    "orders_created": False,
}
(root / "suspension_estimate_20261008.json").write_text(
    json.dumps(plan, indent=2), encoding="utf-8"
)
store = ExtensionStore(default_store_path())
store.initialize()
jobs = [
    sync(store, "suspend_d", {"trade_date": day}, load_token(), max_pages=5)
    for day in days
]
report = {
    "kind": "official_full_day_suspension_reconciliation",
    "jobs": jobs,
    "coverage": [reconcile_daily(store, day) for day in days],
    "all_daily_dates_complete": False,
    "historical_industry_pit_created": False,
    "orders_created": False,
}
report["all_daily_dates_complete"] = all(row["complete"] for row in report["coverage"])
report["artifact_digest"] = store.archive_quality(1, report)
(root / "suspension_acceptance_20261008.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "all_daily_dates_complete": report["all_daily_dates_complete"],
            "coverage": [
                {
                    k: r[k]
                    for k in (
                        "date",
                        "proven_full_day_suspensions",
                        "unexplained_no_daily_bar",
                        "complete",
                    )
                }
                for r in report["coverage"]
            ],
        },
        ensure_ascii=False,
    )
)
