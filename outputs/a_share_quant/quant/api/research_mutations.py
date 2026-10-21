"""Research-ledger and factor-registry write operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..factors import FactorRegistryStore
from ..research import ResearchBudgetExceeded, ResearchLedger
from .parsing import bounded_int


@dataclass(frozen=True)
class ResearchMutationDependencies:
    ledger: ResearchLedger
    factors: FactorRegistryStore
    factor_lock: Callable[[], dict[str, object]]
    record_experiment: Callable[[str, str, dict[str, object], str, str], int]
    factor_pipeline_version: str


def archive_research_cycle(
    payload: dict[str, object], deps: ResearchMutationDependencies
) -> tuple[int, dict[str, object]]:
    strategy_name = (
        str(payload.get("strategy", "baseline")).strip().lower() or "baseline"
    )
    result = deps.ledger.archive(
        strategy_name, reason=str(payload.get("reason", "")).strip()
    )
    return 200, {
        "ok": True,
        "archive": result,
        "ledger": deps.ledger.status(strategy_name),
    }


def register_factor(
    payload: dict[str, object], deps: ResearchMutationDependencies
) -> tuple[int, dict[str, object]]:
    factor_lock = deps.factor_lock()
    sealed = deps.ledger.has_active_sealed_result()
    if factor_lock["locked"] or sealed:
        return 409, {
            "ok": False,
            "code": "factor_registry_locked",
            "error": (
                "密封测试已完成，因子管线已冻结；如需修改请先归档当前研究周期"
                if sealed
                else factor_lock["reason"]
            ),
            "factor_registry": factor_lock,
        }
    if not deps.ledger.can_record("factor-lab"):
        raise ResearchBudgetExceeded("因子注册研究预算已用尽，请先归档当前研究周期")
    factor_type = str(payload.get("factor_type", ""))
    factor = deps.factors.register(
        str(payload.get("name", "")),
        factor_type,
        0
        if factor_type.strip().lower() == "fundamental"
        else bounded_int(payload.get("window", 20), 3, 120),
        str(payload.get("metric", "")),
    )
    try:
        ledger_id = deps.record_experiment(
            "factor-lab",
            "factor-registry",
            {
                "kind": "factor_registration",
                "factor_key": factor.key,
                "factor_type": factor.factor_type,
                "window": factor.window,
                "metric": factor.metric,
                "factor_pipeline_version": deps.factor_pipeline_version,
            },
            "registered",
            "用户注册因子计入多重检验试验总数；纸面验证期内会冻结因子集合。",
        )
    except Exception:
        deps.factors.unregister(factor.key)
        raise
    return 201, {
        "ok": True,
        "factor": factor.as_dict(),
        "experiment_id": ledger_id,
        "registered_factor_trials": deps.factors.custom_count(enabled_only=True),
        "definitions": [item.as_dict() for item in deps.factors.definitions()],
    }
