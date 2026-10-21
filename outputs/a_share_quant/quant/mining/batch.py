from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .genetic import canonical_expression_hash


def require_supported_acceptance_candidates(runs):
    """Do not consume a sealed opportunity while silently skipping deep scores."""
    for run in runs:
        result = run.get("result")
        if not isinstance(result, Mapping):
            continue
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if (
                candidate.get("source") == "external_deep"
                or candidate.get("factor_type") == "external_deep"
                or candidate.get("model_id")
            ):
                raise PermissionError(
                    "Deep score acceptance adapter is not connected; "
                    "sealed opportunity was not consumed"
                )


def aggregate_factor_mining_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine all exploratory runs for one sealed snapshot into one test batch."""

    n_tested = 0
    grouped: dict[str, dict[str, Any]] = {}
    for run in runs:
        try:
            n_tested += max(0, int(run.get("n_tested", 0)))
        except (TypeError, ValueError):
            pass
        run_id = str(run.get("run_id") or "")
        result = run.get("result")
        candidates = result.get("candidates") if isinstance(result, Mapping) else None
        if not isinstance(candidates, list):
            continue
        for raw in candidates:
            if not isinstance(raw, Mapping):
                continue
            expression = str(raw.get("expression") or "").strip()
            model_id = str(raw.get("model_id") or "").strip()
            if not expression and not model_id:
                continue
            if model_id:
                identity = "deep:" + model_id + ":" + str(raw.get("version") or "")
            else:
                try:
                    identity = canonical_expression_hash(expression)
                except (ValueError, SyntaxError):
                    identity = expression
            entry = grouped.get(identity)
            candidate = dict(raw)
            if entry is None:
                entry = {"candidate": candidate, "run_ids": set(), "factor_keys": set()}
                grouped[identity] = entry
            elif _fitness(candidate) > _fitness(entry["candidate"]):
                candidate.setdefault(
                    "cross_universe_reference",
                    entry["candidate"].get("cross_universe_reference"),
                )
                entry["candidate"] = candidate
            elif not entry["candidate"].get(
                "cross_universe_reference"
            ) and candidate.get("cross_universe_reference"):
                entry["candidate"]["cross_universe_reference"] = candidate[
                    "cross_universe_reference"
                ]
            if run_id:
                entry["run_ids"].add(run_id)
            factor_key = str(raw.get("factor_key") or "").strip()
            if factor_key:
                entry["factor_keys"].add(factor_key)

    combined: list[dict[str, Any]] = []
    for identity, entry in grouped.items():
        candidate = dict(entry["candidate"])
        factor_keys = sorted(entry["factor_keys"])
        if candidate.get("expression"):
            candidate["expression"] = str(candidate["expression"]).strip()
        candidate["canonical_hash"] = identity
        candidate["factor_key"] = (
            factor_keys[0] if factor_keys else str(candidate.get("factor_key") or "")
        )
        candidate["factor_keys"] = factor_keys
        candidate["source_run_ids"] = sorted(entry["run_ids"])
        combined.append(candidate)
    combined.sort(
        key=lambda item: (
            _fitness(item),
            str(item.get("expression") or item.get("model_id") or ""),
        ),
        reverse=True,
    )
    return {"n_tested": n_tested, "candidates": combined}


def _fitness(candidate: Mapping[str, Any]) -> float:
    raw_value = candidate.get("fitness")
    if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
        return -math.inf
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return -math.inf
    return value if math.isfinite(value) else -math.inf
