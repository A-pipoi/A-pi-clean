"""Execution-capacity and trade-industry summaries for backtest runs."""

from __future__ import annotations

import os
from collections.abc import Callable

from ..data.schema import Bar
from .symbols import stock_metadata_candidates, stock_metadata_for_symbol

StockMetadataLookup = Callable[..., tuple[dict[str, dict[str, str]], bool]]


def backtest_operational_reports(
    result: object,
    bars: list[Bar],
    *,
    max_participation: float,
    stock_metadata_lookup: StockMetadataLookup,
    cache_dir: str,
) -> dict[str, dict[str, object]]:
    """Summarize executable capacity and turnover by available industry data.

    This is an execution report, not a fabricated alpha attribution. Industry
    return attribution needs PIT position snapshots; the auditable quantity
    available at this layer is trade notional by industry.
    """

    bar_by_key = {(bar.symbol, bar.date): bar for bar in bars}
    fills = list(getattr(result, "fills", []))
    missing_industry_symbols = sorted(
        {
            candidate
            for fill in fills
            if not (
                (bar := bar_by_key.get((fill.order.symbol, fill.date)))
                and str(bar.industry or "").strip()
            )
            for candidate in stock_metadata_candidates(fill.order.symbol)
        }
    )
    stock_metadata: dict[str, dict[str, str]] = {}
    industry_metadata_stale = False
    industry_metadata_error: str | None = None
    if missing_industry_symbols:
        try:
            stock_metadata, industry_metadata_stale = stock_metadata_lookup(
                missing_industry_symbols,
                token=os.getenv("TUSHARE_TOKEN"),
                cache_dir=cache_dir,
            )
        except (RuntimeError, OSError, ValueError) as error:
            industry_metadata_error = str(error)[:300]

    participation: list[float] = []
    over_limit = 0
    industry_notional: dict[str, float] = {}
    industry_fills: dict[str, int] = {}
    classified_fills = 0
    unclassified_fills = 0
    for fill in fills:
        bar = bar_by_key.get((fill.order.symbol, fill.date))
        if bar is not None and bar.volume > 0:
            ratio = float(fill.quantity) / float(bar.volume)
            participation.append(ratio)
            if ratio > max_participation + 1e-12:
                over_limit += 1
        identity = stock_metadata_for_symbol(fill.order.symbol, stock_metadata)
        bar_industry = str(
            bar.industry if bar is not None and bar.industry is not None else ""
        ).strip()
        industry = bar_industry or str(identity.get("industry") or "").strip()
        if industry:
            classified_fills += 1
        else:
            industry = "未知行业"
            unclassified_fills += 1
        notional = float(fill.costs.notional)
        industry_notional[industry] = industry_notional.get(industry, 0.0) + notional
        industry_fills[industry] = industry_fills.get(industry, 0) + 1
    capacity: dict[str, object] = {
        "status": "available" if participation else "no_fills",
        "max_participation_limit": max_participation,
        "max_participation": max(participation) if participation else None,
        "average_participation": sum(participation) / len(participation)
        if participation
        else None,
        "fills_checked": len(participation),
        "fills_over_limit": over_limit,
        "source": "Tushare daily volume + Python fills",
    }
    total_notional = sum(industry_notional.values())
    industry_rows = [
        {
            "industry": industry,
            "notional": notional,
            "share": notional / total_notional if total_notional else None,
            "fills": industry_fills[industry],
        }
        for industry, notional in sorted(
            industry_notional.items(), key=lambda item: item[1], reverse=True
        )
    ]
    if not fills:
        industry_status = "no_fills"
    elif classified_fills == len(fills):
        industry_status = "available"
    elif classified_fills:
        industry_status = "partial"
    else:
        industry_status = "unavailable"
    industry_warnings: list[str] = []
    if industry_metadata_stale:
        industry_warnings.append("行业分类来自过期 stock_basic 缓存")
    if industry_metadata_error:
        industry_warnings.append(
            f"stock_basic 行业信息不可用：{industry_metadata_error}"
        )
    if unclassified_fills:
        industry_warnings.append(
            f"{unclassified_fills}/{len(fills)} 笔成交缺少行业分类"
        )
    return {
        "capacity": capacity,
        "industry_trade": {
            "status": industry_status,
            "basis": "优先使用日线行业字段，缺失时用 Tushare stock_basic 当前行业补齐；按成交额统计，非点时收益归因",
            "classification_source": "daily_bar_or_current_stock_basic",
            "point_in_time": False,
            "classified_fills": classified_fills,
            "unclassified_fills": unclassified_fills,
            "metadata_stale": industry_metadata_stale,
            "metadata_error": industry_metadata_error,
            "warnings": industry_warnings,
            "rows": industry_rows,
        },
    }
