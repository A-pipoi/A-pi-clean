"""Predeclared weight comparisons on the FIRST training window only."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..backtest import CostModel, EventDrivenBacktester
from ..data.schema import Bar
from ..factors.store import FactorDefinition
from ..research import ResearchLedger
from ..strategies.audited_baseline import AuditedBaseline, weight_profiles
from .performance import build_performance_report


def compare_training_weights(
    bars: Sequence[Bar],
    *,
    strategy_name: str,
    parent_id: int,
    dataset: str,
    definitions: Sequence[FactorDefinition],
    initial_cash: float,
    cost_model: CostModel,
    rebalance_every: int,
    ledger: ResearchLedger,
    record: Callable[..., int],
    corporate_actions: bool = False,
    action_provider: object | None = None,
    factory: Callable[..., AuditedBaseline] = AuditedBaseline,
) -> dict[str, Any]:
    dates = sorted({bar.date for bar in bars})
    if len(dates) < 40:
        raise ValueError("预设权重比较至少需要40个研究交易日作为首个训练窗口")
    end = dates[39]
    training = [bar for bar in bars if bar.date <= end]
    full = strategy_name == "baseline_full_audited"
    rows = []
    for profile, weights in weight_profiles(full).items():
        parameters = {
            "kind": "weight_comparison",
            "parent_experiment_id": parent_id,
            "profile": profile,
            "weights": weights,
            "training_start": str(dates[0]),
            "training_end": str(end),
            "training_days": 40,
            "trial_count": 1,
            "automatic_selection": False,
            "corporate_actions": corporate_actions,
            "data_role": "first_walk_forward_training_window",
        }
        experiment_id = record(
            strategy_name,
            dataset,
            parameters,
            "started",
            "预注册权重训练比较；单独扣研究预算，不使用后续样本",
        )
        try:
            strategy = factory(
                full=full,
                profile=profile,
                rebalance_every=rebalance_every,
                factor_definitions=definitions,
            )
            result = EventDrivenBacktester(
                initial_cash=initial_cash, cost_model=cost_model
            ).run(
                training,
                strategy,
                corporate_actions=corporate_actions,
                action_provider=action_provider,
            )
            report = build_performance_report(result)
            participation = strategy.factor_composite.audit.report()
            usable = any(
                item["used_observations"] > 0 for item in participation["factors"]
            )
            row = {
                "profile": profile,
                "weights": weights,
                "experiment_id": experiment_id,
                "status": "available" if usable else "insufficient_factor_coverage",
                "annualized_return": report.annualized_return if usable else None,
                "sharpe": report.sharpe if usable else None,
                "max_drawdown": report.max_drawdown if usable else None,
                "turnover": result.turnover if usable else None,
                "total_cost": result.total_cost,
                "fills": len(result.fills),
                "participation": participation,
            }
            ledger.update_experiment_parameters(
                experiment_id, {"training_weight_result": row}
            )
            ledger.update_result(
                experiment_id,
                status="completed" if usable else "needs_review",
                notes="仅40日训练窗口诊断，不构成样本外验证或自动选优",
            )
            rows.append(row)
        except Exception as error:
            ledger.update_result(
                experiment_id, status="failed", notes=type(error).__name__
            )
            raise
    return {
        "status": "available"
        if all(row["status"] == "available" for row in rows)
        else "insufficient_factor_coverage",
        "training_start": str(dates[0]),
        "training_end": str(end),
        "training_days": 40,
        "data_role": "first_walk_forward_training_window",
        "trial_count": 3,
        "budget_slots_consumed": 3,
        "automatic_selection": False,
        "rows": rows,
        "note": "三组权重事先固定，成本口径一致；只看首个40日训练窗口，样本短、不自动挑赢家；验证及密封区间不参与比较",
    }
