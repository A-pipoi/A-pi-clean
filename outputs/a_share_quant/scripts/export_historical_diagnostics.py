"""Export read-only real raw-store diagnostics for isolated visual acceptance."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_analytics import diagnostics
from quant.data.extension_store import ExtensionStore, default_store_path

report = diagnostics(ExtensionStore(default_store_path()))
destination = Path("data_cache/data_extension/historical_browser_report.json")
destination.write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "report": str(destination),
            "ic_dates": len(report["disclosure_alignment_ic"].get("series", [])),
            "fund_periods": len(report["fund_historical_layers"].get("periods", [])),
        }
    )
)
