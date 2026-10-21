"""Recheck immutable coverage plan using independently evidenced code identity."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_coverage import reconcile_daily
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

store = ExtensionStore(default_store_path())
store.initialize()
root = Path("data_cache/data_extension")
result = sync(
    store,
    "bse_mapping",
    {},
    load_token(),
    fetch=lambda token, name, params: query(token, name, params),
)
print(json.dumps(result), flush=True)
if result["status"] != "complete":
    raise ValueError("identity supplier coverage incomplete")
path = root / "full_history_acceptance.json"
if not path.exists():
    print("Identity source saved; full-market worker still in progress.", flush=True)
else:
    original = json.loads(path.read_text(encoding="utf-8"))
    started = time.monotonic()
    coverage = [reconcile_daily(store, r["date"]) for r in original["coverage"]]
    report = {
        **original,
        "coverage": coverage,
        "complete": all(r["complete"] for r in coverage),
        "reconciliation_version": "issuer_identity_v2",
        "reconciliation_elapsed_seconds": time.monotonic() - started,
        "previous_artifact_digest": original["artifact_digest"],
    }
    report.pop("artifact_digest", None)
    report["artifact_digest"] = store.archive_quality(1, report)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "complete": report["complete"],
                "sessions": report["sessions"],
                "failed_dates": sum(not r["complete"] for r in coverage),
            }
        ),
        flush=True,
    )
