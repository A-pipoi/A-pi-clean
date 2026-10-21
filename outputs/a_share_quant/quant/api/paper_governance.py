"""Paper governance services with explicit runtime dependencies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from quant.api.contracts import (
    AsIntCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    PaperSealedGateStatusCallback,
    SealedGatePayloadCallback,
    SealedPipelineKeyCallback,
    SealedPublicPayloadCallback,
    SealedResultPassedCallback,
    SealedScopeCallback,
)
from quant.data import DEFAULT_PAPER_UNIVERSE_INDEX, DEFAULT_SYMBOLS
from quant.execution import PaperScheduler
from quant.factors import FACTOR_PIPELINE_VERSION
from quant.research import ResearchLedger


@dataclass(frozen=True)
class PaperGovernanceDependencies:
    PAPER_SCHEDULER: PaperScheduler | None
    RESEARCH_LEDGER: ResearchLedger
    _as_int: AsIntCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _paper_sealed_gate_status: PaperSealedGateStatusCallback
    _sealed_gate_payload: SealedGatePayloadCallback
    _sealed_pipeline_key: SealedPipelineKeyCallback
    _sealed_public_payload: SealedPublicPayloadCallback
    _sealed_result_passed: SealedResultPassedCallback
    _sealed_scope: SealedScopeCallback


def _sealed_gate_payload(
    strategy_name: str,
    symbols: list[str] | None = None,
    adjust: str = "",
    *,
    deps: PaperGovernanceDependencies,
) -> dict[str, object]:
    universe = list(symbols or DEFAULT_SYMBOLS)
    sealed = deps.RESEARCH_LEDGER.get_sealed_test(
        strategy_name,
        deps._sealed_pipeline_key(deps._sealed_scope(universe, adjust)),
        include_bars=False,
    )
    passed = deps._sealed_result_passed(sealed)
    if passed:
        reason = None
    elif sealed is None:
        reason = f"当前策略与 {len(universe)} 只股票的密封快照尚未建立"
    elif not isinstance(sealed.get("result"), dict):
        reason = "当前密封快照尚未执行唯一一次验收"
    elif (
        sealed.get("status") != "passed"
        or deps._object_mapping(sealed.get("result")).get("passed") is not True
    ):
        reason = "当前策略与股票池的密封验收未通过"
    else:
        reason = "密封验收使用的因子定义与当前版本不一致"
    return {
        "passed": passed,
        "status": sealed.get("status") if sealed else "not_created",
        "sealed_test": deps._sealed_public_payload(sealed),
        "reason": reason,
        "strategy": strategy_name,
        "symbol_count": len(universe),
    }


def _paper_sealed_gate_status(
    scheduler_status: dict[str, object] | None = None,
    *,
    deps: PaperGovernanceDependencies,
) -> dict[str, object]:
    """Report the sealed gate for the actual paper strategy and resolved universe.

    Do not let the SOP fall back to the legacy 24-symbol research pool when the
    active paper scheduler is configured for PIT index constituents. Before a
    PIT snapshot has been resolved, report the gate as unknown/blocked instead
    of implying that the unrelated fixed-pool acceptance covers it.
    """
    status = (
        deps._object_mapping(scheduler_status)
        if scheduler_status is not None
        else deps.PAPER_SCHEDULER.status()
        if deps.PAPER_SCHEDULER is not None
        else {}
    )
    strategy = str(status.get("strategy") or "baseline").strip().lower()
    symbols = [
        str(item).strip()
        for item in deps._object_list(status.get("symbols"))
        if str(item).strip()
    ]
    universe_index = str(status.get("universe_index") or "").strip().upper()
    universe_value = status.get("universe")
    universe = (
        deps._object_mapping(universe_value)
        if isinstance(universe_value, Mapping)
        else None
    )
    if universe_index and (
        not universe or universe.get("mode") != "pit_index_members" or (not symbols)
    ):
        return {
            "passed": False,
            "status": "unresolved_universe",
            "sealed_test": None,
            "reason": f"等待解析 {universe_index} 的 PIT 成分股；尚不能确认纸面股票池的密封验收",
            "strategy": strategy,
            "symbol_count": None,
            "universe": {"index_code": universe_index, "mode": "pit_index_members"},
        }
    if universe is None:
        universe = {}
    if universe_index:
        sealed = deps.RESEARCH_LEDGER.latest_sealed_test_for_universe(
            strategy,
            index_code=universe_index,
            protocol="pit_historical_index_v1",
            include_bars=False,
        )
        if sealed is None:
            return {
                "passed": False,
                "status": "not_created",
                "sealed_test": None,
                "reason": f"{universe_index} 历史成分 PIT 密封验收尚未建立",
                "strategy": strategy,
                "symbol_count": len(symbols),
                "universe": {
                    key: value for key, value in universe.items() if key != "symbols"
                },
            }
        research_universe = deps._object_mapping(
            deps._object_mapping(sealed.get("metadata")).get("research_universe")
        )
        sealed_parameters = deps._object_mapping(sealed.get("parameters"))
        membership_date = str(universe.get("membership_date") or "")[:10]
        sealed_end = str(sealed.get("sealed_end") or "")[:10]
        paper_rebalance = str(status.get("rebalance") or "weekly").strip().lower()
        matches_index = (
            isinstance(research_universe, dict)
            and str(research_universe.get("index_code") or "").upper() == universe_index
            and bool(research_universe.get("pit_membership"))
        )
        covers_membership = bool(
            membership_date and sealed_end and (sealed_end >= membership_date)
        )
        membership_evidence: dict[str, object] = {
            "mode": "within_sealed_window" if covers_membership else "unverified",
            "sealed_end": sealed_end,
            "current_membership_date": membership_date,
        }
        # A later timestamp alone is not a new research universe. Compare the
        # immutable accepted PIT snapshot, never today's metadata symbol union.
        if not covers_membership and membership_date and sealed_end and matches_index:
            full_seal = deps.RESEARCH_LEDGER.latest_sealed_test_for_universe(
                strategy, index_code=universe_index,
                protocol="pit_historical_index_v1", include_bars=True,
            )
            pit = deps._object_mapping((full_seal or {}).get("pit_data"))
            snapshots = [
                deps._object_mapping(item)
                for item in deps._object_list(pit.get("snapshots"))
                if isinstance(item, Mapping)
            ]
            eligible = [
                item for item in snapshots
                if str(item.get("trade_date") or "")[:10]
                and str(item.get("trade_date"))[:10] <= sealed_end
            ]
            terminal = max(eligible, key=lambda item: str(item["trade_date"]), default={})
            members = deps._object_list(terminal.get("members"))
            accepted_symbols = [
                str(item.get("ts_code") or "").strip().upper()
                for item in members if isinstance(item, Mapping)
            ]
            current_symbols = [item.upper() for item in symbols]
            identical = bool(
                full_seal and full_seal.get("id") == sealed.get("id")
                and str(pit.get("index_code") or "").upper() == universe_index
                and universe.get("stale") is not True
                and accepted_symbols and all(accepted_symbols)
                and len(accepted_symbols) == len(members)
                and len(set(accepted_symbols)) == len(accepted_symbols)
                and len(set(current_symbols)) == len(current_symbols)
                and set(accepted_symbols) == set(current_symbols)
                and str(universe.get("index_code") or "").upper() == universe_index
                and deps._as_int(universe.get("count")) == len(current_symbols)
            )
            covers_membership = identical
            membership_evidence.update({
                "mode": "identical_constituents" if identical else "changed_or_missing_evidence",
                "accepted_snapshot_date": terminal.get("trade_date"),
                "accepted_count": len(accepted_symbols),
                "current_count": len(current_symbols),
                "added": sorted(set(current_symbols) - set(accepted_symbols)),
                "removed": sorted(set(accepted_symbols) - set(current_symbols)),
                "sealed_test_id": sealed.get("id"),
            })
        matches_rebalance = (
            str(sealed_parameters.get("rebalance") or "").strip().lower()
            == paper_rebalance
        )
        passed = (
            matches_index
            and covers_membership
            and matches_rebalance
            and deps._sealed_result_passed(sealed)
        )
        reason = (
            None
            if passed
            else "密封快照未使用当前指数的历史成分 PIT 数据"
            if not matches_index
            else f"密封窗口截止 {sealed_end or '未知'}，未覆盖当前成分快照 {membership_date or '未知'}"
            if not covers_membership
            else f"密封验收调仓频率与纸面策略不一致（{sealed_parameters.get('rebalance') or '未知'} / {paper_rebalance}）"
            if not matches_rebalance
            else "当前 PIT 密封验收未通过或因子定义已变化"
        )
        return {
            "passed": passed,
            "status": sealed.get("status"),
            "sealed_test": deps._sealed_public_payload(sealed),
            "reason": reason,
            "strategy": strategy,
            "symbol_count": len(symbols),
            "membership_evidence": membership_evidence,
            "universe": {
                key: value for key, value in universe.items() if key != "symbols"
            },
        }
    if not symbols:
        symbols = list(DEFAULT_SYMBOLS)
    gate = deps._sealed_gate_payload(strategy, symbols, "")
    return {
        **gate,
        "universe": {
            key: value
            for key, value in (universe or {"mode": "explicit_symbols"}).items()
            if key != "symbols"
        },
    }


def _paper_cycle_sealed_gate_payload(
    strategy_name: str,
    symbols: list[str],
    adjust: str,
    universe: dict[str, object],
    rebalance: str,
    *,
    deps: PaperGovernanceDependencies,
) -> dict[str, object]:
    """Bind a manual paper cycle to the resolved universe, never a similar pool."""
    if DEFAULT_PAPER_UNIVERSE_INDEX:
        if (
            universe.get("mode") != "pit_index_members"
            or str(universe.get("index_code") or "").strip().upper()
            != DEFAULT_PAPER_UNIVERSE_INDEX
        ):
            return {
                "passed": False,
                "status": "unresolved_universe",
                "sealed_test": None,
                "reason": f"纸面交易要求先解析 {DEFAULT_PAPER_UNIVERSE_INDEX} 的 PIT 成分股，拒绝使用固定或手选股票池",
                "strategy": strategy_name,
                "symbol_count": len(symbols),
                "universe": {
                    "mode": universe.get("mode"),
                    "index_code": DEFAULT_PAPER_UNIVERSE_INDEX,
                },
            }
        return deps._paper_sealed_gate_status(
            {
                "strategy": strategy_name,
                "symbols": symbols,
                "universe_index": DEFAULT_PAPER_UNIVERSE_INDEX,
                "universe": universe,
                "rebalance": rebalance,
            }
        )
    return deps._sealed_gate_payload(strategy_name, symbols, adjust)


def _validation_gate(
    strategy_version: str,
    strategy_validation_passed: bool,
    *,
    sealed_pipeline_key: str | None = None,
    sealed_scope: str | None = None,
    deps: PaperGovernanceDependencies,
) -> dict[str, object]:
    """Expose whether a result is safe to present as a research conclusion."""
    latest_sensitivity = deps.RESEARCH_LEDGER.latest(
        strategy_version, kind="sensitivity"
    )
    latest_record_parameters = deps._object_mapping(
        (latest_sensitivity or {}).get("parameters")
    )
    latest_parameters = (
        deps._object_mapping(latest_record_parameters.get("parameters"))
        or latest_record_parameters
    )
    pipeline_match = (
        latest_parameters.get("factor_pipeline_version") == FACTOR_PIPELINE_VERSION
    )
    sensitivity_scope_match = (
        sealed_scope is None or latest_parameters.get("sealed_scope") == sealed_scope
    )
    sensitivity_passed = bool(
        latest_sensitivity
        and latest_sensitivity.get("status") == "passed"
        and pipeline_match
        and sensitivity_scope_match
    )
    sealed = deps.RESEARCH_LEDGER.get_sealed_test(
        strategy_version,
        sealed_pipeline_key
        or deps._sealed_pipeline_key(deps._sealed_scope(list(DEFAULT_SYMBOLS), "")),
        include_bars=False,
    )
    sealed_passed = deps._sealed_result_passed(sealed)
    if not strategy_validation_passed:
        status = "需要复核"
    elif latest_sensitivity is None:
        status = "待敏感性检验"
    elif not sensitivity_passed:
        status = "需要复核"
    elif not sealed:
        status = "待密封测试"
    elif not sealed_passed:
        status = "密封测试未通过或因子已漂移"
    else:
        status = "验证通过"
    return {
        "strategy_validation_passed": bool(strategy_validation_passed),
        "sensitivity_passed": sensitivity_passed,
        "factor_pipeline_version": FACTOR_PIPELINE_VERSION,
        "sensitivity_pipeline_match": pipeline_match,
        "sensitivity_scope_match": sensitivity_scope_match,
        "sealed_test_passed": sealed_passed,
        "sealed_test": deps._sealed_public_payload(sealed),
        "conclusion_ready": bool(
            strategy_validation_passed and sensitivity_passed and sealed_passed
        ),
        "status": status,
        "latest_sensitivity": latest_sensitivity,
    }


def _factor_registry_status(
    effective_samples: object, *, deps: PaperGovernanceDependencies
) -> dict[str, object]:
    """Expose the SOP lock that prevents factor drift during validation."""
    try:
        samples = max(0, deps._as_int(effective_samples or 0))
    except ValueError:
        samples = 0
    locked = samples >= 10
    return {
        "locked": locked,
        "stage": "C" if locked else "A/B",
        "effective_samples": samples,
        "reason": "纸面验证已进入 SOP 阶段 C，因子集合已冻结；如需修改请先归档并重新开始验证周期。"
        if locked
        else "纸面验证样本不足 8 周，因子集合当前可注册。",
    }
