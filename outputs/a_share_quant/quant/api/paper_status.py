"""Paper account, scheduler and governance status payloads."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

from ..execution import PaperStore


@dataclass(frozen=True)
class PaperStatusDependencies:
    store: PaperStore
    scheduler: Callable[[], Any]
    timezone: tzinfo
    logger: logging.Logger
    object_mapping: Callable[[object], dict[str, Any]]
    optional_date: Callable[[object], date | None]
    enrich_state: Callable[[dict[str, Any]], dict[str, Any]]
    risk_limits_payload: Callable[[], dict[str, Any]]
    factor_registry_status: Callable[[object], dict[str, Any]]
    paper_sealed_gate_status: Callable[..., dict[str, Any]]
    sealed_public_payload: Callable[[Mapping[str, object] | None], dict[str, object]]
    ledger_status: Callable[[str], dict[str, Any]]


def paper_scheduler_payload(deps: PaperStatusDependencies) -> dict[str, object]:
    scheduler = deps.scheduler()
    return {
        "ok": True,
        "scheduler": scheduler.status()
        if scheduler is not None
        else {"enabled": False, "running": False},
    }


def paper_state_payload(deps: PaperStatusDependencies) -> dict[str, object]:
    scheduler = deps.scheduler()
    scheduler_status = deps.object_mapping(
        scheduler.status()
        if scheduler is not None
        else {"enabled": False, "running": False}
    )
    last_result = deps.object_mapping(scheduler_status.get("last_result"))
    paper_state = deps.enrich_state(deps.store.state())
    if last_result.get("data_quality") is not None:
        paper_state["data_quality"] = last_result.get("data_quality")
    if last_result.get("data_freshness") is not None:
        paper_state["data_freshness"] = last_result.get("data_freshness")
    paper_state["pre_trade_risk"] = last_result.get("pre_trade_risk")
    paper_state["paper_universe"] = last_result.get(
        "paper_universe"
    ) or scheduler_status.get("universe")
    paper_state["paper_trading_allowed"] = bool(
        last_result.get("paper_trading_allowed", False)
    )
    return {
        "ok": True,
        "paper": paper_state,
        "risk_limits": deps.risk_limits_payload(),
        "scheduler": scheduler_status,
    }


def paper_sop_payload(deps: PaperStatusDependencies) -> dict[str, object]:
    summary = deps.store.cycle_summary()
    first_date = deps.optional_date(summary.get("first_cycle_date"))
    last_date = deps.optional_date(summary.get("last_cycle_date"))
    covered_days = (last_date - first_date).days + 1 if first_date and last_date else 0
    effective_samples = int(summary.get("equity_observations") or 0)
    scheduler = deps.scheduler()
    scheduler_status = deps.object_mapping(
        scheduler.status() if scheduler is not None else {}
    )
    today = datetime.now(deps.timezone).date()
    if (
        scheduler is not None
        and scheduler_status.get("universe_index")
        and not scheduler_status.get("universe")
        and today.weekday() < 5
    ):
        try:
            scheduler.resolve_universe_for_status(today)
            scheduler_status = scheduler.status()
        except Exception as error:
            deps.logger.exception(
                "could not resolve PIT universe for paper status: %s",
                type(error).__name__,
                exc_info=None,
            )
    last_result = scheduler_status.get("last_result")
    last_result = last_result if isinstance(last_result, dict) else {}
    freshness = last_result.get("data_freshness")
    freshness = freshness if isinstance(freshness, dict) else {}
    data_quality = bool(last_result.get("cycle_date")) and not last_result.get("skipped") and freshness.get("fresh") is True
    stage = (
        "D"
        if covered_days >= 90 and effective_samples >= 40
        else "C"
        if effective_samples >= 10
        else "A/B"
    )
    factor_registry = deps.factor_registry_status(effective_samples)
    paper_gate = deps.paper_sealed_gate_status(scheduler_status)
    sealed_value = paper_gate.get("sealed_test")
    sealed = dict(sealed_value) if isinstance(sealed_value, dict) else None
    sealed_passed = bool(paper_gate.get("passed"))
    checklist = [
        {
            "key": "real_data",
            "label": "Tushare真实行情与数据版本",
            "status": "通过" if data_quality else "待运行",
        },
        {
            "key": "sealed_test",
            "label": "当前纸面策略与股票池密封验收",
            "status": "通过" if sealed_passed else "不通过",
            "detail": paper_gate.get("reason"),
        },
        {
            "key": "cycle_ledger",
            "label": "纸面循环有持久化记录",
            "status": "通过" if summary["cycles"] else "不通过",
        },
        {
            "key": "order_ledger",
            "label": "委托与成交可追溯",
            "status": "通过" if summary["orders"] else "待运行",
        },
        {
            "key": "three_months",
            "label": "有效运行至少3个月",
            "status": "通过" if covered_days >= 90 else "不通过",
        },
        {
            "key": "eight_weeks",
            "label": "至少8周有效样本",
            "status": "通过" if effective_samples >= 40 else "不通过",
        },
    ]
    return {
        "ok": True,
        "stage": stage,
        "summary": {**summary, "covered_calendar_days": covered_days},
        "recent_cycles": deps.store.recent_cycles(10),
        "checklist": checklist,
        "ready_for_stage_d": all(
            deps.object_mapping(item).get("status") == "通过" for item in checklist
        ),
        "factor_registry": factor_registry,
        "sealed_test": deps.sealed_public_payload(sealed),
        "paper_sealed_gate": {
            "passed": sealed_passed,
            "status": paper_gate.get("status"),
            "reason": paper_gate.get("reason"),
            "strategy": paper_gate.get("strategy"),
            "symbol_count": paper_gate.get("symbol_count"),
            "universe": paper_gate.get("universe"),
        },
        "research_budget": deps.ledger_status("baseline"),
        "note": "纸面交易只用于验证，不将模拟收益回流训练，也不代表实盘收益保证。",
    }
