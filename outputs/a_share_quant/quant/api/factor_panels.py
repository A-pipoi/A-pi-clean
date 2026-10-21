"""PIT feature alignment and reusable factor-panel calculations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from ..data.pit import (
    materialize_pit_feature_observations,
    materialize_pit_industry_observations,
)
from ..data.schema import Bar, FeatureObservation, PITIndustryObservation
from ..factors import calculate_factor
from ..factors.expression import FactorPanel, Panel, evaluate_expression
from ..factors.store import FactorDefinition
from .parsing import object_list, object_mapping


def factor_mining_pit_quality(
    pit_data: dict[str, object], bars: list[Bar]
) -> dict[str, object]:
    dates = sorted({bar.date for bar in bars})
    records: list[dict[str, object]] = []
    for key in ("fundamental_records", "daily_basic_records"):
        records.extend(
            object_mapping(item)
            for item in object_list(pit_data.get(key))
            if isinstance(item, dict)
        )
    observations = materialize_pit_feature_observations(records, dates)
    industry_records = [
        object_mapping(item)
        for item in object_list(pit_data.get("industry_records"))
        if isinstance(item, dict)
    ]
    industry_observations = materialize_pit_industry_observations(
        industry_records, dates
    )
    required_pairs = {(bar.date, bar.symbol) for bar in bars}
    denominator = max(1, len(required_pairs))
    finite_pairs = {
        name: {(item.as_of, item.symbol) for item in observations
               if item.name == name and item.value is not None and math.isfinite(item.value)} & required_pairs
        for name in ("market_cap", "roe", "netprofit_yoy", "tr_yoy", "debt_to_assets")
    }
    market_cap_count = len(finite_pairs["market_cap"])
    financial_coverage = {name: len(pairs) / denominator for name, pairs in finite_pairs.items() if name != "market_cap"}
    required_industry_pairs={(bar.date,bar.symbol) for bar in bars}
    observed_industry_pairs={(item.as_of,item.symbol) for item in industry_observations}
    industry_count = len(required_industry_pairs.intersection(observed_industry_pairs))
    source_meta = object_mapping(pit_data.get("source_metadata"))
    industry_meta = object_mapping(source_meta.get("industry"))
    industry_is_verified_source = (
        industry_meta.get("availability_quality") == "verified"
    )
    size_coverage = market_cap_count / denominator
    industry_coverage = industry_count / max(1,len(required_industry_pairs))
    if industry_is_verified_source and required_industry_pairs and industry_count == len(required_industry_pairs):
        industry_quality = "verified"
    elif industry_count:
        industry_quality = "insufficient_coverage"
    else:
        industry_quality = "unverified"
    return {
        "financial": "available"
        if any(
            item.name in {"roe", "netprofit_yoy", "tr_yoy", "debt_to_assets"}
            and item.value is not None
            for item in observations
        )
        else "unavailable",
        "size": "verified"
        if size_coverage >= 0.6
        else "insufficient_coverage"
        if market_cap_count
        else "unavailable",
        "size_coverage": round(size_coverage, 4),
        "financial_coverage": {name: round(value, 4) for name, value in financial_coverage.items()},
        "financial_coverage_ready": bool(required_pairs) and all(value >= .90 for value in financial_coverage.values()),
        "financial_minimum_coverage": .90,
        "industry": industry_quality,
        "industry_coverage": round(industry_coverage, 4),
        "industry_neutralization_enabled": industry_quality == "verified",
        "market_cap_unit": "10k_CNY",
    }


def pit_observations_for_bars(
    sealed: dict[str, object], bars: list[Bar]
) -> tuple[
    list[FeatureObservation],
    list[PITIndustryObservation],
    dict[str, str],
    dict[str, object],
]:
    bundle = object_mapping(sealed.get("pit_data"))
    feature_records: list[dict[str, object]] = []
    for key in ("fundamental_records", "daily_basic_records"):
        feature_records.extend(
            object_mapping(item)
            for item in object_list(bundle.get(key))
            if isinstance(item, dict)
        )
    sessions = sorted({bar.date for bar in bars})
    features = materialize_pit_feature_observations(feature_records, sessions)
    industry_records = [
        object_mapping(item)
        for item in object_list(bundle.get("industry_records"))
        if isinstance(item, dict)
    ]
    industries = materialize_pit_industry_observations(industry_records, sessions)
    quality_object = factor_mining_pit_quality(bundle, bars)
    quality = {
        "size": str(quality_object["size"]),
        "industry": str(quality_object["industry"]),
    }
    return features, industries, quality, quality_object


def factor_values_for_definition(
    definition: FactorDefinition, bars: Sequence[Bar], panel: FactorPanel
) -> Panel | None:
    if definition.factor_type == "expression":
        try:
            return evaluate_expression(definition.expression, panel).values
        except (TypeError, ValueError):
            return None
    if definition.factor_type == "fundamental":
        return panel.fields.get(definition.metric)
    result: Panel = [[None for _ in panel.symbols] for _ in panel.dates]
    date_rows = {day: index for index, day in enumerate(panel.dates)}
    symbol_columns = {symbol: index for index, symbol in enumerate(panel.symbols)}
    by_symbol: dict[str, list[Bar]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    for symbol, history in by_symbol.items():
        column = symbol_columns[symbol]
        for index, bar in enumerate(history):
            value = calculate_factor(definition, history[: index + 1])
            if value is not None:
                result[date_rows[bar.date]][column] = float(value)
    return result


def factor_panel_correlation(
    left: Panel, right: Panel, panel: FactorPanel, start: date, end: date
) -> float | None:
    correlations: list[float] = []
    for index, day in enumerate(panel.dates):
        if day < start or day > end:
            continue
        pairs = [
            (float(a), float(b))
            for a, b in zip(left[index], right[index])
            if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
        ]
        if len(pairs) < 3:
            continue
        left_mean = sum(a for a, _ in pairs) / len(pairs)
        right_mean = sum(b for _, b in pairs) / len(pairs)
        numerator = sum((a - left_mean) * (b - right_mean) for a, b in pairs)
        denominator = math.sqrt(
            sum((a - left_mean) ** 2 for a, _ in pairs)
            * sum((b - right_mean) ** 2 for _, b in pairs)
        )
        if denominator:
            correlations.append(numerator / denominator)
    return max(correlations, key=abs) if correlations else None
