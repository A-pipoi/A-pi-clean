from __future__ import annotations

"""Point-in-time access to Tushare's three primary financial statements.

The statement APIs return report-period data.  A backtest may only use a row
after its announcement date, so this module keeps both dates and selects the
latest announced row that is available at ``as_of``.  Raw columns are kept in
the response for auditability; no statement value is silently forward-filled.
"""

import json
import logging
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded
from quant.time_utils import china_today

from .adapters import _normalize_tushare_symbol, _tushare_http_query

LOGGER = logging.getLogger(__name__)

STATEMENT_ENDPOINTS: dict[str, str] = {
    "income": "income",
    "balancesheet": "balancesheet",
    "cashflow": "cashflow",
}

STATEMENT_LABELS: dict[str, str] = {
    "income": "利润表",
    "balancesheet": "资产负债表",
    "cashflow": "现金流量表",
}


def fetch_tushare_statements(
    symbols: Iterable[str],
    *,
    as_of: date | None = None,
    statement: str = "all",
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
    force_refresh: bool = False,
) -> tuple[dict[str, dict[str, dict[str, Any] | None]], dict[str, Any]]:
    """Fetch the latest point-in-time row for each requested statement.

    ``statement`` accepts ``income``, ``balancesheet``, ``cashflow`` or
    ``all``.  Tushare's complete field set is requested so callers can use
    the raw three-statement columns without another adapter change.
    """

    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载三大财务报表")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    selected = _selected_statements(statement)
    wanted = tuple(
        dict.fromkeys(_normalize_tushare_symbol(symbol) for symbol in symbols)
    )
    if not wanted:
        raise ValueError("symbols cannot be empty")

    cutoff = as_of or china_today()
    cache_root = Path(cache_dir).expanduser() if cache_dir else None
    result: dict[str, dict[str, dict[str, Any] | None]] = {
        code: {name: None for name in selected} for code in wanted
    }
    errors: list[str] = []
    cache_hits = 0
    cache_misses = 0

    for symbol_index, code in enumerate(wanted):
        for statement_index, statement_name in enumerate(selected):
            cache_path = _cache_path(cache_root, code, statement_name, cutoff)
            row = None if force_refresh else _read_cache(cache_path, cache_ttl_seconds)
            if row is not None:
                cache_hits += 1
            else:
                cache_misses += 1
                try:
                    frame = _tushare_http_query(
                        access_token,
                        STATEMENT_ENDPOINTS[statement_name],
                        {
                            "ts_code": code,
                            "start_date": (cutoff - timedelta(days=365 * 5)).strftime(
                                "%Y%m%d"
                            ),
                            "end_date": cutoff.strftime("%Y%m%d"),
                        },
                        fields="",
                    )
                    row = _latest_statement_row(frame, code, statement_name, cutoff)
                    _write_cache(cache_path, row)
                except ResourceLimitExceeded:
                    raise
                except Exception as error:
                    # Never persist provider exception messages or traceback chains.
                    LOGGER.exception(
                        "Financial statement query failed: %s",
                        type(error).__name__,
                        exc_info=None,
                    )
                    errors.append(f"{code}/{statement_name}: {type(error).__name__}")
                    row = None
            result[code][statement_name] = row
            if statement_index < len(selected) - 1 or symbol_index < len(wanted) - 1:
                time.sleep(0.15)

    available = {
        code: [name for name, row in statements.items() if row is not None]
        for code, statements in result.items()
    }
    metadata = {
        "source": "tushare:" + "+".join(STATEMENT_ENDPOINTS[name] for name in selected),
        "endpoints": [STATEMENT_ENDPOINTS[name] for name in selected],
        "statement_types": list(selected),
        "as_of": cutoff.isoformat(),
        "symbols": list(wanted),
        "available": available,
        "available_symbols": sorted(code for code, items in available.items() if items),
        "missing_symbols": sorted(
            code for code, items in available.items() if not items
        ),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": errors[:20],
        "status": "available"
        if any(available.values())
        else "unavailable"
        if errors
        else "empty",
    }
    return result, metadata


def flatten_statement_rows(
    statements: dict[str, dict[str, dict[str, Any] | None]],
) -> list[dict[str, Any]]:
    """Flatten the grouped response for tables and exports."""

    rows: list[dict[str, Any]] = []
    for code, items in statements.items():
        for row in items.values():
            if row is None:
                continue
            rows.append({"ts_code": code, **row})
    return rows


def _selected_statements(statement: str) -> tuple[str, ...]:
    normalized = str(statement or "all").strip().lower()
    if normalized == "all":
        return tuple(STATEMENT_ENDPOINTS)
    if normalized not in STATEMENT_ENDPOINTS:
        choices = ", ".join(["all", *STATEMENT_ENDPOINTS])
        raise ValueError(f"statement must be one of: {choices}")
    return (normalized,)


def _latest_statement_row(
    frame: Any, code: str, statement: str, cutoff: date
) -> dict[str, Any] | None:
    if frame is None or getattr(frame, "empty", False):
        return None
    candidates: list[tuple[date, date, str, dict[str, Any]]] = []
    for source_row in frame.to_dict(orient="records"):
        announcement_date = _parse_date(source_row.get("ann_date"))
        report_date = _parse_date(source_row.get("end_date"))
        # Missing announcement dates are deliberately excluded: using them
        # would turn an unknown publication time into a hidden look-ahead.
        if (
            announcement_date is None
            or report_date is None
            or announcement_date > cutoff
        ):
            continue
        report_type = str(source_row.get("report_type") or "")
        candidates.append(
            (announcement_date, report_date, report_type, dict(source_row))
        )
    if not candidates:
        return None

    announcement_date, report_date, report_type, raw = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2]),
    )
    available_at = datetime.combine(
        announcement_date,
        datetime.min.time(),
        tzinfo=timezone(timedelta(hours=8)),
    ).replace(hour=16)
    return {
        "statement": statement,
        "statement_name": STATEMENT_LABELS[statement],
        "ts_code": code,
        "symbol": code.split(".", 1)[0],
        "report_date": report_date.isoformat(),
        "announcement_date": announcement_date.isoformat(),
        "available_at": available_at.isoformat(),
        "report_type": report_type,
        "raw": raw,
        "source": f"Tushare:{STATEMENT_ENDPOINTS[statement]}",
    }


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


def _cache_path(
    cache_dir: Path | None, code: str, statement: str, as_of: date
) -> Path | None:
    if cache_dir is None:
        return None
    safe_code = code.replace(".", "_")
    return cache_dir / f"tushare_{statement}_{safe_code}_{as_of.isoformat()}.json"


def _read_cache(path: Path | None, ttl_seconds: int) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        row = payload.get("row")
        if time.time() - fetched_at > ttl_seconds or not isinstance(row, dict):
            return None
        return row
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(path: Path | None, row: dict[str, Any] | None) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"fetched_at": time.time(), "row": row}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        pass
