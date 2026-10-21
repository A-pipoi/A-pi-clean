"""Mining acceptance services with explicit runtime dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from quant.api.contracts import (
    AsFloatCallback,
    AsIntCallback,
    BarsFromPayloadCallback,
    BuildFactorPanelCallback,
    EvaluateExpressionCallback,
    EvaluateFactorPanelCallback,
    FactorMiningScopeCallback,
    FactorPanelCorrelationCallback,
    FactorRegistryStatusCallback,
    FactorValuesForDefinitionCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    PitObservationsForBarsCallback,
    RecordResearchExperimentCallback,
)
from quant.api.errors import ApiConflictError
from quant.api.mining_portfolio import (
    cycle_policy,
    refresh_portfolios,
    trial_population,
)
from quant.execution import PaperStore
from quant.factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from quant.factors.expression import Panel
from quant.mining import aggregate_factor_mining_runs
from quant.mining.arena import arena_candidate_filter
from quant.mining.portfolio_metrics import portfolio_gate, portfolio_report
from quant.research import ResearchBudgetExceeded, ResearchLedger
from quant.research.deep_formal import deep_candidate_panel, deep_candidates_ready
from quant.validation.statistics import benjamini_hochberg

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MiningAcceptanceDependencies:
    build_factor_panel: BuildFactorPanelCallback
    evaluate_expression: EvaluateExpressionCallback
    evaluate_factor_panel: EvaluateFactorPanelCallback
    ApiConflictError: type[ApiConflictError]
    FACTOR_STORE: FactorRegistryStore
    PAPER_STORE: PaperStore
    RESEARCH_LEDGER: ResearchLedger
    _as_float: AsFloatCallback
    _as_int: AsIntCallback
    _bars_from_payload: BarsFromPayloadCallback
    _factor_mining_scope: FactorMiningScopeCallback
    _factor_panel_correlation: FactorPanelCorrelationCallback
    _factor_registry_status: FactorRegistryStatusCallback
    _factor_values_for_definition: FactorValuesForDefinitionCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _pit_observations_for_bars: PitObservationsForBarsCallback
    _record_research_experiment: RecordResearchExperimentCallback


def _accept_factor_mining_run(
    run_id: str, *, deps: MiningAcceptanceDependencies
) -> dict[str, object]:
    run = deps.RESEARCH_LEDGER.factor_mining_run(run_id)
    if run is None:
        raise LookupError("因子挖掘批次不存在")
    preflight_runs = deps.RESEARCH_LEDGER.factor_mining_runs_for_sealed_test(
        deps._as_int(run.get("sealed_test_id"))
    )
    deep_candidates_ready(deps.RESEARCH_LEDGER, preflight_runs)
    policy = cycle_policy(
        deps.RESEARCH_LEDGER.path, deps._as_int(run.get("sealed_test_id"))
    )
    _, pipeline_key = deps._factor_mining_scope()
    sealed = deps.RESEARCH_LEDGER.get_sealed_test(
        "baseline", pipeline_key, include_bars=False
    )
    if sealed is None or deps._as_int(sealed.get("id") or 0) != deps._as_int(
        run.get("sealed_test_id")
    ):
        raise PermissionError("该挖掘批次对应的密封快照已归档或不再是当前周期快照")
    claim = deps.RESEARCH_LEDGER.claim_factor_mining_acceptance(run_id)
    if not claim.get("allowed"):
        raise deps.ApiConflictError(
            str(claim.get("detail") or "密封因子批次已经评估"),
            str(claim.get("code") or "sealed_factor_batch_already_opened"),
        )
    sealed_id = deps._as_int(sealed.get("id"))
    n_tested = 0
    candidate_rows: list[dict[str, object]] = []
    vectors: dict[str, Panel] = {}
    p_values: list[float] = []
    try:
        # Sealed price payloads are read only after the unique claim succeeds.
        sealed = deps.RESEARCH_LEDGER.get_sealed_test(
            "baseline", pipeline_key, include_bars=True
        )
        if sealed is None or deps._as_int(sealed.get("id")) != sealed_id:
            raise PermissionError("Claimed sealed snapshot changed")
        batch_runs = deps.RESEARCH_LEDGER.factor_mining_runs_for_sealed_test(sealed_id)
        acceptance_batch = aggregate_factor_mining_runs(batch_runs)
        candidates = arena_candidate_filter(
            deps.RESEARCH_LEDGER.path, sealed_id, acceptance_batch["candidates"]
        )
        n_tested = max(1, deps._as_int(acceptance_batch.get("n_tested")))
        sealed_bars = deps._bars_from_payload(sealed.get("bars", []))
        feature_observations, industry_observations, pit_quality, _ = (
            deps._pit_observations_for_bars(sealed, sealed_bars)
        )
        panel = deps.build_factor_panel(
            sealed_bars,
            data_version=str(sealed.get("data_version") or "unknown"),
            observations=feature_observations,
            industry_observations=industry_observations,
            pit_quality=pit_quality,
        )
        sealed_start = date.fromisoformat(str(sealed["sealed_start"]))
        sealed_end = date.fromisoformat(str(sealed["sealed_end"]))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            is_deep = bool(candidate.get("model_id"))
            if is_deep:
                values = deep_candidate_panel(deps.RESEARCH_LEDGER, panel, candidate)
            elif candidate.get("expression"):
                values = deps.evaluate_expression(
                    str(candidate["expression"]), panel
                ).values
            else:
                continue
            horizon_key = str(candidate.get("label_horizon") or 5)
            report = deps.evaluate_factor_panel(
                panel,
                values,
                start=sealed_start,
                end=sealed_end,
                n_tested=n_tested,
                horizons=(int(horizon_key),) if is_deep else (1, 5, 10, 20),
                quantiles=10,
            )
            report_horizons = deps._object_mapping(report.get("horizons"))
            training_report = refresh_portfolios(
                dict(candidate.get("evaluation") or {}),
                ledger=deps.RESEARCH_LEDGER,
                sealed_id=sealed_id,
            )
            training_portfolio = training_report.get("portfolio", {}).get(
                str(policy.quantiles)
            )
            gate = portfolio_gate(training_portfolio, policy)
            report["portfolio"] = {
                str(policy.quantiles): portfolio_report(
                    panel,
                    values,
                    n_tested=n_tested,
                    policy=policy,
                    trial_sharpes=trial_population(batch_runs, str(policy.quantiles)),
                    start=sealed_start,
                    end=sealed_end,
                )
            }
            horizon = deps._object_mapping(report_horizons.get(horizon_key))
            raw_p = horizon.get("raw_p_value")
            dsr_p = horizon.get("dsr_p_value")
            candidate_rows.append(
                {
                    "factor_key": candidate.get("factor_key"),
                    "factor_keys": candidate.get("factor_keys", []),
                    "source_run_ids": candidate.get("source_run_ids", []),
                    "expression": candidate.get("expression"),
                    "model_id": candidate.get("model_id"),
                    "version": candidate.get("version"),
                    "factor_type": candidate.get("factor_type", "expression"),
                    "label_horizon": int(horizon_key),
                    "training_fitness": candidate.get("fitness"),
                    "evaluation": report,
                    "cross_universe_reference": candidate.get(
                        "cross_universe_reference"
                    ),
                    "raw_p_value": raw_p,
                    "dsr_p_value": dsr_p,
                    "training_portfolio": training_portfolio,
                    "portfolio_gate": gate,
                }
            )
            identity = str(
                candidate.get("factor_key")
                or candidate.get("model_id")
                or candidate.get("expression")
            )
            vectors[identity] = values
            p_values.append(deps._as_float(dsr_p) if dsr_p is not None else 1.0)
        bh = benjamini_hochberg(p_values, q=0.05)
        adjusted = deps._object_list(bh.get("adjusted"))
        rejected = deps._object_list(bh.get("rejected"))
        active_vectors = []
        for definition in deps.FACTOR_STORE.definitions(enabled_only=True):
            panel_values = deps._factor_values_for_definition(
                definition, sealed_bars, panel
            )
            if panel_values is not None:
                active_vectors.append(panel_values)
        for stored_factor in deps.FACTOR_STORE.expression_library():
            if stored_factor.get("status") != "accepted":
                continue
            try:
                active_vectors.append(
                    deps.evaluate_expression(
                        str(stored_factor["expression"]), panel
                    ).values
                )
            except (TypeError, ValueError):
                continue
        discoveries: list[Panel] = []
        for index, row in enumerate(candidate_rows):
            vector = vectors[
                str(row.get("factor_key") or row.get("model_id") or row["expression"])
            ]
            correlations = [
                abs(value)
                for other in active_vectors + discoveries
                if (
                    value := deps._factor_panel_correlation(
                        vector, other, panel, sealed_start, sealed_end
                    )
                )
                is not None
            ]
            maximum_correlation = max(correlations, default=0.0)
            is_duplicate = maximum_correlation > 0.7
            corrected = (
                bool(rejected[index]) and deps._as_float(adjusted[index]) <= 0.05
            )
            row_gate = deps._object_mapping(row["portfolio_gate"])
            portfolio_passed = row_gate["passed"]
            joint_passed = corrected and (policy.mode == "ic_only" or portfolio_passed)
            status = (
                "duplicate-discovery"
                if is_duplicate
                else "accepted"
                if joint_passed
                else "overfitting-suspect"
            )
            row.update(
                {
                    "max_abs_correlation_existing": maximum_correlation,
                    "duplicate_discovery": is_duplicate,
                    "bh_adjusted_p_value": adjusted[index],
                    "corrected_significant": corrected,
                    "status": status,
                    "ic_passed": corrected,
                    "portfolio_passed": portfolio_passed,
                    "metric_conflict": corrected != portfolio_passed,
                    "decision_note": "IC-only legacy switch"
                    if policy.mode == "ic_only"
                    else "Requires IC significance AND training DSR/net Sharpe gates",
                }
            )
            if status == "accepted":
                discoveries.append(vector)
            if not row.get("model_id"):
                keys = deps._object_list(row.get("factor_keys"))
                for key in keys or [row.get("factor_key")]:
                    if key:
                        deps.FACTOR_STORE.update_mined_candidate(
                            str(key), status=status, metadata=row
                        )
        passed_count = sum(item.get("status") == "accepted" for item in candidate_rows)
        acceptance_result: dict[str, object] = {
            "passed": passed_count > 0,
            "status": "passed" if passed_count else "needs_review",
            "run_id": run_id,
            "sealed_test_id": sealed_id,
            "sealed_start": str(sealed["sealed_start"]),
            "sealed_end": str(sealed["sealed_end"]),
            "data_version": str(sealed.get("data_version") or "unknown"),
            "N_tested": n_tested,
            "included_runs": [
                {"run_id": item["run_id"], "n_tested": item["n_tested"]}
                for item in batch_runs
            ],
            "evaluated_candidates": len(candidate_rows),
            "effective_discoveries": passed_count,
            "correction": {
                "method": (
                    "HAC raw p + DSR(sealed-window cumulative N_tested) "
                    "+ Benjamini-Hochberg"
                ),
                "q": 0.05,
                "adjusted_p_values": adjusted,
            },
            "candidates": candidate_rows,
            "portfolio_policy": gate["thresholds"] if candidate_rows else {},
            "acceptance_mode": policy.mode,
            "note": (
                "该验收只授权研究周期内的因子研究；不会自动改变策略或纸面交易配置。"
                "通过的因子须在归档当前周期后显式提升，并重新完成策略验证。"
            ),
        }
        acceptance = deps.RESEARCH_LEDGER.finish_factor_mining_acceptance(
            run_id, status=str(acceptance_result["status"]), result=acceptance_result
        )
        return acceptance
    except Exception as error:
        try:
            deps.RESEARCH_LEDGER.finish_factor_mining_acceptance(
                run_id,
                status="failed",
                result={"error": str(error)[:500], "N_tested": n_tested},
            )
        except Exception:
            LOGGER.exception(
                "Unable to persist failed factor-mining acceptance: %s", run_id
            )
        raise


def _promote_mined_factor(
    key: str, *, deps: MiningAcceptanceDependencies
) -> dict[str, object]:
    if not key:
        raise ValueError("factor_key is required")
    factor = next(
        (
            item
            for item in deps.FACTOR_STORE.expression_library()
            if item.get("key") == key
        ),
        None,
    )
    if factor is None:
        raise LookupError("表达式因子不存在")
    if factor.get("status") != "accepted":
        raise PermissionError("只有 sealed 验收通过且非重复发现的因子可以提升")
    training_metadata = deps._object_mapping(
        deps._object_mapping(factor.get("metadata")).get("training")
    )
    dependencies = {
        item
        for item in deps._object_list(training_metadata.get("dependencies"))
        if isinstance(item, str)
    }
    pit_dependencies = dependencies & {
        "pe",
        "pb",
        "roe",
        "netprofit_yoy",
        "tr_yoy",
        "debt_to_assets",
        "market_cap",
        "float_market_cap",
        "industry",
    }
    if pit_dependencies:
        raise PermissionError(
            "该因子依赖 PIT 字段（"
            + ", ".join(sorted(pit_dependencies))
            + "）；当前 PIT 面板只接入研究/密封验收，尚未接入策略与纸面运行时，"
            "已阻止提升以避免无数据计算。"
        )
    factor_lock = deps._factor_registry_status(
        deps.PAPER_STORE.cycle_summary().get("equity_observations", 0)
    )
    if factor_lock.get("locked") or deps.RESEARCH_LEDGER.has_active_sealed_result():
        raise PermissionError(
            "当前策略/因子管线仍处于验证锁定状态；请先归档当前研究周期"
        )
    if not deps.RESEARCH_LEDGER.can_record("factor-lab"):
        raise ResearchBudgetExceeded("因子研究预算已用尽，请先归档当前研究周期")
    if not deps.FACTOR_STORE.promote_expression(key):
        raise PermissionError("表达式因子状态已变化，未能提升")
    try:
        experiment_id = deps._record_research_experiment(
            "factor-lab",
            "factor-registry",
            {
                "kind": "expression_factor_promotion",
                "factor_key": key,
                "expression": factor["expression"],
                "data_version": factor["data_version"],
                "code_version": factor["code_version"],
                "sealed_test_id": factor["sealed_test_id"],
                "factor_pipeline_version": FACTOR_PIPELINE_VERSION,
            },
            "registered",
            "sealed 验收通过后经用户显式提升；启用前已归档上一研究周期。",
        )
    except Exception:
        deps.FACTOR_STORE.disable_expression(key)
        raise
    return {
        "factor_key": key,
        "experiment_id": experiment_id,
        "enabled": True,
        "note": "因子已加入当前研究管线；纸面交易仍需新的策略验证与 sealed 策略验收。",
    }
