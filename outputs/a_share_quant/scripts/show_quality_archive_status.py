"""Read-only summary of the shared phase reports exposed to research ledger."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_store import ExtensionStore, default_store_path

path = default_store_path()
items = ExtensionStore(path).quality_artifacts() if path.exists() else []
latest = {
    phase: next((item for item in items if item["phase"] == phase), None)
    for phase in (1, 2, 3)
}
print(
    json.dumps(
        {"shared_store_exists": path.exists(), "latest_by_phase": latest},
        ensure_ascii=False,
    )
)
