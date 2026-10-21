from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import logging
import math
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded, limited_tushare_call
from quant.errors import ProviderShapeError
from quant.time_utils import china_today

from .raw_incremental import load_history, save_history
from .schema import Bar, DataSnapshot

LOGGER = logging.getLogger(__name__)

_AKSHARE_BAR_CACHE: dict[tuple[str, str, str, str], tuple[Bar, ...]] = {}
_AKSHARE_CACHE_LOCK = threading.RLock()
_TUSHARE_BAR_CACHE: dict[tuple[str, str, str, str], tuple[Bar, ...]] = {}
_TUSHARE_CACHE_LOCK = threading.RLock()
_TUSHARE_LIMIT_CACHE: dict[tuple[str, str, str], tuple[float, dict[date, tuple[float | None, float | None]]]] = {}
_TUSHARE_LIMIT_CACHE_LOCK = threading.RLock()
_TUSHARE_LIMIT_ERROR_CACHE: tuple[float, str] | None = None
_TUSHARE_STOCK_BASIC_CACHE: tuple[float, list[dict[str, str]]] | None = None
_TUSHARE_STOCK_BASIC_LOCK = threading.RLock()
_TUSHARE_INDEX_CACHE: dict[str, tuple[float, dict[str, str | float]]] = {}
_TUSHARE_INDEX_CACHE_LOCK = threading.RLock()
_TUSHARE_BREADTH_CACHE: dict[str, tuple[float, dict[str, str | float | int]]] = {}
_TUSHARE_BREADTH_CACHE_LOCK = threading.RLock()
TUSHARE_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
}
TUSHARE_BENCHMARK_NAMES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
}
TUSHARE_REALTIME_INDEX_NAMES = {
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000905.SH": "中证500",
    "000016.SH": "上证50",
    "000010.SH": "上证180",
    "000698.SH": "科创100",
    "000852.SH": "中证1000",
    "000903.SH": "中证100",
    "000906.SH": "中证800",
    "000922.CSI": "中证红利",
    "399005.SZ": "中小100",
    "399102.SZ": "创业板综",
    "399330.SZ": "深证100",
    "399673.SZ": "创业板50",
    "399303.SZ": "国证2000",
    "399975.SZ": "证券公司",
    "399986.SZ": "中证银行",
}


class MarketDataAdapter(ABC):
    """唯一的数据访问边界；策略和模型不应直接依赖供应商 SDK。"""

    @abstractmethod
    def snapshot(self) -> DataSnapshot:
        raise NotImplementedError

    @abstractmethod
    def daily_bars(self, start: date | None = None, end: date | None = None) -> list[Bar]:
        raise NotImplementedError


class CsvDailyAdapter(MarketDataAdapter):
    """读取标准字段 CSV；生产环境可在此接口旁注册 AkShare/Tushare/Wind 适配器。"""

    def __init__(self, path: str | Path, source: str = "csv") -> None:
        self.path = Path(path)
        self._snapshot = DataSnapshot(
            version=f"{self.path.name}:{self.path.stat().st_mtime_ns}",
            source=source,
            created_at=datetime.now(UTC),
            metadata={"path": str(self.path)},
        )

    def snapshot(self) -> DataSnapshot:
        return self._snapshot

    def daily_bars(self, start: date | None = None, end: date | None = None) -> list[Bar]:
        bars: list[Bar] = []
        with self.path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                day = date.fromisoformat(row["date"])
                if start and day < start:
                    continue
                if end and day > end:
                    continue
                bars.append(
                    Bar(
                        symbol=row["symbol"],
                        date=day,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        limit_up=_optional_float(row.get("limit_up")),
                        limit_down=_optional_float(row.get("limit_down")),
                        paused=_as_bool(row.get("paused")),
                        listed=not _as_bool(row.get("not_listed")),
                        is_st=_as_bool(row.get("is_st")),
                        industry=row.get("industry") or None,
                        is_delisted=_as_bool(row.get("is_delisted")),
                    )
                )
        return sorted(bars, key=lambda bar: (bar.date, bar.symbol))


class TushareDailyAdapter(MarketDataAdapter):
    """Tushare Pro daily A-share adapter with the internal ``Bar`` schema.

    The token is intentionally supplied by the caller (normally the
    ``TUSHARE_TOKEN`` environment variable) and is never written to cache or
    response metadata. ``pro_bar`` provides raw, qfq, and hfq daily bars;
    ``stk_limit`` can be queried when available so the event-driven engine can
    retain its limit-up/limit-down rules. The A-pi API can deliberately disable
    that optional query when the account does not have the interface permission.
    """

    def __init__(
        self,
        symbols: Iterable[str],
        token: str | None = None,
        adjust: str = "hfq",
        timeout: float = 30.0,
        cache_dir: str | Path | None = None,
        cache_ttl_seconds: int = 24 * 60 * 60,
        retry_count: int = 3,
        retry_backoff_seconds: float = 0.8,
        fetch_limits: bool = True,
        force_refresh: bool = False,
        resume_completed_refresh: bool = False,
        signal_adjust: str = "raw",
    ) -> None:
        normalized = tuple(_normalize_tushare_symbol(symbol) for symbol in symbols)
        if not normalized:
            raise ValueError("symbols cannot be empty")
        if adjust not in {"", "qfq", "hfq"}:
            raise ValueError("adjust must be one of '', 'qfq', or 'hfq'")
        if signal_adjust not in {"raw", "adj"}:
            raise ValueError("signal_adjust must be 'raw' or 'adj'")
        if signal_adjust == "adj" and adjust:
            raise ValueError("signal_adjust='adj' requires raw execution bars (adjust='')")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        if retry_count < 1 or retry_count > 5:
            raise ValueError("retry_count must be between 1 and 5")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        self.symbols = normalized
        self.token = token or os.getenv("TUSHARE_TOKEN", "").strip()
        self.adjust = adjust
        self.signal_adjust = signal_adjust
        self.timeout = timeout
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self.cache_ttl_seconds = cache_ttl_seconds
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds
        self.fetch_limits = fetch_limits
        self.force_refresh = bool(force_refresh)
        self.resume_completed_refresh = bool(resume_completed_refresh)
        self._snapshot = DataSnapshot(
            version=f"tushare:pro_bar:{adjust or 'raw'}:pending",
            source="tushare",
            created_at=datetime.now(UTC),
            metadata={
                "symbols": list(normalized),
                "adjust": adjust,
                "endpoint": "pro_bar",
                "adjustment_source": "tushare:pro_bar(adj=hfq)" if adjust == "hfq" else "tushare:pro_bar(adj=qfq)" if adjust == "qfq" else "raw_daily",
                "adjustment_factor_endpoint": "adj_factor" if adjust else None,
                "adjustment_coverage_status": "provider_adjusted" if adjust else "raw_prices",
                "signal_adjust": signal_adjust,
                "signal_adjustment_source": "tushare:adj_factor" if signal_adjust == "adj" else None,
                "signal_adjustment_coverage_status": "pending" if signal_adjust == "adj" else "not_requested",
                "cache_dir": str(self.cache_dir) if self.cache_dir else None,
                "token_configured": bool(self.token),
            },
        )

    @staticmethod
    def is_available() -> bool:
        try:
            return importlib.util.find_spec("tushare") is not None
        except (ImportError, ValueError):
            return False

    @staticmethod
    def token_configured() -> bool:
        return bool(os.getenv("TUSHARE_TOKEN", "").strip())

    def snapshot(self) -> DataSnapshot:
        return self._snapshot

    def daily_bars(self, start: date | None = None, end: date | None = None) -> list[Bar]:
        if start and end and start > end:
            raise ValueError("start cannot be later than end")
        if not self.token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，请先设置 Tushare Pro Token")
        tushare, pro = _load_tushare(self.token)
        end_date = end or china_today()
        start_date = start or (end_date - timedelta(days=365 * 5))
        bars: list[Bar] = []
        errors: list[str] = []
        cache_hits = 0
        cache_misses = 0
        live_fetches = 0
        incremental_fetches = 0
        historical_bars_reused = 0
        resumed_symbols: list[str] = []
        limits_requested = self.fetch_limits and self.adjust == ""
        limits_available = limits_requested
        limit_data_status = (
            "pending"
            if limits_requested
            else "incompatible_adjustment"
            if self.fetch_limits
            else "not_requested"
        )
        limit_data_error: str | None = None
        available_symbols: set[str] = set()
        data_errors: list[str] = []

        def fetch_limits_for(code: str) -> dict[date, tuple[float | None, float | None]]:
            nonlocal limits_available, limit_data_status, limit_data_error
            try:
                limits = _fetch_tushare_limits(
                    pro,
                    code,
                    start_date,
                    end_date,
                    self.retry_count,
                    self.retry_backoff_seconds,
                    self.token,
                    self.force_refresh,
                )
                if not limits:
                    limits_available = False
                    limit_data_status = "empty"
                else:
                    limit_data_status = "available"
                return limits
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
                limits_available = False
                limit_data_status = "unavailable"
                limit_data_error = type(error).__name__
                errors.append(f"{code} 涨跌停价: {type(error).__name__}")
                return {}

        from .progress import report_data_progress
        for index, code in enumerate(self.symbols):
            report_data_progress(index, len(self.symbols), code)
            cache_key = (code, start_date.isoformat(), end_date.isoformat(), self.adjust)
            if self.force_refresh and self.resume_completed_refresh and not self.adjust and self.fetch_limits:
                from .raw_incremental import load_completed_refresh
                resumed = load_completed_refresh(self.cache_dir, code, start_date, end_date)
                if resumed is not None:
                    bars.extend(resumed)
                    available_symbols.add(code)
                    resumed_symbols.append(code)
                    cache_hits += 1
                    continue
            cached = None if self.force_refresh else _tushare_memory_cache_get(cache_key)
            if cached is None and not self.force_refresh:
                cached = _disk_cache_get(
                    self.cache_dir,
                    cache_key,
                    ttl_seconds=self.cache_ttl_seconds,
                    prefix="tushare",
                )
            if cached is not None:
                if limits_requested:
                    limits = fetch_limits_for(code)
                    if limits:
                        cached = tuple(
                            replace(
                                bar,
                                limit_up=limits.get(bar.date, (None, None))[0],
                                limit_down=limits.get(bar.date, (None, None))[1],
                            )
                            for bar in cached
                        )
                        _tushare_memory_cache_put(cache_key, cached)
                        _disk_cache_put(self.cache_dir, cache_key, cached, prefix="tushare")
                cache_hits += 1
                available_symbols.add(code)
                bars.extend(cached)
                continue
            cache_misses += 1
            try:
                history = (
                    load_history(self.cache_dir, code, start_date, end_date)
                    if self.force_refresh and not self.adjust else None
                )
                fetch_start = history[1] if history else start_date
                frame = _fetch_tushare_frame(
                    tushare,
                    pro,
                    code,
                    fetch_start,
                    end_date,
                    self.adjust,
                    self.token,
                    self.retry_count,
                    self.retry_backoff_seconds,
                )
                limits = {}
                if limits_requested:
                    limits = fetch_limits_for(code)
                converted = tuple(_tushare_frame_to_bars(frame, code, limits))
                converted = tuple(bar for bar in converted
                                  if bar.symbol == code and fetch_start <= bar.date <= end_date)
                if history and not converted:
                    raise RuntimeError("增量刷新未返回有效尾部行情；禁止回退历史缓存")
                if converted:
                    # Replace the entire overlap, including rows absent from the
                    # new response. Historical limit values are never trusted.
                    prefix = history[0] if history else ()
                    converted = tuple(sorted(
                        {bar.date: bar for bar in (*prefix, *converted)}.values(),
                        key=lambda bar: bar.date,
                    ))
                    if limits_requested:
                        converted = tuple(replace(
                            bar, limit_up=limits.get(bar.date, (None, None))[0],
                            limit_down=limits.get(bar.date, (None, None))[1],
                        ) for bar in converted)
                        if any(bar.limit_up is None or bar.limit_down is None
                               or not 0 < bar.limit_down <= bar.limit_up for bar in converted):
                            limits_available = False
                            if limit_data_status == "available":
                                limit_data_status = "partial"
                    if history:
                        incremental_fetches += 1
                        historical_bars_reused += len(prefix)
                    if self.force_refresh and not self.adjust:
                        save_history(self.cache_dir, code, start_date, end_date, converted,
                                     history[2] if history else time.time())
                if converted:
                    available_symbols.add(code)
                    live_fetches += 1
                    _tushare_memory_cache_put(cache_key, converted)
                    _disk_cache_put(self.cache_dir, cache_key, converted, prefix="tushare")
                bars.extend(converted)
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
                message = f"{code}: {type(error).__name__}"
                errors.append(message)
                data_errors.append(message)
            if code != self.symbols[-1]:
                time.sleep(0.25)
        bars = [bar for bar in bars if (not start or bar.date >= start) and (not end or bar.date <= end)]
        if not bars:
            detail = f"；{'; '.join(errors[:3])}" if errors else ""
            raise RuntimeError(f"Tushare 未返回可用历史行情{detail}")
        adjustment_digest = None
        if self.signal_adjust == "adj":
            bars, adjustment_digest = _apply_tushare_signal_adjustment(
                bars, self.token
            )
        if limits_requested and not limits_available and limit_data_status == "available":
            limit_data_status = "partial"
        elif limits_requested and limits_available and limit_data_status == "pending" and resumed_symbols:
            limit_data_status = "available"
        content = json.dumps([_bar_to_json(bar) for bar in bars], ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        latest_trade_date = max(bar.date for bar in bars)
        report_data_progress(len(self.symbols), len(self.symbols), "")
        latest_by_symbol = {
            code: max(bar.date for bar in bars if bar.symbol == code)
            for code in available_symbols
        }
        proven_suspensions: list[str] = []
        proven_delistings: dict[str, str] = {}
        if self.force_refresh and self.resume_completed_refresh and not self.adjust:
            lagging = {code for code, day in latest_by_symbol.items() if day < end_date}
            if lagging:
                suspension_rows = _tushare_http_query(
                    self.token, "suspend_d", {"trade_date": end_date.strftime("%Y%m%d")},
                    fields="ts_code,trade_date,suspend_type,suspend_timing",
                ).to_dict(orient="records")
                proven_suspensions = sorted({str(row.get("ts_code")) for row in suspension_rows
                    if str(row.get("ts_code")) in lagging
                    and _as_date(row.get("trade_date")) == end_date
                    and str(row.get("suspend_type")) == "S"
                    and not str(row.get("suspend_timing") or "").strip()})
                # A delisted constituent can remain in the last published PIT
                # membership. Keep its history/membership intact, but do not
                # demand a new exchange bar after its verified delisting date.
                # Never infer delisting from missing bars or an empty response.
                for code in sorted(lagging - set(proven_suspensions)):
                    rows = _tushare_http_query(
                        self.token, "stock_basic", {"ts_code": code, "list_status": "D"},
                        fields="ts_code,list_status,delist_date",
                    ).to_dict(orient="records")
                    if len(rows) != 1:
                        continue
                    row = rows[0]
                    try:
                        delisted_on = _as_date(row.get("delist_date"))
                    except (ValueError, TypeError):
                        continue
                    if (delisted_on is not None
                            and row.get("ts_code") == code and row.get("list_status") == "D"
                            and latest_by_symbol[code] < delisted_on <= end_date):
                        proven_delistings[code] = delisted_on.isoformat()
        if self.force_refresh:
            # One current symbol cannot make another stale symbol look fresh.
            tradable_dates = [day for code, day in latest_by_symbol.items()
                              if code not in proven_suspensions and code not in proven_delistings]
            latest_trade_date = min(tradable_dates) if tradable_dates else min(latest_by_symbol.values())
        data_source = (
            "tushare_verified_close_resume"
            if resumed_symbols and cache_hits == len(resumed_symbols)
            else "tushare_live"
            if live_fetches and not cache_hits
            else "tushare_mixed"
            if live_fetches and cache_hits
            else "tushare_cache"
        )
        self._snapshot = DataSnapshot(
            version=f"tushare:pro_bar:{self.adjust or 'raw'}:{digest}"
            + (f":signal-adj:{adjustment_digest}" if adjustment_digest else ""),
            source="tushare",
            created_at=datetime.now(UTC),
            metadata={
                "symbols": list(self.symbols),
                "adjust": self.adjust,
                "signal_adjust": self.signal_adjust,
                "signal_adjustment_source": "tushare:adj_factor" if self.signal_adjust == "adj" else None,
                "signal_adjustment_coverage_status": "complete" if self.signal_adjust == "adj" else "not_requested",
                "endpoint": "pro_bar",
                "adjustment_source": "tushare:pro_bar(adj=hfq)" if self.adjust == "hfq" else "tushare:pro_bar(adj=qfq)" if self.adjust == "qfq" else "raw_daily",
                "adjustment_factor_endpoint": "adj_factor" if self.adjust else None,
                "adjustment_coverage_status": "provider_adjusted" if self.adjust else "raw_prices",
                "cache_dir": str(self.cache_dir) if self.cache_dir else None,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "live_fetches": live_fetches,
                "incremental_fetches": incremental_fetches,
                "historical_bars_reused": historical_bars_reused,
                "resumed_completed_symbols": resumed_symbols,
                "resume_max_age_seconds": 600 if self.resume_completed_refresh else None,
                "closing_resume_verified": bool(resumed_symbols and cache_hits == len(resumed_symbols)),
                "proven_full_day_suspensions": proven_suspensions,
                "proven_delistings": proven_delistings,
                "delisting_proof_source": "tushare:stock_basic(list_status=D)" if proven_delistings else None,
                "suspension_proof_source": "tushare:suspend_d" if proven_suspensions else None,
                "bar_count": len(bars),
                "available_symbols": sorted(available_symbols),
                "missing_symbols": sorted(set(self.symbols) - available_symbols),
                "coverage_ratio": len(available_symbols) / len(self.symbols),
                "data_errors": data_errors[:20],
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "limits_available": limits_available,
                "limit_data_status": limit_data_status,
                "limit_data_error": limit_data_error,
                "data_source": data_source,
                "latest_trade_date": latest_trade_date.isoformat(),
                "latest_trade_dates": {code: day.isoformat() for code, day in latest_by_symbol.items()},
                "cache_used": cache_hits > 0 or historical_bars_reused > 0,
            },
        )
        return sorted(bars, key=lambda bar: (bar.date, bar.symbol))


def search_tushare_stocks(
    query: str = "",
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    limit: int = 12,
    cache_ttl_seconds: int = 24 * 60 * 60,
) -> tuple[list[dict[str, str]], int, bool]:
    """Search the cached Tushare stock universe by code, name, or pinyin."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")

    rows, stale = _load_tushare_stock_basic(
        token=str(token or os.getenv("TUSHARE_TOKEN") or "").strip(),
        cache_dir=Path(cache_dir).expanduser() if cache_dir else None,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    normalized_query = str(query).strip().lower()
    if not normalized_query:
        return rows[:limit], len(rows), stale

    def score(row: dict[str, str]) -> tuple[int, str, str]:
        code = row.get("symbol", "").lower()
        ts_code = row.get("ts_code", "").lower()
        name = row.get("name", "").lower()
        pinyin = row.get("pinyin", "").lower()
        industry = row.get("industry", "").lower()
        if normalized_query in {code, ts_code, name}:
            rank = 0
        elif code.startswith(normalized_query) or ts_code.startswith(normalized_query):
            rank = 1
        elif name.startswith(normalized_query) or pinyin.startswith(normalized_query):
            rank = 2
        elif normalized_query in name or normalized_query in pinyin:
            rank = 3
        elif normalized_query in industry:
            rank = 4
        else:
            rank = 99
        return rank, code, name

    matched = [row for row in rows if score(row)[0] < 99]
    matched.sort(key=score)
    return matched[:limit], len(matched), stale


def get_tushare_a_share_universe(
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    offset: int = 0,
    limit: int = 100,
    cache_ttl_seconds: int = 24 * 60 * 60,
) -> tuple[list[dict[str, str]], int, bool]:
    """Return a paginated list of active A-share equities from stock_basic.

    This endpoint describes the complete candidate universe only. It does not
    fetch historical bars for every symbol; research jobs still need an
    explicit, bounded selection or a bulk data warehouse.
    """
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    rows, stale = _load_tushare_stock_basic(
        token=str(token or os.getenv("TUSHARE_TOKEN") or "").strip(),
        cache_dir=Path(cache_dir).expanduser() if cache_dir else None,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    valid_markets = {"主板", "创业板", "科创板", "北交所"}
    universe = [row for row in rows if row.get("market") in valid_markets]
    return universe[offset : offset + limit], len(universe), stale


def get_tushare_stock_metadata(
    symbols: list[str],
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
) -> tuple[dict[str, dict[str, str]], bool]:
    """Return point-in-time-independent identity metadata for selected symbols.

    This uses the same cached ``stock_basic`` universe as the search endpoint;
    it does not query current prices or alter the research sample.
    """
    rows, stale = _load_tushare_stock_basic(
        token=str(token or os.getenv("TUSHARE_TOKEN") or "").strip(),
        cache_dir=Path(cache_dir).expanduser() if cache_dir else None,
        cache_ttl_seconds=24 * 60 * 60,
    )
    wanted = {str(symbol).strip().upper() for symbol in symbols}
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        ts_code = row.get("ts_code", "").upper()
        symbol = row.get("symbol", "").upper()
        if ts_code in wanted or symbol in wanted:
            result[ts_code] = row
            result[symbol] = row
    return result, stale


def fetch_tushare_listing_dates(
    symbols: Iterable[str],
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 30 * 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[dict[str, date], dict[str, Any]]:
    """Load static listing dates for active, paused, and delisted equities.

    ``list_date`` is immutable identity metadata and is PIT-safe. Querying all
    three stock_basic statuses keeps constituents that later delisted from
    disappearing out of a historical index universe.
    """

    requested = tuple(dict.fromkeys(_normalize_tushare_symbol(item) for item in symbols))
    if not requested:
        raise ValueError("symbols cannot be empty")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    cache_path = Path(cache_dir).expanduser() / "tushare_listing_dates.json" if cache_dir else None
    cached_dates: dict[str, date] = {}
    cached_delisting_dates: dict[str, date] = {}
    cache_has_delisting_dates = False
    cached_at = 0.0
    if cache_path and cache_path.is_file():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("fetched_at", 0))
            raw_dates = payload.get("listing_dates", {})
            if isinstance(raw_dates, dict):
                for code, raw_day in raw_dates.items():
                    try:
                        cached_dates[str(code).upper()] = date.fromisoformat(str(raw_day)[:10])
                    except (TypeError, ValueError):
                        continue
            raw_delisting_dates = payload.get("delisting_dates")
            cache_has_delisting_dates = isinstance(raw_delisting_dates, dict)
            if cache_has_delisting_dates:
                for code, raw_day in raw_delisting_dates.items():
                    try:
                        cached_delisting_dates[str(code).upper()] = date.fromisoformat(str(raw_day)[:10])
                    except (TypeError, ValueError):
                        continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            cached_dates = {}
            cached_delisting_dates = {}
            cache_has_delisting_dates = False
    # Old listing-only caches cannot establish PIT eligibility after delisting.
    cache_fresh = (
        bool(cached_dates)
        and cache_has_delisting_dates
        and time.time() - cached_at <= cache_ttl_seconds
    )
    if cache_fresh and not force_refresh and all(symbol in cached_dates for symbol in requested):
        return {symbol: cached_dates[symbol] for symbol in requested}, {
            "source": "tushare:stock_basic",
            "statuses": ["L", "D", "P"],
            "cache_used": True,
            "stale": False,
            "delisting_dates": _select_optional_dates(requested, cached_delisting_dates),
            "data_version": _listing_dates_version(cached_dates, cached_delisting_dates),
        }

    access_token = str(token or os.getenv("TUSHARE_TOKEN", "")).strip()
    if not access_token:
        if cached_dates and cache_has_delisting_dates:
            return _select_listing_dates(requested, cached_dates), {
                "source": "tushare:stock_basic",
                "statuses": ["L", "D", "P"],
                "cache_used": True,
                "stale": True,
                "delisting_dates": _select_optional_dates(requested, cached_delisting_dates),
                "data_version": _listing_dates_version(cached_dates, cached_delisting_dates),
            }
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法读取 stock_basic.list_date/delist_date")

    listing_dates: dict[str, date] = {}
    delisting_dates: dict[str, date] = {}
    for status in ("L", "D", "P"):
        frame = _tushare_http_query(
            access_token,
            "stock_basic",
            {"exchange": "", "list_status": status},
            fields="ts_code,list_date,delist_date",
        )
        rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
        for row in rows:
            code = str(row.get("ts_code") or "").strip().upper()
            raw_day = str(row.get("list_date") or "").strip()
            if not code or not raw_day:
                continue
            try:
                normalized = _normalize_tushare_symbol(code)
                listing_dates[normalized] = date.fromisoformat(raw_day[:10])
                raw_delist_day = str(row.get("delist_date") or "").strip()
                if raw_delist_day:
                    delisting_dates[normalized] = date.fromisoformat(raw_delist_day[:10])
            except (TypeError, ValueError):
                continue
    missing = [symbol for symbol in requested if symbol not in listing_dates]
    if missing:
        raise RuntimeError(
            "stock_basic.list_date 缺失，无法严格执行历史上市年龄过滤："
            + ", ".join(missing[:8])
        )
    fetched_at = time.time()
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "fetched_at": fetched_at,
                        "listing_dates": {code: day.isoformat() for code, day in sorted(listing_dates.items())},
                        "delisting_dates": {code: day.isoformat() for code, day in sorted(delisting_dates.items())},
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError:
            pass
    return _select_listing_dates(requested, listing_dates), {
        "source": "tushare:stock_basic",
        "statuses": ["L", "D", "P"],
        "cache_used": False,
        "stale": False,
        "delisting_dates": _select_optional_dates(requested, delisting_dates),
        "data_version": _listing_dates_version(listing_dates, delisting_dates),
    }


def _select_listing_dates(symbols: Iterable[str], values: dict[str, date]) -> dict[str, date]:
    missing = [symbol for symbol in symbols if symbol not in values]
    if missing:
        raise RuntimeError(
            "stock_basic.list_date 缺失，无法严格执行历史上市年龄过滤："
            + ", ".join(missing[:8])
        )
    return {symbol: values[symbol] for symbol in symbols}


def _select_optional_dates(symbols: Iterable[str], values: dict[str, date]) -> dict[str, date]:
    return {symbol: values[symbol] for symbol in symbols if symbol in values}


def _listing_dates_version(
    values: dict[str, date], delisting_dates: dict[str, date] | None = None
) -> str:
    content = json.dumps(
        {
            "listing_dates": {key: day.isoformat() for key, day in sorted(values.items())},
            "delisting_dates": {
                key: day.isoformat() for key, day in sorted((delisting_dates or {}).items())
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]


def fetch_tushare_index(
    ts_code: str = "000001.SH",
    *,
    token: str | None = None,
    end: date | None = None,
    lookback_days: int = 45,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 60 * 60,
    force_refresh: bool = False,
) -> dict[str, str | float]:
    """Return the latest daily bar for a Tushare index.

    ``index_daily`` reports ``pct_chg`` as percentage points (for example,
    ``0.42`` means +0.42%), so the API keeps that unit explicit as
    ``change_pct`` for the frontend.
    """
    normalized_code = str(ts_code).strip().upper()
    if normalized_code not in TUSHARE_INDEX_NAMES:
        raise ValueError(f"不支持的指数代码：{normalized_code}")
    if lookback_days < 5 or lookback_days > 365:
        raise ValueError("lookback_days must be between 5 and 365")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载指数行情")

    cache_key = normalized_code
    now = time.time()
    if not force_refresh:
        with _TUSHARE_INDEX_CACHE_LOCK:
            cached = _TUSHARE_INDEX_CACHE.get(cache_key)
            if cached and now - cached[0] < cache_ttl_seconds:
                return cached[1]

    cache_path = Path(cache_dir).expanduser() / f"tushare_index_{normalized_code.replace('.', '_')}.json" if cache_dir else None
    if not force_refresh and cache_path and cache_path.is_file():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at", 0)) if isinstance(payload, dict) else 0.0
            disk_index = payload.get("index") if isinstance(payload, dict) else None
            if isinstance(disk_index, dict) and now - fetched_at < cache_ttl_seconds:
                result = {str(key): value for key, value in disk_index.items() if key in {"ts_code", "trade_date", "close", "pre_close", "change_pct"}}
                if {"ts_code", "trade_date", "close", "change_pct"}.issubset(result):
                    with _TUSHARE_INDEX_CACHE_LOCK:
                        _TUSHARE_INDEX_CACHE[cache_key] = (fetched_at, result)
                    return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    end_date = end or china_today()
    start_date = end_date - timedelta(days=lookback_days)
    try:
        frame = _tushare_http_query(
            access_token,
            "index_daily",
            {
                "ts_code": normalized_code,
                "start_date": start_date.strftime("%Y%m%d"),
                "end_date": end_date.strftime("%Y%m%d"),
            },
            fields="ts_code,trade_date,close,pre_close,pct_chg",
        )
    except Exception as error:
        if isinstance(error, RuntimeError):
            raise
        raise RuntimeError(f"Tushare 指数行情加载失败: {error}") from error
    rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
    parsed: list[dict[str, str | float]] = []
    for row in rows:
        trade_date = _as_date(row.get("trade_date"))
        close = _finite_float(row.get("close"))
        pre_close = _finite_float(row.get("pre_close"))
        pct_chg = _finite_float(row.get("pct_chg"))
        if trade_date is None or close is None or close <= 0:
            continue
        if pct_chg is None and pre_close is not None and pre_close != 0:
            pct_chg = (close / pre_close - 1) * 100
        parsed.append(
            {
                "ts_code": normalized_code,
                "trade_date": trade_date.isoformat(),
                "close": close,
                "pre_close": pre_close if pre_close is not None else 0.0,
                "change_pct": pct_chg if pct_chg is not None else 0.0,
            }
        )
    if not parsed:
        raise RuntimeError(f"Tushare 未返回{TUSHARE_INDEX_NAMES[normalized_code]}可用行情")
    result = max(parsed, key=lambda item: str(item["trade_date"]))
    with _TUSHARE_INDEX_CACHE_LOCK:
        _TUSHARE_INDEX_CACHE[cache_key] = (now, result)
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"fetched_at": now, "index": result}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError:
            pass
    return result


def fetch_tushare_indices(
    ts_codes: Iterable[str] | None = None,
    *,
    token: str | None = None,
    end: date | None = None,
    lookback_days: int = 45,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 60 * 60,
    force_refresh: bool = False,
) -> tuple[list[dict[str, str | float]], list[dict[str, str]]]:
    """Fetch several major indices with one date-scoped ``index_daily`` request.

    Tushare applies a strict request-frequency limit to ``index_daily``.  A
    single date-scoped request is therefore important here: current Tushare
    parameter rules use ``trade_date`` to fetch the market-wide rows, which
    are then filtered locally.  Cached symbols are returned without a network
    request and failures are reported per symbol.
    """
    if lookback_days < 5 or lookback_days > 365:
        raise ValueError("lookback_days must be between 5 and 365")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载指数行情")

    requested = list(ts_codes or TUSHARE_INDEX_NAMES)
    results: list[dict[str, str | float]] = []
    errors: list[dict[str, str]] = []
    pending: list[str] = []
    stale_cached: dict[str, dict[str, str | float]] = {}
    seen: set[str] = set()
    now = time.time()
    for code in requested:
        normalized = str(code).strip().upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized not in TUSHARE_INDEX_NAMES:
            errors.append(
                {
                    "ts_code": normalized,
                    "name": TUSHARE_INDEX_NAMES.get(normalized, normalized),
                    "error": f"不支持的指数代码：{normalized}",
                }
            )

        cache_path = (
            Path(cache_dir).expanduser() / f"tushare_index_{normalized.replace('.', '_')}.json"
            if cache_dir
            else None
        )
        cached: dict[str, str | float] | None = None
        with _TUSHARE_INDEX_CACHE_LOCK:
            memory = _TUSHARE_INDEX_CACHE.get(normalized)
            if not force_refresh and memory and now - memory[0] < cache_ttl_seconds:
                cached = memory[1]
        if cached is None and not force_refresh and cache_path and cache_path.is_file():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                fetched_at = float(payload.get("fetched_at", 0)) if isinstance(payload, dict) else 0.0
                disk_index = payload.get("index") if isinstance(payload, dict) else None
                if isinstance(disk_index, dict) and now - fetched_at < cache_ttl_seconds:
                    candidate = {
                        str(key): value
                        for key, value in disk_index.items()
                        if key in {"ts_code", "trade_date", "close", "pre_close", "change_pct"}
                    }
                    if {"ts_code", "trade_date", "close", "change_pct"}.issubset(candidate):
                        cached = candidate
                        with _TUSHARE_INDEX_CACHE_LOCK:
                            _TUSHARE_INDEX_CACHE[normalized] = (fetched_at, candidate)
                elif isinstance(disk_index, dict):
                    candidate = {
                        str(key): value
                        for key, value in disk_index.items()
                        if key in {"ts_code", "trade_date", "close", "pre_close", "change_pct"}
                    }
                    if {"ts_code", "trade_date", "close", "change_pct"}.issubset(candidate):
                        stale_cached[normalized] = candidate
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                cached = None
        if cached is not None:
            cached["source"] = "cache"
            cached["stale"] = False
            results.append(cached)
        elif normalized in TUSHARE_INDEX_NAMES:
            pending.append(normalized)

    if pending:
        known_trade_dates = [
            str(item["trade_date"])
            for item in results
            if item.get("trade_date")
        ]
        query_day = end or china_today()
        while query_day.weekday() >= 5:
            query_day -= timedelta(days=1)
        if known_trade_dates:
            query_trade_date = max(known_trade_dates).replace("-", "")
        else:
            query_trade_date = query_day.strftime("%Y%m%d")
        try:
            frame = _tushare_http_query(
                access_token,
                "index_daily",
                {
                    "ts_code": "",
                    "trade_date": query_trade_date,
                },
                fields="ts_code,trade_date,close,pre_close,pct_chg",
            )
            rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
            if not rows and not known_trade_dates:
                # Before the market opens, the Shanghai-local current date is not yet an
                # available A-share trade date.  Resolve the previous open
                # date through Tushare's calendar, then make one normal
                # index_daily request for that date.  This keeps the provider
                # live and avoids confusing an expected empty current-day
                # response with a broken token or disconnected service.
                calendar_frame = _tushare_http_query(
                    access_token,
                    "trade_cal",
                    {
                        "exchange": "SSE",
                        "start_date": (query_day - timedelta(days=lookback_days)).strftime("%Y%m%d"),
                        "end_date": query_trade_date,
                        "is_open": "1",
                    },
                    fields="cal_date,is_open",
                )
                calendar_rows = (
                    []
                    if calendar_frame is None or getattr(calendar_frame, "empty", False)
                    else calendar_frame.to_dict(orient="records")
                )
                previous_open_dates = sorted(
                    {
                        str(row.get("cal_date"))
                        for row in calendar_rows
                        if str(row.get("is_open")) == "1"
                        and str(row.get("cal_date")) < query_trade_date
                    }
                )
                if previous_open_dates:
                    fallback_frame = _tushare_http_query(
                        access_token,
                        "index_daily",
                        {
                            "ts_code": "",
                            "trade_date": previous_open_dates[-1],
                        },
                        fields="ts_code,trade_date,close,pre_close,pct_chg",
                    )
                    rows = (
                        []
                        if fallback_frame is None or getattr(fallback_frame, "empty", False)
                        else fallback_frame.to_dict(orient="records")
                    )
            rows_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in pending}
            for row in rows:
                row_code = str(row.get("ts_code") or "").strip().upper()
                if row_code in rows_by_code:
                    rows_by_code[row_code].append(row)
            for code in pending:
                parsed: list[dict[str, str | float]] = []
                for row in rows_by_code[code]:
                    trade_date = _as_date(row.get("trade_date"))
                    close = _finite_float(row.get("close"))
                    pre_close = _finite_float(row.get("pre_close"))
                    pct_chg = _finite_float(row.get("pct_chg"))
                    if trade_date is None or close is None or close <= 0:
                        continue
                    if pct_chg is None and pre_close is not None and pre_close != 0:
                        pct_chg = (close / pre_close - 1) * 100
                    parsed.append(
                        {
                            "ts_code": code,
                            "trade_date": trade_date.isoformat(),
                            "close": close,
                            "pre_close": pre_close if pre_close is not None else 0.0,
                            "change_pct": pct_chg if pct_chg is not None else 0.0,
                        }
                    )
                if not parsed:
                    stale = stale_cached.get(code)
                    if stale is not None and not force_refresh:
                        stale["source"] = "cache"
                        stale["stale"] = True
                        results.append(stale)
                        errors.append(
                            {
                                "ts_code": code,
                                "name": TUSHARE_INDEX_NAMES[code],
                                "error": f"Tushare 未返回新行情；已展示缓存数据（{stale['trade_date']}）",
                            }
                        )
                    else:
                        errors.append(
                            {
                                "ts_code": code,
                                "name": TUSHARE_INDEX_NAMES[code],
                                "error": f"Tushare 未返回{TUSHARE_INDEX_NAMES[code]}可用行情",
                            }
                        )
                    continue
                result = max(parsed, key=lambda item: str(item["trade_date"]))
                result["source"] = "tushare"
                result["stale"] = False
                results.append(result)
                with _TUSHARE_INDEX_CACHE_LOCK:
                    _TUSHARE_INDEX_CACHE[code] = (now, result)
                if cache_dir:
                    cache_path = Path(cache_dir).expanduser() / f"tushare_index_{code.replace('.', '_')}.json"
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
                        temporary.write_text(
                            json.dumps({"fetched_at": now, "index": result}, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        temporary.replace(cache_path)
                    except OSError:
                        pass
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
            message = type(error).__name__
            for code in pending:
                stale = stale_cached.get(code)
                if stale is not None and not force_refresh:
                    stale["source"] = "cache"
                    stale["stale"] = True
                    results.append(stale)
                    errors.append(
                        {
                            "ts_code": code,
                            "name": TUSHARE_INDEX_NAMES[code],
                            "error": f"{message}；已展示缓存数据（{stale['trade_date']}）",
                        }
                    )
                else:
                    errors.append(
                        {
                            "ts_code": code,
                            "name": TUSHARE_INDEX_NAMES[code],
                            "error": message,
                        }
                    )

        # Some Tushare accounts/providers reject the market-wide index_daily
        # query (trade_date without ts_code). Recover only missing indexes with
        # the supported per-index query, and preserve a clear live source label.
        returned_codes = {str(item.get("ts_code", "")).upper() for item in results}
        for code in pending:
            if code in returned_codes:
                continue
            try:
                item = fetch_tushare_index(
                    code,
                    token=access_token,
                    end=end or china_today(),
                    lookback_days=lookback_days,
                    cache_dir=cache_dir,
                    cache_ttl_seconds=cache_ttl_seconds,
                    force_refresh=force_refresh,
                )
            except ResourceLimitExceeded:
                raise
            except (RuntimeError, TypeError, ValueError) as error:
                LOGGER.exception("Single-index fallback failed: %s", type(error).__name__, exc_info=None)
                message = type(error).__name__
                existing = next((entry for entry in errors if entry.get("ts_code") == code), None)
                if existing is None:
                    errors.append({"ts_code": code, "name": TUSHARE_INDEX_NAMES[code], "error": message})
                else:
                    existing["error"] = f"{existing['error']}；单指数回退失败：{message}"
                continue
            item["source"] = "tushare"
            item["fallback"] = True
            item["stale"] = False
            results.append(item)
            errors = [entry for entry in errors if entry.get("ts_code") != code]
            returned_codes.add(code)
    if not results:
        detail = "；".join(item["error"] for item in errors[:3]) or "无可用指数数据"
        raise RuntimeError(f"Tushare 多指数行情不可用：{detail}")
    return results, errors


def fetch_tushare_index_history(
    ts_code: str = "000300.SH",
    *,
    token: str | None = None,
    start: date | None = None,
    end: date | None = None,
    cache_dir: str | Path | None = None,
    force_refresh: bool = True,
) -> dict[str, object]:
    """Fetch a real benchmark history for excess-return reporting.

    This endpoint is deliberately separate from the dashboard's latest-index
    snapshot: benchmark comparisons need the same date range as the backtest,
    and the response states whether the rows came from Tushare or a cache.
    """

    normalized = str(ts_code).strip().upper()
    if normalized not in TUSHARE_BENCHMARK_NAMES:
        raise ValueError(f"不支持的基准指数：{normalized}")
    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载基准指数")
    end_date = end or china_today()
    start_date = start or (end_date - timedelta(days=365 * 5))
    if start_date > end_date:
        raise ValueError("benchmark start cannot be later than end")
    cache_path = (
        Path(cache_dir).expanduser()
        / f"tushare_benchmark_{normalized.replace('.', '_')}_{start_date}_{end_date}.json"
        if cache_dir
        else None
    )
    if not force_refresh and cache_path and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and isinstance(cached.get("rows"), list) and cached["rows"]:
                return {**cached, "source": "cache", "stale": False}
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    frame = _tushare_http_query(
        access_token,
        "index_daily",
        {
            "ts_code": normalized,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d"),
        },
        fields="ts_code,trade_date,close,pct_chg",
    )
    rows: list[dict[str, object]] = []
    raw_rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
    for row in raw_rows:
        trade_date = _as_date(row.get("trade_date"))
        close = _finite_float(row.get("close"))
        pct_chg = _finite_float(row.get("pct_chg"))
        if trade_date is None or close is None or close <= 0:
            continue
        rows.append(
            {
                "date": trade_date.isoformat(),
                "close": close,
                "change_pct": pct_chg,
            }
        )
    rows.sort(key=lambda item: str(item["date"]))
    if not rows:
        raise RuntimeError(f"Tushare 未返回{TUSHARE_BENCHMARK_NAMES[normalized]}基准行情")
    result: dict[str, object] = {
        "ts_code": normalized,
        "name": TUSHARE_BENCHMARK_NAMES[normalized],
        "start_date": rows[0]["date"],
        "end_date": rows[-1]["date"],
        "rows": rows,
        "source": "tushare",
        "stale": False,
    }
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return result


def fetch_tushare_adjustment_health(
    symbols: Iterable[str],
    *,
    token: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, object]:
    """Check explicit ``adj_factor`` coverage for adjusted-price research."""

    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法检查复权因子")
    normalized_symbols = list(dict.fromkeys(_normalize_tushare_symbol(item) for item in symbols))
    if not normalized_symbols:
        raise ValueError("symbols cannot be empty")
    end_date = end or china_today()
    start_date = start or (end_date - timedelta(days=365 * 5))
    if start_date > end_date:
        raise ValueError("adjustment start cannot be later than end")
    rows: list[dict[str, object]] = []
    for symbol in normalized_symbols:
        try:
            frame = _tushare_http_query(
                access_token,
                "adj_factor",
                {
                    "ts_code": symbol,
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                },
                fields="ts_code,trade_date,adj_factor",
            )
            raw_rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
            valid_dates = sorted(
                {
                    day.isoformat()
                    for item in raw_rows
                    if (day := _as_date(item.get("trade_date"))) is not None
                    and _finite_float(item.get("adj_factor")) is not None
                }
            )
            rows.append(
                {
                    "ts_code": symbol,
                    "available": bool(valid_dates),
                    "rows": len(valid_dates),
                    "first_date": valid_dates[0] if valid_dates else None,
                    "last_date": valid_dates[-1] if valid_dates else None,
                    "source": "tushare:adj_factor",
                }
            )
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            LOGGER.exception("Adjustment health query failed: %s", type(error).__name__, exc_info=None)
            rows.append(
                {
                    "ts_code": symbol,
                    "available": False,
                    "rows": 0,
                    "first_date": None,
                    "last_date": None,
                    "source": "tushare:adj_factor",
                    "error": type(error).__name__,
                }
            )
        if symbol != normalized_symbols[-1]:
            time.sleep(0.25)
    available = sum(1 for item in rows if item["available"])
    return {
        "source": "tushare:adj_factor",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": rows,
        "coverage_ratio": available / len(rows),
        "status": "available" if available == len(rows) else "partial" if available else "unavailable",
    }


def fetch_tushare_market_breadth(
    trade_date: date,
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 60 * 60,
    force_refresh: bool = False,
) -> dict[str, str | float | int]:
    """Summarize the A-share market from Tushare's real daily endpoint."""
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载市场宽度")
    cache_key = trade_date.isoformat()
    now = time.time()
    with _TUSHARE_BREADTH_CACHE_LOCK:
        cached = _TUSHARE_BREADTH_CACHE.get(cache_key)
        if not force_refresh and cached and now - cached[0] < cache_ttl_seconds:
            return cached[1]

    cache_path = Path(cache_dir).expanduser() / f"tushare_market_breadth_{cache_key}.json" if cache_dir else None
    if not force_refresh and cache_path and cache_path.is_file():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at", 0)) if isinstance(payload, dict) else 0.0
            breadth = payload.get("breadth") if isinstance(payload, dict) else None
            if isinstance(breadth, dict) and now - fetched_at < cache_ttl_seconds:
                result = {str(key): value for key, value in breadth.items()}
                with _TUSHARE_BREADTH_CACHE_LOCK:
                    _TUSHARE_BREADTH_CACHE[cache_key] = (fetched_at, result)
                return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    try:
        frame = _tushare_http_query(
            access_token,
            "daily",
            {"trade_date": trade_date.strftime("%Y%m%d")},
            fields="ts_code,trade_date,close,pre_close,pct_chg,amount",
        )
    except Exception as error:
        if isinstance(error, RuntimeError):
            raise
        raise RuntimeError(f"Tushare 市场宽度加载失败: {error}") from error
    rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
    up = down = flat = 0
    turnover = 0.0
    observed = 0
    for row in rows:
        close = _finite_float(row.get("close"))
        pre_close = _finite_float(row.get("pre_close"))
        pct_chg = _finite_float(row.get("pct_chg"))
        amount = _finite_float(row.get("amount"))
        if pct_chg is None and close is not None and pre_close is not None and pre_close != 0:
            pct_chg = (close / pre_close - 1) * 100
        if pct_chg is None:
            continue
        observed += 1
        if pct_chg > 0:
            up += 1
        elif pct_chg < 0:
            down += 1
        else:
            flat += 1
        if amount is not None and amount > 0:
            turnover += amount
    if not observed:
        raise RuntimeError(f"Tushare 未返回 {trade_date.isoformat()} 的市场日线")
    breadth_result: dict[str, str | float | int] = {
        "trade_date": trade_date.isoformat(),
        "up": up,
        "down": down,
        "flat": flat,
        "stocks": observed,
        "turnover_billion": turnover / 100_000,
    }
    with _TUSHARE_BREADTH_CACHE_LOCK:
        _TUSHARE_BREADTH_CACHE[cache_key] = (now, breadth_result)
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"fetched_at": now, "breadth": breadth_result}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError:
            pass
    return breadth_result


def _load_tushare_stock_basic(
    *,
    token: str,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
) -> tuple[list[dict[str, str]], bool]:
    global _TUSHARE_STOCK_BASIC_CACHE
    now = time.time()
    with _TUSHARE_STOCK_BASIC_LOCK:
        if _TUSHARE_STOCK_BASIC_CACHE and now - _TUSHARE_STOCK_BASIC_CACHE[0] < cache_ttl_seconds:
            return _TUSHARE_STOCK_BASIC_CACHE[1], False

    cache_path = cache_dir / "tushare_stock_basic.json" if cache_dir else None
    cached_rows: list[dict[str, str]] | None = None
    cached_at = 0.0
    if cache_path and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            cached_rows = payload.get("rows") if isinstance(payload, dict) else None
            cached_at = float(payload.get("fetched_at", 0)) if isinstance(payload, dict) else 0.0
            if not isinstance(cached_rows, list):
                cached_rows = None
        except (OSError, ValueError, TypeError):
            cached_rows = None

    if cached_rows and now - cached_at < cache_ttl_seconds:
        rows = _normalize_stock_basic_rows(cached_rows)
        with _TUSHARE_STOCK_BASIC_LOCK:
            _TUSHARE_STOCK_BASIC_CACHE = (now, rows)
        return rows, False

    if not token:
        if cached_rows:
            rows = _normalize_stock_basic_rows(cached_rows)
            with _TUSHARE_STOCK_BASIC_LOCK:
                _TUSHARE_STOCK_BASIC_CACHE = (now, rows)
            return rows, True
        raise RuntimeError("Tushare Token 未配置，无法加载全市场股票列表")

    try:
        response = _tushare_http_query(
            token,
            "stock_basic",
            {"exchange": "", "list_status": "L"},
            fields="ts_code,symbol,name,area,industry,market,exchange,list_date,cnspell",
        )
        rows = _normalize_stock_basic_rows(response.to_dict(orient="records"))
        if not rows:
            raise RuntimeError("Tushare 未返回可用的股票列表")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"fetched_at": now, "rows": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        with _TUSHARE_STOCK_BASIC_LOCK:
            _TUSHARE_STOCK_BASIC_CACHE = (now, rows)
        return rows, False
    except Exception as error:
        if cached_rows:
            rows = _normalize_stock_basic_rows(cached_rows)
            with _TUSHARE_STOCK_BASIC_LOCK:
                _TUSHARE_STOCK_BASIC_CACHE = (now, rows)
            return rows, True
        if isinstance(error, RuntimeError):
            raise
        raise RuntimeError(f"Tushare 全市场股票列表加载失败: {error}") from error


def _normalize_stock_basic_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        ts_code = str(row.get("ts_code") or "").strip().upper()
        symbol = str(row.get("symbol") or "").strip()
        name = str(row.get("name") or "").strip()
        if not ts_code or not symbol or not name:
            continue
        normalized.append(
            {
                "ts_code": ts_code,
                "symbol": symbol,
                "name": name,
                "pinyin": str(row.get("cnspell") or "").strip().lower(),
                "area": str(row.get("area") or "").strip(),
                "industry": str(row.get("industry") or "").strip(),
                "market": str(row.get("market") or "").strip(),
                "exchange": str(row.get("exchange") or "").strip(),
                "list_date": str(row.get("list_date") or "").strip(),
            }
        )
    return sorted(normalized, key=lambda item: (item["symbol"], item["name"]))


def _load_tushare(token: str) -> tuple[Any, Any]:
    try:
        import tushare as ts
    except ImportError as error:
        raise RuntimeError("未安装 Tushare，请执行 python -m pip install -r requirements-tushare.txt") from error
    try:
        pro = ts.pro_api(token)
        original_query = pro.query
        def quota_query(*args: Any, **kwargs: Any) -> Any:
            return limited_tushare_call(original_query, *args, **kwargs)
        pro.query = quota_query
        return ts, pro
    except Exception as error:
        raise RuntimeError(f"Tushare Pro 初始化失败: {error}") from error


def _fetch_tushare_frame(
    tushare: Any,
    pro: Any,
    code: str,
    start_date: date,
    end_date: date,
    adjust: str,
    token: str,
    retry_count: int,
    retry_backoff_seconds: float,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(retry_count):
        try:
            if not adjust:
                # The official HTTP API is more reliable in restricted
                # networks than the SDK's /daily path and returns unadjusted
                # daily bars directly.
                return _tushare_http_query(
                    token,
                    "daily",
                    {
                        "ts_code": code,
                        "start_date": start_date.strftime("%Y%m%d"),
                        "end_date": end_date.strftime("%Y%m%d"),
                    },
                    fields="ts_code,trade_date,open,high,low,close,vol",
                )
            return tushare.pro_bar(
                ts_code=code,
                api=pro,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                freq="D",
                asset="E",
                adj=adjust or None,
                retry_count=retry_count,
            )
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
            last_error = error
            if attempt + 1 < retry_count and retry_backoff_seconds:
                time.sleep(retry_backoff_seconds * (2**attempt))
    assert last_error is not None
    raise last_error


class _TushareRows:
    empty = False

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.empty = not rows

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("only records orientation is supported")
        return self.rows


def _tushare_http_query(
    token: str,
    api_name: str,
    params: dict[str, Any],
    *,
    fields: str = "",
) -> _TushareRows:
    from .provider_endpoints import provider_endpoint
    api_name = provider_endpoint(api_name)
    try:
        import requests
    except ImportError as error:
        raise RuntimeError("未安装 requests，无法访问 Tushare HTTP API") from error
    endpoint = os.getenv("A_PI_TUSHARE_API_URL", "https://api.tushare.pro").strip() or "https://api.tushare.pro"
    timeout = max(5.0, float(os.getenv("A_PI_TUSHARE_HTTP_TIMEOUT_SECONDS", "30")))
    attempts = min(5, max(1, int(os.getenv("A_PI_TUSHARE_HTTP_RETRIES", "2"))))
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(attempts):
        try:
            response = limited_tushare_call(requests.post,
                endpoint,
                json={"api_name": api_name, "token": token, "params": params, "fields": fields},
                headers={"User-Agent": "A-pi/1.0 Tushare client"},
                timeout=(5.0, timeout),
            )
            response.raise_for_status()
            candidate = response.json()
            if not isinstance(candidate, dict):
                raise ProviderShapeError("Tushare returned a non-object JSON payload")
            payload = candidate
            break
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(0.8 * (2**attempt))
    if payload is None:
        raise RuntimeError(f"Tushare HTTPS request failed after {attempts} attempts: {last_error}") from last_error
    code = payload.get("code", -1)
    if code != 0:
        message = payload.get("msg") or payload.get("detail") or "unknown Tushare error"
        raise RuntimeError(f"Tushare {api_name} 接口错误 {code}: {message}")
    data = payload.get("data") or {}
    columns = data.get("fields") or []
    return _TushareRows([dict(zip(columns, row)) for row in data.get("items", [])])


def fetch_tushare_realtime_indices(token: str | None = None) -> list[dict[str, str | float]]:
    """Fetch the latest live exchange-index snapshot without cache fallback."""
    access_token = str(token or os.getenv("TUSHARE_TOKEN") or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载实时指数行情")

    codes = list(TUSHARE_REALTIME_INDEX_NAMES)
    frame = _tushare_http_query(
        access_token,
        "rt_idx_k",
        {"ts_code": ",".join(codes)},
        fields="ts_code,trade_time,close,pre_close,pct_chg",
    )
    rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
    results: list[dict[str, str | float]] = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("ts_code") or "").strip().upper()
        if code not in TUSHARE_REALTIME_INDEX_NAMES or code in seen:
            continue
        close = _finite_float(row.get("close"))
        pre_close = _finite_float(row.get("pre_close"))
        trade_time = str(row.get("trade_time") or row.get("time") or "").strip()
        if close is None or close <= 0 or pre_close is None or pre_close <= 0 or not trade_time:
            continue
        seen.add(code)
        results.append(
            {
                "ts_code": code,
                "name": TUSHARE_REALTIME_INDEX_NAMES[code],
                "trade_time": trade_time,
                "close": close,
                "pre_close": pre_close,
                "change_pct": (close / pre_close - 1) * 100,
                "source": "tushare_rt_idx_k",
            }
        )
    if not results:
        raise RuntimeError("Tushare 实时指数接口未返回有效行情；请检查 rt_idx_k 权限或稍后重试")
    return results


def _fetch_tushare_limits(
    pro: Any,
    code: str,
    start_date: date,
    end_date: date,
    retry_count: int,
    retry_backoff_seconds: float,
    token: str,
    force_refresh: bool = False,
) -> dict[date, tuple[float | None, float | None]]:
    global _TUSHARE_LIMIT_ERROR_CACHE
    cache_key = (code, start_date.isoformat(), end_date.isoformat())
    now = time.time()
    with _TUSHARE_LIMIT_CACHE_LOCK:
        if not force_refresh and _TUSHARE_LIMIT_ERROR_CACHE and now - _TUSHARE_LIMIT_ERROR_CACHE[0] < 15 * 60:
            raise RuntimeError(_TUSHARE_LIMIT_ERROR_CACHE[1])
        cached = _TUSHARE_LIMIT_CACHE.get(cache_key)
        if not force_refresh and cached and now - cached[0] < 24 * 60 * 60:
            return dict(cached[1])
    last_error: Exception | None = None
    for attempt in range(retry_count):
        try:
            frame = _tushare_http_query(
                token,
                "stk_limit",
                {
                    "ts_code": code,
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                },
                fields="ts_code,trade_date,up_limit,down_limit",
            )
            result: dict[date, tuple[float | None, float | None]] = {}
            if frame is None or getattr(frame, "empty", False):
                with _TUSHARE_LIMIT_CACHE_LOCK:
                    _TUSHARE_LIMIT_CACHE[cache_key] = (now, result)
                return result
            for row in frame.to_dict(orient="records"):
                trading_day = _as_date(row.get("trade_date"))
                if trading_day is None:
                    continue
                result[trading_day] = (_finite_float(row.get("up_limit")), _finite_float(row.get("down_limit")))
            with _TUSHARE_LIMIT_CACHE_LOCK:
                _TUSHARE_LIMIT_CACHE[cache_key] = (now, result)
            return result
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            LOGGER.exception("Tushare limits retry failed: %s", type(error).__name__, exc_info=None)
            last_error = error
            if attempt + 1 < retry_count and retry_backoff_seconds:
                time.sleep(retry_backoff_seconds * (2**attempt))
    assert last_error is not None
    with _TUSHARE_LIMIT_CACHE_LOCK:
        _TUSHARE_LIMIT_ERROR_CACHE = (now, type(last_error).__name__)
    raise last_error


def _normalize_tushare_symbol(symbol: str) -> str:
    code = str(symbol).strip().upper().replace(" ", "")
    if "." in code:
        code, market = code.split(".", 1)
        if market not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"invalid Tushare market suffix: {symbol}")
    else:
        market = "BJ" if code.startswith(("4", "8", "9")) else "SH" if code.startswith(("5", "6")) else "SZ"
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"invalid A-share symbol: {symbol}")
    return f"{code}.{market}"


def _tushare_frame_to_bars(
    frame: Any,
    code: str,
    limits: dict[date, tuple[float | None, float | None]],
) -> list[Bar]:
    if frame is None or getattr(frame, "empty", False):
        return []
    rows = frame.to_dict(orient="records")
    bars: list[Bar] = []
    for row in rows:
        trading_day = _as_date(row.get("trade_date"))
        open_price = _finite_float(row.get("open"))
        high_price = _finite_float(row.get("high"))
        low_price = _finite_float(row.get("low"))
        close_price = _finite_float(row.get("close"))
        volume_lots = _finite_float(row.get("vol"))
        if (
            trading_day is None
            or open_price is None
            or high_price is None
            or low_price is None
            or close_price is None
            or volume_lots is None
        ):
            continue
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue
        limit_up, limit_down = limits.get(trading_day, (None, None))
        bars.append(
            Bar(
                symbol=str(row.get("ts_code") or code).upper(),
                date=trading_day,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=max(0.0, volume_lots * 100.0),
                amount=None if _finite_float(row.get('amount')) is None else float(row['amount']) * 1000.0,
                limit_up=limit_up,
                limit_down=limit_down,
                paused=volume_lots == 0,
            )
        )
    return bars


class AkshareDailyAdapter(MarketDataAdapter):
    """AkShare daily A-share adapter with a stable internal Bar schema.

    AkShare is intentionally imported lazily so legacy integrations remain
    isolated from the production Tushare data path.
    The historical endpoint returns volume in lots (手); the backtest engine
    uses share quantities, so this adapter converts volume to shares.
    """

    def __init__(
        self,
        symbols: Iterable[str],
        adjust: str = "hfq",
        timeout: float = 30.0,
        cache_dir: str | Path | None = None,
        cache_ttl_seconds: int = 24 * 60 * 60,
        retry_count: int = 3,
        retry_backoff_seconds: float = 0.8,
    ) -> None:
        normalized = tuple(_normalize_akshare_symbol(symbol) for symbol in symbols)
        if not normalized:
            raise ValueError("symbols cannot be empty")
        if adjust not in {"", "qfq", "hfq"}:
            raise ValueError("adjust must be one of '', 'qfq', or 'hfq'")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        if retry_count < 1 or retry_count > 5:
            raise ValueError("retry_count must be between 1 and 5")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds cannot be negative")
        self.symbols = normalized
        self.adjust = adjust
        self.timeout = timeout
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self.cache_ttl_seconds = cache_ttl_seconds
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds
        self._snapshot = DataSnapshot(
            version=f"akshare:stock_zh_a_hist:{adjust or 'raw'}:pending",
            source="akshare",
            created_at=datetime.now(UTC),
            metadata={
                "symbols": list(normalized),
                "adjust": adjust,
                "endpoint": "stock_zh_a_hist",
                "cache_dir": str(self.cache_dir) if self.cache_dir else None,
            },
        )

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("akshare") is not None

    def snapshot(self) -> DataSnapshot:
        return self._snapshot

    def daily_bars(self, start: date | None = None, end: date | None = None) -> list[Bar]:
        if start and end and start > end:
            raise ValueError("start cannot be later than end")
        ak = _load_akshare()
        end_date = end or china_today()
        start_date = start or (end_date - timedelta(days=365 * 5))
        bars: list[Bar] = []
        errors: list[str] = []
        cache_hits = 0
        cache_misses = 0
        for code in self.symbols:
            cache_key = (code, start_date.isoformat(), end_date.isoformat(), self.adjust)
            cached = _memory_cache_get(cache_key)
            if cached is None:
                cached = _disk_cache_get(
                    self.cache_dir,
                    cache_key,
                    ttl_seconds=self.cache_ttl_seconds,
                )
            if cached is not None:
                cache_hits += 1
                bars.extend(cached)
                continue
            cache_misses += 1
            try:
                frame = _fetch_akshare_frame(ak, code, start_date, end_date, self.adjust, self.timeout, self.retry_count, self.retry_backoff_seconds)
                converted = tuple(_akshare_frame_to_bars(code, frame))
                if converted:
                    _memory_cache_put(cache_key, converted)
                    _disk_cache_put(self.cache_dir, cache_key, converted)
                bars.extend(converted)
            except Exception as error:  # keep other symbols available when one query fails
                logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
                errors.append(f"{code}: {type(error).__name__}")
            if code != self.symbols[-1]:
                time.sleep(0.25)
        bars = [bar for bar in bars if (not start or bar.date >= start) and (not end or bar.date <= end)]
        if not bars:
            detail = f"；{'; '.join(errors[:3])}" if errors else ""
            raise RuntimeError(f"AkShare 未返回可用历史行情{detail}")
        content = json.dumps([_bar_to_json(bar) for bar in bars], ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        self._snapshot = DataSnapshot(
            version=f"akshare:stock_zh_a_hist:{self.adjust or 'raw'}:{digest}",
            source="akshare",
            created_at=datetime.now(UTC),
            metadata={
                "symbols": list(self.symbols),
                "adjust": self.adjust,
                "endpoint": "stock_zh_a_hist",
                "cache_dir": str(self.cache_dir) if self.cache_dir else None,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "bar_count": len(bars),
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
        )
        return sorted(bars, key=lambda bar: (bar.date, bar.symbol))


def _fetch_akshare_frame(
    ak: Any,
    code: str,
    start_date: date,
    end_date: date,
    adjust: str,
    timeout: float,
    retry_count: int,
    retry_backoff_seconds: float,
) -> Any:
    kwargs = {
        "symbol": code,
        "period": "daily",
        "start_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
        "adjust": adjust,
    }
    last_error: Exception | None = None
    for attempt in range(retry_count):
        try:
            try:
                return ak.stock_zh_a_hist(**kwargs, timeout=timeout)
            except TypeError as error:
                # AkShare versions differ: older releases do not expose timeout.
                if "timeout" not in str(error).lower():
                    raise
                return ak.stock_zh_a_hist(**kwargs)
        except Exception as error:
            logging.getLogger(__name__).exception("quant.data.adapters failed: %s", type(error).__name__, exc_info=None)
            last_error = error
            if attempt + 1 < retry_count and retry_backoff_seconds:
                time.sleep(retry_backoff_seconds * (2**attempt))
    assert last_error is not None
    raise last_error


def _memory_cache_get(key: tuple[str, str, str, str]) -> tuple[Bar, ...] | None:
    with _AKSHARE_CACHE_LOCK:
        return _AKSHARE_BAR_CACHE.get(key)


def _memory_cache_put(key: tuple[str, str, str, str], bars: tuple[Bar, ...]) -> None:
    with _AKSHARE_CACHE_LOCK:
        _AKSHARE_BAR_CACHE[key] = bars


def _tushare_memory_cache_get(key: tuple[str, str, str, str]) -> tuple[Bar, ...] | None:
    with _TUSHARE_CACHE_LOCK:
        return _TUSHARE_BAR_CACHE.get(key)


def _tushare_memory_cache_put(key: tuple[str, str, str, str], bars: tuple[Bar, ...]) -> None:
    with _TUSHARE_CACHE_LOCK:
        _TUSHARE_BAR_CACHE[key] = bars


def _disk_cache_path(
    cache_dir: Path | None,
    key: tuple[str, str, str, str],
    *,
    prefix: str = "akshare",
) -> Path | None:
    if cache_dir is None:
        return None
    code, start, end, adjust = key
    safe_adjust = adjust or "raw"
    safe_code = code.replace(".", "_")
    return cache_dir / f"{prefix}_{safe_code}_{start}_{end}_{safe_adjust}.json"


def _disk_cache_get(
    cache_dir: Path | None,
    key: tuple[str, str, str, str],
    *,
    ttl_seconds: int,
    prefix: str = "akshare",
) -> tuple[Bar, ...] | None:
    path = _disk_cache_path(cache_dir, key, prefix=prefix)
    if path is None or not path.is_file():
        return None
    try:
        if time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        bars = tuple(_bar_from_json(item) for item in payload.get("bars", []))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    if not bars:
        return None
    _memory_cache_put(key, bars)
    return bars


def _disk_cache_put(
    cache_dir: Path | None,
    key: tuple[str, str, str, str],
    bars: tuple[Bar, ...],
    *,
    prefix: str = "akshare",
) -> None:
    path = _disk_cache_path(cache_dir, key, prefix=prefix)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "fetched_at": datetime.now(UTC).isoformat(), "bars": [_bar_to_json(bar) for bar in bars]}
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # A read-only cache must not make otherwise valid market data unusable.
        return


def _bar_to_json(bar: Bar) -> dict[str, Any]:
    payload = {
        "symbol": bar.symbol,
        "date": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
        "limit_up": bar.limit_up,
        "limit_down": bar.limit_down,
        "paused": bar.paused,
        "listed": bar.listed,
        "is_st": bar.is_st,
        "industry": bar.industry,
        "is_delisted": bar.is_delisted,
    }
    if bar.signal_close is not None:
        payload.update({
            "signal_open": bar.signal_open,
            "signal_high": bar.signal_high,
            "signal_low": bar.signal_low,
            "signal_close": bar.signal_close,
        })
    return payload


def _bar_from_json(payload: dict[str, Any]) -> Bar:
    return Bar(
        symbol=str(payload["symbol"]),
        date=date.fromisoformat(str(payload["date"])),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume=float(payload["volume"]),
        amount=None if payload.get('amount') is None else float(payload['amount']),
        limit_up=None if payload.get("limit_up") is None else float(payload["limit_up"]),
        limit_down=None if payload.get("limit_down") is None else float(payload["limit_down"]),
        paused=bool(payload.get("paused", False)),
        listed=bool(payload.get("listed", True)),
        is_st=bool(payload.get("is_st", False)),
        industry=payload.get("industry") or None,
        is_delisted=bool(payload.get("is_delisted", False)),
        signal_open=None if payload.get("signal_open") is None else float(payload["signal_open"]),
        signal_high=None if payload.get("signal_high") is None else float(payload["signal_high"]),
        signal_low=None if payload.get("signal_low") is None else float(payload["signal_low"]),
        signal_close=None if payload.get("signal_close") is None else float(payload["signal_close"]),
    )


def _apply_tushare_signal_adjustment(
    bars: list[Bar], token: str
) -> tuple[list[Bar], str]:
    """Attach causal, chain-linked signal OHLC while leaving execution fields raw."""
    from dataclasses import replace

    grouped: dict[str, list[Bar]] = {}
    for bar in bars:
        grouped.setdefault(bar.symbol, []).append(bar)
    adjusted: list[Bar] = []
    factor_rows: list[tuple[str, str, float]] = []
    ordered_symbols = sorted(grouped)
    for symbol_index, symbol in enumerate(ordered_symbols):
        symbol_bars = grouped[symbol]
        first_day = min(bar.date for bar in symbol_bars)
        last_day = max(bar.date for bar in symbol_bars)
        frame = _tushare_http_query(
            token,
            "adj_factor",
            {
                "ts_code": symbol,
                "start_date": first_day.strftime("%Y%m%d"),
                "end_date": last_day.strftime("%Y%m%d"),
            },
            fields="ts_code,trade_date,adj_factor",
        )
        records = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
        factors: dict[date, float] = {}
        for row in records:
            day = _as_date(row.get("trade_date"))
            value = _finite_float(row.get("adj_factor"))
            if day is None or value is None or value <= 0:
                continue
            if day in factors and factors[day] != value:
                raise RuntimeError(f"adj_factor {symbol} {day}: conflicting duplicate values")
            factors[day] = value
        missing = [bar.date.isoformat() for bar in symbol_bars if bar.date not in factors]
        if missing:
            raise RuntimeError(
                f"adj_factor coverage incomplete for {symbol}: {len(missing)} missing trading dates; "
                f"first missing {missing[0]}"
            )
        anchor = factors[min(bar.date for bar in symbol_bars)]
        for bar in sorted(symbol_bars, key=lambda item: item.date):
            scale = factors[bar.date] / anchor
            adjusted.append(replace(
                bar,
                signal_open=bar.open * scale,
                signal_high=bar.high * scale,
                signal_low=bar.low * scale,
                signal_close=bar.close * scale,
            ))
            factor_rows.append((symbol, bar.date.isoformat(), factors[bar.date]))
        if symbol_index + 1 < len(ordered_symbols):
            time.sleep(0.25)
    digest = hashlib.sha256(
        json.dumps(factor_rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    return sorted(adjusted, key=lambda bar: (bar.date, bar.symbol)), digest


def _load_akshare() -> Any:
    try:
        import akshare as ak
    except ImportError as error:
        raise RuntimeError("未安装 AkShare；生产数据路径请使用 TushareDailyAdapter 和 requirements-tushare.txt") from error
    return ak


def _normalize_akshare_symbol(symbol: str) -> str:
    code = str(symbol).strip().upper().replace(" ", "")
    if "." in code:
        code = code.split(".", 1)[0]
    if code[:2] in {"SH", "SZ", "BJ"}:
        code = code[2:]
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"invalid A-share symbol: {symbol}")
    return code


def _akshare_symbol_with_market(code: str) -> str:
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _akshare_frame_to_bars(code: str, frame: Any) -> list[Bar]:
    if frame is None or getattr(frame, "empty", False):
        return []
    rows = frame.to_dict(orient="records")
    bars: list[Bar] = []
    symbol = _akshare_symbol_with_market(code)
    for row in rows:
        trading_day = _as_date(row.get("日期", row.get("date")))
        open_price = _finite_float(row.get("开盘", row.get("open")))
        high_price = _finite_float(row.get("最高", row.get("high")))
        low_price = _finite_float(row.get("最低", row.get("low")))
        close_price = _finite_float(row.get("收盘", row.get("close")))
        volume_lots = _finite_float(row.get("成交量", row.get("volume")))
        if (
            trading_day is None
            or open_price is None
            or high_price is None
            or low_price is None
            or close_price is None
            or volume_lots is None
        ):
            continue
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue
        volume_shares = max(0.0, volume_lots * 100.0)
        name = str(row.get("名称", ""))
        bars.append(
            Bar(
                symbol=symbol,
                date=trading_day,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume_shares,
                paused=volume_shares == 0,
                is_st="ST" in name.upper(),
            )
        )
    return bars


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}
