import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant.data.extension_store import ExtensionStore, canonical, default_store_path
from quant.data.extension_sync import load_token
from quant.data.extension_transport import query

rows = query(
    load_token(), "fund_portfolio", {"period": "20190630", "offset": 0, "limit": 5000}
)
report = {
    "kind": "fund_batch_size_observation",
    "period": "20190630",
    "requested_limit": 5000,
    "returned_rows": len(rows),
    "source_hash": hashlib.sha256(canonical(rows).encode()).hexdigest(),
    "observed_at": datetime.now(UTC).isoformat(),
    "official_ceiling_unspecified": True,
    "not_a_full_period_request": True,
    "proven_batch_size": 5000 if len(rows) == 5000 else None,
}
store = ExtensionStore(default_store_path())
report["artifact_digest"] = store.archive_quality(3, report)
Path("data_cache/data_extension/fund_batch_probe.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print(json.dumps(report))
