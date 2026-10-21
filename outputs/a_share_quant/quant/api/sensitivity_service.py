"""Research-only parameter sensitivity scan orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from quant.api.contracts import (
    FilterBacktestUniverseCallback,
    ResearchBarsCallback,
    ResolveBacktestUniverseCallback,
)

from ..backtest import CostModel
from ..data import TushareDailyAdapter
from ..data.research_eligibility import apply_universe_research_policy
from ..factors import FactorRegistryStore
from ..research import ResearchBudgetExceeded, ResearchLedger
from ..strategies import get_strategy_preset
from ..validation import run_parameter_sensitivity
from .parsing import bounded_int, optional_date


@dataclass(frozen=True)
class SensitivityDependencies:
    require_strategy: Callable[[str], None]
    resolve_universe: ResolveBacktestUniverseCallback
    filter_universe: FilterBacktestUniverseCallback
    make_adapter: Callable[..., TushareDailyAdapter]
    require_complete_universe: Callable[[Mapping[str, object]], None]
    sealed_scope: Callable[..., str]
    sealed_pipeline_key: Callable[[str], str]
    ensure_sealed_snapshot: Callable[..., dict[str, object]]
    research_bars: ResearchBarsCallback
    record_experiment: Callable[..., int]
    as_float: Callable[[object], float]
    scoped_dataset_version: Callable[[str, str], str]
    validation_gate: Callable[..., dict[str, object]]
    dataset_metadata: Callable[..., dict[str, object]]
    ledger: ResearchLedger
    factors: FactorRegistryStore
    token: str | None
    cache_dir: Any
    factor_pipeline_version: str
    sealed_test_days: int


def run_sensitivity_request(
    payload: dict[str, Any], deps: SensitivityDependencies
) -> dict[str, object]:
    strategy_name = str(payload.get("strategy", "baseline")).strip().lower()
    preset = get_strategy_preset(strategy_name)
    deps.require_strategy(strategy_name)
    if strategy_name == "lightgbm":
        raise ValueError("LightGBM 的模型和特征口径固定；不开放参数敏感性扫描")
    lookback = bounded_int(payload.get("lookback", preset.lookback), 5, 60)
    top_n = bounded_int(payload.get("top_n", preset.top_n), 1, 10)
    rebalance = str(payload.get("rebalance", "weekly")).strip().lower()
    if rebalance not in {"daily", "weekly", "monthly"}:
        raise ValueError("rebalance must be 'daily', 'weekly', or 'monthly'")
    start_date = optional_date(payload.get("start_date"))
    end_date = optional_date(payload.get("end_date"))
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    if not deps.ledger.can_record(strategy_name):
        raise ResearchBudgetExceeded(
            f"策略 {strategy_name!r} 的研究预算已用尽，请先归档当前研究周期"
        )
    universe = deps.resolve_universe(payload, start_date, end_date)
    symbols = universe["symbols"]
    adapter = deps.make_adapter(
        symbols=symbols,
        token=deps.token,
        adjust="",
        cache_dir=deps.cache_dir,
        fetch_limits=True,
        force_refresh=True,
    )
    raw_bars = adapter.daily_bars(
        start=universe["start_date"], end=universe["end_date"]
    )
    snapshot = adapter.snapshot()
    deps.require_complete_universe(snapshot.metadata)
    bars = deps.filter_universe(raw_bars, universe)
    bars, universe = apply_universe_research_policy(bars, payload, universe)
    if not bars:
        raise ValueError("没有可用于参数敏感性扫描的行情数据")
    scope = deps.sealed_scope(symbols, "", universe_version=universe["version"])
    sealed = deps.ensure_sealed_snapshot(
        strategy_name,
        deps.sealed_pipeline_key(scope),
        bars,
        snapshot,
        symbols=symbols,
        adjust="",
        sealed_days=deps.sealed_test_days,
        pit_data=universe.get("pit_data"),
        pit_metadata=universe.get("pit_metadata"),
    )
    if sealed.get("result") is not None:
        raise ResearchBudgetExceeded(
            "密封测试已完成，当前研究周期已锁定；请先归档研究周期后再调参"
        )
    research_bars = deps.research_bars(bars, sealed)
    if not research_bars:
        raise ValueError("密封区间之前没有可用于敏感性扫描的研究行情")
    rebalance_every = {"daily": 1, "weekly": 5, "monthly": 21}[rebalance]
    custom_factor_trials = deps.factors.custom_count(enabled_only=True)
    scan = run_parameter_sensitivity(
        research_bars,
        strategy_name=strategy_name,
        lookback=lookback,
        top_n=top_n,
        rebalance_every=rebalance_every,
        initial_cash=deps.as_float(payload.get("initial_cash", 1_000_000)),
        cost_model=CostModel(max_participation=0.10),
        delta=deps.as_float(payload.get("delta", 0.30)),
        factor_definitions=deps.factors.definitions(enabled_only=True),
        extra_trials=custom_factor_trials,
    )
    dataset = deps.scoped_dataset_version(snapshot.version, universe["version"])
    ledger_id = deps.record_experiment(
        strategy_name,
        dataset,
        {
            "kind": "sensitivity",
            "parameters": {
                "lookback": lookback,
                "top_n": top_n,
                "rebalance": rebalance,
                "factor_pipeline_version": deps.factor_pipeline_version,
                "sealed_scope": scope,
                "research_universe": universe["metadata"],
            },
            "trial_count": scan.trial_count,
            "multiple_testing_trials": scan.multiple_testing_trials,
            "registered_factor_trials": custom_factor_trials,
        },
        "passed" if scan.passed else "needs_review",
        scan.note,
    )
    latest_backtest = deps.ledger.latest(strategy_name, kind="backtest")
    validation_gate = deps.validation_gate(
        strategy_name,
        bool(latest_backtest and latest_backtest.get("status") == "passed"),
        sealed_pipeline_key=deps.sealed_pipeline_key(scope),
        sealed_scope=scope,
    )
    run_count = deps.ledger.count(strategy_name)
    return {
        "ok": True,
        "engine": "python",
        "strategy": strategy_name,
        "dataset": dataset,
        "dataset_metadata": deps.dataset_metadata(snapshot.metadata, universe),
        "sensitivity": scan.as_dict(),
        "factor_pipeline": {
            "version": deps.factor_pipeline_version,
            "factor_definitions": [
                item.as_dict() for item in deps.factors.definitions(enabled_only=True)
            ],
        },
        "research": {
            "experiment_id": ledger_id,
            "runs": run_count,
            "run_count": run_count,
            "budget": deps.ledger.budget,
            "remaining": max(0, deps.ledger.budget - run_count),
            "validation_gate": validation_gate,
        },
        "validation_gate": validation_gate,
    }
