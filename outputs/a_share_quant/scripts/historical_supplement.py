"""Real disclosure-calendar supplement; no feature or sealed mutation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

store = ExtensionStore(default_store_path())
store.initialize()
token = load_token()
if not token:
    raise ValueError("token unavailable")
results = []
for period in ("20240331", "20240630", "20240930", "20241231"):
    result = sync(
        store,
        "disclosure_date",
        {"end_date": period},
        token,
        max_pages=100,
        fetch=lambda token, endpoint, params: query(token, endpoint, params),
    )
    results.append({"period": period, **result})
    print(json.dumps(results[-1]), flush=True)
report = {
    "kind": "historical_disclosure_calendar_supplement",
    "jobs": results,
    "complete": all(r["status"] == "complete" for r in results),
    "historical_first_seen_created": False,
    "production_features_modified": False,
}
report["artifact_digest"] = store.archive_quality(1, report)
destination = Path("data_cache/data_extension/historical_disclosure_calendar.json")
destination.write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
