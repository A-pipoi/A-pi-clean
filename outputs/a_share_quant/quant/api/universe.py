"""Point-in-time universe resolution and per-session filtering."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypedDict

from quant.errors import InputTypeError, ProviderShapeError

from ..data.filters import UniverseRules, filter_universe
from ..data.schema import Bar
from ..data.universe import HistoricalUniverse
from .parsing import MAX_RESEARCH_SYMBOLS
from .parsing import symbols as parse_symbols

CHINA_TIMEZONE = timezone(timedelta(hours=8))


class ResolvedBacktestUniverse(TypedDict, total=False):
    """Validated, auditable universe metadata used by research routes."""

    mode: str
    symbols: list[str]
    version: str
    metadata: dict[str, object]
    pit_data: dict[str, Any] | None
    pit_metadata: dict[str, Any] | None
    historical_universe: HistoricalUniverse
    listing_dates: dict[str, date]
    delisting_dates: dict[str, date]
    start_date: date | None
    end_date: date | None


FetchMembershipHistory = Callable[..., Mapping[str, Any]]
FetchListingDates = Callable[..., tuple[dict[str, date], dict[str, Any]]]


def resolve_backtest_universe(
    payload: Mapping[str, Any],
    start_date: date | None,
    end_date: date | None,
    *,
    force_membership_refresh: bool = False,
    default_symbols: list[str],
    index_code: str,
    index_name: str,
    min_listing_days: int,
    max_symbols: int = MAX_RESEARCH_SYMBOLS,
    token: str,
    cache_dir: str,
    fetch_membership: FetchMembershipHistory,
    fetch_listing_dates: FetchListingDates,
) -> ResolvedBacktestUniverse:
    """Resolve a reproducible fixed or historical-index stock universe."""

    mode = str(payload.get("universe_mode", "fixed")).strip().lower()
    if mode in {"fixed", "custom", "seed"}:
        selected_symbols = parse_symbols(payload.get("symbols", default_symbols))
        return {
            "mode": "fixed",
            "symbols": selected_symbols,
            "version": "",
            "metadata": {
                "mode": "fixed",
                "selection": "user_or_seed_list",
                "pit_membership": False,
                "candidate_symbol_count": len(selected_symbols),
                "note": "固定/自选股票池不是历史指数成分；可能存在前视选择偏差。",
            },
            "pit_data": None,
            "pit_metadata": None,
            "start_date": start_date,
            "end_date": end_date,
        }
    if mode not in {"pit_index", "pit_hs300"}:
        raise ValueError("universe_mode must be 'pit_index' or 'fixed'")

    requested_index = (
        str(payload.get("universe_index_code") or index_code).strip().upper()
    )
    if requested_index != index_code:
        raise ValueError(f"主回测 PIT 宇宙固定为 {index_code}，不能事后切换指数")
    effective_start = start_date or date(2024, 1, 2)
    effective_end = end_date or datetime.now(CHINA_TIMEZONE).date()
    if effective_start > effective_end:
        raise ValueError("PIT 宇宙的开始日期不能晚于结束日期")
    membership = fetch_membership(
        index_code,
        start_date=effective_start,
        end_date=effective_end,
        token=token,
        cache_dir=cache_dir,
        cache_ttl_seconds=1 if force_membership_refresh else 24 * 60 * 60,
    )
    if membership.get("stale"):
        raise RuntimeError("指数历史成分缓存已过期，不能用于严格 PIT 回测")
    historical_universe = HistoricalUniverse.from_index_weight_history(membership)
    selected_symbols = sorted(
        historical_universe.symbols_for_range(effective_start, effective_end)
    )
    if not selected_symbols:
        raise RuntimeError("沪深300历史成分在所选回测区间内为空")
    if len(selected_symbols) > max_symbols:
        raise ValueError(
            f"该区间需要 {len(selected_symbols)} 只历史成分股，超过当前 {max_symbols} 只安全上限；"
            "请缩短区间，不能静默截断样本"
        )
    listing_dates, listing_metadata = fetch_listing_dates(
        selected_symbols,
        token=token,
        cache_dir=cache_dir,
        force_refresh=force_membership_refresh,
    )
    if listing_metadata.get("stale"):
        raise RuntimeError(
            "沪深300历史 PIT 所需的上市/退市日期缓存过期；刷新 stock_basic 后再运行"
        )
    raw_delisting_dates = listing_metadata.get("delisting_dates")
    if not isinstance(raw_delisting_dates, Mapping):
        raise ProviderShapeError(
            "严格 PIT 回测缺少 stock_basic.delist_date，无法排除退市后仍残留于指数快照的证券"
        )
    delisting_dates: dict[str, date] = {}
    for symbol, raw_day in raw_delisting_dates.items():
        try:
            delisting_dates[str(symbol).strip().upper()] = (
                raw_day
                if isinstance(raw_day, date)
                else date.fromisoformat(str(raw_day).strip()[:10])
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"stock_basic.delist_date 格式无效：{symbol}") from error
    missing_listing_dates = [
        symbol for symbol in selected_symbols if symbol not in listing_dates
    ]
    if missing_listing_dates:
        raise RuntimeError(
            "历史成分缺少 stock_basic.list_date，已停止以避免前视/上市年龄误判："
            + ", ".join(missing_listing_dates[:8])
        )
    membership_symbols = list(selected_symbols)
    delisted_before_window = {
        symbol: day for symbol, day in delisting_dates.items() if day < effective_start
    }
    selected_symbols = [
        symbol for symbol in selected_symbols if symbol not in delisted_before_window
    ]
    if not selected_symbols:
        raise RuntimeError("PIT 区间内没有仍可交易的沪深300历史成分")

    universe_version = hashlib.sha256(
        json.dumps(
            {
                "membership": membership.get("data_version"),
                "listing_dates": listing_metadata.get("data_version"),
                "delisting_dates": {
                    symbol: day.isoformat()
                    for symbol, day in sorted(delisting_dates.items())
                },
                "min_listing_days": min_listing_days,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    summary: dict[str, object] = {
        "mode": "pit_historical_index",
        "index_code": index_code,
        "index_name": membership.get("index_name", index_name),
        "selection": "membership_snapshot_as_of_each_trading_date",
        "pit_membership": True,
        "membership_effective_date_basis": "index_weight.trade_date",
        "membership_publication_timestamp_available": False,
        "requested_start": effective_start.isoformat(),
        "requested_end": effective_end.isoformat(),
        "snapshot_start": membership.get("first_snapshot"),
        "snapshot_end": membership.get("last_snapshot"),
        "snapshot_count": membership.get("snapshot_count"),
        "member_data_version": membership.get("data_version"),
        "listing_data_version": listing_metadata.get("data_version"),
        "listing_dates_stale": False,
        "delisting_dates_version": listing_metadata.get("data_version"),
        "delisting_dates_stale": False,
        "min_listing_days": min_listing_days,
        "candidate_symbol_count": len(selected_symbols),
        "historical_membership_symbol_count": len(membership_symbols),
        "delisted_before_window_count": len(delisted_before_window),
        "delisted_before_window_symbols": sorted(delisted_before_window),
        "version": universe_version,
    }
    pit_data: dict[str, Any] = {
        "schema_version": "backtest-historical-universe-v1",
        "index_code": index_code,
        "snapshots": membership.get("snapshots", []),
        "listing_dates": {
            symbol: day.isoformat() for symbol, day in sorted(listing_dates.items())
        },
        "delisting_dates": {
            symbol: day.isoformat() for symbol, day in sorted(delisting_dates.items())
        },
    }
    pit_metadata: dict[str, Any] = {"research_universe": summary}
    return {
        "mode": "pit_index",
        "symbols": selected_symbols,
        "version": universe_version,
        "metadata": summary,
        "pit_data": pit_data,
        "pit_metadata": pit_metadata,
        "historical_universe": historical_universe,
        "listing_dates": listing_dates,
        "delisting_dates": delisting_dates,
        "start_date": effective_start,
        "end_date": effective_end,
    }


def filter_backtest_universe(
    bars: list[Bar], universe: ResolvedBacktestUniverse, *, min_listing_days: int
) -> list[Bar]:
    historical = universe.get("historical_universe")
    if not isinstance(historical, HistoricalUniverse):
        return bars
    listing_dates = universe.get("listing_dates")
    if not isinstance(listing_dates, dict):
        raise InputTypeError("严格 PIT 回测缺少上市日期映射")
    delisting_dates = universe.get("delisting_dates")
    return filter_universe(
        bars,
        None,
        UniverseRules(
            min_listing_days=min_listing_days,
            exclude_st=False,
            exclude_paused=False,
            exclude_delisted=False,
        ),
        listing_dates=listing_dates,
        delisting_dates=delisting_dates if isinstance(delisting_dates, dict) else None,
        historical_universe=historical,
    )
