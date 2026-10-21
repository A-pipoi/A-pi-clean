from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from quant.data import TushareDailyAdapter
from quant.errors import InputTypeError

from ..portfolio.allocator import mean_variance_weights
from ..portfolio.risk import estimate_portfolio_risk


@dataclass(frozen=True)
class PortfolioApiDependencies:
    daily_adapter: type[TushareDailyAdapter]
    cache_dir: Path
    token_provider: Callable[[], str | None]
    pit_panel_path: Callable[[], str]
    symbols: Callable[[object], list[str]]
    shanghai_timezone: Any


def handle_portfolio_optimize(
    payload: dict[str, Any], deps: PortfolioApiDependencies
) -> dict[str, Any]:
    """Build one explicit, research-only full-covariance portfolio from strict PIT inputs."""
    from ..data.pit import materialize_pit_industry_observations

    symbols = deps.symbols(payload.get("symbols"))
    if len(symbols) > 120:
        raise ValueError("PIT 组合优化最多接受 120 个候选标的")
    max_weight = 0.20
    industry_band = 0.05
    if "max_weight" in payload and float(payload["max_weight"]) != max_weight:
        raise ValueError("组合优化持仓上限固定为 20%，不开放临时调参")
    if (
        "max_active_industry_deviation" in payload
        and float(payload["max_active_industry_deviation"]) != industry_band
    ):
        raise ValueError("行业主动偏离上限固定为 5%，不开放临时调参")
    as_of_value = payload.get("as_of")
    requested_as_of = date.fromisoformat(str(as_of_value)[:10]) if as_of_value else None
    today = datetime.now(deps.shanghai_timezone).date()
    if requested_as_of and requested_as_of > today:
        raise ValueError("as_of cannot be in the future")

    panel_path = deps.pit_panel_path().strip()
    if not panel_path:
        raise RuntimeError("未配置严格 PIT 行业与基准权重面板；优化器已 fail-closed")
    panel_bytes = Path(panel_path).expanduser().read_bytes()
    panel = json.loads(panel_bytes.decode("utf-8"))
    if (
        not isinstance(panel, dict)
        or panel.get("schema_version") != "pit-industry-brinson-v1"
    ):
        raise ValueError("PIT 面板 schema_version 必须为 pit-industry-brinson-v1")
    raw_source_meta = panel.get("source_metadata")
    source_meta: dict[str, object] = (
        raw_source_meta if isinstance(raw_source_meta, dict) else {}
    )
    if (
        not str(source_meta.get("provider") or "").strip()
        or not str(source_meta.get("source_version") or "").strip()
        or source_meta.get("industry_availability_quality") != "verified"
        or source_meta.get("benchmark_weight_availability_quality") != "verified"
    ):
        raise ValueError("PIT 面板的供应方、版本或行业/基准权重可得性证明未通过")
    industry_records = panel.get("industry_records")
    benchmark_records = panel.get("benchmark_weight_records")
    if not isinstance(industry_records, list) or not isinstance(
        benchmark_records, list
    ):
        raise InputTypeError("PIT 面板缺少行业记录或基准权重记录")
    if any(
        not isinstance(row, dict) or not str(row.get("source_version") or "").strip()
        for row in industry_records
    ):
        raise ValueError("每条 PIT 行业记录必须包含来源版本")

    adapter = deps.daily_adapter(
        symbols,
        token=deps.token_provider(),
        adjust="",
        cache_dir=deps.cache_dir,
        fetch_limits=False,
        force_refresh=True,
    )
    query_end = requested_as_of or today
    bars = adapter.daily_bars(start=query_end - timedelta(days=240), end=query_end)
    if requested_as_of is None:
        usable_dates = sorted({bar.date for bar in bars})
        if not usable_dates:
            raise RuntimeError("Tushare 未返回候选标的日线")
        as_of = usable_dates[-1]
    else:
        as_of = requested_as_of
    bars = [bar for bar in bars if bar.date <= as_of]
    by_symbol: dict[str, list[Any]] = {}
    for bar in bars:
        by_symbol.setdefault(str(bar.symbol).upper(), []).append(bar)
    missing = [
        symbol
        for symbol in symbols
        if as_of not in {bar.date for bar in by_symbol.get(symbol, [])}
    ]
    if missing:
        raise RuntimeError(
            f"{as_of.isoformat()} 缺少候选标的收盘日线：{', '.join(missing[:8])}"
        )

    return_panel: dict[str, dict[date, float]] = {}
    forecasts: dict[str, float] = {}
    for symbol in symbols:
        ordered = sorted(
            (bar for bar in by_symbol.get(symbol, []) if bar.close > 0),
            key=lambda item: item.date,
        )
        returns = {
            right.date: float(right.close) / float(left.close) - 1.0
            for left, right in itertools.pairwise(ordered)
            if float(left.close) > 0
        }
        return_panel[symbol] = returns
        recent = sorted(returns.items())[-20:]
        if not recent:
            raise RuntimeError(f"{symbol} 缺少可用于预期收益估计的历史收益")
        forecasts[symbol] = sum(value for _, value in recent) / len(recent)

    sessions = sorted({bar.date for bar in bars})
    observations = materialize_pit_industry_observations(industry_records, sessions)
    observations = [item for item in observations if item.as_of == as_of]
    weights_by_symbol: dict[str, float] = {}
    cutoff = datetime.combine(as_of, time(9, 30), tzinfo=deps.shanghai_timezone)
    for row in benchmark_records:
        if (
            not isinstance(row, dict)
            or str(row.get("as_of") or "")[:10] != as_of.isoformat()
        ):
            continue
        available_text = row.get("available_at") or row.get("first_seen_at")
        try:
            available_at = datetime.fromisoformat(
                str(available_text)
            )
        except (TypeError, ValueError) as error:
            raise ValueError("每条当日基准权重必须包含含时区的可得时间") from error
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("基准权重 available_at 必须包含时区")
        if available_at.astimezone(deps.shanghai_timezone) >= cutoff:
            raise ValueError("基准权重并非在当日开盘前可得，拒绝优化")
        if not str(row.get("source_version") or "").strip():
            raise ValueError("每条基准权重必须包含来源版本")
        symbol = str(row.get("symbol") or row.get("ts_code") or "").strip().upper()
        weight = float(row.get("weight") or 0.0)
        if symbol and weight > 0:
            weights_by_symbol[symbol] = weight
    weight_total = sum(weights_by_symbol.values())
    if not 0.97 <= weight_total <= 1.03:
        raise ValueError(
            f"{as_of.isoformat()} 基准权重合计 {weight_total:.4f}，不在 0.97–1.03 范围"
        )
    normalized_weights = {
        symbol: weight / weight_total for symbol, weight in weights_by_symbol.items()
    }

    weights = mean_variance_weights(
        forecasts,
        return_panel,
        industry_observations=observations,
        benchmark_constituent_weights=normalized_weights,
        as_of=as_of,
        max_weight=max_weight,
        max_active_industry_deviation=industry_band,
        risk_aversion=8.0,
        window=60,
        min_observations=20,
    )
    risk = estimate_portfolio_risk(
        weights, return_panel, window=60, min_observations=20
    )
    return {
        "status": "available",
        "mode": "research_only_pit_mean_variance",
        "as_of": as_of.isoformat(),
        "benchmark_code": str(source_meta.get("benchmark_code") or ""),
        "data_version": adapter.snapshot().version,
        "pit_panel_sha256": hashlib.sha256(panel_bytes).hexdigest(),
        "candidate_count": len(symbols),
        "industry_coverage_count": len({item.symbol for item in observations}),
        "benchmark_constituent_count": len(normalized_weights),
        "forecast_basis": "mean_of_last_20_close_to_close_daily_returns",
        "covariance_basis": "last_60_common_close_to_close_daily_returns_shrunk_10_percent",
        "weights": weights,
        "pre_trade_risk": risk,
        "constraints": {
            "max_weight": max_weight,
            "max_active_industry_deviation": industry_band,
            "risk_aversion": 8.0,
        },
        "paper_strategy_changed": False,
    }
