"""Bounded provider permission/coverage verification, not historical OOS proof."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_financial import financial_status
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync

root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
root.mkdir(parents=True, exist_ok=True)
symbols = ["000001.SZ", "600036.SH"]
plan = [
    (
        "trade_cal",
        {"exchange": "SSE", "start_date": "19900101", "end_date": "20261008"},
        20,
    )
]
for symbol in symbols:
    plan.extend(
        [
            (endpoint, {"ts_code": symbol}, 3)
            for endpoint in ("fina_audit", "forecast", "dividend")
        ]
    )
estimate = {
    "scope": "fixed 2-bank supplier acceptance; no factor selection",
    "jobs": [
        {"endpoint": endpoint, "params": params, "maximum_requests": pages}
        for endpoint, params, pages in plan
    ],
    "maximum_requests": sum(pages for _, _, pages in plan),
    "sealed_samples_modified": False,
    "research_budget_used": False,
}
(root / "financial_policy_estimate_20261008.json").write_text(
    json.dumps(estimate, ensure_ascii=False, indent=2), encoding="utf-8"
)
store = ExtensionStore(default_store_path())
store.initialize()
reports = []
for endpoint, params, pages in plan:
    result = sync(store, endpoint, params, load_token(), max_pages=pages)
    reports.append({"endpoint": endpoint, "params": params, **result})
    if result["status"] == "failed":
        break
result = {
    "kind": "financial_policy_supplier_acceptance",
    "jobs": reports,
    "observed_status": financial_status(store, datetime.now(UTC), symbols),
    "historical_pit_created": False,
    "historical_oos_accepted": False,
    "production_orders_created": False,
    "status": "supplier_ingested"
    if len(reports) == len(plan) and all(j["status"] == "complete" for j in reports)
    else "incomplete",
}
result["artifact_digest"] = store.archive_quality(2, result)
(root / "financial_policy_acceptance_20261008.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "status": result["status"],
            "jobs": [
                {k: j[k] for k in ("endpoint", "rows", "status") if k in j}
                for j in reports
            ],
            "forecast_revisions": len(result["observed_status"]["forecast_revisions"]),
            "audit_eligibility": result["observed_status"]["audit_eligibility"],
        }
    )
)
