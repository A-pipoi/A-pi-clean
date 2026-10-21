"""Training-only diagnostics. Price-label evidence is not an executable backtest."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

from quant.factors.analysis import _correlation, _rank, rank_ic
from quant.factors.expression import FactorPanel, Panel


def ic_half_life(horizons: Mapping) -> dict:
    curve = [
        {"horizon": int(key), "mean_ic": value.get("mean_ic")}
        for key, value in horizons.items()
    ]
    curve.sort(key=lambda point: point["horizon"])
    usable = [
        point
        for point in curve
        if isinstance(point["mean_ic"], (int, float))
        and math.isfinite(point["mean_ic"])
    ]
    if not usable or max(abs(point["mean_ic"]) for point in usable) == 0:
        return {"curve": curve, "status": "unavailable", "half_life": None}
    peak = max(usable, key=lambda point: abs(point["mean_ic"]))
    direction = 1 if peak["mean_ic"] > 0 else -1
    crossing = next(
        (
            point["horizon"]
            for point in usable
            if point["horizon"] > peak["horizon"]
            and point["mean_ic"] * direction <= abs(peak["mean_ic"]) / 2
        ),
        None,
    )
    return {
        "curve": curve,
        "peak_horizon": peak["horizon"],
        "half_life": crossing,
        "status": "observed" if crossing else "right_censored",
        "definition": "first horizon after the absolute peak reaching half "
        "its signed IC; no extrapolation",
    }


def registry_correlation_matrix(panel: FactorPanel, registry: Mapping) -> dict:
    """Mean daily cross-sectional correlations, without labels or sealed data."""
    keys = sorted(registry)
    for values in registry.values():
        if values is not None and (
            len(values) != len(panel.dates)
            or any(len(row) != len(panel.symbols) for row in values)
        ):
            raise ValueError("Correlation matrix panel dimensions mismatch")
    matrix: list[list[dict[str, float | None] | None]] = [
        [None for _ in keys] for _ in keys
    ]
    for left_index, left_key in enumerate(keys):
        for right_index in range(left_index, len(keys)):
            left, right = registry[left_key], registry[keys[right_index]]
            pearson, spearman, samples = [], [], 0
            if left is not None and right is not None:
                for left_row, right_row in zip(left, right):
                    pairs = [
                        (a, b)
                        for a, b in zip(left_row, right_row)
                        if a is not None
                        and b is not None
                        and math.isfinite(a)
                        and math.isfinite(b)
                    ]
                    if len(pairs) < 3:
                        continue
                    a, b = [p[0] for p in pairs], [p[1] for p in pairs]
                    p, s = _correlation(a, b), _correlation(_rank(a), _rank(b))
                    if p is not None and s is not None:
                        pearson.append(p)
                        spearman.append(s)
                        samples += len(pairs)
            cell = {
                "pearson": sum(pearson) / len(pearson) if pearson else None,
                "spearman": sum(spearman) / len(spearman) if spearman else None,
                "observations": len(pearson),
                "paired_samples": samples,
            }
            matrix[left_index][right_index] = cell
            matrix[right_index][left_index] = dict(cell)
    return {
        "keys": keys,
        "matrix": matrix,
        "sealed_dates_used": False,
        "scope": "enabled_registry_plus_current_candidate_training_only",
        "method": "mean_daily_cross_sectional_pairwise_complete_minimum_3",
        "start": str(min(panel.dates)) if panel.dates else None,
        "end": str(max(panel.dates)) if panel.dates else None,
    }


def registry_diagnostics(
    panel: FactorPanel,
    values: Panel,
    registry: Mapping[str, Panel],
    *,
    horizon: int = 5,
) -> dict:
    correlations = []
    residual_ics = []
    close = panel.fields["close"]
    for name, reference in registry.items():
        pearson, spearman = [], []
        for left_row, right_row in zip(values, reference):
            pairs = [
                (a, b)
                for a, b in zip(left_row, right_row)
                if a is not None
                and b is not None
                and math.isfinite(a)
                and math.isfinite(b)
            ]
            if len(pairs) < 3:
                continue
            left, right = [a for a, _ in pairs], [b for _, b in pairs]
            p, s = _correlation(left, right), _correlation(_rank(left), _rank(right))
            if p is not None:
                pearson.append(p)
            if s is not None:
                spearman.append(s)
        correlations.append(
            {
                "factor_key": name,
                "pearson": sum(pearson) / len(pearson) if pearson else None,
                "spearman": sum(spearman) / len(spearman) if spearman else None,
                "max_abs_daily_correlation": max(
                    (abs(p) for p in pearson), default=None
                ),
                "observations": len(pearson),
            }
        )
    for row in range(max(0, len(panel.dates) - horizon)):
        columns = [
            i
            for i, value in enumerate(values[row])
            if value is not None
            and math.isfinite(value)
            and all(
                reference[row][i] is not None
                and math.isfinite(cast(float, reference[row][i]))
                for reference in registry.values()
            )
            and close[row][i] is not None
            and cast(float, close[row][i]) > 0
            and close[row + horizon][i] is not None
            and math.isfinite(cast(float, close[row + horizon][i]))
        ]
        if not registry or len(columns) <= len(registry) + 2:
            continue
        # Intercept-adjusted Gram-Schmidt OLS, including collinear inputs.
        target = [float(cast(float, values[row][i])) for i in columns]
        average = sum(target) / len(target)
        residual = [v - average for v in target]
        basis: list[list[float]] = []
        for reference in registry.values():
            vector = [float(cast(float, reference[row][i])) for i in columns]
            mean = sum(vector) / len(vector)
            vector = [v - mean for v in vector]
            for previous in basis:
                projection = sum(a * b for a, b in zip(vector, previous))
                vector = [a - projection * b for a, b in zip(vector, previous)]
            length = math.sqrt(sum(v * v for v in vector))
            if length > 1e-10:
                basis.append([v / length for v in vector])
        for vector in basis:
            coefficient = sum(a * b for a, b in zip(residual, vector))
            residual = [a - coefficient * b for a, b in zip(residual, vector)]
        if max(abs(v) for v in residual) <= 1e-10:
            continue
        ic = rank_ic(
            {panel.symbols[i]: value for i, value in zip(columns, residual)},
            {
                panel.symbols[i]: cast(float, close[row + horizon][i])
                / cast(float, close[row][i])
                - 1
                for i in columns
            },
        )
        if ic is not None:
            residual_ics.append(ic)
    maxima = [
        entry["max_abs_daily_correlation"]
        for entry in correlations
        if entry["max_abs_daily_correlation"] is not None
    ]
    return {
        "correlations": correlations,
        "max_corr_with_registry": max(maxima, default=None),
        "incremental_rank_ic": sum(residual_ics) / len(residual_ics)
        if residual_ics
        else None,
        "incremental_observations": len(residual_ics),
        "label_horizon": horizon,
        "sealed_dates_used": False,
        "note": "Training-only OLS residual IC; "
        "missing registry observations are not imputed.",
    }
