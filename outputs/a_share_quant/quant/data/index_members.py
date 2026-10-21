from __future__ import annotations

import calendar
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quant.time_utils import china_today

from .adapters import _normalize_tushare_symbol, _tushare_http_query

INDEX_UNIVERSE_NAMES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "399006.SZ": "创业板指",
}


@dataclass(frozen=True)
class _IndexWeightRecord:
    symbol: str
    trade_date: date
    weight: float | None


def fetch_tushare_index_member_history(
    index_code: str,
    *,
    start_date: date,
    end_date: date,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
) -> dict[str, Any]:
    """Fetch and cache the complete monthly index_weight snapshot sequence.

    Tushare's index_weight endpoint is queried one calendar month at a time to
    avoid silently truncating a multi-year response. One year of leading
    history is included so the first requested session can use the latest
    snapshot that was already known at that time.
    """

    code = str(index_code).strip().upper()
    if not code or "." not in code:
        raise ValueError("index_code must include a market suffix, e.g. 000300.SH")
    if start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载历史指数成分序列")

    history_start = _add_months(start_date, -12)
    cache_dir_path = Path(cache_dir).expanduser() if cache_dir else None
    path = _history_cache_path(cache_dir_path, code, start_date, end_date)
    cached = _read_cache(path, cache_ttl_seconds)
    if cached is not None:
        return cached

    grouped: dict[date, dict[str, float | None]] = {}
    for month_start, month_end in _month_ranges(history_start, end_date):
        frame = _tushare_http_query(
            access_token,
            "index_weight",
            {
                "index_code": code,
                "start_date": month_start.strftime("%Y%m%d"),
                "end_date": month_end.strftime("%Y%m%d"),
            },
            fields="index_code,con_code,trade_date,weight",
        )
        rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
        for row in rows:
            trade_day = _parse_date(row.get("trade_date"))
            raw_symbol = str(row.get("con_code", "")).strip()
            if (
                trade_day is None
                or trade_day < month_start
                or trade_day > month_end
                or trade_day < history_start
                or trade_day > end_date
                or not raw_symbol
            ):
                continue
            row_index = str(row.get("index_code") or code).strip().upper()
            if row_index and row_index != code:
                continue
            try:
                symbol = _normalize_tushare_symbol(raw_symbol)
            except ValueError:
                continue
            grouped.setdefault(trade_day, {})[symbol] = _float_or_none(row.get("weight"))

    if not grouped:
        raise RuntimeError(f"Tushare 未返回 {code} 在 {history_start.isoformat()} 至 {end_date.isoformat()} 的历史成分快照")
    snapshots = [
        {
            "trade_date": trade_day.isoformat(),
            "members": [
                {"ts_code": symbol, "weight": weight}
                for symbol, weight in sorted(members.items())
            ],
        }
        for trade_day, members in sorted(grouped.items())
        if members
    ]
    requested_start: str = start_date.strftime("%Y-%m-%d")
    eligible = [item for item in snapshots if str(item["trade_date"]) <= requested_start]
    if not eligible:
        raise RuntimeError(
            f"{code} 历史成分序列没有覆盖研究起点 {start_date.isoformat()} 之前的快照；"
            "已停止，不能使用当前成分回填历史"
        )
    content = json.dumps(
        {"index_code": code, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "snapshots": snapshots},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result: dict[str, Any] = {
        "ok": True,
        "source": "tushare:index_weight",
        "index_code": code,
        "index_name": INDEX_UNIVERSE_NAMES.get(code, code),
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "history_start": history_start.isoformat(),
        "first_snapshot": snapshots[0]["trade_date"],
        "last_snapshot": snapshots[-1]["trade_date"],
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
        "data_version": hashlib.sha256(content.encode("utf-8")).hexdigest()[:20],
        "stale": False,
    }
    _write_cache(path, result)
    return result


def fetch_tushare_index_members(
    index_code: str,
    *,
    as_of: date | None = None,
    token: str | None = None,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: int = 24 * 60 * 60,
) -> dict[str, Any]:
    """Return the latest historical ``index_weight`` snapshot not after as_of."""
    code = str(index_code).strip().upper()
    if not code or "." not in code:
        raise ValueError("index_code must include a market suffix, e.g. 000300.SH")
    access_token = str(token or "").strip()
    if not access_token:
        raise RuntimeError("未配置 TUSHARE_TOKEN，无法加载历史指数成分股")
    if cache_ttl_seconds <= 0:
        raise ValueError("cache_ttl_seconds must be positive")
    cutoff = as_of or china_today()
    cache_dir_path = Path(cache_dir).expanduser() if cache_dir else None
    path = _cache_path(cache_dir_path, code, cutoff)
    cached = _read_cache(path, cache_ttl_seconds)
    if cached is not None:
        return cached

    start_date = cutoff - timedelta(days=365 * 3)
    frame = _tushare_http_query(
        access_token,
        "index_weight",
        {
            "index_code": code,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": cutoff.strftime("%Y%m%d"),
        },
        fields="index_code,con_code,trade_date,weight",
    )
    rows = [] if frame is None or getattr(frame, "empty", False) else frame.to_dict(orient="records")
    parsed: list[_IndexWeightRecord] = []
    for row in rows:
        trade_date = _parse_date(row.get("trade_date"))
        symbol = _normalize_tushare_symbol(str(row.get("con_code", "")))
        if trade_date is None or trade_date > cutoff or not symbol:
            continue
        parsed.append(_IndexWeightRecord(symbol, trade_date, _float_or_none(row.get("weight"))))
    if not parsed:
        raise RuntimeError(f"Tushare 未返回 {code} 在 {cutoff.isoformat()} 前的指数成分股")
    latest_date = max(item.trade_date for item in parsed)
    members = [
        {
            "ts_code": item.symbol,
            "symbol": item.symbol.split(".", 1)[0],
            "weight": item.weight,
        }
        for item in parsed
        if item.trade_date == latest_date
    ]
    result: dict[str, Any] = {
        "ok": True,
        "source": "tushare:index_weight",
        "index_code": code,
        "index_name": INDEX_UNIVERSE_NAMES.get(code, code),
        "as_of": cutoff.isoformat(),
        "trade_date": latest_date.isoformat(),
        "count": len(members),
        "members": sorted(members, key=lambda item: item["ts_code"]),
        "stale": False,
    }
    _write_cache(path, result)
    return result


def _cache_path(cache_dir: Path | None, code: str, as_of: date) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / f"tushare_index_weight_{code.replace('.', '_')}_{as_of.isoformat()}.json"


def _history_cache_path(cache_dir: Path | None, code: str, start: date, end: date) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / (
        f"tushare_index_weight_history_{code.replace('.', '_')}_"
        f"{start.isoformat()}_{end.isoformat()}.json"
    )


def _add_months(value: date, amount: int) -> date:
    month_index = value.year * 12 + value.month - 1 + amount
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, last_day)
        ranges.append((max(start, cursor), min(end, month_end)))
        cursor = _add_months(cursor, 1).replace(day=1)
    return ranges


def _read_cache(path: Path | None, ttl_seconds: int) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("fetched_at", 0)) > ttl_seconds:
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(path: Path | None, result: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"fetched_at": time.time(), "result": result}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
