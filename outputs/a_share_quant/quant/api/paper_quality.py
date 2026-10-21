"""Paper quality services with explicit runtime dependencies."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quant.api.contracts import (
    AsFloatCallback,
    GetTushareStockMetadataCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    RiskLimitsPayloadCallback,
)
from quant.execution import PaperStore
from quant.portfolio import PAPER_RISK_LIMITS


@dataclass(frozen=True)
class PaperQualityDependencies:
    stock_metadata: GetTushareStockMetadataCallback
    DATA_CACHE_DIR: Path
    PAPER_STORE: PaperStore
    _as_float: AsFloatCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _risk_limits_payload: RiskLimitsPayloadCallback


def _risk_limits_payload(*, deps: PaperQualityDependencies) -> dict[str, object]:
    return {
        "max_single_weight": PAPER_RISK_LIMITS.max_single_weight,
        "max_industry_weight": PAPER_RISK_LIMITS.max_industry_weight,
        "max_gross_leverage": PAPER_RISK_LIMITS.max_gross_leverage,
        "max_drawdown": PAPER_RISK_LIMITS.max_drawdown,
        "max_annualized_volatility": PAPER_RISK_LIMITS.max_annualized_volatility,
        "max_var_95": PAPER_RISK_LIMITS.max_var_95,
        "max_es_95": PAPER_RISK_LIMITS.max_es_95,
        "max_daily_variance": PAPER_RISK_LIMITS.max_daily_variance,
        "max_pairwise_correlation": PAPER_RISK_LIMITS.max_pairwise_correlation,
    }


def _limits_data_quality(
    metadata: dict[str, object], *, context: str, deps: PaperQualityDependencies
) -> dict[str, object]:
    available = bool(metadata.get("limits_available", False))
    if available:
        return {"limits_available": True, "warnings": []}
    status = str(metadata.get("limit_data_status") or "unknown")
    detail = str(metadata.get("limit_data_error") or "").strip()
    suffix = f"：{detail}" if detail else ""
    return {
        "limits_available": False,
        "warnings": [
            f"{context}未取得 Tushare stk_limit 涨跌停价（状态：{status}）{suffix}"
        ],
    }


def _enrich_paper_state(
    state: dict[str, object], *, deps: PaperQualityDependencies
) -> dict[str, object]:
    """Add real metadata needed by the risk dashboard without changing the ledger."""
    enriched = dict(state)
    enriched["risk_limits"] = deps._risk_limits_payload()
    enriched["cycle_summary"] = deps.PAPER_STORE.cycle_summary()
    enriched["recent_cycles"] = deps.PAPER_STORE.recent_cycles()
    positions = [
        deps._object_mapping(item)
        for item in deps._object_list(state.get("positions"))
        if isinstance(item, Mapping)
    ]
    if not positions:
        enriched["industry_exposure"] = []
        enriched["industry_metadata_stale"] = False
        return enriched
    symbols = [str(item.get("symbol", "")) for item in positions if item.get("symbol")]
    metadata: dict[str, dict[str, str]] = {}
    stale = False
    if symbols:
        try:
            metadata, stale = deps.stock_metadata(
                symbols,
                token=os.getenv("TUSHARE_TOKEN"),
                cache_dir=str(deps.DATA_CACHE_DIR),
            )
        except (RuntimeError, ValueError):
            metadata = {}
    equity = deps._as_float(state.get("equity") or 0.0)
    by_industry: dict[str, float] = {}
    enriched_positions: list[dict[str, object]] = []
    for position in positions:
        symbol = str(position.get("symbol", ""))
        identity = metadata.get(symbol) or {}
        name = str(identity.get("name") or position.get("name") or "")
        industry = str(identity.get("industry") or position.get("industry") or "")
        enriched_positions.append({**position, "name": name, "industry": industry})
        industry = industry or "未分类"
        by_industry[industry] = by_industry.get(industry, 0.0) + deps._as_float(
            position.get("market_value") or 0.0
        )
    enriched["positions"] = enriched_positions
    enriched["industry_exposure"] = [
        {
            "industry": industry,
            "market_value": value,
            "weight": value / equity if equity > 0 else None,
        }
        for industry, value in sorted(
            by_industry.items(), key=lambda item: item[1], reverse=True
        )
    ]
    enriched["industry_metadata_stale"] = stale
    return enriched


def _require_complete_universe(
    metadata: Mapping[str, object], *, context: str, deps: PaperQualityDependencies
) -> None:
    missing = deps._object_list(metadata.get("missing_symbols"))
    if missing:
        available = deps._object_list(metadata.get("available_symbols"))
        raise RuntimeError(
            f"{context}行情完整性不足：股票池共 {len(available) + len(missing)} 只，仅返回 {len(available)} 只；缺少 {', '.join(str(item) for item in missing[:8])}。已停止本次运行，避免用部分股票数据得出错误结论。"
        )
