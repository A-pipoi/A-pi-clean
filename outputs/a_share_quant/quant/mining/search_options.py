"""Bound public search controls before expensive data preparation."""

from __future__ import annotations

import math
from collections.abc import Mapping


def _numeric(value: object) -> str | int | float:
    if not isinstance(value, (str, int, float)):
        raise TypeError("Search numeric options must be numbers or numeric strings")
    return value


def validate_search_options(payload: Mapping[str, object]) -> list[int] | None:
    if str(payload.get("fitness_mode", "icir")) not in {"icir", "legacy"}:
        raise ValueError("fitness_mode must be icir or legacy")
    for name, default in (("correlation_lambda", 0.5), ("complexity_mu", 0.0005)):
        value = float(_numeric(payload.get(name, default)))
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError(f"{name} must be finite and between 0 and 10")
    if "temporal_cv" in payload and not isinstance(payload["temporal_cv"], bool):
        raise ValueError("temporal_cv must be boolean")
    for name in ("field_subset", "operator_subset"):
        if name in payload:
            values = payload[name]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 64
                or not all(
                    isinstance(value, str) and 1 <= len(value) <= 64 for value in values
                )
            ):
                raise ValueError(f"{name} must be a nonempty bounded list of names")
    raw = payload.get("seeds")
    if raw is None:
        return None
    if (
        not isinstance(raw, list)
        or not 2 <= len(raw) <= 4
        or not all(type(seed) is int and 0 <= seed <= 2147483647 for seed in raw)
        or len(set(raw)) != len(raw)
    ):
        raise ValueError("seeds must contain 2–4 distinct nonnegative integers")
    generations = max(1, min(20, int(_numeric(payload.get("generations", 10)))))
    population = max(4, min(100, int(_numeric(payload.get("population_size", 50)))))
    if generations * population * len(raw) > 2000:
        raise ValueError("All seeds together may generate at most 2,000 GP individuals")
    return raw
