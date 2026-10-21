"""Current-workspace factor values and signals from confirmed daily bars."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..data.schema import Bar
from ..factors import calculate_factor
from ..factors.store import FactorDefinition
from ..numeric import as_float


def build_live_factor_rows(
    bars: Sequence[Bar],
    token: str | None,
    definitions: Sequence[FactorDefinition],
    fundamental_rows: list[dict[str, object]] | None = None,
    *,
    search_stocks: Callable[..., tuple[list[dict[str, Any]], int, bool]],
    cache_dir: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    by_symbol: dict[str, list[Bar]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    factor_rows: list[dict[str, object]] = []
    signals: list[dict[str, object]] = []
    fundamental_by_key = {
        (str(row.get("ts_code", "")).upper(), str(row.get("metric", ""))): row
        for row in (fundamental_rows or [])
    }
    for symbol, items in sorted(by_symbol.items()):
        ordered = sorted(items, key=lambda item: item.date)
        if len(ordered) < 21:
            continue
        latest = ordered[-1]
        meta, _, _ = search_stocks(
            symbol.split(".", 1)[0],
            token=token,
            cache_dir=cache_dir,
            limit=1,
        )
        stock = meta[0] if meta else {}
        values: dict[str, float] = {}
        for definition in definitions:
            if definition.factor_type == "fundamental":
                fundamental = fundamental_by_key.get(
                    (symbol.upper(), definition.metric)
                )
                if fundamental is None:
                    continue
                fundamental_value = as_float(fundamental["value"])
                values[definition.key] = fundamental_value
                factor_rows.append(
                    {
                        "factor": definition.name,
                        "factor_key": definition.key,
                        "factor_type": definition.factor_type,
                        "symbol": symbol,
                        "name": stock.get("name", symbol),
                        "value": fundamental_value,
                        "sample_days": 0,
                        "trade_date": fundamental.get("announcement_date"),
                        "report_date": fundamental.get("report_date"),
                        "announcement_date": fundamental.get("announcement_date"),
                        "available_at": fundamental.get("available_at"),
                        "status": "可用",
                        "source": "Tushare:fina_indicator",
                    }
                )
                continue
            factor_value = calculate_factor(definition, ordered)
            if factor_value is None:
                continue
            values[definition.key] = factor_value
            factor_rows.append(
                {
                    "factor": definition.name,
                    "factor_key": definition.key,
                    "factor_type": definition.factor_type,
                    "symbol": symbol,
                    "name": stock.get("name", symbol),
                    "value": factor_value,
                    "sample_days": definition.window,
                    "trade_date": latest.date.isoformat(),
                    "status": "可用",
                    "source": "Tushare",
                }
            )
        momentum = values.get("builtin_momentum_20")
        if momentum is None:
            continue
        signals.append(
            {
                "symbol": symbol,
                "name": stock.get("name", symbol),
                "industry": stock.get("industry", "未分类") or "未分类",
                "trade_date": latest.date.isoformat(),
                "change_pct": latest.close / ordered[-2].close - 1.0
                if ordered[-2].close
                else None,
                "momentum": momentum,
                "signal": "强势" if momentum > 0 else "观察",
            }
        )
    signals.sort(key=lambda row: as_float(row["momentum"]), reverse=True)
    return factor_rows, signals
