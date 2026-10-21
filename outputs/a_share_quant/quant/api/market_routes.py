"""Read-only Tushare market, universe and financial API routes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as wall_time
from pathlib import Path

from quant.api.contracts import (
    FetchTushareAdjustmentHealthCallback,
    FetchTushareFundamentalFactorsCallback,
    FetchTushareIndexMembersCallback,
    FetchTushareIndicesCallback,
    FetchTushareRealtimeIndicesCallback,
    FetchTushareStatementsCallback,
    FlattenStatementRowsCallback,
    GetTushareAShareUniverseCallback,
    GetTushareStockMetadataCallback,
    SearchTushareStocksCallback,
)
from quant.time_utils import SHANGHAI_TZ

MARKET_GET_PATHS = frozenset(
    {
        "/api/data/adjustment-health",
        "/api/stocks/search",
        "/api/config",
        "/api/fundamentals",
        "/api/financials",
        "/api/financial-statements",
        "/api/index/universe",
        "/api/stocks/universe",
        "/api/market/indices",
        "/api/market/ticker",
    }
)


@dataclass(frozen=True)
class MarketRouteDependencies:
    token: Callable[[], str | None]
    cache_dir: Path
    default_symbols: Sequence[str]
    default_universe_note: str
    index_universe_names: Mapping[str, str]
    max_research_symbols: int
    bounded_int: Callable[[object, int, int], int]
    optional_date: Callable[[object], date | None]
    symbols: Callable[[object], list[str]]
    normalize_symbol: Callable[[object], str]
    search_stocks: SearchTushareStocksCallback
    adjustment_health: FetchTushareAdjustmentHealthCallback
    stock_metadata: GetTushareStockMetadataCallback
    fundamental_factors: FetchTushareFundamentalFactorsCallback
    financial_statements: FetchTushareStatementsCallback
    flatten_statements: FlattenStatementRowsCallback
    index_members: FetchTushareIndexMembersCallback
    a_share_universe: GetTushareAShareUniverseCallback
    indices: FetchTushareIndicesCallback
    realtime_indices: FetchTushareRealtimeIndicesCallback


def _arg(query: Mapping[str, list[str]], key: str, default: str = "") -> str:
    return query.get(key, [default])[0]


def _market_phase(now: datetime) -> tuple[str, bool]:
    current_time = now.time()
    morning = (
        wall_time(9, 30)
        <= current_time
        <= wall_time(11, 30)
    )
    afternoon = (
        wall_time(13, 0)
        <= current_time
        <= wall_time(15, 0)
    )
    session_open = now.weekday() < 5 and (morning or afternoon)
    if now.weekday() >= 5:
        return "休市", False
    if session_open:
        return "交易中", True
    if (
        wall_time(11, 30)
        < current_time
        < wall_time(13, 0)
    ):
        return "午间休市", False
    if current_time > wall_time(15, 0):
        return "已收盘", False
    return "非交易时段", False


def _parse_quote_time(value: object) -> datetime:
    """Parse provider quote time; naive Tushare values are Shanghai-local."""
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def handle_market_get(
    path: str,
    query: Mapping[str, list[str]],
    deps: MarketRouteDependencies,
) -> tuple[int, dict[str, object]]:
    """Return the original HTTP status and payload for one market route."""
    if path not in MARKET_GET_PATHS:
        raise ValueError(f"unsupported market route: {path}")
    try:
        return 200, _market_payload(path, query, deps)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return 400, {"ok": False, "error": str(error)}
    except RuntimeError as error:
        code = (
            "realtime_market_unavailable"
            if path == "/api/market/ticker"
            else "data_provider_unavailable"
        )
        return 502, {"ok": False, "code": code, "error": str(error)}


def _market_payload(
    path: str, query: Mapping[str, list[str]], deps: MarketRouteDependencies
) -> dict[str, object]:
    token = deps.token()
    cache_dir = deps.cache_dir
    if path == "/api/data/adjustment-health":
        symbols = [
            item.strip()
            for item in _arg(query, "symbols").replace("，", ",").split(",")
            if item.strip()
        ]
        if not symbols:
            symbols = list(deps.default_symbols)
        if len(symbols) > 24:
            raise ValueError("symbols cannot contain more than 24 items")
        health = deps.adjustment_health(
            symbols,
            token=token,
            start=deps.optional_date(_arg(query, "start_date")),
            end=deps.optional_date(_arg(query, "end_date")),
        )
        return {"ok": True, "adjustment": health}

    if path == "/api/stocks/search":
        search = _arg(query, "q").strip()[:80]
        limit = deps.bounded_int(_arg(query, "limit", "12"), 1, 50)
        results, match_count, stale = deps.search_stocks(
            search, token=token, cache_dir=cache_dir, limit=limit
        )
        return {
            "ok": True,
            "query": search,
            "results": results,
            "match_count": match_count,
            "universe_source": "tushare:stock_basic",
            "stale": stale,
        }

    if path == "/api/config":
        metadata, stale = deps.stock_metadata(
            list(deps.default_symbols), token=token, cache_dir=str(cache_dir)
        )
        seed_universe: list[dict[str, object]] = []
        for symbol in deps.default_symbols:
            normalized = deps.normalize_symbol(symbol)
            row = metadata.get(normalized) or metadata.get(symbol) or {}
            seed_universe.append(
                {
                    "symbol": normalized.split(".", 1)[0],
                    "ts_code": normalized,
                    "name": row.get("name", ""),
                    "industry": row.get("industry", ""),
                    "market": row.get("market", ""),
                }
            )
        return {
            "ok": True,
            "source": "tushare:stock_basic",
            "generated_at": datetime.now(UTC).isoformat(),
            "stale": stale,
            "seed_universe": seed_universe,
            "index_universes": [
                {"ts_code": code, "name": name}
                for code, name in deps.index_universe_names.items()
            ],
            "data_sources": [
                {"key": "tushare", "label": "Tushare Pro · 未复权 A股日线"}
            ],
            "universe_note": deps.default_universe_note,
            "max_research_symbols": deps.max_research_symbols,
        }

    if path == "/api/fundamentals":
        raw_symbols = _arg(query, "symbols", ",".join(deps.default_symbols))
        symbols = deps.symbols(raw_symbols.split(","))
        rows, metadata = deps.fundamental_factors(
            symbols,
            as_of=deps.optional_date(_arg(query, "as_of")),
            token=token,
            cache_dir=cache_dir,
            force_refresh=True,
        )
        return {"ok": True, "rows": rows, "metadata": metadata}

    if path in {"/api/financials", "/api/financial-statements"}:
        raw_symbols = _arg(query, "symbols", ",".join(deps.default_symbols[:3]))
        symbols = deps.symbols(raw_symbols.split(","))
        statement = _arg(query, "statement", "all").strip().lower()
        grouped, metadata = deps.financial_statements(
            symbols,
            as_of=deps.optional_date(_arg(query, "as_of")),
            statement=statement,
            token=token,
            cache_dir=cache_dir,
            force_refresh=True,
        )
        return {
            "ok": True,
            "statements": grouped,
            "rows": deps.flatten_statements(grouped),
            "metadata": metadata,
        }

    if path == "/api/index/universe":
        index_code = _arg(query, "index_code", "000300.SH").strip().upper()
        return deps.index_members(
            index_code,
            as_of=deps.optional_date(_arg(query, "as_of")),
            token=token,
            cache_dir=cache_dir,
        )

    if path == "/api/stocks/universe":
        mode = _arg(query, "mode", "seed").strip().lower()
        if mode == "all":
            offset = deps.bounded_int(_arg(query, "offset", "0"), 0, 100_000)
            limit = deps.bounded_int(_arg(query, "limit", "100"), 1, 200)
            rows, total, stale = deps.a_share_universe(
                token=token, cache_dir=str(cache_dir), offset=offset, limit=limit
            )
            return {
                "ok": True,
                "mode": "all",
                "results": rows,
                "universe": rows,
                "count": len(rows),
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(rows) < total,
                "source": "tushare:stock_basic",
                "note": "全市场 A 股候选列表；回测和纸面交易仍需选择有限股票池，避免逐只拉取行情触发限频。",
                "stale": stale,
            }
        if mode != "seed":
            raise ValueError("mode must be seed or all")
        limit = deps.bounded_int(
            _arg(query, "limit", str(len(deps.default_symbols))), 1, 24
        )
        selected = deps.default_symbols[:limit]
        metadata, stale = deps.stock_metadata(
            list(selected), token=token, cache_dir=str(cache_dir)
        )
        rows = []
        for symbol in selected:
            normalized = deps.normalize_symbol(symbol)
            row = metadata.get(normalized) or metadata.get(symbol) or {}
            rows.append(
                {
                    "symbol": normalized,
                    "code": normalized.split(".", 1)[0],
                    "name": row.get("name", ""),
                    "industry": row.get("industry", ""),
                    "market": row.get("market", ""),
                }
            )
        return {
            "ok": True,
            "symbols": [row["symbol"] for row in rows],
            "universe": rows,
            "count": len(rows),
            "source": "curated_tushare_seed",
            "note": deps.default_universe_note,
            "stale": stale,
        }

    if path == "/api/market/indices":
        indices, errors = deps.indices(
            token=token, cache_dir=cache_dir, force_refresh=True
        )
        latest_trade_date = max(item["trade_date"] for item in indices)
        return {
            "ok": True,
            "source": "tushare",
            "indices": indices,
            "errors": errors,
            "latest_trade_date": latest_trade_date,
        }

    indices = deps.realtime_indices(token=token)
    now = datetime.now(SHANGHAI_TZ)
    market_phase, session_open = _market_phase(now)
    if session_open:
        fresh_indices = []
        for item in indices:
            try:
                quote_time = _parse_quote_time(item["trade_time"])
            except ValueError:
                continue
            age_seconds = (now - quote_time).total_seconds()
            if quote_time.date() == now.date() and -60 <= age_seconds <= 180:
                fresh_indices.append(item)
        indices = fresh_indices
        if not indices:
            raise RuntimeError(
                "实时指数行情日期落后或超过 3 分钟未更新，已停止显示旧行情"
            )
    latest_trade_time = max(str(item["trade_time"]) for item in indices)
    return {
        "ok": True,
        "source": "tushare_rt_idx_k",
        "is_live": True,
        "market_phase": market_phase,
        "latest_trade_time": latest_trade_time,
        "indices": indices,
    }
