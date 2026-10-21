"""PIT feature-store materialization, independent of the HTTP handler."""

from __future__ import annotations

import logging
import os
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quant.data import TushareDailyAdapter
from quant.errors import ProviderShapeError
from quant.numeric import as_int

from ..data.schema import Bar
from ..features import FeatureStore
from .parsing import optional_date
from .universe import ResolvedBacktestUniverse

LOGGER = logging.getLogger(__name__)


# Stable error codes for PIT feature warm-up failures -> (HTTP status, Chinese hint).
FEATURE_MATERIALIZATION_ERROR_CODES: dict[str, tuple[int, str]] = {
    "invalid_feature_request": (400, "预热请求参数无效，请检查开始/结束日期与复权设置。"),
    "range_too_long": (400, "预热区间超过 5 年上限，请缩短开始与结束日期之间的跨度。"),
    "universe_too_large": (400, "PIT 股票池数量超过安全上限，请缩短区间后重试。"),
    "tushare_token_missing": (503, "服务端未配置 TUSHARE_TOKEN，无法拉取真实行情；请在 .env 中配置后重启服务。"),
    "quota": (429, "Tushare 调用额度或频率已达上限（或接口权限不足），请稍后再试或检查积分/限额设置。"),
    "tushare_timeout": (504, "连接 Tushare 超时，请检查网络后重试。"),
    "tushare_network": (502, "无法连接 Tushare（网络或代理异常），请检查网络后重试。"),
    "incomplete_universe": (502, "部分成分股未返回行情，为避免用残缺数据生成特征已停止；可稍后重试。"),
    "data_source_not_live": (502, "本次行情不是实时 Tushare 拉取（命中缓存或混合来源），按 PIT 规则拒绝写入。"),
    "data_stale": (502, "拉取到的行情落后于最新交易日探针，已拒绝写入，请稍后重试。"),
    "provider_empty": (502, "Tushare 未返回可用数据（成分或行情为空）。"),
    "provider_shape": (502, "Tushare 返回的数据结构异常，已停止预热。"),
    "feature_data_unavailable": (502, "特征数据暂不可用，详见原始错误。"),
    "unknown": (500, "预热发生未预期错误，详见原始错误与服务日志。"),
}
_MAX_ERROR_CHARS = 500


def _redact(message: str, token: str | None = None) -> str:
    secret = (token if token is not None else os.getenv("TUSHARE_TOKEN", "")).strip()
    if secret and len(secret) >= 8:
        message = message.replace(secret, "***")
    message = re.sub(r"(?i)(token\s*[=:]\s*)[^\s,;&'\"]+", r"\1***", message)
    message = re.sub(r"\b[0-9a-fA-F]{40,}\b", "***", message)
    message = message.strip()
    return message if len(message) <= _MAX_ERROR_CHARS else message[:_MAX_ERROR_CHARS] + "…（已截断）"


def classify_materialization_error(
    error: BaseException, *, token: str | None = None
) -> dict[str, object]:
    """Map a warm-up failure to a stable code, HTTP status and Chinese hint.

    The original message is kept (redacted, truncated); it is never swallowed.
    """
    from .resource_limits import ResourceLimitExceeded

    raw = str(error).strip()
    name = type(error).__name__
    lowered = raw.lower()
    names = {klass.__name__ for klass in type(error).__mro__}
    if isinstance(error, ResourceLimitExceeded) or re.search(
        r"每分钟最多|最多访问|积分|权限|频率|限额|quota|rate.?limit|too many requests", raw, re.IGNORECASE
    ):
        code = "quota"
    elif "TUSHARE_TOKEN" in raw or re.search(r"token\s*未配置|token_unavailable|token unavailable", raw, re.IGNORECASE):
        code = "tushare_token_missing"
    elif isinstance(error, (TimeoutError, socket.timeout)) or "Timeout" in name or re.search(
        r"timed? ?out|timeout|超时", lowered
    ):
        code = "tushare_timeout"
    elif isinstance(error, ConnectionError) or names & {"ConnectionError", "URLError", "ProxyError", "SSLError"} or re.search(
        r"connection|network|max retries|name resolution|网络|连接", lowered
    ):
        code = "tushare_network"
    elif "超过安全上限" in raw:
        code = "universe_too_large"
    elif "最多覆盖" in raw:
        code = "range_too_long"
    elif "行情完整性不足" in raw:
        code = "incomplete_universe"
    elif "真实 Tushare 拉取" in raw or "不接受缓存" in raw:
        code = "data_source_not_live"
    elif "落后于真实数据探针" in raw:
        code = "data_stale"
    elif isinstance(error, ProviderShapeError):
        code = "provider_shape"
    elif "未返回" in raw or "为空" in raw or "交集为空" in raw:
        code = "provider_empty"
    elif isinstance(error, (ValueError, TypeError)):
        code = "invalid_feature_request"
    elif isinstance(error, RuntimeError):
        code = "feature_data_unavailable"
    else:
        code = "unknown"
    status, hint = FEATURE_MATERIALIZATION_ERROR_CODES[code]
    message = _redact(raw, token) or name
    return {
        "status": status,
        "code": code,
        "hint": hint,
        "error": f"{hint} 原始错误：{message}" if message != hint else hint,
        "detail": message,
        "error_type": name,
    }



SYMBOL_BATCH_SIZE = 40
FEATURE_LOOKBACK_DAYS = 120


def _truthy(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def iter_month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive calendar-month shards clipped to [start, end]."""
    if start > end:
        return []
    windows: list[tuple[date, date]] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        import calendar

        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, last_day)
        shard_start = max(start, cursor)
        shard_end = min(end, month_end)
        if shard_start <= shard_end:
            windows.append((shard_start, shard_end))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return windows


def shard_id_for(
    index_code: str,
    start: date,
    end: date,
    *,
    adjust: str = "",
    signal_adjust: str = "adj",
) -> str:
    from ..features.store import FEATURE_SCHEMA_VERSION

    return (
        f"{index_code}|{start.isoformat()}|{end.isoformat()}|{FEATURE_SCHEMA_VERSION}"
        f"|{adjust or 'raw'}|{signal_adjust or 'raw'}"
    )


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[offset : offset + size] for offset in range(0, len(items), size)]


@dataclass(frozen=True)
class FeatureMaterializationDependencies:
    today: Callable[[], date]
    resolve_universe: Callable[
        [Mapping[str, Any], date | None, date | None], ResolvedBacktestUniverse
    ]
    filter_universe: Callable[[list[Bar], ResolvedBacktestUniverse], list[Bar]]
    make_adapter: type[TushareDailyAdapter]
    require_complete_universe: Callable[[Mapping[str, object]], None]
    data_probe: Callable[[], dict[str, object]]
    scoped_dataset_version: Callable[[str, str], str]
    feature_store: FeatureStore
    max_symbols: int
    index_code: str
    cache_dir: Path
    token: str | None



def coverage_latest_trade_date(metadata: dict[str, object], bars: list | None = None) -> str:
    """Upper coverage date for freshness gates (not the lagging-symbol min).

    Adapter ``force_refresh`` sets ``latest_trade_date`` to the *minimum* per-symbol
    latest so one suspension cannot look fresh. Warm-up must still accept a batch
    when the liquid tip of the universe reached the probe date (e.g. post-holiday).
    """
    dates: list[str] = []
    per = metadata.get("latest_trade_dates")
    if isinstance(per, dict):
        dates.extend(str(value) for value in per.values() if value)
    if bars:
        try:
            dates.extend(
                bar.date.isoformat() for bar in bars if getattr(bar, "date", None)
            )
        except (AttributeError, TypeError, ValueError):
            LOGGER.warning("Invalid bar date while checking feature coverage")
    single = metadata.get("latest_trade_date")
    if single:
        dates.append(str(single))
    return max(dates) if dates else ""


def materialize_pit_features(
    payload: dict[str, object],
    deps: FeatureMaterializationDependencies,
    *,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Persist real daily features for the pre-registered PIT universe.

    Month shards + in-shard symbol batches. Verified shards/symbols are skipped on
    resume; every new fetch still requires a live Tushare response (never dirty cache).
    """
    def _progress(**fields: object) -> None:
        if check_cancelled is not None:
            check_cancelled()
        if on_progress is not None:
            on_progress(fields)

    mode = str(payload.get("universe_mode", "pit_index")).strip().lower()
    if mode not in {"pit_index", "pit_hs300"}:
        raise ValueError(
            "特征库预热仅接受严格 PIT 沪深300历史成分；固定股票池不会写入 PIT 特征库"
        )
    catch_up = _truthy(payload.get("catch_up"), True)
    resume = _truthy(payload.get("resume"), True)
    requested_start = optional_date(payload.get("start_date"))
    end = optional_date(payload.get("end_date")) or deps.today()
    start = requested_start or date(2024, 1, 2)
    if catch_up:
        status: dict[str, object] = {}
        try:
            status = deps.feature_store.status()
        except Exception:  # noqa: BLE001
            status = {}
        lagging = optional_date(status.get("lagging_feature_date"))
        current_max = optional_date(status.get("current_schema_max_feature_date"))
        if lagging is not None and current_max is not None and (current_max - lagging).days > 120:
            lagging = current_max - timedelta(days=120)
        if lagging is not None:
            catch_start = lagging + timedelta(days=1)
            start = max(start, catch_start)
    if start > end:
        # Already caught up — return status without fetching.
        store_status = deps.feature_store.status()
        return {
            "materialization": {
                "status": "up_to_date",
                "rows": 0,
                "written": 0,
                "shards_completed": 0,
                "shards_resumed": 0,
                "shards_fetched": 0,
            },
            "feature_store": store_status,
            "universe": {"mode": "pit_historical_index", "skipped": True},
            "data_version": None,
            "source": "verified_shards",
            "latest_trade_date": store_status.get("latest_trade_date"),
            "bar_count": 0,
            "requested_feature_start": (requested_start or start).isoformat(),
            "effective_feature_start": start.isoformat(),
            "feature_warmup_start": (start - timedelta(days=FEATURE_LOOKBACK_DAYS)).isoformat(),
            "research_budget_charged": False,
            "orders_created": 0,
            "catch_up": catch_up,
            "resume": resume,
            "note": "特征已覆盖至请求结束日，无需再次拉取。",
        }
    if end > deps.today():
        raise ValueError("特征库结束日期不能晚于北京时间今天")
    if (end - start).days > 5 * 366:
        raise ValueError("单次特征库预热最多覆盖5年；请缩短区间")
    adjust = str(payload.get("adjust", "")).strip().lower()
    if adjust not in {"", "qfq", "hfq"}:
        raise ValueError("adjust must be one of '', 'qfq', or 'hfq'")
    if adjust:
        raise ValueError("特征库只接受未复权行情，并用因果 adj_factor 生成信号价")

    windows = iter_month_windows(start, end)
    feature_warmup_start = start - timedelta(days=FEATURE_LOOKBACK_DAYS)
    _progress(
        stage="universe",
        progress=5,
        message=f"正在规划 {len(windows)} 个月分片",
        shard_total=len(windows),
        shard_index=0,
    )

    total_written = 0
    total_rows = 0
    total_bars = 0
    shards_resumed = 0
    shards_fetched = 0
    shards_completed = 0
    last_data_version: str | None = None
    last_latest = ""
    last_universe_meta: dict[str, object] = {}
    live_fetches = 0
    resumed_versions: list[str] = []

    for shard_index, (shard_start, shard_end) in enumerate(windows):
        sid = shard_id_for(deps.index_code, shard_start, shard_end)
        existing = deps.feature_store.get_warmup_shard(sid) if resume else None
        if existing and existing.get("status") == "completed" and existing.get("data_source") == "tushare_live":
            shards_resumed += 1
            shards_completed += 1
            total_written += as_int(existing.get("rows_written") or 0)
            resumed_version = str(existing.get("data_version") or "")
            if resumed_version:
                resumed_versions.append(resumed_version)
            _progress(
                stage="materialize",
                progress=10 + int(80 * (shard_index + 1) / max(len(windows), 1)),
                message=f"跳过已校验分片 {shard_start}～{shard_end}",
                shard_id=sid,
                shard_index=shard_index + 1,
                shard_total=len(windows),
                shards_resumed=shards_resumed,
            )
            continue

        lookback_start = shard_start - timedelta(days=FEATURE_LOOKBACK_DAYS)
        _progress(
            stage="universe",
            progress=10 + int(80 * shard_index / max(len(windows), 1)),
            message=f"解析分片宇宙 {shard_start}～{shard_end}",
            shard_id=sid,
            shard_index=shard_index + 1,
            shard_total=len(windows),
        )
        universe = deps.resolve_universe(
            {"universe_mode": "pit_index", "universe_index_code": deps.index_code},
            lookback_start,
            shard_end,
        )
        metadata = universe.get("metadata")
        if isinstance(metadata, dict):
            data_fetch_start = universe.get("start_date")
            if not isinstance(data_fetch_start, date):
                raise ProviderShapeError("PIT 特征宇宙缺少数据拉取起始日期")
            metadata["requested_start"] = start.isoformat()
            metadata["shard_start"] = shard_start.isoformat()
            metadata["shard_end"] = shard_end.isoformat()
            metadata["feature_warmup_start"] = lookback_start.isoformat()
            metadata["data_fetch_start"] = data_fetch_start.isoformat()
            last_universe_meta = metadata
        symbols = [str(item).upper() for item in universe["symbols"]]
        if len(symbols) > deps.max_symbols:
            raise ValueError(
                f"PIT 特征宇宙需要 {len(symbols)} 只股票，超过安全上限 {deps.max_symbols}"
            )

        completed_symbols = set()
        rows_written_shard = as_int(existing.get("rows_written") or 0) if existing else 0
        if resume and existing and existing.get("status") == "in_progress":
            stored_symbols = existing.get("completed_symbols") or []
            if not isinstance(stored_symbols, list):
                raise ProviderShapeError("Stored completed_symbols must be a list")
            completed_symbols = {
                str(item).upper() for item in stored_symbols
            }
        pending = [symbol for symbol in symbols if symbol not in completed_symbols]
        if not pending:
            deps.feature_store.upsert_warmup_shard(
                {
                    "shard_id": sid,
                    "index_code": deps.index_code,
                    "start_date": shard_start.isoformat(),
                    "end_date": shard_end.isoformat(),
                    "status": "completed",
                    "symbol_count": len(symbols),
                    "completed_symbols": symbols,
                    "rows_written": rows_written_shard,
                    "data_version": (existing or {}).get("data_version") or "",
                    "data_source": "tushare_live",
                }
            )
            shards_resumed += 1
            shards_completed += 1
            continue

        deps.feature_store.upsert_warmup_shard(
            {
                "shard_id": sid,
                "index_code": deps.index_code,
                "start_date": shard_start.isoformat(),
                "end_date": shard_end.isoformat(),
                "status": "in_progress",
                "symbol_count": len(symbols),
                "completed_symbols": sorted(completed_symbols),
                "rows_written": rows_written_shard,
                "data_version": (existing or {}).get("data_version") or "",
                "data_source": "",
            }
        )

        fetch_start = universe["start_date"]
        fetch_end = universe["end_date"]
        if not isinstance(fetch_start, date) or not isinstance(fetch_end, date):
            raise ProviderShapeError("PIT 特征宇宙缺少有效拉取日期")
        batches = _batches(pending, SYMBOL_BATCH_SIZE)
        for batch_index, batch in enumerate(batches):
            _progress(
                stage="bars",
                progress=10
                + int(
                    80
                    * (shard_index + (batch_index / max(len(batches), 1)))
                    / max(len(windows), 1)
                ),
                message=f"拉取真实行情 {shard_start}～{shard_end} · 批次 {batch_index + 1}/{len(batches)}",
                shard_id=sid,
                shard_index=shard_index + 1,
                shard_total=len(windows),
                symbol_count=len(symbols),
                processed_symbols=len(completed_symbols),
                current_symbol=batch[0] if batch else "",
            )
            adapter = deps.make_adapter(
                symbols=batch,
                token=deps.token,
                adjust="",
                signal_adjust="adj",
                cache_dir=deps.cache_dir,
                fetch_limits=False,
                force_refresh=True,
            )
            raw_bars = adapter.daily_bars(start=fetch_start, end=fetch_end)
            snapshot = adapter.snapshot()
            deps.require_complete_universe(snapshot.metadata)
            if snapshot.metadata.get("data_source") != "tushare_live":
                raise RuntimeError(
                    "PIT 特征库预热只接受本次真实 Tushare 拉取，不接受缓存或混合行情"
                )
            live_fetches += 1
            # Lagging-symbol min (adapter force_refresh) is for universe integrity;
            # warm-up freshness uses coverage tip vs probe so post-holiday / 停牌个股不误杀整批.
            tip = coverage_latest_trade_date(
                dict(snapshot.metadata or {}), raw_bars
            )
            batch_lagging = str(snapshot.metadata.get("latest_trade_date") or "")
            probe_latest = str((deps.data_probe() or {}).get("latest_trade_date") or "")
            # Only enforce freshness on the final catch-up shard (touches pending_end).
            if shard_end >= end and probe_latest and tip and tip < probe_latest:
                raise RuntimeError(
                    f"特征行情落后于真实数据探针：{tip} < {probe_latest}"
                    + (f"（批次最慢 {batch_lagging}）" if batch_lagging and batch_lagging != tip else "")
                )
            last_latest = tip or batch_lagging or last_latest
            bars = deps.filter_universe(raw_bars, universe)
            if not bars:
                raise RuntimeError("PIT 成分与真实日线交集为空，未写入特征库")
            data_version = deps.scoped_dataset_version(
                snapshot.version, str(universe["version"])
            )
            last_data_version = data_version
            _progress(
                stage="materialize",
                progress=10
                + int(
                    80
                    * (shard_index + ((batch_index + 0.5) / max(len(batches), 1)))
                    / max(len(windows), 1)
                ),
                message=f"写入特征 {shard_start}～{shard_end}",
                shard_id=sid,
                shard_index=shard_index + 1,
                shard_total=len(windows),
                symbol_count=len(symbols),
                processed_symbols=len(completed_symbols) + len(batch),
            )
            materialized = deps.feature_store.materialize(
                bars,
                data_version,
                feature_start=shard_start,
                feature_end=shard_end,
            )
            written = as_int(materialized.get("written") or 0)
            rows_written_shard += written
            total_written += written
            total_rows += as_int(materialized.get("rows") or 0)
            total_bars += len(bars)
            completed_symbols.update(batch)
            deps.feature_store.upsert_warmup_shard(
                {
                    "shard_id": sid,
                    "index_code": deps.index_code,
                    "start_date": shard_start.isoformat(),
                    "end_date": shard_end.isoformat(),
                    "status": "in_progress",
                    "symbol_count": len(symbols),
                    "completed_symbols": sorted(completed_symbols),
                    "rows_written": rows_written_shard,
                    "data_version": data_version,
                    "data_source": "tushare_live",
                }
            )

        deps.feature_store.upsert_warmup_shard(
            {
                "shard_id": sid,
                "index_code": deps.index_code,
                "start_date": shard_start.isoformat(),
                "end_date": shard_end.isoformat(),
                "status": "completed",
                "symbol_count": len(symbols),
                "completed_symbols": symbols,
                "rows_written": rows_written_shard,
                "data_version": last_data_version or "",
                "data_source": "tushare_live",
            }
        )
        shards_fetched += 1
        shards_completed += 1

    if last_data_version is None and len(set(resumed_versions)) == 1:
        last_data_version = resumed_versions[0]

    if live_fetches == 0 and shards_resumed == 0:
        raise RuntimeError("PIT 成分与真实日线交集为空，未写入特征库")

    _progress(
        stage="materialize",
        progress=95,
        message="分片预热完成，正在刷新状态",
        shard_total=len(windows),
        shard_index=len(windows),
        shards_resumed=shards_resumed,
        shards_fetched=shards_fetched,
    )
    try:
        deps.feature_store.record_materialization(
            data_version=str(last_data_version or "resume"),
            written=total_written,
        )
    except Exception as error:  # noqa: BLE001 - optional status receipt, not feature rows
        LOGGER.warning("Feature materialization receipt failed: %s", type(error).__name__)

    source = "tushare_live" if live_fetches else "verified_shards"
    return {
        "materialization": {
            "status": "available" if total_rows or shards_resumed else "insufficient_history",
            "schema_version": "daily-price-features-v2",
            "data_version": last_data_version,
            "rows": total_rows,
            "written": total_written,
            "feature_start": start.isoformat(),
            "feature_end": end.isoformat(),
            "shards_total": len(windows),
            "shards_completed": shards_completed,
            "shards_resumed": shards_resumed,
            "shards_fetched": shards_fetched,
            "live_fetches": live_fetches,
        },
        "feature_store": deps.feature_store.status(),
        "universe": last_universe_meta,
        "data_version": last_data_version,
        "source": source,
        "latest_trade_date": last_latest,
        "bar_count": total_bars,
        "requested_feature_start": (requested_start or start).isoformat(),
        "effective_feature_start": start.isoformat(),
        "feature_warmup_start": feature_warmup_start.isoformat(),
        "research_budget_charged": False,
        "orders_created": 0,
        "catch_up": catch_up,
        "resume": resume,
        "note": "按月分片+标的批次续跑：仅新分片要求本次 Tushare live；已校验分片/标的可跳过。",
    }
