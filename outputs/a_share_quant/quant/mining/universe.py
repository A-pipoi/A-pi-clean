"""Fixed, auditable PIT universe rules for factor-mining research."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from quant.errors import InputTypeError

from ..data.filters import UniverseRules, filter_universe
from ..data.schema import Bar
from ..data.universe import (
    FACTOR_MINING_MIN_LISTING_DAYS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    FACTOR_MINING_UNIVERSE_NAME,
    HistoricalUniverse,
)
from ..numeric import as_int

FACTOR_MINING_UNIVERSE_VERSION = "hs300-monthly-pit-v1"


def factor_mining_universe_payload(
    membership_history: Mapping[str, object],
    listing_dates: Mapping[str, date],
    listing_metadata: Mapping[str, object],
    symbols: list[str],
    included_symbols: set[str],
    start_date: date,
    end_date: date,
) -> dict[str, object]:
    """Build the immutable metadata snapshot attached to a sealed PIT dataset."""

    snapshots = membership_history.get("snapshots")
    if not isinstance(snapshots, list):
        raise InputTypeError("沪深300历史成员快照格式无效")
    return {
        "schema_version": FACTOR_MINING_UNIVERSE_VERSION,
        "index_code": FACTOR_MINING_UNIVERSE_INDEX_CODE,
        "index_name": FACTOR_MINING_UNIVERSE_NAME,
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "snapshot_start": membership_history.get("first_snapshot"),
        "snapshot_end": membership_history.get("last_snapshot"),
        "snapshot_count": as_int(membership_history.get("snapshot_count") or len(snapshots)),
        "member_data_version": str(membership_history.get("data_version") or ""),
        "listing_data_version": str(listing_metadata.get("data_version") or ""),
        "listing_data_stale": bool(listing_metadata.get("stale")),
        "listing_source": dict(listing_metadata),
        "min_listing_days": FACTOR_MINING_MIN_LISTING_DAYS,
        "candidate_symbol_count": len(symbols),
        "sample_symbol_count": len(included_symbols),
        "symbols": list(symbols),
        "snapshots": snapshots,
        "listing_dates": {symbol: listing_dates[symbol].isoformat() for symbol in sorted(listing_dates)},
    }


def apply_factor_mining_universe(
    bars: list[Bar], universe_payload: Mapping[str, object]
) -> list[Bar]:
    """Filter each date to that date's historical index constituents and listing age."""

    if universe_payload.get("schema_version") != FACTOR_MINING_UNIVERSE_VERSION:
        raise PermissionError("因子挖掘快照的研究宇宙版本不匹配，已阻止复用")
    if str(universe_payload.get("index_code") or "").upper() != FACTOR_MINING_UNIVERSE_INDEX_CODE:
        raise PermissionError("因子挖掘只能使用预注册的沪深300历史成分宇宙")
    raw_listing_dates = universe_payload.get("listing_dates")
    if not isinstance(raw_listing_dates, Mapping):
        raise PermissionError("研究宇宙快照缺少上市日期，无法验证60日上市年龄规则")
    listing_dates = {
        str(symbol).upper(): str(raw_day).strip()
        for symbol, raw_day in raw_listing_dates.items()
        if str(raw_day or "").strip()
    }
    history = HistoricalUniverse.from_index_weight_history(universe_payload)
    return filter_universe(
        bars,
        None,
        UniverseRules(
            min_listing_days=FACTOR_MINING_MIN_LISTING_DAYS,
            exclude_st=False,
            exclude_paused=False,
            exclude_delisted=False,
        ),
        listing_dates=listing_dates,
        historical_universe=history,
    )


def validate_factor_mining_universe_request(payload: Mapping[str, object]) -> None:
    """Reject any attempt to vary the pre-registered research universe."""

    requested_index = payload.get("universe_index_code", payload.get("index_code"))
    if requested_index is not None and str(requested_index).strip().upper() != FACTOR_MINING_UNIVERSE_INDEX_CODE:
        raise ValueError(
            f"因子挖掘宇宙已预注册为 {FACTOR_MINING_UNIVERSE_INDEX_CODE}；"
            "不能切换指数或比较多个宇宙后择优"
        )
    if any(key in payload for key in ("symbols", "universe", "research_universe")):
        raise ValueError("因子挖掘不接受自选标的或自选研究宇宙；固定使用沪深300历史成分")
