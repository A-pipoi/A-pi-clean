from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from quant.api.contracts import (
    CheckOrderLimitsCallback,
    GetTushareStockMetadataCallback,
    ResolveStrategyConfigCallback,
)
from quant.data import TushareDailyAdapter

from ..execution.store import PaperStore
from ..portfolio.risk import estimate_portfolio_risk


def _optional_float(value: object) -> float | None:
    """Parse an optional JSON number without relying on an untyped conversion."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, bytes, bytearray, int, float)):
        raise TypeError("numeric value must be a JSON string or number")
    return float(value)


@dataclass(frozen=True)
class PaperApiDependencies:
    """Application services injected into the paper HTTP routes."""

    store: PaperStore
    cache_dir: Path
    timezone: tzinfo
    daily_adapter: type[TushareDailyAdapter]
    token_provider: Callable[[], str | None]
    normalize_symbol: Callable[[object], str]
    bounded_int: Callable[[object, int, int], int]
    limits_data_quality: Callable[..., dict[str, Any]]
    data_freshness: Callable[[dict[str, Any], date], dict[str, Any]]
    stock_metadata: GetTushareStockMetadataCallback
    check_order_limits: CheckOrderLimitsCallback
    risk_limits: Any
    enrich_state: Callable[[dict[str, Any]], dict[str, Any]]
    risk_limits_payload: Callable[[], dict[str, Any]]
    scheduler: Callable[[], Any]
    default_universe_index: str
    resolve_universe: Callable[..., dict[str, Any]]
    symbols: Callable[[object], list[str]]
    get_strategy_preset: Callable[[str], Any]
    resolve_strategy_config: ResolveStrategyConfigCallback
    factor_store: Any
    sealed_gate: Callable[..., dict[str, Any]]
    run_strategy_cycle: Callable[..., dict[str, Any]]


def handle_paper_order(
    send_json: Callable[[int, dict[str, Any]], None],
    headers: Mapping[str, str],
    payload: dict[str, Any],
    deps: PaperApiDependencies,
) -> None:
    idempotency_key = (
        str(
            payload.get("idempotency_key") or headers.get("Idempotency-Key") or ""
        ).strip()[:200]
        or None
    )
    china_today = datetime.now(deps.timezone).date()
    market_closed = china_today.weekday() >= 5
    if idempotency_key:
        existing = deps.store.order_by_idempotency_key(idempotency_key)
        if existing:
            send_json(
                200,
                {
                    "ok": True,
                    "order": existing,
                    "paper": deps.enrich_state(deps.store.state()),
                    "risk_limits": deps.risk_limits_payload(),
                    "paper_trading_allowed": not market_closed,
                    "idempotent_replay": True,
                },
            )
            return

    if market_closed:
        send_json(
            409,
            {
                "ok": False,
                "code": "market_closed",
                "error": "周末为 A 股非交易日，已禁止生成纸面委托",
                "paper_trading_allowed": False,
            },
        )
        return

    symbol = deps.normalize_symbol(payload.get("symbol", ""))
    side = str(payload.get("side", "")).strip().upper()
    quantity = deps.bounded_int(payload.get("quantity", 0), 1, 10_000_000)
    limit_price = payload.get("limit_price")
    parsed_limit = _optional_float(limit_price)
    initial_state = deps.store.state()
    position_symbols = [
        str(item["symbol"]) for item in initial_state.get("positions", [])
    ]
    data_symbols = list(dict.fromkeys([*position_symbols, symbol]))
    adapter = deps.daily_adapter(
        data_symbols,
        token=deps.token_provider(),
        adjust="",
        cache_dir=deps.cache_dir,
        fetch_limits=True,
        force_refresh=True,
    )
    bars = adapter.daily_bars(start=china_today - timedelta(days=365), end=china_today)
    if not bars:
        raise RuntimeError("Tushare 今日没有可用于纸面委托的日线")
    dataset_metadata = dict(adapter.snapshot().metadata)
    bars_by_symbol: dict[str, list[Any]] = {}
    for bar in bars:
        bars_by_symbol.setdefault(str(bar.symbol), []).append(bar)
    candidate_bars = bars_by_symbol.get(symbol, [])
    if not candidate_bars:
        raise RuntimeError(f"Tushare 没有返回委托标的 {symbol} 的日线")
    bar = max(candidate_bars, key=lambda item: item.date)
    data_quality = deps.limits_data_quality(dataset_metadata, context="纸面委托")
    data_freshness = deps.data_freshness(dataset_metadata, bar.date)
    if not data_quality["limits_available"] or not data_freshness["fresh"]:
        send_json(
            409,
            {
                "ok": False,
                "code": "paper_data_quality_blocked",
                "error": "纸面委托已阻断：实时行情、涨跌停约束和数据新鲜度必须同时通过",
                "data_quality": data_quality,
                "data_freshness": data_freshness,
                "paper_trading_allowed": False,
            },
        )
        return

    latest_bars = {
        code: max(items, key=lambda item: item.date)
        for code, items in bars_by_symbol.items()
    }
    prices = {code: float(item.close) for code, item in latest_bars.items()}
    current_state = deps.store.state(prices)
    positions: dict[str, int | float] = {
        str(item["symbol"]): int(item["quantity"])
        for item in current_state["positions"]
    }
    metadata, _ = deps.stock_metadata(
        list(set(prices) | set(positions)),
        token=deps.token_provider(),
        cache_dir=str(deps.cache_dir),
    )
    industries: dict[str, str | None] = {}
    for code in set(prices) | set(positions):
        latest_bar = latest_bars.get(code)
        market_industry = latest_bar.industry if latest_bar is not None else None
        industry = market_industry or (metadata.get(code) or {}).get("industry")
        industries[code] = str(industry) if industry else None

    risk_estimate: dict[str, Any] | None = None
    if side == "BUY":
        projected_positions = dict(positions)
        projected_positions[symbol] = projected_positions.get(symbol, 0) + quantity
        equity = float(current_state["equity"])
        projected_weights = (
            {
                code: max(0.0, amount * prices.get(code, 0.0) / equity)
                for code, amount in projected_positions.items()
                if amount > 0
            }
            if equity > 0
            else {}
        )
        histories: dict[str, dict[date, float]] = {}
        for code, items in bars_by_symbol.items():
            ordered = sorted(
                (item for item in items if float(item.close) > 0),
                key=lambda item: item.date,
            )
            histories[code] = {
                right.date: float(right.close) / float(left.close) - 1.0
                for left, right in itertools.pairwise(ordered)
                if float(left.close) > 0
            }
        risk_estimate = estimate_portfolio_risk(
            projected_weights,
            histories,
            window=60,
            min_observations=20,
        )

    equity_curve = [
        float(item["equity"])
        for item in current_state.get("equity_curve", [])
        if item.get("equity") is not None
    ]
    equity = float(current_state["equity"])
    peak_equity = max(equity_curve + [equity], default=equity)
    current_drawdown = max(0.0, 1.0 - equity / peak_equity) if peak_equity > 0 else None
    risk_violations = deps.check_order_limits(
        positions,
        prices,
        industries,
        equity,
        symbol,
        side,
        quantity,
        deps.risk_limits,
        current_drawdown=current_drawdown,
        risk_estimate=risk_estimate,
    )
    if risk_violations:
        send_json(
            409,
            {
                "ok": False,
                "code": "risk_rejected",
                "error": "纸面委托触发风险上限，未提交",
                "pre_trade_risk": risk_estimate,
                "violations": [
                    {"rule": item.rule, "message": item.message}
                    for item in risk_violations
                ],
            },
        )
        return

    order = deps.store.create_order(
        symbol,
        side,
        quantity,
        limit_price=parsed_limit,
        source="manual",
        idempotency_key=idempotency_key,
        signal_date=bar.date,
    )
    deps.store.record_quotes(list(latest_bars.values()))
    state = deps.enrich_state(deps.store.record_equity(bar.date, prices))
    response: dict[str, Any] = {
        "ok": True,
        "order": order,
        "execution": {
            "status": "pending_next_session",
            "signal_date": bar.date.isoformat(),
            "fill_basis": "next_trading_session_open_after_risk_recheck",
        },
        "paper": state,
        "risk_limits": deps.risk_limits_payload(),
        "pre_trade_risk": risk_estimate,
        "data_quality": data_quality,
        "data_freshness": data_freshness,
        "paper_trading_allowed": True,
    }
    scheduler = deps.scheduler()
    if scheduler is not None:
        response["scheduler"] = scheduler.status()
    send_json(200, response)


def handle_paper_cycle(
    send_json: Callable[[int, dict[str, Any]], None],
    payload: dict[str, Any],
    deps: PaperApiDependencies,
) -> None:
    scheduler = deps.scheduler()
    if scheduler is None:
        send_json(409, {"ok": False, "code": "scheduler_disabled",
                        "error": "当前账户维护入口未启用，不能通过另一条路径绕过调度"})
        return
    # Execution never changes account strategy/pool based on request defaults.
    # Configuration remains an explicit, separately authenticated operation.
    for key, expected in (("strategy", scheduler.strategy), ("rebalance", scheduler.rebalance),
                          ("universe_index", scheduler.universe_index)):
        if key in payload and payload[key] != expected:
            send_json(409, {"ok": False, "code": "paper_config_mismatch",
                            "error": "执行请求与当前账户配置不一致，请先显式修改纸面配置"})
            return
    if any(key in payload for key in ("symbols", "lookback", "top_n", "initial_cash")):
        send_json(409, {"ok": False, "code": "paper_config_override",
                        "error": "日循环不接受临时股票池、参数或资金覆盖"})
        return
    if "manual_catchup" in payload and not isinstance(payload["manual_catchup"], bool):
        send_json(400, {"ok": False, "error": "manual_catchup must be boolean"})
        return
    if payload.get("manual_catchup") is True:
        day, reason = payload.get("expected_day"), payload.get("reason")
        if not isinstance(day, str) or not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
            send_json(400, {"ok": False, "error": "人工补跑必须明确提供当日日期和原因"})
            return
        cycle = scheduler.run_manual_catchup(expected_day=day, reason=reason)
    else:
        cycle = scheduler.run_once()
    if isinstance(cycle.get("state"), dict):
        cycle["state"] = deps.enrich_state(cycle["state"])
    response: dict[str, Any] = {"ok": True, **cycle}
    response["risk_limits"] = deps.risk_limits_payload()
    response["scheduler"] = scheduler.status()
    send_json(200, response)
