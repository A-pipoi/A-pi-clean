"""Sealed snapshot services with explicit runtime dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from quant.api.contracts import (
    BarToPayloadCallback,
    FactorSnapshotCallback,
    FactorSnapshotHashCallback,
    ObjectMappingCallback,
)
from quant.data.schema import Bar
from quant.factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from quant.research import ResearchLedger


@dataclass(frozen=True)
class SealedSnapshotDependencies:
    FACTOR_STORE: FactorRegistryStore
    RESEARCH_LEDGER: ResearchLedger
    _bar_to_payload: BarToPayloadCallback
    _factor_snapshot: FactorSnapshotCallback
    _factor_snapshot_hash: FactorSnapshotHashCallback
    _object_mapping: ObjectMappingCallback


def _sealed_pipeline_key(scope: str, *, deps: SealedSnapshotDependencies) -> str:
    return f"{FACTOR_PIPELINE_VERSION}:sealed:{scope}"


def _ensure_sealed_snapshot(
    strategy_name: str,
    pipeline_key: str,
    bars: list[Bar],
    snapshot: object,
    *,
    symbols: list[str],
    adjust: str,
    sealed_days: int,
    pit_data: dict[str, object] | None = None,
    pit_metadata: dict[str, object] | None = None,
    additional_parameters: dict[str, object] | None = None,
    deps: SealedSnapshotDependencies,
) -> dict[str, object]:
    existing = deps.RESEARCH_LEDGER.get_sealed_test(
        strategy_name, pipeline_key, include_bars=True
    )
    if existing is not None:
        return existing
    ordered = sorted(bars, key=lambda bar: (bar.date, bar.symbol))
    dates = sorted({bar.date for bar in ordered})
    if len(dates) < sealed_days + 40:
        raise ValueError(
            f"密封测试至少需要 {sealed_days + 40} 个交易日，当前只有 {len(dates)} 个；请扩大 Tushare 回测区间"
        )
    sealed_start = dates[-sealed_days]
    sealed_end = dates[-1]
    stored_bars = [bar for bar in ordered if bar.date <= sealed_end]
    payload = [deps._bar_to_payload(bar) for bar in stored_bars]
    content = json.dumps(
        {"bars": payload, "pit_data": pit_data or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    metadata = dict(getattr(snapshot, "metadata", {}) or {})
    metadata.update(
        {
            "source": "tushare",
            "data_role": "sealed_holdout_snapshot",
            "symbols": list(symbols),
            "adjust": adjust,
            "sealed_days": sealed_days,
            "pit_quality": deps._object_mapping((pit_metadata or {}).get("pit_quality"))
            or {"size": "unverified", "industry": "unverified"},
            "pit_sources": deps._object_mapping((pit_metadata or {}).get("sources")),
        }
    )
    research_universe = (pit_metadata or {}).get("research_universe")
    if isinstance(research_universe, dict):
        metadata["research_universe"] = dict(research_universe)
    pit_digest = hashlib.sha256(
        json.dumps(
            pit_data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    data_version = str(getattr(snapshot, "version", "tushare:unknown"))
    if pit_data:
        data_version = f"{data_version}:pit:{pit_digest}"
        metadata["pit_data_version"] = pit_digest
    return deps.RESEARCH_LEDGER.create_sealed_test(
        strategy_version=strategy_name,
        factor_pipeline_version=pipeline_key,
        data_version=data_version,
        sealed_start=sealed_start.isoformat(),
        sealed_end=sealed_end.isoformat(),
        dataset_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        bars=payload,
        pit_data=pit_data or {},
        metadata=metadata,
        parameters={
            "sealed_days": sealed_days,
            "symbols": list(symbols),
            "adjust": adjust,
            "factor_pipeline_version": FACTOR_PIPELINE_VERSION,
            "research_universe": dict(research_universe)
            if isinstance(research_universe, dict)
            else None,
            **(additional_parameters or {}),
        },
    )


def _research_bars(
    bars: list[Bar], sealed: dict[str, object], *, deps: SealedSnapshotDependencies
) -> list[Bar]:
    start = date.fromisoformat(str(sealed["sealed_start"]))
    return [bar for bar in bars if bar.date < start]


def _factor_snapshot(*, deps: SealedSnapshotDependencies) -> list[dict[str, object]]:
    return [item.as_dict() for item in deps.FACTOR_STORE.definitions(enabled_only=True)]


def _factor_snapshot_hash(*, deps: SealedSnapshotDependencies) -> str:
    content = json.dumps(
        deps._factor_snapshot(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sealed_result_passed(
    sealed: dict[str, object] | None, *, deps: SealedSnapshotDependencies
) -> bool:
    if (
        not sealed
        or sealed.get("status") != "passed"
        or (not isinstance(sealed.get("result"), dict))
    ):
        return False
    result = deps._object_mapping(sealed["result"])
    return (
        result.get("passed") is True
        and result.get("factor_snapshot_hash") == deps._factor_snapshot_hash()
    )
