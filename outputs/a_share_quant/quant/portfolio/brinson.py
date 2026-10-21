from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from ..backtest.engine import BacktestResult
from ..data.pit import materialize_pit_industry_observations
from ..data.schema import Bar, PITIndustryObservation
from ..numeric import as_float as _as_float

SHANGHAI = timezone(timedelta(hours=8))
WEIGHT_CUTOFF = time(9, 30)


def build_strict_pit_brinson_report(
    result: BacktestResult,
    bars: Sequence[Bar],
    *,
    industry_records: Sequence[Mapping[str, object]],
    benchmark_weight_records: Sequence[Mapping[str, object]],
    benchmark_code: str,
    benchmark_name: str | None = None,
    source_version: str | None = None,
) -> dict[str, Any]:
    """Run Brinson-Fachler with Carino linking, failing closed on PIT gaps.

    Industry labels must carry provider-proven ``available_at`` (or an audited
    ``first_seen_at``); effective dates alone are not enough. Benchmark weight
    rows are explicit as-of observations and must have been available before
    that session opened. Returns use close-to-close constituent prices and
    invested-sleeve weights (cash and fees are intentionally outside this
    gross industry decomposition).
    """
    if not industry_records or not benchmark_weight_records:
        return _unavailable("缺少带真实可得时间的行业记录或基准权重记录", source_version=source_version)
    sessions = sorted({point.date for point in result.equity_curve})
    if len(sessions) < 2:
        return _unavailable("净值曲线不足两个交易日", source_version=source_version)
    proven_industry_records: list[dict[str, Any]] = []
    for row in industry_records:
        if (
            str(row.get("source_version") or "").strip()
            and _explicit_aware_timestamp(row.get("available_at") or row.get("first_seen_at"))
        ):
            proven_industry_records.append(dict(row))
    industries = materialize_pit_industry_observations(proven_industry_records, sessions)
    industry_by_day: dict[date, dict[str, PITIndustryObservation]] = defaultdict(dict)
    for item in industries:
        if item.industry and item.source_version and item.availability_basis in {"provider_available_at", "first_seen_at"}:
            industry_by_day[item.as_of][item.symbol] = item
    benchmark_by_day, weight_errors = _materialize_benchmark_weights(benchmark_weight_records, sessions)
    if weight_errors:
        return _unavailable(weight_errors[0], source_version=source_version)
    bars_by_symbol: dict[str, dict[date, Bar]] = defaultdict(dict)
    for bar in bars:
        bars_by_symbol[bar.symbol.upper()][bar.date] = bar
    curve = sorted(result.equity_curve, key=lambda item: item.date)
    daily: list[dict[str, Any]] = []
    for previous, current in itertools.pairwise(curve):
        start_day, end_day = previous.date, current.date
        portfolio_weights = dict(getattr(previous, "weights", {}) or {})
        portfolio_weights = {str(k).upper(): float(v) for k, v in portfolio_weights.items() if float(v) > 1e-12}
        weight_total = sum(portfolio_weights.values())
        if weight_total <= 0:
            return _unavailable(f"{start_day.isoformat()} 组合没有可归因的持仓", source_version=source_version)
        # Normalize to the invested stock sleeve; cash is excluded and stated in the report basis.
        portfolio_weights = {symbol: value / weight_total for symbol, value in portfolio_weights.items()}
        benchmark_weights = benchmark_by_day.get(start_day)
        if not benchmark_weights:
            return _unavailable(f"{start_day.isoformat()} 缺少当时已可得的基准权重", source_version=source_version)
        portfolio_industry = industry_by_day.get(start_day, {})
        missing = sorted((set(portfolio_weights) | set(benchmark_weights)) - set(portfolio_industry))
        if missing:
            return _unavailable(
                f"{start_day.isoformat()} 行业 PIT 覆盖不完整：{', '.join(missing[:8])}",
                source_version=source_version,
            )
        industry_returns: dict[str, dict[str, float]] = {
            "portfolio": defaultdict(float),
            "benchmark": defaultdict(float),
        }
        industry_weights: dict[str, dict[str, float]] = {
            "portfolio": defaultdict(float),
            "benchmark": defaultdict(float),
        }
        portfolio_return = benchmark_return = 0.0
        for symbol, weight in portfolio_weights.items():
            asset_return = _close_return(bars_by_symbol, symbol, start_day, end_day)
            if asset_return is None:
                return _unavailable(f"{symbol} 缺少 {start_day}→{end_day} 的完整收益数据", source_version=source_version)
            industry = portfolio_industry[symbol].industry
            industry_weights["portfolio"][industry] += weight
            industry_returns["portfolio"][industry] += weight * asset_return
            portfolio_return += weight * asset_return
        for symbol, weight in benchmark_weights.items():
            industry = portfolio_industry[symbol].industry
            asset_return = _close_return(bars_by_symbol, symbol, start_day, end_day)
            if asset_return is None:
                return _unavailable(f"基准成分 {symbol} 缺少 {start_day}→{end_day} 的收益数据", source_version=source_version)
            industry_weights["benchmark"][industry] += weight
            industry_returns["benchmark"][industry] += weight * asset_return
            benchmark_return += weight * asset_return

        p_industries = industry_weights["portfolio"]
        b_industries = industry_weights["benchmark"]
        if set(p_industries) != set(b_industries):
            return _unavailable(f"{start_day.isoformat()} 组合与基准行业口径不一致，不能完整分解", source_version=source_version)
        allocation = selection = interaction = 0.0
        sector_rows: list[dict[str, Any]] = []
        for industry in sorted(p_industries):
            wp, wb = p_industries[industry], b_industries[industry]
            rp = industry_returns["portfolio"][industry] / wp if wp > 0 else 0.0
            rb = industry_returns["benchmark"][industry] / wb if wb > 0 else 0.0
            a = (wp - wb) * (rb - benchmark_return)
            s = wb * (rp - rb)
            i = (wp - wb) * (rp - rb)
            allocation += a
            selection += s
            interaction += i
            sector_rows.append({
                "industry": industry,
                "portfolio_weight": wp,
                "benchmark_weight": wb,
                "portfolio_return": rp,
                "benchmark_return": rb,
                "allocation": a,
                "selection": s,
                "interaction": i,
            })
        daily.append({
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "active_return": portfolio_return - benchmark_return,
            "allocation": allocation,
            "selection": selection,
            "interaction": interaction,
            "industries": sector_rows,
        })
    totals = _carino_link(daily)
    return {
        "status": "available",
        "method": "Brinson-Fachler with Carino multi-period linking",
        "benchmark_code": benchmark_code,
        "benchmark_name": benchmark_name or benchmark_code,
        "source_version": source_version,
        "industry_availability_required": "provider_available_at_or_audited_first_seen_at",
        "benchmark_weight_availability_required": "available_before_each_session_open",
        "coverage": {"industry": 1.0, "benchmark_weights": 1.0, "constituent_returns": 1.0},
        "return_basis": "daily close-to-close gross returns on invested stock sleeve; cash and fees excluded",
        "portfolio_trading_timing_caveat": "daily beginning-of-period holdings attribution; trades executed within the session are reflected from the following period",
        "observations": len(daily),
        "linked": totals,
        "daily": daily,
    }


def _materialize_benchmark_weights(
    records: Sequence[Mapping[str, object]], sessions: Sequence[date]
) -> tuple[dict[date, dict[str, float]], list[str]]:
    session_set = set(sessions)
    raw_by_day: dict[date, dict[str, float]] = defaultdict(dict)
    for row in records:
        symbol = str(row.get("symbol") or row.get("ts_code") or "").strip().upper()
        day = _parse_day(row.get("as_of") or row.get("trade_date"))
        available_at = _parse_timestamp(row.get("available_at"))
        source_version = str(row.get("source_version") or "").strip()
        try:
            weight = _as_float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        if not symbol or day is None or day not in session_set or not source_version:
            continue
        if not math.isfinite(weight) or weight < 0 or available_at is None:
            continue
        cutoff = datetime.combine(day, WEIGHT_CUTOFF, tzinfo=SHANGHAI)
        if available_at >= cutoff:
            continue
        raw_by_day[day][symbol] = weight
    output: dict[date, dict[str, float]] = {}
    for day, weights in raw_by_day.items():
        total = sum(weights.values())
        if total <= 0 or abs(total - 1.0) > 0.03:
            return {}, [f"{day.isoformat()} 基准权重合计 {total:.4f}，超出归一化容差"]
        output[day] = {symbol: weight / total for symbol, weight in weights.items() if weight > 0}
    return output, []


def _close_return(bars: Mapping[str, Mapping[date, Bar]], symbol: str, start: date, end: date) -> float | None:
    left, right = bars.get(symbol, {}).get(start), bars.get(symbol, {}).get(end)
    if left is None or right is None or left.close <= 0:
        return None
    value = right.close / left.close - 1.0
    return value if math.isfinite(value) and value > -1.0 else None


def _carino_link(daily: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    portfolio_total = math.prod(1.0 + float(item["portfolio_return"]) for item in daily) - 1.0
    benchmark_total = math.prod(1.0 + float(item["benchmark_return"]) for item in daily) - 1.0
    difference = portfolio_total - benchmark_total
    overall_k = (
        (math.log1p(portfolio_total) - math.log1p(benchmark_total)) / difference
        if abs(difference) > 1e-14 else 1.0 / (1.0 + portfolio_total)
    )
    linked: dict[str, float] = {}
    for component in ("allocation", "selection", "interaction"):
        total = 0.0
        for item in daily:
            rp, rb = float(item["portfolio_return"]), float(item["benchmark_return"])
            gap = rp - rb
            daily_k = (
                (math.log1p(rp) - math.log1p(rb)) / gap
                if abs(gap) > 1e-14 else 1.0 / (1.0 + rp)
            )
            total += daily_k * float(item[component]) / overall_k
        linked[component] = total
    linked.update({
        "portfolio_return": portfolio_total,
        "benchmark_return": benchmark_total,
        "active_return": difference,
        "reconciliation_error": sum(linked.values()) - difference,
    })
    return linked


def _unavailable(reason: str, *, source_version: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "source_version": source_version,
        "industry_neutralization_enabled": False,
        "attribution": None,
    }


def _parse_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(SHANGHAI)


def _explicit_aware_timestamp(value: object) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None and value.utcoffset() is not None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
