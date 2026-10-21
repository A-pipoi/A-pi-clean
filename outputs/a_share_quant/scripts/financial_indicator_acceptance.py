"""Fixed two-symbol official financial-field enrichment; no historical PIT claim."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_features import values_as_of
from quant.data.extension_scheduler import FINANCIAL_INDICATOR_FIELDS
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

root = Path(__file__).resolve().parents[1] / "data_cache/data_extension"
plan = {
    "symbols": ["000001.SZ", "600036.SH"],
    "period": "20260630",
    "fields": FINANCIAL_INDICATOR_FIELDS,
    "maximum_requests": 6,
    "historical_pit_created": False,
    "research_budget_used": False,
}
(root / "financial_indicator_estimate_20261008.json").write_text(
    json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
)
store = ExtensionStore(default_store_path())
store.initialize()
jobs = []
for symbol in plan["symbols"]:
    jobs.append(
        sync(
            store,
            "fina_indicator",
            {"ts_code": symbol, "period": plan["period"]},
            load_token(),
            max_pages=3,
            fetch=lambda token, name, params: query(
                token, name, params, fields=FINANCIAL_INDICATOR_FIELDS
            ),
        )
    )
values, proof = values_as_of(store, datetime.now(UTC))
report = {
    "kind": "official_financial_indicator_field_acceptance",
    "plan": plan,
    "jobs": jobs,
    "values": {
        name: {
            symbol: value
            for symbol, value in values[name].items()
            if symbol in plan["symbols"]
        }
        for name in ("quality_roa", "growth_profit", "quality_cashflow")
    },
    "source_hash": proof["source_hash"],
    "historical_pit_created": False,
    "sealed_samples_modified": False,
}
report["artifact_digest"] = store.archive_quality(2, report)
(root / "financial_indicator_acceptance_20261008.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {"jobs": jobs, "values": report["values"], "historical_pit_created": False},
        ensure_ascii=False,
    )
)
