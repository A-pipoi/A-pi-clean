"""Persist observed feature coverage without provider requests or strategy runs."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_features import values_as_of
from quant.data.extension_store import ExtensionStore, default_store_path

store = ExtensionStore(default_store_path())
cutoff = datetime.now(UTC)
values, proof = values_as_of(store, cutoff)
report = {
    "kind": "observed_derived_feature_coverage",
    "cutoff": cutoff.isoformat(),
    "coverage": {name: len(items) for name, items in values.items()},
    "derived_values": {
        name: items
        for name, items in values.items()
        if name in proof["derived_features"]["formulas"]
    },
    "proof": proof,
    "provider_requests": 0,
    "production_orders_created": False,
    "mature_oos_ic_accepted": False,
    "all_requirements_complete": False,
}
report["artifact_digest"] = store.archive_quality(2, report)
destination = (
    Path(__file__).resolve().parents[1]
    / "data_cache/data_extension/derived_features_acceptance_20261008.json"
)
destination.write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "coverage": report["coverage"],
            "artifact_digest": report["artifact_digest"],
            "provider_requests": 0,
            "mature_oos_ic_accepted": False,
        }
    )
)
