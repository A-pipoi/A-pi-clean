"""Audited synchronous backtest orchestration."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from quant.api.contracts import (
    AsFloatCallback,
    BacktestOperationalReportsCallback,
    BoundedIntCallback,
    BuildStrategyCallback,
    DatasetMetadataWithUniverseCallback,
    EnsureSealedSnapshotCallback,
    FilterBacktestUniverseCallback,
    LimitsDataQualityCallback,
    OptionalDateCallback,
    RecordResearchExperimentCallback,
    ReportBacktestProgressCallback,
    RequireCompleteUniverseCallback,
    RequireStrategyDependenciesCallback,
    ResearchBarsCallback,
    ResolveBacktestUniverseCallback,
    ScopedDatasetVersionCallback,
    SealedPipelineKeyCallback,
    SealedScopeCallback,
    StrictPitBrinsonReportCallback,
    ValidateStrategyCallback,
    ValidationGateCallback,
)
from quant.backtest import CostModel, EventDrivenBacktester
from quant.data import TushareDailyAdapter
from quant.factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from quant.features import FeatureStore
from quant.notifications import NotificationOutbox
from quant.portfolio.analytics import build_portfolio_risk_report
from quant.reports.audit import build_audit_checklist
from quant.reports.contribution import build_security_contribution
from quant.reports.performance import build_monthly_returns, build_performance_report
from quant.research import ResearchBudgetExceeded, ResearchLedger
from quant.strategies import (
    StrategyPresetConflict,
    get_strategy_preset,
    resolve_strategy_config,
)
from quant.time_utils import SHANGHAI_TZ

from ..strategies.rulebook import RecipeStore, RulebookStrategy
from .backtest_jobs import BacktestCancelled


@dataclass(frozen=True)
class BacktestDependencies:
    DATA_CACHE_DIR: Path
    FACTOR_STORE: FactorRegistryStore
    FEATURE_STORE: FeatureStore
    RESEARCH_LEDGER: ResearchLedger
    SEALED_TEST_DAYS: int
    TushareDailyAdapter: type[TushareDailyAdapter]
    _as_float: AsFloatCallback
    _backtest_operational_reports: BacktestOperationalReportsCallback
    _bounded_int: BoundedIntCallback
    _dataset_metadata_with_universe: DatasetMetadataWithUniverseCallback
    _ensure_sealed_snapshot: EnsureSealedSnapshotCallback
    _filter_backtest_universe: FilterBacktestUniverseCallback
    _limits_data_quality: LimitsDataQualityCallback
    _optional_date: OptionalDateCallback
    _record_research_experiment: RecordResearchExperimentCallback
    _report_backtest_progress: ReportBacktestProgressCallback
    _require_complete_universe: RequireCompleteUniverseCallback
    _require_strategy_dependencies: RequireStrategyDependenciesCallback
    _research_bars: ResearchBarsCallback
    _resolve_backtest_universe: ResolveBacktestUniverseCallback
    _scoped_dataset_version: ScopedDatasetVersionCallback
    _sealed_pipeline_key: SealedPipelineKeyCallback
    _sealed_scope: SealedScopeCallback
    _strict_pit_brinson_report: StrictPitBrinsonReportCallback
    _validation_gate: ValidationGateCallback
    build_strategy: BuildStrategyCallback
    validate_strategy: ValidateStrategyCallback


def run_backtest_request(
    payload: dict[str, Any],
    deps: BacktestDependencies,
    *,
    _prepared_market_data: dict[str, Any] | None = None,
) -> tuple[int, dict[str, object]]:
    from quant.strategies.audited_baseline import AUDITED_STRATEGIES

    strategy_name = str(payload.get("strategy", "baseline")).strip().lower()
    audited_candidate = strategy_name in AUDITED_STRATEGIES
    if payload.get("policy_comparison") is True:
        if audited_candidate:
            return 400, {"ok": False, "error": "审计基准已包含三组训练权重比较，请与四象限政策比较分开运行"}
        if _prepared_market_data is not None:
            return 400, {"ok": False, "error": "nested policy comparison is not supported"}
        return _run_policy_comparison_request(payload, deps)
    experiment_id = None
    try:
        strategy_name = str(payload.get("strategy", "baseline")).strip().lower()
        strategy_preset = get_strategy_preset(strategy_name)
        recipe_version=str(payload.get('recipe_version') or '')
        recipe=RecipeStore(deps.RESEARCH_LEDGER.path.with_name('strategy_recipes.sqlite3')).get(recipe_version) if strategy_name=='rulebook' else None
        deps._require_strategy_dependencies(strategy_name)
        requested_lookback = deps._bounded_int(
            payload.get("lookback", strategy_preset.lookback), 5, 60
        )
        requested_top_n = deps._bounded_int(
            payload.get("top_n", strategy_preset.top_n), 1, 10
        )
        _, lookback, top_n = resolve_strategy_config(
            strategy_name, requested_lookback, requested_top_n
        )
        if recipe is not None:
            lookback = RulebookStrategy(recipe).history
            top_n = int(recipe['top_n'])
        initial_cash = deps._as_float(payload.get("initial_cash", 1000000))
        if not math.isfinite(initial_cash) or initial_cash <= 0 or initial_cash > 100000000:
            raise ValueError("initial_cash is outside the allowed range")
        data_source = str(payload.get("data_source", "tushare")).strip().lower()
        if data_source != "tushare":
            raise ValueError("A-pi 已停用合成数据，请使用 data_source='tushare'")
        start_date = deps._optional_date(payload.get("start_date"))
        end_date = deps._optional_date(payload.get("end_date"))
        if start_date and end_date and (start_date > end_date):
            raise ValueError("start_date cannot be later than end_date")
        if not deps.RESEARCH_LEDGER.can_record(strategy_name):
            raise ResearchBudgetExceeded(
                f"策略 {strategy_name!r} 的研究预算已用尽，请先归档当前研究周期"
            )
        if audited_candidate and deps.RESEARCH_LEDGER.budget - deps.RESEARCH_LEDGER.count(strategy_name) < 4:
            raise ResearchBudgetExceeded("审计基准含3组训练权重比较，需要至少4个剩余研究预算名额")
        adjust = str(payload.get("adjust", "")).strip().lower()
        signal_adjust = str(payload.get("signal_adjust", "raw")).strip().lower()
        corporate_actions = str(payload.get("corporate_actions", "off")).strip().lower()
        if corporate_actions not in {"off", "on"}:
            raise ValueError("corporate_actions must be 'off' or 'on'")
        if corporate_actions == "on" and adjust:
            raise ValueError("corporate_actions='on' requires adjust='' so raw execution prices are preserved")
        if signal_adjust not in {"raw", "adj"}:
            raise ValueError("signal_adjust must be 'raw' or 'adj'")
        if signal_adjust == "adj":
            if adjust:
                raise ValueError("signal_adjust='adj' requires adjust='' so execution prices remain raw")
            if strategy_name in {"linear_ml", "lightgbm", "multi_signal"}:
                raise ValueError(f"strategy {strategy_name!r} does not support signal_adjust='adj'")
        rebalance = str(payload.get("rebalance", "weekly")).strip().lower()
        if rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance must be 'daily', 'weekly', or 'monthly'")
        cost_profile = str(payload.get("cost_profile", "standard")).strip().lower()
        if cost_profile not in {"standard", "low"}:
            raise ValueError("cost_profile must be 'standard' or 'low'")
        if _prepared_market_data is None:
            deps._report_backtest_progress(16, "正在解析回测宇宙（PIT 成分）")
            universe = deps._resolve_backtest_universe(payload, start_date, end_date)
            symbols = universe["symbols"]
            deps._report_backtest_progress(
                18, f"宇宙已就绪：{len(symbols)} 只标的，开始研究准入预检"
            )
            from quant.data.research_eligibility import preflight_audit_evidence

            preflight_audit_evidence(payload, list(symbols))
            adapter_token = os.getenv("TUSHARE_TOKEN")
            if signal_adjust != "raw":
                adapter = deps.TushareDailyAdapter(
                    symbols=symbols, token=adapter_token, adjust=adjust,
                    cache_dir=deps.DATA_CACHE_DIR, fetch_limits=True, force_refresh=True,
                    signal_adjust=signal_adjust,
                )
            else:
                adapter = deps.TushareDailyAdapter(
                    symbols=symbols, token=adapter_token, adjust=adjust,
                    cache_dir=deps.DATA_CACHE_DIR, fetch_limits=True, force_refresh=True,
                )
            from quant.data.progress import DATA_PROGRESS

            def _on_data_progress(current: int, total: int, symbol: str) -> None:
                total = max(int(total), 1)
                current = max(0, min(int(current), total))
                pct = 20 + int(12 * current / total)
                deps._report_backtest_progress(
                    pct, f"正在拉取真实行情 {current}/{total} · {symbol}"
                )

            progress_token = DATA_PROGRESS.set(_on_data_progress)
            try:
                deps._report_backtest_progress(20, "开始拉取 Tushare 日线")
                raw_bars = adapter.daily_bars(
                    start=universe["start_date"], end=universe["end_date"]
                )
            finally:
                DATA_PROGRESS.reset(progress_token)
            snapshot = adapter.snapshot()
            deps._report_backtest_progress(33, "行情拉取完成，正在做完整性检查")
        else:
            universe = deepcopy(_prepared_market_data["universe"])
            symbols = list(universe["symbols"])
            raw_bars = list(_prepared_market_data["bars"])
            source_snapshot = _prepared_market_data["snapshot"]
            snapshot = replace(
                source_snapshot,
                version=f"{source_snapshot.version}:policy-signal:{signal_adjust}",
                metadata={**dict(source_snapshot.metadata), "signal_adjust": signal_adjust},
            )
            if signal_adjust == "raw":
                raw_bars = [
                    replace(bar, signal_open=None, signal_high=None, signal_low=None, signal_close=None)
                    for bar in raw_bars
                ]
        deps._require_complete_universe(snapshot.metadata, context="回测")
        deps._report_backtest_progress(34, "完整性检查通过，正在筛选宇宙并应用研究准入")
        bars = deps._filter_backtest_universe(raw_bars, universe)
        from quant.data.research_eligibility import apply_universe_research_policy
        bars, universe = apply_universe_research_policy(bars, payload, universe)
        deps._report_backtest_progress(
            35, f"行情已载入：{len(bars)} 条日线，准备 PIT 回测"
        )
        dataset = deps._scoped_dataset_version(snapshot.version, universe["version"])
        dataset_metadata = deps._dataset_metadata_with_universe(
            snapshot.metadata, universe
        )
        if not bars:
            raise ValueError("没有可用于回测的行情数据")
        action_coverage = None
        if corporate_actions == "on":
            from quant.backtest.dividend_coverage import (
                require_reconciled_dividend_coverage,
            )
            from quant.data.extension_store import default_store_path

            action_coverage = require_reconciled_dividend_coverage(
                default_store_path(),
                symbols=sorted({bar.symbol for bar in bars}),
                start_date=min(bar.date for bar in bars),
                end_date=max(bar.date for bar in bars),
            )
            dataset = f"{dataset}:corporate-actions:on:v1:{action_coverage['event_snapshot_digest']}"
            dataset_metadata["corporate_action_coverage"] = action_coverage
        if strategy_name in {"baseline_cost_aware", "baseline_full_audited"}:
            from quant.data.strategy_fundamentals import observed_financial_inputs
            first_day = min(bar.date for bar in bars)
            _, financial_report = observed_financial_inputs(first_day, sorted({bar.symbol for bar in bars}))
            if financial_report.get("status") != "available":
                raise ValueError("财务低换手候选仅支持真实已观测财务区间；历史PIT覆盖不足，未建立密封集或扣研究预算。请先做前向研究纸面验证。")
        sealed_scope_options = {"universe_version": universe["version"]}
        if signal_adjust != "raw":
            sealed_scope_options["signal_adjust"] = signal_adjust
        if corporate_actions != "off":
            sealed_scope_options["corporate_actions"] = corporate_actions
        sealed_scope = deps._sealed_scope(symbols, adjust, **sealed_scope_options)
        sealed_pipeline_key = deps._sealed_pipeline_key(sealed_scope)
        sealed = deps._ensure_sealed_snapshot(
            strategy_name,
            sealed_pipeline_key,
            bars,
            snapshot,
            symbols=symbols,
            adjust=adjust,
            sealed_days=deps.SEALED_TEST_DAYS,
            pit_data=universe.get("pit_data"),
            pit_metadata=universe.get("pit_metadata"),
            additional_parameters={
                **({"signal_adjust": signal_adjust} if signal_adjust != "raw" else {}),
                **({"corporate_actions": corporate_actions, "corporate_action_coverage": action_coverage}
                   if corporate_actions != "off" else {}),
            } or None,
        )
        if sealed.get("result") is not None:
            raise ResearchBudgetExceeded(
                "密封测试已完成，当前研究周期已锁定；请先归档研究周期后再调参"
            )
        research_bars = deps._research_bars(bars, sealed)
        if not research_bars:
            raise ValueError("密封区间之前没有可用于回测的研究行情")
        dataset_metadata.update(
            {
                "research_data_role": "pre_sealed",
                "sealed_test_id": sealed["id"],
                "sealed_test_start": sealed["sealed_start"],
                "sealed_test_end": sealed["sealed_end"],
                "sealed_test_status": sealed["status"],
            }
        )
        rebalance_every = {"daily": 1, "weekly": 5, "monthly": 21}[rebalance]
        factor_definitions = deps.FACTOR_STORE.definitions(enabled_only=True)
        feature_store_info = (
            deps.FEATURE_STORE.materialize(bars, dataset)
            if strategy_name == "lightgbm"
            else None
        )
        deps._report_backtest_progress(40, "数据准备完成，正在登记研究预算")
        experiment_id = deps._record_research_experiment(
            strategy_name,
            dataset,
            {
                "kind": "backtest",
                "initial_cash": initial_cash,
                "lookback": lookback,
                "top_n": top_n,
                "rebalance": rebalance,
                "cost_profile": cost_profile,
                "symbols": symbols,
                "research_universe": universe["metadata"],
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "factor_pipeline_version": FACTOR_PIPELINE_VERSION,
                "sealed_test_id": sealed["id"],
                "sealed_test_start": sealed["sealed_start"],
                "sealed_test_end": sealed["sealed_end"],
                "sealed_scope": sealed_scope,
                "feature_store": feature_store_info,
                "recipe_version":recipe_version if recipe else None,
                "recipe":recipe,
                **({"signal_adjust": signal_adjust} if signal_adjust != "raw" else {}),
                **({"corporate_actions": corporate_actions,
                    "corporate_action_coverage": action_coverage}
                   if corporate_actions != "off" else {}),
            },
            "started",
            "回测运行次数纳入20次研究预算；结果必须继续通过敏感性与密封测试集审查。",
        )
        cost_model = CostModel(max_participation=0.1)
        if cost_profile == "low":
            cost_model = CostModel(
                commission_rate=0.00015,
                min_commission=5.0,
                transfer_fee_rate=1e-05,
                fixed_slippage_bps=2.5,
                impact_bps=5.0,
                max_participation=0.1,
            )
        strategy_factory = lambda: RulebookStrategy(recipe,rebalance_every) if recipe is not None else deps.build_strategy(
            strategy_name,
            rebalance_every=rebalance_every,
            factor_definitions=factor_definitions,
            feature_store=deps.FEATURE_STORE if strategy_name == "lightgbm" else None,
            data_version=dataset if strategy_name == "lightgbm" else None,
        )
        selected_strategy = strategy_factory()
        training_weight_comparison = None
        action_provider = None
        if corporate_actions == "on":
            from quant.execution.corporate_actions import observed_events

            def action_provider(day: date) -> list[dict[str, Any]]:
                cutoff = datetime.combine(
                    day, time.max, tzinfo=SHANGHAI_TZ
                )
                return observed_events(cutoff)

        if audited_candidate:
            from quant.reports.training_weights import compare_training_weights

            training_weight_comparison = compare_training_weights(
                research_bars, strategy_name=strategy_name, parent_id=experiment_id, dataset=dataset,
                definitions=factor_definitions, initial_cash=initial_cash, cost_model=cost_model,
                rebalance_every=rebalance_every, ledger=deps.RESEARCH_LEDGER,
                record=deps._record_research_experiment, corporate_actions=corporate_actions == "on",
                action_provider=action_provider,
            )
        backtest_result = EventDrivenBacktester(
            initial_cash=initial_cash, cost_model=cost_model
        ).run(
            research_bars,
            selected_strategy,
            corporate_actions=corporate_actions == "on",
            action_provider=action_provider,
        )
        deps._report_backtest_progress(65, "回测撮合完成，正在计算归因和组合风险")
        performance_report = build_performance_report(backtest_result)
        security_contribution = build_security_contribution(backtest_result, research_bars)
        monthly_returns = build_monthly_returns(
            backtest_result.equity_curve, backtest_result.initial_cash
        )
        operational_reports = deps._backtest_operational_reports(
            backtest_result,
            research_bars,
            max_participation=cost_model.max_participation,
        )
        risk_analytics = build_portfolio_risk_report(backtest_result, research_bars)
        brinson_report = deps._strict_pit_brinson_report(backtest_result, research_bars)
        deps._report_backtest_progress(75, "正在运行 Purged Walk-Forward 验证")
        validation = deps.validate_strategy(
            research_bars,
            strategy_factory,
            initial_cash=initial_cash,
            cost_model=cost_model,
            embargo=None,
            rebalance_every=rebalance_every,
        )
        deps._report_backtest_progress(92, "验证完成，正在写入研究台账")
        pit_checked = any(
            
                item.name == "点时历史检查" and item.status == "通过"
                for item in validation.checks
            
        )
        walk_forward_checked = any(
            
                item.name == "Purged Walk-Forward" and item.status == "通过"
                for item in validation.checks
            
        )
        audit = build_audit_checklist(
            backtest_result,
            pit_checked=pit_checked,
            walk_forward_checked=walk_forward_checked,
            limits_checked=bool(dataset_metadata.get("limits_available", False)),
        )
        deps.RESEARCH_LEDGER.update_result(
            experiment_id,
            status="passed" if validation.passed else "needs_review",
            notes=validation.note,
        )
        deps.RESEARCH_LEDGER.update_experiment_parameters(
            experiment_id,
            {
                "monthly_returns": monthly_returns,
                "capacity_report": operational_reports["capacity"],
                "industry_trade_report": operational_reports["industry_trade"],
                "risk_analytics": risk_analytics,
                "brinson_report": brinson_report,
                "security_contribution": security_contribution,
                "training_weight_comparison": training_weight_comparison,
                "factor_pipeline": selected_strategy.factor_metadata(),
                "validation_summary": {
                    "passed": validation.passed,
                    "folds": validation.folds,
                    "oos_observations": validation.oos_observations,
                },
            },
        )
        validation_gate = deps._validation_gate(
            strategy_name,
            validation.passed,
            sealed_pipeline_key=sealed_pipeline_key,
            sealed_scope=sealed_scope,
        )
        notification_status = "queued"
        try:
            NotificationOutbox(deps.RESEARCH_LEDGER.path.with_name("notifications.sqlite3")).enqueue(
                f"backtest:{experiment_id}", "A-pi 回测任务完成",
                "研究产物已写入本账户台账，请在工作台查看验证状态；通知不代表验证通过或收益保证。")
        except (OSError, sqlite3.Error):
            # An optional delivery queue failure must not invalidate a saved experiment.
            notification_status = "queue_unavailable"
        return (
            200,
            {
                "ok": True,
                "engine": "python",
                "strategy": strategy_name,
                "strategy_label": strategy_preset.label,
                "strategy_version": recipe_version if recipe else strategy_preset.label.split("·")[-1].strip(),
                "strategy_description": strategy_preset.description,
                "factor_pipeline": selected_strategy.factor_metadata(),
                "research": {
                    "experiment_id": experiment_id,
                    "notification_status": notification_status,
                    "runs": deps.RESEARCH_LEDGER.count(strategy_name),
                    "run_count": deps.RESEARCH_LEDGER.count(strategy_name),
                    "budget": deps.RESEARCH_LEDGER.budget,
                    "remaining": max(
                        0,
                        deps.RESEARCH_LEDGER.budget
                        - deps.RESEARCH_LEDGER.count(strategy_name),
                    ),
                    "validation_gate": validation_gate,
                },
                "validation_gate": validation_gate,
                "dataset": dataset,
                "data_source": data_source,
                "dataset_metadata": dataset_metadata,
                "data_quality": deps._limits_data_quality(
                    dataset_metadata, context="本次回测"
                ),
                "bars": len(bars),
                "parameters": {
                    "recipe_version":recipe_version if recipe else None,
                    "recipe":recipe,
                    "lookback": lookback,
                    "top_n": top_n,
                    "initial_cash": initial_cash,
                    "symbols": symbols,
                    "research_universe": universe["metadata"],
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "adjust": adjust,
                    **({"signal_adjust": signal_adjust} if signal_adjust != "raw" else {}),
                    **({"corporate_actions": corporate_actions} if corporate_actions != "off" else {}),
                    "rebalance": rebalance,
                    "rebalance_every": rebalance_every,
                    "cost_profile": cost_profile,
                },
                "final_equity": backtest_result.final_equity,
                "total_cost": backtest_result.total_cost,
                "fills": len(backtest_result.fills),
                "training_weight_comparison": training_weight_comparison,
                "fills_detail": [
                    {
                        "symbol": fill.order.symbol,
                        "side": fill.order.side,
                        "date": fill.date.isoformat(),
                        "quantity": fill.quantity,
                    }
                    for fill in backtest_result.fills
                ],
                "corporate_action_report": backtest_result.corporate_action_report,
                "rejected": len(backtest_result.rejected),
                "metrics": {
                    "annualized_return": performance_report.annualized_return,
                    "sharpe": performance_report.sharpe,
                    "sortino": performance_report.sortino,
                    "calmar": performance_report.calmar,
                    "max_drawdown": performance_report.max_drawdown,
                    "recovery_days": performance_report.recovery_days,
                    "turnover": performance_report.turnover,
                    "cost_to_gross_profit": performance_report.cost_to_gross_profit,
                },
                "monthly_returns": monthly_returns,
                "capacity_report": operational_reports["capacity"],
                "industry_trade_report": operational_reports["industry_trade"],
                "risk_analytics": risk_analytics,
                "brinson_report": brinson_report,
                "security_contribution": security_contribution,
                "audit": [
                    {
                        "name": item.name,
                        "status": item.status,
                        "evidence": item.evidence,
                    }
                    for item in audit
                ],
                "strategy_validation": {
                    "passed": validation.passed,
                    "data_days": validation.data_days,
                    "folds": validation.folds,
                    "embargo": validation.embargo,
                    "fold_ranges": validation.fold_ranges,
                    "oos_observations": validation.oos_observations,
                    "annualized_return": validation.annualized_return,
                    "sharpe": validation.sharpe,
                    "max_drawdown": validation.max_drawdown,
                    "note": validation.note,
                    "checks": [
                        {
                            "name": item.name,
                            "status": item.status,
                            "evidence": item.evidence,
                        }
                        for item in validation.checks
                    ],
                },
                "equity_curve": [
                    {"date": point.date.isoformat(), "equity": point.equity}
                    for point in backtest_result.equity_curve
                ],
            },
        )
    except BacktestCancelled as error:
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(experiment_id, status="cancelled", notes=str(error)[:500])
        raise
    except ResearchBudgetExceeded as error:
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(
                experiment_id, status="failed", notes=str(error)[:500]
            )
        return (
            409,
            {"ok": False, "code": "research_budget_exceeded", "error": str(error)},
        )
    except StrategyPresetConflict as error:
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(
                experiment_id, status="failed", notes=str(error)[:500]
            )
        return (
            400,
            {
                "ok": False,
                "code": error.code,
                "error": str(error),
                "expected": error.expected,
                "got": error.got,
            },
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(
                experiment_id, status="failed", notes=str(error)[:500]
            )
        body: dict[str, object] = {"ok": False, "error": str(error)}
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            body["code"] = code
        return (400, body)
    except RuntimeError as error:
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(
                experiment_id, status="failed", notes=str(error)[:500]
            )
        return (
            502,
            {"ok": False, "code": "data_provider_unavailable", "error": str(error)},
        )
    except Exception as error:
        logging.getLogger(__name__).exception("quant.api.backtest_service failed: %s", type(error).__name__, exc_info=None)
        if experiment_id is not None:
            deps.RESEARCH_LEDGER.update_result(
                experiment_id, status="failed", notes=type(error).__name__
            )
        return (500, {"ok": False, "error": f"backtest_failed: {type(error).__name__}"})


def _run_policy_comparison_request(
    payload: dict[str, Any], deps: BacktestDependencies
) -> tuple[int, dict[str, object]]:
    from quant.backtest.comparison import run_four_policy_comparison
    from quant.backtest.dividend_coverage import require_reconciled_dividend_coverage
    from quant.data.extension_store import default_store_path

    strategy_name = str(payload.get("strategy", "baseline")).strip().lower()
    if strategy_name in {"linear_ml", "lightgbm", "multi_signal"}:
        return 400, {
            "ok": False,
            "error": f"strategy {strategy_name!r} does not support the adj signal quadrant",
        }
    if str(payload.get("adjust", "")).strip():
        return 400, {"ok": False, "error": "policy comparison requires adjust='' and raw execution prices"}
    try:
        deps._require_strategy_dependencies(strategy_name)
        remaining = deps.RESEARCH_LEDGER.budget - deps.RESEARCH_LEDGER.count(strategy_name)
        if remaining < 4:
            return 409, {
                "ok": False,
                "code": "research_budget_exceeded",
                "error": "四象限比较需要至少 4 个剩余普通研究预算名额",
                "remaining": max(0, remaining),
            }
        start_date = deps._optional_date(payload.get("start_date"))
        end_date = deps._optional_date(payload.get("end_date"))
        universe = deps._resolve_backtest_universe(payload, start_date, end_date)
        symbols = list(universe["symbols"])
        adapter = deps.TushareDailyAdapter(
            symbols=symbols,
            token=os.getenv("TUSHARE_TOKEN"),
            adjust="",
            cache_dir=deps.DATA_CACHE_DIR,
            fetch_limits=True,
            force_refresh=True,
            signal_adjust="adj",
        )
        bars = adapter.daily_bars(start=universe["start_date"], end=universe["end_date"])
        snapshot = adapter.snapshot()
        deps._require_complete_universe(snapshot.metadata, context="四象限比较")
        if not bars:
            return 400, {"ok": False, "error": "没有可用于四象限比较的行情数据"}
        from quant.data.research_eligibility import apply_universe_research_policy

        coverage_bars = deps._filter_backtest_universe(bars, universe)
        coverage_bars, _ = apply_universe_research_policy(
            coverage_bars, payload, deepcopy(universe)
        )
        if not coverage_bars:
            return 400, {"ok": False, "error": "研究准入筛选后没有可比较的行情数据"}
        coverage = require_reconciled_dividend_coverage(
            default_store_path(),
            symbols=sorted({bar.symbol for bar in coverage_bars}),
            start_date=min(bar.date for bar in coverage_bars),
            end_date=max(bar.date for bar in coverage_bars),
        )
        prepared = {"universe": universe, "bars": bars, "snapshot": snapshot}
        cell_results: list[dict[str, Any]] = []

        def run_cell(
            cell_payload: dict[str, Any], shared_data: object
        ) -> tuple[int, dict[str, Any]]:
            cell_payload.pop("policy_comparison", None)
            status, result = run_backtest_request(
                cell_payload, deps, _prepared_market_data=shared_data  # type: ignore[arg-type]
            )
            if status == 200:
                cell_results.append(result)
            return status, result

        def persist_comparison(experiment_id: int, report_data: dict[str, object]) -> None:
            deps.RESEARCH_LEDGER.update_experiment_parameters(
                experiment_id, {"policy_comparison": report_data}
            )

        report = run_four_policy_comparison(
            payload,
            prepared,
            remaining_budget=remaining,
            run_cell=run_cell,
            persist=persist_comparison,
        )
        experiment_ids = report.get("experiment_ids")
        if not isinstance(experiment_ids, list):
            raise TypeError("comparison report missing experiment identifiers")
        return 200, {
            "ok": True,
            "comparison": report,
            "runs": cell_results,
            "research": {
                "experiments": experiment_ids,
                "budget_slots_consumed": len(experiment_ids),
                "remaining": max(
                    0,
                    deps.RESEARCH_LEDGER.budget - deps.RESEARCH_LEDGER.count(strategy_name),
                ),
            },
            "data_snapshot": {
                "version": snapshot.version,
                "bars": len(bars),
                "corporate_action_coverage_digest": coverage["event_snapshot_digest"],
            },
        }
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return 400, {"ok": False, "error": str(error)}
    except RuntimeError as error:
        return 409, {
            "ok": False,
            "code": "policy_comparison_incomplete",
            "error": str(error),
            "completed_experiments": [
                item.get("research", {}).get("experiment_id")
                for item in cell_results
                if isinstance(item.get("research"), dict)
            ],
        }
