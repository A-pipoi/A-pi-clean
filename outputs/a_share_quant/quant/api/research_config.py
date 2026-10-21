"""Pure derivation of reproducible research request identifiers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


def sealed_scope(
    symbols: Sequence[str], adjust: str, *, universe_version: str = "", signal_adjust: str = "raw",
    corporate_actions: str = "off",
) -> str:
    payload: dict[str, object] = {
        "symbols": sorted(str(item).upper() for item in symbols),
        "adjust": str(adjust or ""),
    }
    if universe_version:
        payload["universe_version"] = universe_version
    if signal_adjust != "raw":
        payload["signal_adjust"] = str(signal_adjust)
    if corporate_actions != "off":
        payload["corporate_actions"] = str(corporate_actions)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def scoped_dataset_version(base_version: str, universe_version: str) -> str:
    return (
        f"{base_version}:universe:{universe_version}"
        if universe_version
        else base_version
    )


def dataset_metadata_with_universe(
    source_metadata: Mapping[str, object], universe_metadata: Mapping[str, object]
) -> dict[str, object]:
    metadata = dict(source_metadata)
    metadata["research_universe"] = dict(universe_metadata)
    return metadata


def sealed_pipeline_key(scope: str, factor_pipeline_version: str) -> str:
    return f"{factor_pipeline_version}:sealed:{scope}"
