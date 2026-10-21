"""Targeted real-price/suspension probes; empty responses never become bars."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

root = Path("data_cache/data_extension")
store = ExtensionStore(default_store_path())
store.initialize()
report = json.loads((root / "full_history_acceptance.json").read_text(encoding="utf-8"))
token = load_token()
results = []
for day in report["coverage"]:
    if day["complete"]:
        continue
    for symbol in day["unexplained_no_daily_bar"]:
        result = sync(
            store,
            "daily",
            {"ts_code": symbol, "trade_date": day["date"]},
            token,
            refresh=True,
            fetch=lambda token, endpoint, params: query(token, endpoint, params),
        )
        results.append({"symbol": symbol, "date": day["date"], **result})
        print(json.dumps(results[-1]), flush=True)
proof = {
    "kind": "targeted_historical_quote_gaps",
    "probes": results,
    "empty_responses_imputed": False,
    "historical_pit_created": False,
    "sealed_samples_modified": False,
}
proof["artifact_digest"] = store.archive_quality(1, proof)
(root / "historical_gap_probes.json").write_text(
    json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
)
