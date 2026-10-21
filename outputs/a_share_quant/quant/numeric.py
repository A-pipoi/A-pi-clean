"""Strict conversions for values crossing JSON and SQLite boundaries."""

from __future__ import annotations

import math


def as_int(value: object) -> int:
    """Convert a finite integral value, rejecting booleans and lossy casts."""

    if isinstance(value, bool) or value is None:
        raise ValueError("expected an integer value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValueError("expected a finite integer value")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as error:
            raise ValueError("expected an integer value") from error
    raise ValueError("expected an integer value")


def as_float(value: object) -> float:
    """Convert a finite JSON-like numeric value without accepting booleans."""

    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
        raise ValueError("expected a numeric value")
    try:
        parsed = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("expected a numeric value") from error
    if not math.isfinite(parsed):
        raise ValueError("expected a finite numeric value")
    return parsed
