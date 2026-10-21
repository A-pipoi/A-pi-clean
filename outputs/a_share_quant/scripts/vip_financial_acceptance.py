"""Pre-estimated, resumable full-quarter VIP supplier acceptance, not a seal run."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_features import values_as_of
from quant.data.extension_scheduler import FINANCIAL_INDICATOR_FIELDS
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query
from quant.data.provider_endpoints import VIP_ENDPOINTS

root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
root.mkdir(parents=True, exist_ok=True)
period = "20260630"
plan = {
    "kind": "5000_point_full_quarter_financial_plan",
    "period": period,
    "jobs": [
        {
            "logical_endpoint": name,
            "provider_api": physical,
            "maximum_requests": 100 if name == "fina_indicator" else 20,
        }
        for name, physical in VIP_ENDPOINTS.items()
    ],
    "production_strategy_parameters_changed": False,
    "sealed_samples_modified": False,
    "formal_research_budget_used": False,
    "permission_denied_retries": 0,
}
estimate = root / "vip_financial_estimate_20261008.json"
if not estimate.exists():
    estimate.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
else:
    if json.loads(estimate.read_text(encoding="utf-8")) != plan:
        raise SystemExit("Pre-registered plan changed; no supplier requests made.")
store = ExtensionStore(default_store_path())
store.initialize()
previous_path = root / "vip_financial_acceptance_20261008.json"
if previous_path.exists():
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    if previous.get("supplier_pagination_complete"):
        print(json.dumps({"status": "already_complete", "provider_requests": 0}))
        raise SystemExit(0)
    continuation = {
        "kind": "vip_checkpoint_continuation_estimate",
        "period": period,
        "maximum_additional_requests": sum(
            j["maximum_requests"] for j in previous["jobs"] if j["status"] != "complete"
        ),
        "only_incomplete_jobs": True,
        "existing_rows_deleted": False,
    }
    (root / "vip_financial_continuation_estimate_20261008.json").write_text(
        json.dumps(continuation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
results = []
started = time.monotonic()
for job in plan["jobs"]:
    fields = (
        FINANCIAL_INDICATOR_FIELDS
        if job["logical_endpoint"] == "fina_indicator"
        else ""
    )
    began = time.monotonic()
    result = sync(
        store,
        job["logical_endpoint"],
        {"period": period},
        load_token(),
        max_pages=job["maximum_requests"],
                fetch=lambda token, name, params, fields=fields: query(token, name, params, fields=fields),
    )
    with store.connect() as db:
        rows = db.execute(
            f"SELECT COUNT(DISTINCT ts_code) FROM ts_{job['logical_endpoint']} WHERE period_date=?",
            (period,),
        ).fetchone()[0]
    results.append(
        {
            **job,
            **result,
            "distinct_symbols": rows,
            "elapsed_seconds": round(time.monotonic() - began, 3),
        }
    )
    print(
        json.dumps(
            {
                "endpoint": job["provider_api"],
                "status": result["status"],
                "rows": result.get("rows"),
                "distinct_symbols": rows,
                "elapsed_seconds": results[-1]["elapsed_seconds"],
            }
        ),
        flush=True,
    )
values, proof = values_as_of(store, datetime.now(UTC))
report = {
    "kind": "5000_point_full_quarter_financial_acceptance",
    "plan": plan,
    "jobs": results,
    "elapsed_seconds": round(time.monotonic() - started, 3),
    "cutoff": proof["cutoff"],
    "feature_counts": {name: len(items) for name, items in values.items()},
    "source_hash": proof["source_hash"],
    "supplier_pagination_complete": all(j["status"] == "complete" for j in results),
    "all_requirements_complete": False,
    "historical_first_seen_created": False,
    "sealed_samples_modified": False,
    "formal_research_budget_used": False,
}
if "continuation" in globals():
    report["continuation_estimate"] = continuation
report["artifact_digest"] = store.archive_quality(2, report)
(root / "vip_financial_acceptance_20261008.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "supplier_pagination_complete": report["supplier_pagination_complete"],
            "elapsed_seconds": report["elapsed_seconds"],
            "feature_counts": report["feature_counts"],
        }
    ),
    flush=True,
)
