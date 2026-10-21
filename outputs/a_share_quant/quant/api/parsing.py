"""Validation and normalization for user-controlled API inputs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from quant.errors import InputTypeError

MAX_RESEARCH_SYMBOLS = 600


def object_mapping(value: object) -> dict[str, object]:
    """Narrow a decoded object to string-keyed JSON properties."""

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def object_list(value: object) -> list[object]:
    """Narrow a decoded JSON array while preserving its values."""

    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def bounded_int(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputTypeError("value must be an integer")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("value must be a finite integer")
    parsed = int(value)
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("value must be an integer")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return parsed


def optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    day = text.split("T", 1)[0].split(" ", 1)[0]
    if len(day) != 10:
        raise ValueError(f"invalid date: {value}")
    try:
        return date.fromisoformat(day)
    except ValueError as error:
        raise ValueError(f"invalid date: {value}") from error


def symbols(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("symbols must be a non-empty list")
    if len(value) > MAX_RESEARCH_SYMBOLS:
        raise ValueError(
            f"symbols cannot contain more than {MAX_RESEARCH_SYMBOLS} items"
        )
    normalized = [str(symbol).strip() for symbol in value if str(symbol).strip()]
    if not normalized:
        raise ValueError("symbols cannot be empty")
    return normalized


def normalize_paper_symbol(value: object) -> str:
    raw = str(value).strip().upper().replace(" ", "")
    if "." in raw:
        code, market = raw.split(".", 1)
        if market not in {"SH", "SZ", "BJ"}:
            raise ValueError("symbol market must be SH, SZ, or BJ")
    else:
        code = raw
        market = (
            "BJ"
            if code.startswith(("4", "8", "9"))
            else "SH"
            if code.startswith(("5", "6"))
            else "SZ"
        )
    if len(code) != 6 or not code.isdigit():
        raise ValueError("symbol must be a six-digit A-share code")
    return f"{code}.{market}"


def symbols_from_query(values: list[str]) -> list[str]:
    if not values:
        return []
    raw = values[0].replace("，", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]
