"""Stable JSON payload conversion for API persistence and responses."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from quant.errors import InputTypeError

from ..data.schema import Bar


def bar_to_payload(bar: Bar) -> dict[str, object]:
    """Serialize a market bar without changing numeric or nullable fields."""

    payload = {
        "symbol": bar.symbol,
        "date": bar.date.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "limit_up": bar.limit_up,
        "limit_down": bar.limit_down,
        "paused": bar.paused,
        "listed": bar.listed,
        "is_st": bar.is_st,
        "industry": bar.industry,
        "is_delisted": bar.is_delisted,
    }
    if bar.signal_close is not None:
        payload.update(
            {
                "signal_open": bar.signal_open,
                "signal_high": bar.signal_high,
                "signal_low": bar.signal_low,
                "signal_close": bar.signal_close,
            }
        )
    return payload


def _snapshot_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputTypeError(f"快照字段 {field} 必须是有限数值")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"快照字段 {field} 必须是有限数值") from None
    if not math.isfinite(result):
        raise ValueError(f"快照字段 {field} 必须是有限数值")
    return result


def _snapshot_flag(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"快照字段 {field} 必须是布尔值")


def bars_from_payload(rows: object) -> list[Bar]:
    """Deserialize a sealed-test bar payload, rejecting malformed rows."""

    if not isinstance(rows, list):
        raise InputTypeError("密封测试快照格式无效")
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, dict):
            raise InputTypeError("密封测试快照包含无效行情行")
        required = {"symbol", "date", "open", "high", "low", "close", "volume"}
        if not required.issubset(row):
            raise ValueError("密封测试快照缺少必要行情字段")
        bars.append(
            Bar(
                symbol=str(row["symbol"]),
                date=date.fromisoformat(str(row["date"])),
                open=_snapshot_number(row["open"], "open"),
                high=_snapshot_number(row["high"], "high"),
                low=_snapshot_number(row["low"], "low"),
                close=_snapshot_number(row["close"], "close"),
                volume=_snapshot_number(row["volume"], "volume"),
                limit_up=_snapshot_number(row["limit_up"], "limit_up")
                if row.get("limit_up") is not None
                else None,
                limit_down=_snapshot_number(row["limit_down"], "limit_down")
                if row.get("limit_down") is not None
                else None,
                paused=_snapshot_flag(row.get("paused", False), "paused"),
                listed=_snapshot_flag(row.get("listed", True), "listed"),
                is_st=_snapshot_flag(row.get("is_st", False), "is_st"),
                industry=str(row["industry"])
                if row.get("industry") is not None
                else None,
                is_delisted=_snapshot_flag(
                    row.get("is_delisted", False), "is_delisted"
                ),
                signal_open=None
                if row.get("signal_open") is None
                else _snapshot_number(row["signal_open"], "signal_open"),
                signal_high=None
                if row.get("signal_high") is None
                else _snapshot_number(row["signal_high"], "signal_high"),
                signal_low=None
                if row.get("signal_low") is None
                else _snapshot_number(row["signal_low"], "signal_low"),
                signal_close=None
                if row.get("signal_close") is None
                else _snapshot_number(row["signal_close"], "signal_close"),
            )
        )
    return sorted(bars, key=lambda bar: (bar.date, bar.symbol))


def sealed_public_payload(sealed: Mapping[str, object] | None) -> dict[str, object]:
    """Return the public subset of a sealed-test ledger record."""

    if not sealed:
        return {"status": "not_created", "sealed": False, "result": None}
    return {
        "id": sealed.get("id"),
        "status": sealed.get("status"),
        "sealed": True,
        "sealed_start": sealed.get("sealed_start"),
        "sealed_end": sealed.get("sealed_end"),
        "data_version": sealed.get("data_version"),
        "dataset_hash": sealed.get("dataset_hash"),
        "metadata": sealed.get("metadata", {}),
        "parameters": sealed.get("parameters", {}),
        "result": sealed.get("result"),
        "created_at": sealed.get("created_at"),
        "evaluated_at": sealed.get("evaluated_at"),
    }
