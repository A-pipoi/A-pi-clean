from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded
from quant.time_utils import china_today

from .adapters import _finite_float, _normalize_tushare_symbol, _tushare_http_query
from .progress import DATA_CACHE_NOT_BEFORE, report_data_progress

LOGGER = logging.getLogger(__name__)

FUNDAMENTAL_METRICS: dict[str, str] = {
    "roe": "净资产收益率",
    "netprofit_yoy": "净利润同比",
    "tr_yoy": "营业收入同比",
    "debt_to_assets": "资产负债率",
}
FUNDAMENTAL_FIELDS = "ts_code,ann_date,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets"
FUNDAMENTAL_HISTORY_FIELDS = f"{FUNDAMENTAL_FIELDS},update_flag"


def fetch_tushare_fundamental_factors(
    symbols: Iterable[str],
    *,
    as_of: date | None = None,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load point-in-time fundamental observations from Tushare ``fina_indicator``.

    Only records whose ``ann_date`` is on or before ``as_of`` are eligible. The
    API returns the announcement date and report period so callers can audit
    when each observation became available.
    """
    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载财务指标")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    wanted = tuple(
        dict.fromkeys(_normalize_tushare_symbol(symbol) for symbol in symbols)
    )
    if not wanted:
        raise ValueError("symbols cannot be empty")
    cutoff = as_of or china_today()
    cache_root = Path(cache_dir).expanduser() if cache_dir else None
    observations: list[dict[str, Any]] = []
    errors: list[str] = []
    cache_hits = 0
    cache_misses = 0

    for index, code in enumerate(wanted):
        cache_path = _cache_path(cache_root, code, cutoff)
        records: list[dict[str, Any]] | None = (
            None if force_refresh else _read_cache(cache_path, cache_ttl_seconds)
        )
        if records is not None:
            cache_hits += 1
        else:
            cache_misses += 1
            try:
                start_date = cutoff - timedelta(days=365 * 4)
                frame = _tushare_http_query(
                    access_token,
                    "fina_indicator",
                    {
                        "ts_code": code,
                        "start_date": start_date.strftime("%Y%m%d"),
                        "end_date": cutoff.strftime("%Y%m%d"),
                    },
                    fields=FUNDAMENTAL_FIELDS,
                )
                records = _latest_records(frame, code, cutoff)
                _write_cache(cache_path, records)
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                LOGGER.exception(
                    "Current financial query failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )
                errors.append(f"{code}: {type(error).__name__}")
                records = []
        observations.extend(records)
        if index < len(wanted) - 1:
            time.sleep(0.15)

    metadata = {
        "source": "tushare:fina_indicator_vip",
        "endpoint": "fina_indicator_vip",
        "as_of": cutoff.isoformat(),
        "symbols": list(wanted),
        "observation_count": len(observations),
        "available_symbols": sorted({str(item["ts_code"]) for item in observations}),
        "missing_symbols": sorted(
            set(wanted) - {str(item["ts_code"]) for item in observations}
        ),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": errors[:20],
        "status": "available" if observations else "unavailable" if errors else "empty",
    }
    return observations, metadata


def fetch_tushare_fundamental_history(
    symbols: Iterable[str],
    *,
    start_date: date,
    end_date: date,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch the announcement-dated history needed to build an as-of PIT panel.

    Report-period ``end_date`` is retained as metadata, never treated as the date
    the value became knowable. Date-only announcements are conservatively assigned
    a 16:00 China-time availability timestamp and materialized from the next session.
    """

    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载历史 PIT 财务指标")
    if start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    wanted = tuple(
        dict.fromkeys(_normalize_tushare_symbol(symbol) for symbol in symbols)
    )
    if not wanted:
        raise ValueError("symbols cannot be empty")
    cache_root = Path(cache_dir).expanduser() if cache_dir else None
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    cache_hits = cache_misses = 0

    for index, code in enumerate(wanted):
        cache_path = _history_cache_path(
            cache_root, "fina_indicator_vip", code, start_date, end_date
        )
        report_data_progress(index, len(wanted), code)
        records = (
            _read_completed_job_cache(cache_path, cache_ttl_seconds)
            if force_refresh
            else _read_cache(cache_path, cache_ttl_seconds)
        )
        if records is not None:
            cache_hits += 1
        else:
            cache_misses += 1
            try:
                frame = _tushare_http_query(
                    access_token,
                    "fina_indicator",
                    {
                        "ts_code": code,
                        "start_date": start_date.strftime("%Y%m%d"),
                        "end_date": end_date.strftime("%Y%m%d"),
                    },
                    fields=FUNDAMENTAL_HISTORY_FIELDS,
                )
                records = _history_rows(frame, code)
                _write_cache(cache_path, records)
            except ResourceLimitExceeded:
                raise
            except Exception as error:
                LOGGER.exception(
                    "Historical financial query failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )
                errors.append(f"{code}: {type(error).__name__}")
                records = []
        output.extend(records)
        if index < len(wanted) - 1:
            time.sleep(0.15)

    metadata = {
        "source": "tushare:fina_indicator_vip",
        "endpoint": "fina_indicator_vip",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": list(wanted),
        "record_count": len(output),
        "available_symbols": sorted({str(item["ts_code"]) for item in output}),
        "missing_symbols": sorted(
            set(wanted) - {str(item["ts_code"]) for item in output}
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
        "availability_basis": "ann_date_at_16_00_china_then_next_trading_session",
        "revision_vintage_caveat": "Tushare provides ann_date/update_flag; this adapter cannot reconstruct an unexposed first-seen timestamp for silent historical corrections.",
    }
    return output, metadata


def _history_rows(frame: Any, code: str) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", False):
        return []
    output: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        announcement_date = _parse_date(row.get("ann_date"))
        report_date = _parse_date(row.get("end_date"))
        if announcement_date is None or report_date is None:
            continue
        available_at = datetime.combine(
            announcement_date,
            datetime.min.time(),
            tzinfo=timezone(timedelta(hours=8)),
        ).replace(hour=16)
        row_version = hashlib.sha256(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        for metric, label in FUNDAMENTAL_METRICS.items():
            raw_value = _finite_float(row.get(metric))
            if raw_value is None:
                continue
            output.append(
                {
                    "ts_code": str(row.get("ts_code") or code).upper(),
                    "symbol": str(row.get("ts_code") or code).upper(),
                    "metric": metric,
                    "name": label,
                    "value": raw_value / 100.0,
                    "raw_value": raw_value,
                    "report_date": report_date.isoformat(),
                    "announcement_date": announcement_date.isoformat(),
                    "available_at": available_at.isoformat(),
                    "availability_basis": "provider_ann_date_conservative_16_00",
                    "update_flag": str(row.get("update_flag") or ""),
                    "source_version": row_version,
                    "source": "Tushare:fina_indicator_vip",
                }
            )
    return output


def _history_cache_path(
    cache_dir: Path | None, endpoint: str, code: str, start_date: date, end_date: date
) -> Path | None:
    if cache_dir is None:
        return None
    safe_code = code.replace(".", "_")
    return (
        cache_dir
        / f"tushare_{endpoint}_history_{safe_code}_{start_date.isoformat()}_{end_date.isoformat()}.json"
    )


def _latest_records(frame: Any, code: str, cutoff: date) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", False):
        return []
    rows = []
    for row in frame.to_dict(orient="records"):
        announcement_date = _parse_date(row.get("ann_date"))
        report_date = _parse_date(row.get("end_date"))
        if (
            announcement_date is None
            or report_date is None
            or announcement_date > cutoff
        ):
            continue
        rows.append((announcement_date, report_date, row))
    if not rows:
        return []
    announcement_date, report_date, row = max(rows, key=lambda item: (item[0], item[1]))
    available_at = datetime.combine(
        announcement_date,
        datetime.min.time(),
        tzinfo=timezone(timedelta(hours=8)),
    ).replace(hour=16)
    records: list[dict[str, Any]] = []
    for metric, label in FUNDAMENTAL_METRICS.items():
        raw_value = _finite_float(row.get(metric))
        if raw_value is None:
            continue
        records.append(
            {
                "ts_code": code,
                "symbol": code.split(".", 1)[0],
                "metric": metric,
                "name": label,
                "value": raw_value / 100.0,
                "raw_value": raw_value,
                "report_date": report_date.isoformat(),
                "announcement_date": announcement_date.isoformat(),
                "available_at": available_at.isoformat(),
                "source": "Tushare:fina_indicator_vip",
            }
        )
    return records


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _cache_path(cache_dir: Path | None, code: str, as_of: date) -> Path | None:
    if cache_dir is None:
        return None
    safe_code = code.replace(".", "_")
    return (
        cache_dir / f"tushare_fina_indicator_vip_{safe_code}_{as_of.isoformat()}.json"
    )


def _read_cache(path: Path | None, ttl_seconds: int) -> list[dict[str, Any]] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        records = payload.get("records")
        if time.time() - fetched_at > ttl_seconds or not isinstance(records, list):
            return None
        return [item for item in records if isinstance(item, dict)]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(path: Path | None, records: list[dict[str, Any]]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(
                {"fetched_at": time.time(), "records": records}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass


def _read_completed_job_cache(
    path: Path | None, ttl_seconds: int
) -> list[dict[str, Any]] | None:
    """Only resume non-empty evidence fetched during this frozen job."""
    cutoff = DATA_CACHE_NOT_BEFORE.get()
    if cutoff is None or path is None:
        return None
    try:
        fetched_at = float(
            json.loads(path.read_text(encoding="utf-8")).get("fetched_at", 0)
        )
        return (
            (_read_cache(path, ttl_seconds) or None) if fetched_at >= cutoff else None
        )
    except (OSError, ValueError, TypeError):
        return None
