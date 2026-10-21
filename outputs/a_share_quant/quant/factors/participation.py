"""Streaming, point-in-time coverage and cross-sectional redundancy diagnostics."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from .store import FactorDefinition


class ParticipationAudit:
    def __init__(self) -> None:
        self.evaluations = 0
        self.symbol_observations = 0
        self.covered: dict[str, int] = defaultdict(int)
        self.used: dict[str, int] = defaultdict(int)
        self.pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.latest: dict[str, Any] = {}

    def observe(
        self,
        definitions: Sequence[FactorDefinition],
        raw: Mapping[str, Mapping[str, float]],
        ranks: Mapping[str, Mapping[str, float]],
        symbols: Sequence[str],
        eligible: set[str],
        weights: Mapping[str, float],
        day: str,
    ) -> None:
        self.evaluations += 1
        self.symbol_observations += len(symbols)
        factors = []
        for item in definitions:
            count = sum(item.key in raw.get(symbol, {}) for symbol in symbols)
            used = sum(
                item.key in ranks.get(symbol, {}) and weights[item.key] > 0
                for symbol in eligible
            )
            self.covered[item.key] += count
            self.used[item.key] += used
            factors.append(
                {
                    "key": item.key,
                    "name": item.name,
                    "weight": weights[item.key],
                    "covered": count,
                    "used": used,
                    "requested": len(symbols),
                    "coverage": count / len(symbols) if symbols else 0.0,
                    "missing_reason": None
                    if count == len(symbols)
                    else "missing_point_in_time_financial"
                    if item.factor_type == "fundamental"
                    else "insufficient_history_or_invalid_value",
                }
            )
        # Correlations are diagnostics, never used to fit or change weights.
        enabled = [item.key for item in definitions if weights[item.key] > 0]
        for left, right in combinations(enabled, 2):
            common = [
                symbol
                for symbol in symbols
                if left in ranks.get(symbol, {}) and right in ranks.get(symbol, {})
            ]
            if len(common) < 20:
                continue
            xs = [ranks[symbol][left] for symbol in common]
            ys = [ranks[symbol][right] for symbol in common]
            xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
            variance = math.sqrt(
                sum((x - xm) ** 2 for x in xs) * sum((y - ym) ** 2 for y in ys)
            )
            if variance > 0:
                self.pairs[(left, right)].append(
                    sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / variance
                )
        self.latest = {
            "day": day,
            "requested_symbols": len(symbols),
            "eligible_symbols": len(eligible),
            "excluded_symbols": len(symbols) - len(eligible),
            "factors": factors,
        }

    def report(self) -> dict[str, Any]:
        return {
            "status": "available" if self.evaluations else "not_evaluated",
            "evaluations": self.evaluations,
            "symbol_observations": self.symbol_observations,
            "latest": self.latest,
            "factors": [
                {
                    **item,
                    "covered_observations": self.covered[item["key"]],
                    "used_observations": self.used[item["key"]],
                    "overall_coverage": self.covered[item["key"]]
                    / self.symbol_observations
                    if self.symbol_observations
                    else 0.0,
                }
                for item in self.latest.get("factors", [])
            ],
            "correlations": [
                {
                    "left": left,
                    "right": right,
                    "mean_rank_correlation": sum(values) / len(values),
                    "mean_absolute_correlation": sum(abs(value) for value in values)
                    / len(values),
                    "evaluations": len(values),
                    "redundancy_warning": sum(abs(value) for value in values)
                    / len(values)
                    >= 0.8,
                }
                for (left, right), values in sorted(self.pairs.items())
            ],
            "correlation_basis": "每次信号截面的有向分位排名相关；至少20只共同股票；绝对相关均值≥0.8提示重复，不自动删因子",
        }
