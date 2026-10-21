"""Finalize scoped acceptance with retry-inclusive timing and explicit limits."""

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_store import ExtensionStore, default_store_path

root = Path("data_cache/data_extension")
experiments = json.loads(
    (root / "historical_experiments_acceptance.json").read_text(encoding="utf-8")
)
history = json.loads(
    (root / "full_history_acceptance.json").read_text(encoding="utf-8")
)
ic = experiments["disclosure_ic"]
fund = experiments["fund_layers"]
ic_ok = len(ic["series"]) == 9 and all(
    p["common_symbols"] >= 20
    and all(
        isinstance(p[b + "_ic"], (int, float)) and math.isfinite(p[b + "_ic"])
        for b in ("ann_date", "end_date")
    )
    for p in ic["series"]
)
fund_ok = len(fund["periods"]) == 12 and all(
    p["source_rows"] > 0 and p["priced_symbols"] > 0 for p in fund["periods"]
)
report = {
    "kind": "historical_scoped_acceptance",
    "checked_at_unix": time.time(),
    "disclosure_diagnostic_computed": ic_ok,
    "fund_layers_computed": fund_ok,
    "two_year_market_reconciled": history["complete"],
    "scope": [
        "9 fixed financial IC dates",
        "12 fund reporting quarters 2019-2021",
        "daily/daily_basic/adj_factor/stk_limit/suspend_d 20241008-20261008",
    ],
    "ic_dates": len(ic["series"]),
    "fund_periods": len(fund["periods"]),
    "fund_distinct_observed_records": sum(p["source_rows"] for p in fund["periods"]),
    "market_sessions": history["sessions"],
    "failed_market_dates": [
        r["date"] for r in history["coverage"] if not r["complete"]
    ],
    "fund_missing_price_symbols_by_period": {
        p["period"]: p["missing_price_symbols"]
        for p in fund["periods"]
        if p["missing_price_symbols"]
    },
    "all_fund_price_labels_complete": fund["all_periods_priced"],
    "timing_since_immutable_plan_saved_seconds": {
        "experiments": max(
            0, time.time() - (root / "historical_research_plan.json").stat().st_mtime
        ),
        "market": max(
            0, time.time() - (root / "full_history_plan.json").stat().st_mtime
        ),
    },
    "timing_note": "Wall-clock observation includes failed attempts, checkpoint continuation and shared provider pacing; not pure compute time.",
    "artifacts": {
        "ic": ic["artifact_digest"],
        "fund": fund["artifact_digest"],
        "market": history["artifact_digest"],
    },
    "scope_computed": ic_ok and fund_ok and bool(history["supplier_jobs_completed"]),
    "all_requirements_complete": False,
    "cost_aware_fund_strategy_approved": False,
    "historical_pit_created": False,
    "sealed_samples_modified": False,
    "research_budget_used": False,
    "limits": [
        "No fabricated historical first_seen/industry availability",
        "Fund layers are gross retrospective diagnostics, not executable strategy approval",
        "Unpriced stocks excluded explicitly, not filled with zero",
        "Not all endpoints/all historical years or 2015-onward option coverage",
    ],
}
store = ExtensionStore(default_store_path())
report["artifact_digest"] = store.archive_quality(3, report)
(root / "historical_scoped_acceptance.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            key: report[key]
            for key in (
                "disclosure_diagnostic_computed",
                "fund_layers_computed",
                "two_year_market_reconciled",
                "market_sessions",
                "fund_distinct_observed_records",
            )
        }
    )
)
