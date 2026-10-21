"""Bounded real-provider reconciliation and quality artifact, no research experiments."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_analytics import diagnostics
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync

store = ExtensionStore(default_store_path())
store.initialize()
job = sync(
    store, "disclosure_date", {"end_date": "20260630"}, load_token(), max_pages=10
)
report = diagnostics(store)
result = {
    "checked_at": datetime.now(UTC).isoformat(),
    "kind": "data_extension_continuation_acceptance",
    "disclosure_calendar_job": job,
    "daily_symbol_reconciliation": report["daily_symbol_reconciliation"],
    "technical_indicator_acceptance": report["technical_indicator_acceptance"],
    "raw_counts": report["raw_store"]["counts"],
    "limitations": [
        "listed symbols without daily bars still require suspension evidence",
        "historical industry available_at evidence unavailable",
        "new prospective IC observations must mature before OOS acceptance",
        "2019-2021 fund universe reproduction and 2015-onward option coverage not accepted",
        "dividend account posting, financial audit exclusion and diagnostic IC contrast not completed",
        "120-session eligibility helper is not yet wired to all strategy paths",
    ],
    "sealed_samples_modified": False,
    "research_budget_used": False,
    "all_requirements_complete": False,
}
digest = store.archive_quality(1, result)
result["artifact_digest"] = digest
path = (
    Path(__file__).resolve().parents[1]
    / "data_cache/data_extension/completion_audit_20261008.json"
)
path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "output": str(path),
            "artifact_digest": digest,
            "disclosure_rows": job.get("rows"),
            "status": job["status"],
            "all_requirements_complete": False,
        }
    )
)
