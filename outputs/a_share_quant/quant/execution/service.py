from __future__ import annotations

import itertools
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from functools import wraps
from typing import Any

from quant.errors import InputTypeError

from ..backtest import CostModel
from ..backtest.engine import PortfolioSnapshot
from ..data import TushareDailyAdapter, get_tushare_stock_metadata
from ..data.adapters import _tushare_http_query
from ..data.schema import Bar
from ..factors.store import FactorDefinition
from ..portfolio import PAPER_RISK_LIMITS, check_order_limits
from ..portfolio.risk import estimate_portfolio_risk
from ..strategies import build_strategy, resolve_strategy_config
from ..validation import validate_strategy
from .store import PaperStore

CHINA_TZ = timezone(timedelta(hours=8))
PAPER_MAX_STALENESS_DAYS = (
    0  # Never place paper orders from a prior trading day's bars.
)
# Keep the paper validation window long enough to form at least four 15-day
# OOS folds with the current 40-day train and dynamic embargo. The minimum
# OOS gate remains 60 observations; this only supplies the history needed to
# evaluate it rather than weakening the gate.
PAPER_VALIDATION_HISTORY_DAYS = max(
    365,
    int(os.getenv("A_PI_PAPER_VALIDATION_HISTORY_DAYS", "365")),
)


def _atomic_cycle(function):
    @wraps(function)
    def run(store, **kwargs):
        # Result decoration is deliberately outside the account write transaction.
        result = function(store, **kwargs)
        mode = kwargs.get("execution_mode", "formal")
        result.update(execution_mode=mode)
        if mode == "research":
            result.update(account_label="未验收研究纸面账户", sealed_passed=False, conclusion_ready=False,
                          evidence_role="observed_research_not_independent_holdout")
        schedule = result.get("rebalance_schedule")
        result.setdefault("maintenance_stages", {
            "quotes": {"status": "completed" if not result.get("skipped") else "pending"},
            "equity": {"status": "completed" if not result.get("skipped") else "pending"},
            "reconciliation": {"status": "completed" if not result.get("skipped") else "pending"},
            "pending_orders": {"status": "completed" if not result.get("skipped") else "blocked"},
            "rebalance": {"status": "completed" if schedule and schedule["due"] else "pending"},
        })
        simulated = result.get("simulated_orders", [])
        traded = any(item.get("status") in {"FILLED", "PARTIALLY_FILLED"} for item in simulated)
        rejected_count = sum(
            1 for item in simulated if str(item.get("status", "")).upper() == "REJECTED"
        )
        all_rejected = bool(simulated) and rejected_count == len(simulated)
        if result.get("skipped"):
            outcome, reason = "blocked", result.get("skip_reason")
        elif traded:
            outcome, reason = "traded", None
        elif all_rejected:
            outcome = "risk_rejected"
            reason = f"共 {rejected_count} 笔委托被拒绝；具体原因详见 risk_rejections 的 violations 规则"
        elif schedule and not schedule["due"]:
            outcome, reason = "not_rebalance_day", "尚未到调仓日"
        elif result.get("pending_orders"):
            outcome = "orders_pending"
            reason = f"当前 {result['pending_orders']} 笔活动策略委托等待后续交易日执行"
            if schedule and schedule["due"] and result.get("generated_orders") == 0:
                reason = "调仓检查未生成差额委托（目标持仓不变或无有效信号）；" + reason
        else:
            outcome = "no_trade"
            reason = "调仓检查未生成差额委托（目标持仓不变或无有效信号）"
        result.setdefault("outcome", outcome)
        result.setdefault("no_trade_reason", reason)
        result.setdefault("next_rebalance", _next_rebalance(schedule))
        return result
    return run


def _next_rebalance(schedule):
    if schedule is None:
        return {"status": "unknown", "reason": "尚未形成交易日调仓计划"}
    remaining = 0 if schedule["due"] else max(
        0, schedule["interval_trading_days"] - (schedule["trading_days_since_previous"] or 0)
    )
    # A completed rebalance starts a new interval; no calendar-date guessing.
    if schedule.get("last_rebalance_date") != schedule.get("previous_rebalance_date"):
        remaining = schedule["interval_trading_days"]
    return {"status": "due" if remaining == 0 else "scheduled",
            "remaining_trading_days": remaining,
            "anchor_date": schedule.get("last_rebalance_date"),
            "reason": "按实际交易日计数；未来交易日期需交易所日历确认"}


def _blocked_maintenance(store, bars, cycle_date, *, reason, strategy, rebalance_every, result):
    with store.transaction():
        current = [bar for bar in bars if bar.date == cycle_date]
        store.record_quotes(current)
        store.record_entitlements(cycle_date)
        state = store.record_equity(cycle_date, {bar.symbol: bar.close for bar in current})
        schedule = _rebalance_schedule(
            sorted({bar.date for bar in bars}), cycle_date=cycle_date,
            last_rebalance_date=store.last_rebalance_date(strategy, rebalance_every),
            rebalance_every=rebalance_every,
        )
        result.update({
            "skipped": True, "skip_reason": reason, "state": state,
            "generated_orders": 0, "simulated_orders": [], "risk_rejections": [],
            "pending_orders": store.active_strategy_order_count(),
            "paper_trading_allowed": False, "outcome": "governance_blocked",
            "no_trade_reason": reason, "rebalance_schedule": schedule,
            "maintenance_stages": {
                "quotes": {"status": "completed"}, "equity": {"status": "completed"},
                "reconciliation": {"status": "completed"},
                "pending_orders": {"status": "blocked", "reason": reason,
                                   "count": len(store.pending_strategy_orders(cycle_date))},
                "rebalance": {"status": "blocked", "reason": reason},
            },
        })
        return result


@_atomic_cycle
def run_strategy_cycle(
    store: PaperStore,
    *,
    symbols: list[str],
    cache_dir: str | None = None,
    strategy: str = "baseline",
    lookback: int | None = None,
    top_n: int | None = None,
    rebalance_every: int = 5,
    initial_cash: float = 1_000_000.0,
    factor_definitions: Sequence[FactorDefinition] | None = None,
    sealed_test: dict[str, Any] | None = None,
    additional_symbols: Sequence[str] = (),
    universe_snapshot: dict[str, Any] | None = None,
    execution_mode: str = "formal",
    research_observer: Callable[[str, str], None] | None = None,
    defer_pending_fills: bool = False,
) -> dict[str, Any]:
    """Generate and simulate one daily signal cycle using Tushare daily bars."""

    if execution_mode not in {"formal", "research"}:
        raise ValueError("invalid paper execution mode")
    if execution_mode == "research":
        from .research_account import ResearchPaperAccount
        if not ResearchPaperAccount.verify_store(store) or research_observer is None:
            raise ValueError("研究运行只能使用显式启用的独立研究账户和样本暴露台账")
        if not universe_snapshot or universe_snapshot.get("mode") != "pit_index_members":
            raise ValueError("研究运行必须使用已解析的 PIT 指数成分")
    if not symbols:
        raise ValueError("symbols cannot be empty")
    today = _china_today()
    if today.weekday() >= 5:
        return {
            "cycle_date": None,
            "strategy": strategy,
            "generated_orders": 0,
            "simulated_orders": [],
            "risk_rejections": [],
            "skipped": True,
            "skip_reason": "周末休眠：A股非交易日，不生成纸面委托",
            "strategy_validation": None,
            "state": store.state(),
            "data_quality": {"limits_available": None, "warnings": ["周末非交易日"]},
            "data_freshness": {
                "fresh": False,
                "source": "market_closed",
                "warnings": ["周末非交易日"],
            },
            "paper_trading_allowed": False,
        }
    if not is_a_share_trading_day(today):
        return {
            "cycle_date": None,
            "strategy": strategy,
            "generated_orders": 0,
            "simulated_orders": [],
            "risk_rejections": [],
            "skipped": True,
            "skip_reason": f"{today.isoformat()} 为 A 股休市日，未请求日线或生成纸面委托",
            "strategy_validation": None,
            "state": store.state(),
            "data_quality": {
                "limits_available": None,
                "warnings": ["Tushare trade_cal 标记休市"],
            },
            "data_freshness": {
                "fresh": False,
                "source": "market_closed",
                "warnings": ["A 股休市日"],
            },
            "paper_trading_allowed": False,
        }
    preset, lookback, top_n = resolve_strategy_config(strategy, lookback, top_n)
    end = today
    start = end - timedelta(days=max(lookback * 4, PAPER_VALIDATION_HISTORY_DAYS))
    candidate_codes = {
        str(symbol).split(".", 1)[0].strip().upper() for symbol in symbols
    }
    data_symbols = list(
        dict.fromkeys(
            [*symbols, *(str(item) for item in additional_symbols if str(item).strip())]
        )
    )
    adapter = TushareDailyAdapter(
        symbols=data_symbols,
        token=None,
        adjust="",
        cache_dir=cache_dir,
        fetch_limits=True,
        force_refresh=True,
        resume_completed_refresh=True,
    )
    bars = adapter.daily_bars(start=start, end=end)
    dataset_metadata = dict(adapter.snapshot().metadata)
    delisted_symbols = set(dataset_metadata.get("proven_delistings", {}))
    dataset_metadata["paper_validation_history_days"] = PAPER_VALIDATION_HISTORY_DAYS
    dataset_metadata["paper_validation_min_oos_observations"] = 60
    _require_complete_universe(dataset_metadata, context="纸面交易")
    data_quality = _limits_data_quality(dataset_metadata)
    trading_dates = sorted({bar.date for bar in bars})
    if len(trading_dates) < lookback + 1:
        raise ValueError("Tushare 行情不足以生成纸面交易信号")
    cycle_date = trading_dates[-1]
    data_freshness = _data_freshness(dataset_metadata, cycle_date)
    # A provider delisting record is not evidence of a liquidation or a
    # corporate-action settlement. Existing exposure needs explicit resolution.
    affected = delisted_symbols & (
        {str(item["symbol"]) for item in store.state().get("positions", [])}
        | set(store.active_order_symbols())
    )
    if affected:
        data_freshness["fresh"] = False
        data_freshness["warnings"].append(
            "退市股票仍有持仓或活动委托，需核对公司行动与委托处置：" + "、".join(sorted(affected))
        )
    paper_trading_allowed = bool(
        data_quality["limits_available"] and data_freshness["fresh"]
    )
    if not paper_trading_allowed:
        reasons = [*data_quality["warnings"], *data_freshness["warnings"]]
        return {
            "cycle_date": cycle_date.isoformat(),
            "strategy": preset.key,
            "strategy_label": preset.label,
            "dataset": adapter.snapshot().version,
            "dataset_metadata": dataset_metadata,
            "generated_orders": 0,
            "simulated_orders": [],
            "risk_rejections": [],
            "skipped": True,
            "skip_reason": "纸面交易数据闸门未通过：" + "；".join(reasons),
            "strategy_validation": None,
            "state": store.state(),
            "data_quality": data_quality,
            "data_freshness": data_freshness,
            "paper_trading_allowed": False,
        }
    if execution_mode == "research":
        assert research_observer is not None
        research_observer(cycle_date.isoformat(), adapter.snapshot().version)
    if execution_mode == "formal" and sealed_test is not None and not bool(sealed_test.get("passed")):
        reason = "密封测试门禁：" + str(sealed_test.get("reason") or "密封验收未通过")
        return _blocked_maintenance(
            store, bars, cycle_date, reason=reason, strategy=preset.key,
            rebalance_every=rebalance_every,
            result={"cycle_date": cycle_date.isoformat(), "strategy": preset.key,
                    "strategy_label": preset.label, "dataset": adapter.snapshot().version,
                    "dataset_metadata": dataset_metadata, "data_quality": data_quality,
                    "data_freshness": {**data_freshness, "source": "sealed_test_blocked"},
                    "sealed_test": sealed_test,
                    "strategy_validation": None},
        )
    candidate_bars = [
        bar for bar in bars if bar.symbol.split(".", 1)[0].upper() in candidate_codes
    ]
    validation = validate_strategy(
        candidate_bars,
        lambda: build_strategy(
            preset.key,
            rebalance_every=rebalance_every,
            factor_definitions=factor_definitions,
        ),
        initial_cash=initial_cash,
        cost_model=CostModel(max_participation=0.10),
        embargo=None,
        rebalance_every=rebalance_every,
    ) if execution_mode == "formal" else None
    validation_payload = _validation_payload(validation) if validation else {
        "passed": False, "status": "research_unvalidated",
        "reason": "研究账户采集前瞻模拟证据，不把统计验收作为采样门槛，不宣称策略有效"}
    if validation is not None and not validation.passed:
        return _blocked_maintenance(
            store, bars, cycle_date, reason="策略样本外验证未通过，未生成纸面委托",
            strategy=preset.key, rebalance_every=rebalance_every,
            result={"cycle_date": cycle_date.isoformat(), "strategy": preset.key,
                    "strategy_label": preset.label, "dataset": adapter.snapshot().version,
                    "dataset_metadata": dataset_metadata, "data_quality": data_quality,
                    "data_freshness": data_freshness, "strategy_validation": validation_payload},
        )
    selected_strategy = build_strategy(
        preset.key,
        rebalance_every=rebalance_every,
        factor_definitions=factor_definitions,
    )
    from ..strategies.baseline import CrossSectionalMomentum

    if not isinstance(selected_strategy, CrossSectionalMomentum):
        raise InputTypeError("纸面循环只支持已经注册的因子复合策略")
    factor_snapshot = selected_strategy.factor_metadata()
    snapshot_keys = factor_snapshot.get("factor_keys")
    factor_keys = (
        [str(key) for key in snapshot_keys] if isinstance(snapshot_keys, list) else []
    )
    snapshot_definitions = (
        factor_definitions
        if factor_definitions is not None
        else selected_strategy.factor_composite.definitions
    )
    factor_snapshot_definitions = [item.as_dict() for item in snapshot_definitions]
    metadata, metadata_stale = get_tushare_stock_metadata(
        data_symbols, cache_dir=cache_dir
    )
    from .corporate_actions import observed_events

    distribution_events = observed_events(datetime.now(UTC))
    return _execute_prepared_cycle(
        store, bars=bars, trading_dates=trading_dates, cycle_date=cycle_date,
        preset=preset, selected_strategy=selected_strategy,
        rebalance_every=rebalance_every, candidate_codes=candidate_codes - {
            symbol.split(".", 1)[0].upper() for symbol in delisted_symbols
        },
        factor_snapshot=factor_snapshot, factor_keys=factor_keys,
        factor_snapshot_definitions=factor_snapshot_definitions,
        dataset_version=adapter.snapshot().version, dataset_metadata=dataset_metadata,
        data_quality=data_quality, data_freshness=data_freshness,
        validation_payload=validation_payload, metadata=metadata,
        metadata_stale=metadata_stale, distribution_events=distribution_events,
        universe_snapshot=universe_snapshot, symbols=symbols,
        defer_pending_fills=defer_pending_fills,
    )


def _execute_prepared_cycle(
    store: PaperStore, *, bars: list[Bar], trading_dates: list[date], cycle_date: date,
    preset: Any, selected_strategy: Any, rebalance_every: int, candidate_codes: set[str],
    factor_snapshot: dict[str, Any], factor_keys: list[str],
    factor_snapshot_definitions: list[dict[str, Any]],
    dataset_version: str, dataset_metadata: dict[str, Any],
    data_quality: dict[str, Any], data_freshness: dict[str, Any],
    validation_payload: dict[str, Any], metadata: dict[str, Any], metadata_stale: bool,
    distribution_events: list[dict[str, Any]], universe_snapshot: dict[str, Any] | None,
    symbols: list[str],
    defer_pending_fills: bool = False,
) -> dict[str, Any]:
    """Commit account-dependent work once, after all slow preparation has finished."""
    from ..strategies.cost_aware_baseline import CostAwareBaseline
    if isinstance(selected_strategy, CostAwareBaseline):
        financial_symbols = sorted({bar.symbol for bar in bars if bar.date <= cycle_date and bar.symbol.split(".", 1)[0].upper() in candidate_codes})
        _, financial_report = selected_strategy.fundamental_provider(cycle_date, financial_symbols)
        if financial_report.get("status") != "available":
            return {"cycle_date": cycle_date.isoformat(), "strategy": preset.key,
                    "generated_orders": 0, "simulated_orders": [], "skipped": True,
                    "paper_trading_allowed": False, "outcome": "blocked",
                    "skip_reason": "财务因子覆盖或PIT可得时间不足；未推进调仓日期，也未撮合旧委托",
                    "no_trade_reason": "financial_inputs_unavailable",
                    "financial_inputs": financial_report, "state": store.state(),
                    "data_quality": data_quality, "data_freshness": data_freshness}
    with store.transaction():
        if store.has_cycle(cycle_date):
            return {
                "cycle_date": cycle_date.isoformat(), "strategy": preset.key,
                "strategy_label": preset.label, "dataset": dataset_version,
                "dataset_metadata": dataset_metadata, "generated_orders": 0,
                "simulated_orders": [], "risk_rejections": [], "skipped": True,
                "pending_orders": store.active_strategy_order_count(),
                "skip_reason": "该交易日已运行过纸面策略循环",
                "state": store.state(), "data_quality": data_quality,
                "data_freshness": data_freshness, "paper_trading_allowed": False,
                "outcome": "completed",
                "idempotency": {"cycle_date": cycle_date.isoformat(), "claimed": False},
            }
        previous_rebalance_date = store.last_rebalance_date(preset.key, rebalance_every)
        rebalance_schedule = _rebalance_schedule(
            trading_dates,
            cycle_date=cycle_date,
            last_rebalance_date=previous_rebalance_date,
            rebalance_every=rebalance_every,
        )
        if not store.claim_cycle(
            cycle_date,
            strategy=preset.key,
            source=f"strategy:{preset.key}",
            factor_pipeline_version=str(factor_snapshot.get("version") or ""),
            factor_keys=factor_keys,
            factor_definitions=factor_snapshot_definitions,
            resume_running=True,
        ):
            return {
                "cycle_date": cycle_date.isoformat(),
                "strategy": preset.key,
                "strategy_label": preset.label,
                "dataset": dataset_version,
                "dataset_metadata": dataset_metadata,
                "generated_orders": 0,
                "simulated_orders": [],
                "risk_rejections": [],
                "skipped": True,
                "skip_reason": "该交易日已被其他纸面交易入口占用，已阻止重复委托",
                "state": store.state(),
                "data_quality": data_quality,
                "data_freshness": data_freshness,
                "paper_trading_allowed": False,
                "idempotency": {"cycle_date": cycle_date.isoformat(), "claimed": False},
            }
        current_bars = {bar.symbol: bar for bar in bars if bar.date == cycle_date}
        distributions = store.settle_distributions(cycle_date, distribution_events)
        history: dict[str, list[Bar]] = {}
        for bar in bars:
            if bar.date <= cycle_date:
                history.setdefault(bar.symbol, []).append(bar)
        prior_history = {
            symbol: [bar for bar in items if bar.date < cycle_date]
            for symbol, items in history.items()
        }
        prior_prices = {
            symbol: items[-1].close for symbol, items in prior_history.items() if items
        }
        close_prices = {symbol: bar.close for symbol, bar in current_bars.items()}
        open_prices = {symbol: bar.open for symbol, bar in current_bars.items()}
        cost_model = CostModel(max_participation=0.10)
        simulated_orders: list[dict[str, Any]] = []
        risk_rejections: list[dict[str, Any]] = []
        # Yesterday's close marks names that did not trade today. Today's close wins.
        prices = dict(prior_prices)
        prices.update({symbol: bar.close for symbol, bar in current_bars.items()})
        industries: dict[str, str | None] = {}
        for symbol in set(prices) | set(current_bars):
            current_bar = current_bars.get(symbol)
            industries[symbol] = (
                current_bar.industry if current_bar is not None else None
            ) or metadata.get(symbol, {}).get("industry")

        def estimate_order_risk(
            state: dict[str, Any],
            symbol: str,
            side: str,
            quantity: int,
            mark_prices: dict[str, float],
            return_panel: dict[str, dict[date, float]],
        ) -> dict[str, Any]:
            positions_now = {
                item["symbol"]: int(item["quantity"]) for item in state["positions"]
            }
            positions_now[symbol] = positions_now.get(symbol, 0) + (
                quantity if side == "BUY" else -quantity
            )
            equity = float(state["equity"])
            if equity <= 0:
                return {"status": "invalid_data", "observations": 0}
            projected_weights = {
                key: max(0.0, amount * mark_prices.get(key, 0.0) / equity)
                for key, amount in positions_now.items()
                if amount > 0
            }
            return estimate_portfolio_risk(
                projected_weights, return_panel, window=60, min_observations=20
            )

        prior_returns = _daily_returns(prior_history)
        signal_returns = _daily_returns(history)

        # A close-generated order may only be filled on a strictly later session.
        # Use that session's open and the previously confirmed return panel.
        # Late manual maintenance must not simulate an already elapsed open.
        for pending in ([] if defer_pending_fills else store.pending_strategy_orders(cycle_date)):
            pending_bar = current_bars.get(str(pending["symbol"]))
            if pending_bar is None:
                continue
            execution_prices = {**prices, **open_prices}
            latest_state = store.state(execution_prices)
            side = str(pending["side"]).upper()
            quantity = int(pending["quantity"])
            risk_estimate = estimate_order_risk(
                latest_state,
                pending_bar.symbol,
                side,
                quantity,
                execution_prices,
                prior_returns,
            )
            curve = [
                float(item["equity"])
                for item in latest_state.get("equity_curve", [])
                if item.get("equity") is not None
            ]
            peak_equity = max(
                curve + [float(latest_state["equity"])],
                default=float(latest_state["equity"]),
            )
            current_drawdown = (
                max(0.0, 1.0 - float(latest_state["equity"]) / peak_equity)
                if peak_equity > 0
                else None
            )
            violations = check_order_limits(
                {item["symbol"]: item["quantity"] for item in latest_state["positions"]},
                execution_prices,
                industries,
                float(latest_state["equity"]),
                pending_bar.symbol,
                side,
                quantity,
                PAPER_RISK_LIMITS,
                current_drawdown=current_drawdown,
                risk_estimate=risk_estimate,
            )
            if violations:
                rejected = store.reject_order(
                    pending["order_id"],
                    "pre_trade_risk: " + "; ".join(item.message for item in violations),
                )
                risk_rejections.append(
                    {
                        "order": rejected,
                        "risk_estimate": risk_estimate,
                        "violations": [
                            {"rule": item.rule, "message": item.message}
                            for item in violations
                        ],
                    }
                )
                simulated_orders.append(rejected)
                continue
            simulated_orders.append(
                store.execute_order(pending["order_id"], pending_bar, cost_model)
            )

        # Marks for signals are at today's close, after any prior signal was filled
        # at this session's open. Strategy only sees the selected, current PIT pool.
        current_state = store.state(close_prices or prior_prices)
        positions = {
            item["symbol"]: item["quantity"] for item in current_state["positions"]
        }
        snapshot = PortfolioSnapshot(
            as_of=cycle_date,
            cash=current_state["cash"],
            positions=positions,
            market_value=current_state["market_value"],
            equity=current_state["equity"],
        )
        orders = []
        commit_rebalance = False
        if rebalance_schedule["due"]:
            selected_strategy._days_seen = rebalance_every - 1
            strategy_history = {
                symbol: history.get(symbol, [])
                for symbol in history
                if symbol.split(".", 1)[0].upper() in candidate_codes
            }
            orders = selected_strategy.orders_for_date(
                cycle_date, strategy_history, snapshot
            )
            factor_snapshot = selected_strategy.factor_metadata()
            financial_status = factor_snapshot.get("financial_input_report", {})
            if not isinstance(financial_status, dict) or financial_status.get("status", "available") == "available":
                commit_rebalance = True
            else:
                rebalance_schedule["blocked_reason"] = "insufficient_financial_price_history"

        projected: dict[str, int | float] = {
            str(symbol): int(quantity) for symbol, quantity in positions.items()
        }
        new_accepted = 0
        pending_symbols = set(store.active_order_symbols())
        # Sells free gross exposure before sibling buys are checked.
        for order in sorted(orders, key=lambda item: (str(item.side).upper() != "SELL", item.symbol)):
            if order.symbol not in current_bars or order.symbol in pending_symbols:
                continue
            side = str(order.side).upper()
            latest_state = store.state(prices)
            book_state = {
                **latest_state,
                "positions": [
                    {"symbol": symbol, "quantity": quantity}
                    for symbol, quantity in projected.items()
                    if quantity > 0
                ],
            }
            risk_estimate = estimate_order_risk(
                book_state,
                order.symbol,
                side,
                int(order.quantity),
                prices,
                signal_returns,
            )
            curve = [
                float(item["equity"])
                for item in latest_state.get("equity_curve", [])
                if item.get("equity") is not None
            ]
            peak_equity = max(
                curve + [float(latest_state["equity"])],
                default=float(latest_state["equity"]),
            )
            current_drawdown = (
                max(0.0, 1.0 - float(latest_state["equity"]) / peak_equity)
                if peak_equity > 0
                else None
            )
            violations = check_order_limits(
                projected,
                prices,
                industries,
                float(latest_state["equity"]),
                order.symbol,
                side,
                int(order.quantity),
                PAPER_RISK_LIMITS,
                current_drawdown=current_drawdown,
                risk_estimate=risk_estimate if side == "BUY" else None,
            )
            paper_order = store.create_order(
                order.symbol,
                order.side,
                order.quantity,
                source=f"strategy:{preset.key}",
                signal_date=cycle_date,
                idempotency_key=f"cycle:{cycle_date.isoformat()}:{preset.key}:{order.symbol}:{str(order.side).upper()}",
            )
            if paper_order.get("status") == "REJECTED":
                violations = []
                risk_rejections.append(
                    {
                        "order": paper_order,
                        "risk_estimate": risk_estimate,
                        "violations": [
                            {
                                "rule": "order_validation",
                                "message": paper_order.get("reject_reason")
                                or "invalid order",
                            }
                        ],
                    }
                )
                simulated_orders.append(paper_order)
                continue
            if violations:
                rejected = store.reject_order(
                    paper_order["order_id"],
                    "pre_trade_risk: " + "; ".join(item.message for item in violations),
                )
                risk_rejections.append(
                    {
                        "order": rejected,
                        "risk_estimate": risk_estimate,
                        "violations": [
                            {"rule": item.rule, "message": item.message}
                            for item in violations
                        ],
                    }
                )
                simulated_orders.append(rejected)
                continue
            simulated_orders.append(paper_order)
            pending_symbols.add(order.symbol)
            new_accepted += 1
            delta = int(order.quantity) if side == "BUY" else -int(order.quantity)
            projected[order.symbol] = projected.get(order.symbol, 0) + delta
        if commit_rebalance and (not orders or new_accepted):
            store.set_last_rebalance_date(preset.key, rebalance_every, cycle_date)
            rebalance_schedule["last_rebalance_date"] = cycle_date.isoformat()
        store.record_quotes(list(current_bars.values()))
        store.record_entitlements(cycle_date)
        state = store.record_equity(
            cycle_date, {symbol: bar.close for symbol, bar in current_bars.items()}
        )
        store.record_cycle(
            cycle_date,
            strategy=preset.key,
            generated_orders=len(orders),
            executed_orders=sum(
                1 for item in simulated_orders if item.get("status") in {"FILLED", "PARTIALLY_FILLED"}
            ),
            source=f"strategy:{preset.key}",
            factor_pipeline_version=str(factor_snapshot.get("version") or ""),
            factor_keys=factor_keys,
            factor_definitions=factor_snapshot_definitions,
        )
        return {
            "cycle_date": cycle_date.isoformat(),
            "strategy": preset.key,
            "strategy_label": preset.label,
            "dataset": dataset_version,
            "dataset_metadata": dataset_metadata,
            "generated_orders": len(orders),
            "simulated_orders": simulated_orders,
            "pending_orders": store.active_strategy_order_count(),
            "risk_rejections": risk_rejections,
            "skipped": False,
            "state": state,
            "strategy_validation": validation_payload,
            "factor_pipeline": factor_snapshot,
            "rebalance_schedule": rebalance_schedule,
            "corporate_distributions": distributions,
            "data_quality": {
                "limits_available": data_quality["limits_available"],
                "warnings": [
                    *data_quality["warnings"],
                    *(["股票行业元数据来自缓存"] if metadata_stale else []),
                ],
            },
            "data_freshness": data_freshness,
            "paper_trading_allowed": True,
            "pre_trade_risk": estimate_portfolio_risk(
                {
                    item["symbol"]: float(item["market_value"])
                    / max(float(current_state["equity"]), 1e-9)
                    for item in current_state["positions"]
                },
                prior_returns,
            ),
            "paper_universe": universe_snapshot
            or {"mode": "explicit_symbols", "count": len(symbols)},
        }


def _daily_returns(histories: dict[str, list[Bar]]) -> dict[str, dict[date, float]]:
    returns: dict[str, dict[date, float]] = {}
    for symbol, bars in histories.items():
        ordered = sorted(
            (bar for bar in bars if bar.close > 0), key=lambda item: item.date
        )
        returns[symbol] = {
            right.date: float(right.close) / float(left.close) - 1.0
            for left, right in itertools.pairwise(ordered)
            if left.close > 0
        }
    return returns


def _rebalance_schedule(
    trading_dates: Sequence[date],
    *,
    cycle_date: date,
    last_rebalance_date: date | None,
    rebalance_every: int,
) -> dict[str, Any]:
    """Use persisted dates and actual exchange sessions, not a rolling-window phase."""

    if rebalance_every <= 0:
        raise ValueError("rebalance_every must be positive")
    if last_rebalance_date is None:
        elapsed = None
        due = True
    else:
        elapsed = sum(
            1
            for trading_date in set(trading_dates)
            if last_rebalance_date < trading_date <= cycle_date
        )
        due = cycle_date > last_rebalance_date and elapsed >= rebalance_every
    return {
        "due": due,
        "interval_trading_days": rebalance_every,
        "trading_days_since_previous": elapsed,
        "previous_rebalance_date": last_rebalance_date.isoformat()
        if last_rebalance_date
        else None,
        "last_rebalance_date": last_rebalance_date.isoformat()
        if last_rebalance_date
        else None,
    }


def _validation_payload(report: Any) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "data_days": report.data_days,
        "folds": report.folds,
        "embargo": report.embargo,
        "oos_observations": report.oos_observations,
        "annualized_return": report.annualized_return,
        "sharpe": report.sharpe,
        "max_drawdown": report.max_drawdown,
        "note": report.note,
        "checks": [
            {"name": item.name, "status": item.status, "evidence": item.evidence}
            for item in report.checks
        ],
    }


def _limits_data_quality(metadata: dict[str, Any]) -> dict[str, Any]:
    available = bool(metadata.get("limits_available", False))
    if available:
        return {"limits_available": True, "warnings": []}
    status = str(metadata.get("limit_data_status") or "unknown")
    detail = str(metadata.get("limit_data_error") or "").strip()
    suffix = f"：{detail}" if detail else ""
    return {
        "limits_available": False,
        "warnings": [f"Tushare stk_limit 涨跌停价不可用（状态：{status}）{suffix}"],
    }


def _data_freshness(metadata: dict[str, Any], cycle_date: date) -> dict[str, Any]:
    latest_value = str(metadata.get("latest_trade_date") or cycle_date.isoformat())[:10]
    try:
        latest = date.fromisoformat(latest_value)
    except ValueError:
        latest = cycle_date
    today = _china_today()
    age_days = max(0, (today - latest).days)
    source = str(metadata.get("data_source") or "unknown")
    warnings: list[str] = []
    verified_resume = (
        source == "tushare_verified_close_resume"
        and metadata.get("closing_resume_verified") is True
        and metadata.get("resume_max_age_seconds") == 600
        and bool(metadata.get("resumed_completed_symbols"))
        and latest == today
    )
    if source != "tushare_live" and not verified_resume:
        warnings.append(f"纸面交易只接受实时 Tushare 行情，当前来源为 {source}")
    if latest > today:
        warnings.append(f"行情日期 {latest.isoformat()} 超过当前日期")
    elif age_days > PAPER_MAX_STALENESS_DAYS:
        warnings.append(f"最新行情为 {latest.isoformat()}，已滞后 {age_days} 天")
    if cycle_date != today:
        warnings.append(
            f"可用日线截止 {cycle_date.isoformat()}，尚无 {today.isoformat()} 的当日收盘数据"
        )
    return {
        "fresh": not warnings,
        "source": source,
        "latest_trade_date": latest.isoformat(),
        "age_days": age_days,
        "max_staleness_days": PAPER_MAX_STALENESS_DAYS,
        "warnings": warnings,
        "proven_full_day_suspensions": metadata.get("proven_full_day_suspensions", []),
        "proven_delistings": metadata.get("proven_delistings", {}),
    }


def _china_today() -> date:
    return datetime.now(CHINA_TZ).date()


def is_a_share_trading_day(day: date) -> bool:
    """Ask Tushare's SSE calendar; missing or malformed responses fail closed."""

    if day.weekday() >= 5:
        return False
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "未配置 TUSHARE_TOKEN，无法核对 A 股交易日历，已禁止纸面委托"
        )
    trade_date = day.strftime("%Y%m%d")
    try:
        rows = _tushare_http_query(
            token,
            "trade_cal",
            {"exchange": "SSE", "start_date": trade_date, "end_date": trade_date},
            fields="cal_date,is_open",
        ).to_dict(orient="records")
    except Exception as error:
        raise RuntimeError(
            f"Tushare 交易日历不可用，已禁止纸面委托：{error}"
        ) from error
    matches = [
        row
        for row in rows
        if str(row.get("cal_date", "")).replace("-", "") == trade_date
    ]
    if len(matches) != 1 or str(matches[0].get("is_open", "")).strip() not in {
        "0",
        "1",
    }:
        raise RuntimeError(
            f"Tushare 交易日历未返回 {day.isoformat()} 的明确开休市状态，已禁止纸面委托"
        )
    return str(matches[0]["is_open"]).strip() == "1"


def _require_complete_universe(metadata: dict[str, Any], *, context: str) -> None:
    missing = list(metadata.get("missing_symbols") or [])
    if missing:
        available = list(metadata.get("available_symbols") or [])
        raise RuntimeError(
            f"{context}行情完整性不足：股票池共 {len(available) + len(missing)} 只，"
            f"仅返回 {len(available)} 只；缺少 {', '.join(missing[:8])}。"
            "已停止本次运行，避免用部分股票数据得出错误结论。"
        )
