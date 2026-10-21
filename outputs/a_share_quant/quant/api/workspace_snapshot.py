"""Workspace snapshot services with explicit runtime dependencies."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from quant.api.contracts import (
    AsFloatCallback,
    AverageCallback,
    BuildLiveFactorRowsCallback,
    DataFreshnessCallback,
    EnrichPaperStateCallback,
    EnsureSealedSnapshotCallback,
    LimitsDataQualityCallback,
    ObjectListCallback,
    RequireCompleteUniverseCallback,
    ResearchBarsCallback,
    RiskLimitsPayloadCallback,
    SealedPipelineKeyCallback,
    SealedScopeCallback,
    ValidationGateCallback,
)
from quant.backtest import CostModel, EventDrivenBacktester
from quant.data import (
    DEFAULT_SYMBOLS,
    TushareDailyAdapter,
    fetch_tushare_fundamental_factors,
    fetch_tushare_indices,
    fetch_tushare_market_breadth,
    fetch_tushare_statements,
)
from quant.execution import PaperScheduler, PaperStore
from quant.factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from quant.reports.audit import build_audit_checklist
from quant.reports.performance import build_performance_report
from quant.research import ResearchBudgetExceeded, ResearchLedger
from quant.strategies import build_strategy
from quant.time_utils import china_today
from quant.validation import validate_strategy


@dataclass(frozen=True)
class WorkspaceSnapshotDependencies:
    DATA_CACHE_DIR: Path
    FACTOR_STORE: FactorRegistryStore
    PAPER_SCHEDULER: PaperScheduler | None
    PAPER_STORE: PaperStore
    RESEARCH_LEDGER: ResearchLedger
    SEALED_TEST_DAYS: int
    TushareDailyAdapter: type[TushareDailyAdapter]
    _as_float: AsFloatCallback
    _average: AverageCallback
    _build_live_factor_rows: BuildLiveFactorRowsCallback
    _data_freshness: DataFreshnessCallback
    _enrich_paper_state: EnrichPaperStateCallback
    _ensure_sealed_snapshot: EnsureSealedSnapshotCallback
    _limits_data_quality: LimitsDataQualityCallback
    _object_list: ObjectListCallback
    _require_complete_universe: RequireCompleteUniverseCallback
    _research_bars: ResearchBarsCallback
    _risk_limits_payload: RiskLimitsPayloadCallback
    _sealed_pipeline_key: SealedPipelineKeyCallback
    _sealed_scope: SealedScopeCallback
    _validation_gate: ValidationGateCallback


def _workspace_seal_or_readonly(
    ledger: ResearchLedger, create: Callable[[], dict[str, object]]
) -> tuple[dict[str, object], bool]:
    """Keep display available without creating or reusing an exposed seal."""
    try:
        return create(), False
    except ResearchBudgetExceeded:
        cutoff = ledger.earliest_evaluated_seal_start()
        if cutoff is None:
            raise
        return {
            "id": None,
            "sealed_start": cutoff,
            "sealed_end": None,
            "status": "overlap_blocked",
        }, True


def _build_workspace_snapshot(
    *, deps: WorkspaceSnapshotDependencies
) -> dict[str, object]:
    """Build the overview from live Tushare data and persisted paper state."""
    token = os.getenv("TUSHARE_TOKEN")
    indices, index_errors = fetch_tushare_indices(
        token=token, cache_dir=deps.DATA_CACHE_DIR, force_refresh=True
    )
    warnings: list[str] = [f"{item['name']}：{item['error']}" for item in index_errors]
    latest_index = (
        max(indices, key=lambda item: str(item["trade_date"])) if indices else None
    )
    latest_trade_date = (
        date.fromisoformat(str(latest_index["trade_date"]))
        if latest_index is not None
        else None
    )
    index = next(
        (
            item
            for item in indices
            if item["ts_code"] == "000001.SH"
            and str(item["trade_date"])
            == (str(latest_trade_date) if latest_trade_date else "")
        ),
        latest_index,
    )
    if latest_index is None:
        warnings.append("Tushare 指数快照不可用；将尝试用真实股票日线继续生成工作区")
    elif index is not None and index["ts_code"] != "000001.SH":
        warnings.append(
            f"上证指数当日快照缺失；当前参考 {index.get('ts_code')} 的真实行情日期"
        )
    adapter = deps.TushareDailyAdapter(
        symbols=DEFAULT_SYMBOLS,
        token=token,
        adjust="",
        cache_dir=deps.DATA_CACHE_DIR,
        fetch_limits=True,
        force_refresh=True,
    )
    try:
        bars = adapter.daily_bars(start=date(2024, 1, 2), end=china_today())
    except RuntimeError as error:
        raise RuntimeError(
            f"工作台回测要求实时 Tushare 行情，实时刷新失败：{error}"
        ) from error
    dataset_metadata = dict(adapter.snapshot().metadata)
    deps._require_complete_universe(dataset_metadata, context="工作台")
    if not bars:
        raise RuntimeError("Tushare 未返回工作台所需的股票日线")
    if latest_trade_date is None:
        latest_trade_date = max(bar.date for bar in bars)
    try:
        breadth = fetch_tushare_market_breadth(
            latest_trade_date,
            token=token,
            cache_dir=deps.DATA_CACHE_DIR,
            force_refresh=True,
        )
        breadth_error = None
    except RuntimeError as error:
        breadth = None
        breadth_error = str(error)
        warnings.append(breadth_error)
    sealed_pipeline_key = deps._sealed_pipeline_key(
        deps._sealed_scope(list(DEFAULT_SYMBOLS), "")
    )
    sealed, overlap_blocked = _workspace_seal_or_readonly(
        deps.RESEARCH_LEDGER,
        lambda: deps._ensure_sealed_snapshot(
            "baseline",
            sealed_pipeline_key,
            bars,
            adapter.snapshot(),
            symbols=list(DEFAULT_SYMBOLS),
            adjust="",
            sealed_days=deps.SEALED_TEST_DAYS,
        ),
    )
    research_bars = deps._research_bars(bars, sealed)
    if overlap_blocked:
        warnings.append(
            "当前密封区间与已评估数据重叠；仅展示最早已评估区间之前的只读研究结果，"
            "不创建新密封快照、不视为密封验收通过。"
        )
    dataset_metadata.update(
        {
            "research_data_role": "pre_exposed_seal_read_only" if overlap_blocked else "pre_sealed",
            "sealed_test_id": sealed["id"],
            "sealed_test_start": sealed["sealed_start"],
            "sealed_test_end": sealed["sealed_end"],
            "sealed_test_status": sealed["status"],
        }
    )
    factor_definitions = deps.FACTOR_STORE.definitions(enabled_only=True)
    cost_model = CostModel(max_participation=0.1)
    result = EventDrivenBacktester(initial_cash=1000000, cost_model=cost_model).run(
        research_bars,
        build_strategy(
            "baseline", rebalance_every=5, factor_definitions=factor_definitions
        ),
    )
    report = build_performance_report(result)
    strategy_factory = lambda: build_strategy(
        "baseline", rebalance_every=5, factor_definitions=factor_definitions
    )
    validation = validate_strategy(
        research_bars,
        strategy_factory,
        initial_cash=1000000,
        cost_model=cost_model,
        embargo=None,
        rebalance_every=5,
    )
    pit_checked = any(
        
            item.name == "点时历史检查" and item.status == "通过"
            for item in validation.checks
        
    )
    walk_forward_checked = any(
        
            item.name == "Purged Walk-Forward" and item.status == "通过"
            for item in validation.checks
        
    )
    data_quality = deps._limits_data_quality(dataset_metadata, context="工作台回测")
    data_freshness = deps._data_freshness(dataset_metadata, latest_trade_date)
    warnings.extend(data_freshness["warnings"])
    audit = build_audit_checklist(
        result,
        pit_checked=pit_checked,
        walk_forward_checked=walk_forward_checked,
        limits_checked=bool(dataset_metadata.get("limits_available", False)),
    )
    fundamental_rows: list[dict[str, object]] = []
    fundamental_metadata: dict[str, object] = {
        "status": "not_requested",
        "observation_count": 0,
    }
    try:
        fundamental_rows, fundamental_metadata = fetch_tushare_fundamental_factors(
            DEFAULT_SYMBOLS,
            as_of=latest_trade_date,
            token=token,
            cache_dir=deps.DATA_CACHE_DIR,
            force_refresh=True,
        )
        fundamental_errors = deps._object_list(fundamental_metadata.get("errors"))
        if fundamental_errors:
            warnings.append(
                "财务指标：" + "；".join(str(item) for item in fundamental_errors[:3])
            )
    except RuntimeError as error:
        fundamental_metadata = {
            "status": "unavailable",
            "observation_count": 0,
            "errors": [str(error)],
        }
        warnings.append(f"财务指标不可用：{error}")
    statement_metadata: dict[str, object] = {
        "status": "not_requested",
        "available_symbols": [],
    }
    try:
        _, statement_metadata = fetch_tushare_statements(
            DEFAULT_SYMBOLS[:1],
            as_of=latest_trade_date,
            statement="all",
            token=token,
            cache_dir=deps.DATA_CACHE_DIR,
            force_refresh=True,
        )
        statement_errors = deps._object_list(statement_metadata.get("errors"))
        if statement_errors:
            warnings.append(
                "三大报表：" + "；".join(str(item) for item in statement_errors[:3])
            )
    except RuntimeError as error:
        statement_metadata = {
            "status": "unavailable",
            "available_symbols": [],
            "errors": [str(error)],
        }
        warnings.append(f"三大报表不可用：{error}")
    factor_rows, signal_rows = deps._build_live_factor_rows(
        bars, token, factor_definitions, fundamental_rows
    )
    paper = deps._enrich_paper_state(deps.PAPER_STORE.state())
    validation_gate = deps._validation_gate(
        "baseline",
        validation.passed,
        sealed_pipeline_key=sealed_pipeline_key,
        sealed_scope=deps._sealed_scope(list(DEFAULT_SYMBOLS), ""),
    )
    research_run_count = deps.RESEARCH_LEDGER.count("baseline")
    return {
        "ok": True,
        "source": "tushare",
        "generated_at": datetime.now(UTC).isoformat(),
        "latest_trade_date": latest_trade_date.isoformat(),
        "warnings": warnings,
        "market": {
            "index": index,
            "indices": indices,
            "index_errors": index_errors,
            "breadth": breadth,
            "breadth_error": breadth_error,
        },
        "portfolio": {
            "dataset": adapter.snapshot().version,
            "dataset_metadata": dataset_metadata,
            "symbols": list(DEFAULT_SYMBOLS),
            "bars": len(bars),
            "final_equity": result.final_equity,
            "metrics": {
                "annualized_return": report.annualized_return,
                "sharpe": report.sharpe,
                "max_drawdown": report.max_drawdown,
                "recovery_days": report.recovery_days,
                "turnover": report.turnover,
            },
            "data_quality": data_quality,
            "data_freshness": data_freshness,
            "paper_trading_allowed": bool(
                not overlap_blocked and data_quality["limits_available"] and data_freshness["fresh"]
            ),
            "audit": [
                {"name": item.name, "status": item.status, "evidence": item.evidence}
                for item in audit
            ],
            "strategy_validation": {
                "passed": validation.passed,
                "data_days": validation.data_days,
                "folds": validation.folds,
                "embargo": validation.embargo,
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
            "validation_gate": validation_gate,
            "factor_pipeline": {
                "version": FACTOR_PIPELINE_VERSION,
                "factor_definitions": [item.as_dict() for item in factor_definitions],
                "fundamental_policy": "历史回测仅使用点时可计算的行情因子；基本面必须提供公告日对齐序列后才进入历史评分",
            },
            "equity_curve": [
                {"date": point.date.isoformat(), "equity": point.equity}
                for point in result.equity_curve
            ],
        },
        "research": {
            "latest_trade_date": latest_trade_date.isoformat(),
            "factor_count": len(factor_definitions),
            "valid_count": len({str(row["symbol"]) for row in factor_rows}),
            "average_momentum": deps._average(
                [
                    deps._as_float(row["value"])
                    for row in factor_rows
                    if row["factor_key"] == "builtin_momentum_20"
                ]
            ),
            "definitions": [item.as_dict() for item in factor_definitions],
            "rows": factor_rows,
            "fundamental_metadata": fundamental_metadata,
            "financial_statements": statement_metadata,
            "run_count": research_run_count,
            "run_budget": deps.RESEARCH_LEDGER.budget,
            "validation_gate": validation_gate,
            "factor_pipeline": {
                **strategy_factory().factor_metadata(),
                "factor_definitions": [item.as_dict() for item in factor_definitions],
                "fundamental_policy": "历史回测仅使用点时可计算的行情因子；基本面必须提供公告日对齐序列后才进入历史评分",
            },
        },
        "signals": signal_rows,
        "paper": paper,
        "risk_limits": deps._risk_limits_payload(),
        "scheduler": deps.PAPER_SCHEDULER.status()
        if deps.PAPER_SCHEDULER is not None
        else {"enabled": False, "running": False},
    }
