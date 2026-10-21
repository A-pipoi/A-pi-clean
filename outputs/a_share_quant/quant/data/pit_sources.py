from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded

from .adapters import _finite_float, _normalize_tushare_symbol, _tushare_http_query
from .fundamentals import _parse_date, _read_completed_job_cache
from .progress import report_data_progress

LOGGER = logging.getLogger(__name__)

DAILY_BASIC_FIELDS = "ts_code,trade_date,total_mv,circ_mv,pe_ttm,pb"
INDUSTRY_HISTORY_FIELDS = (
    "ts_code,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,in_date,out_date,is_new"
)


def fetch_tushare_daily_basic_history(
    symbols: Iterable[str],
    *,
    start_date: date,
    end_date: date,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch daily valuation/size rows; Tushare's post-close updates take effect next session."""

    if start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载历史市值")
    wanted = tuple(
        dict.fromkeys(_normalize_tushare_symbol(symbol) for symbol in symbols)
    )
    root = Path(cache_dir).expanduser() if cache_dir else None
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    cache_hits = cache_misses = 0
    for index, code in enumerate(wanted):
        report_data_progress(index, len(wanted), code)
        path = _cache_path(root, "daily_basic", code, start_date, end_date)
        rows = (
            _read_completed_job_cache(path, cache_ttl_seconds)
            if force_refresh
            else _read_cache(path, cache_ttl_seconds)
        )
        if rows is not None:
            cache_hits += 1
        else:
            cache_misses += 1
            try:
                frame = _tushare_http_query(
                    access_token,
                    "daily_basic",
                    {
                        "ts_code": code,
                        "start_date": start_date.strftime("%Y%m%d"),
                        "end_date": end_date.strftime("%Y%m%d"),
                    },
                    fields=DAILY_BASIC_FIELDS,
                )
                rows = _daily_basic_rows(frame, code)
                _write_cache(path, rows)
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                LOGGER.exception(
                    "Historical valuation query failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )
                errors.append(f"{code}: {type(error).__name__}")
                rows = []
        output.extend(rows)
        if index < len(wanted) - 1:
            time.sleep(0.12)
    metadata = {
        "source": "tushare:daily_basic",
        "endpoint": "daily_basic",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "record_count": len(output),
        "symbols": list(wanted),
        "available_symbols": sorted({str(row["ts_code"]) for row in output}),
        "missing_symbols": sorted(
            set(wanted) - {str(row["ts_code"]) for row in output}
        ),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": errors[:20],
        "status": "available"
        if output and not errors
        else "partial"
        if output
        else "unavailable"
        if errors
        else "empty",
        "availability_basis": "trade_date_at_17_00_china_then_next_trading_session",
        "market_cap_unit": "10k_CNY",
    }
    return output, metadata


def fetch_tushare_industry_history(
    symbols: Iterable[str],
    *,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch effective-date industry history without pretending it is publication history."""

    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载行业历史")
    wanted = tuple(
        dict.fromkeys(_normalize_tushare_symbol(symbol) for symbol in symbols)
    )
    root = Path(cache_dir).expanduser() if cache_dir else None
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    cache_hits = cache_misses = 0
    for index, code in enumerate(wanted):
        report_data_progress(index, len(wanted), code)
        path = _industry_cache_path(root, code)
        rows = (
            _read_completed_job_cache(path, cache_ttl_seconds)
            if force_refresh
            else _read_cache(path, cache_ttl_seconds)
        )
        if rows is not None:
            cache_hits += 1
        else:
            cache_misses += 1
            try:
                frame = _tushare_http_query(
                    access_token,
                    "index_member_all",
                    {"ts_code": code},
                    fields=INDUSTRY_HISTORY_FIELDS,
                )
                rows = _industry_rows(frame, code)
                _write_cache(path, rows)
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                LOGGER.exception(
                    "Historical industry query failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )
                errors.append(f"{code}: {type(error).__name__}")
                rows = []
        output.extend(rows)
        if index < len(wanted) - 1:
            time.sleep(0.12)
    metadata = {
        "source": "tushare:index_member_all",
        "endpoint": "index_member_all",
        "record_count": len(output),
        "symbols": list(wanted),
        "available_symbols": sorted({str(row["ts_code"]) for row in output}),
        "missing_symbols": sorted(
            set(wanted) - {str(row["ts_code"]) for row in output}
        ),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": errors[:20],
        "status": "available"
        if output and not errors
        else "partial"
        if output
        else "unavailable"
        if errors
        else "empty",
        "availability_quality": "effective_dates_only_unverified",
        "neutralization_enabled": False,
        "limitation": "Tushare rows expose in_date/out_date but no reliable publication or first-available timestamp; excluded from strict PIT industry neutralization.",
    }
    return output, metadata


def _daily_basic_rows(frame: Any, code: str) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", False):
        return []
    output: list[dict[str, Any]] = []
    mapping = {
        "total_mv": "market_cap",
        "circ_mv": "float_market_cap",
        "pe_ttm": "pe",
        "pb": "pb",
    }
    for row in frame.to_dict(orient="records"):
        trade_date = _parse_date(row.get("trade_date"))
        if trade_date is None:
            continue
        available_at = f"{trade_date.isoformat()}T17:00:00+08:00"
        version = _row_version(row)
        for source_field, metric in mapping.items():
            value = _finite_float(row.get(source_field))
            if value is None:
                continue
            output.append(
                {
                    "ts_code": str(row.get("ts_code") or code).upper(),
                    "symbol": str(row.get("ts_code") or code).upper(),
                    "metric": metric,
                    "value": value,
                    "trade_date": trade_date.isoformat(),
                    "report_date": trade_date.isoformat(),
                    "available_at": available_at,
                    "availability_basis": "provider_update_window_proxy",
                    "source_version": version,
                    "source": "Tushare:daily_basic",
                    "unit": "10k_CNY"
                    if metric in {"market_cap", "float_market_cap"}
                    else None,
                }
            )
    return output


def _industry_rows(frame: Any, code: str) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", False):
        return []
    output: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        if not _parse_date(row.get("in_date")):
            continue
        item = {str(key): value for key, value in row.items()}
        item["ts_code"] = str(row.get("ts_code") or code).upper()
        item["symbol"] = item["ts_code"]
        item["industry"] = str(row.get("l1_name") or "").strip()
        item["availability_basis"] = "effective_dates_only_unverified"
        item["source_version"] = _row_version(row)
        item["source"] = "Tushare:index_member_all"
        output.append(item)
    return output


def _row_version(row: dict[str, Any]) -> str:
    canonical = json.dumps(
        row, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _cache_path(
    root: Path | None, endpoint: str, code: str, start: date, end: date
) -> Path | None:
    if root is None:
        return None
    safe = code.replace(".", "_")
    return (
        root
        / f"tushare_{endpoint}_pit_{safe}_{start.isoformat()}_{end.isoformat()}.json"
    )


def _industry_cache_path(root: Path | None, code: str) -> Path | None:
    if root is None:
        return None
    return root / f"tushare_industry_pit_{code.replace('.', '_')}.json"


def _read_cache(path: Path | None, ttl: int) -> list[dict[str, Any]] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("fetched_at", 0)) > ttl:
            return None
        rows = payload.get("records")
        return (
            [row for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else None
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temp.write_text(
            json.dumps(
                {"fetched_at": time.time(), "records": rows}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        temp.replace(path)
    except OSError:
        return
