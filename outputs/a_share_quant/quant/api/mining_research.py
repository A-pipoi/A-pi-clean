"""Mining research services with explicit runtime dependencies."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import math
import os
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import cast

from quant.api.contracts import (
    AsIntCallback,
    BoundedIntCallback,
    FactorMiningCrossUniverseReferenceCallback,
    FactorMiningDatasetCallback,
    FactorMiningScopeCallback,
    FactorRegistryStatusCallback,
    FactorValuesForDefinitionCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    PitObservationsForBarsCallback,
    SealedPipelineKeyCallback,
    SealedPublicPayloadCallback,
    ValidateFactorMiningUniverseRequestCallback,
)
from quant.data import (
    FACTOR_MINING_MIN_LISTING_DAYS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    FACTOR_MINING_UNIVERSE_NAME,
    INDEX_UNIVERSE_NAMES,
)
from quant.execution import PaperStore
from quant.factors import FactorRegistryStore
from quant.factors.analysis import evaluate_factor_panel
from quant.factors.expression import (
    EXPRESSION_TEMPLATES,
    Panel,
    build_factor_panel,
    evaluate_expression,
)
from quant.mining import run_factor_mining
from quant.mining.arena import ARENA_SESSION
from quant.mining.batch import aggregate_factor_mining_runs
from quant.mining.census import template_census
from quant.mining.diagnostics import (
    ic_half_life,
    registry_correlation_matrix,
    registry_diagnostics,
)
from quant.mining.genetic import canonical_expression_hash
from quant.mining.layered import layered_diagnostics
from quant.mining.search_options import validate_search_options
from quant.mining.universe import FACTOR_MINING_UNIVERSE_VERSION
from quant.notifications import queue_decay_alerts
from quant.research import ResearchLedger

from .mining_composition import compose_request
from .mining_evolution import evolution_request
from .mining_jobs import MINING_JOB, report_mining_progress
from .mining_portfolio import training_portfolios

LOGGER = logging.getLogger(__name__)
MINING_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mining_request_id", default=None
)


@dataclass(frozen=True)
class MiningResearchDependencies:
    FACTOR_STORE: FactorRegistryStore
    PAPER_STORE: PaperStore
    RESEARCH_LEDGER: ResearchLedger
    _as_int: AsIntCallback
    _bounded_int: BoundedIntCallback
    _factor_mining_cross_universe_reference: FactorMiningCrossUniverseReferenceCallback
    _factor_mining_dataset: FactorMiningDatasetCallback
    _factor_mining_scope: FactorMiningScopeCallback
    _factor_registry_status: FactorRegistryStatusCallback
    _factor_values_for_definition: FactorValuesForDefinitionCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _pit_observations_for_bars: PitObservationsForBarsCallback
    _sealed_pipeline_key: SealedPipelineKeyCallback
    _sealed_public_payload: SealedPublicPayloadCallback
    _validate_factor_mining_universe_request: (
        ValidateFactorMiningUniverseRequestCallback
    )


def _factor_mining_scope(*, deps: MiningResearchDependencies) -> tuple[str, str]:
    canonical = json.dumps(
        {
            "purpose": "factor-mining",
            "universe_index": FACTOR_MINING_UNIVERSE_INDEX_CODE,
            "universe_version": FACTOR_MINING_UNIVERSE_VERSION,
            "min_listing_days": FACTOR_MINING_MIN_LISTING_DAYS,
            "adjust": "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    scope = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return (
        scope,
        f"{deps._sealed_pipeline_key(scope)}:pit-v3:{FACTOR_MINING_UNIVERSE_VERSION}",
    )


def _factor_mining_status(*, deps: MiningResearchDependencies) -> dict[str, object]:
    _, pipeline_key = deps._factor_mining_scope()
    sealed = deps.RESEARCH_LEDGER.get_sealed_test(
        "baseline", pipeline_key, include_bars=False
    )
    acceptance = (
        deps.RESEARCH_LEDGER.factor_mining_acceptance(deps._as_int(sealed.get("id")))
        if sealed
        else None
    )
    sealed_metadata = deps._object_mapping(sealed.get("metadata")) if sealed else {}
    universe_metadata = deps._object_mapping(sealed_metadata.get("research_universe"))
    runs = deps.RESEARCH_LEDGER.factor_mining_runs(limit=20)
    sealed_tested_total = (
        deps.RESEARCH_LEDGER.factor_mining_trial_total(deps._as_int(sealed.get("id")))
        if sealed
        else 0
    )
    factor_lock = deps._factor_registry_status(
        deps.PAPER_STORE.cycle_summary().get("equity_observations", 0)
    )
    promotion_block_reason = (
        "当前策略或因子配置已被 sealed 验收冻结，请先归档当前研究周期"
        if factor_lock.get("locked") or deps.RESEARCH_LEDGER.has_active_sealed_result()
        else ""
    )
    return {
        "sealed_test": deps._sealed_public_payload(sealed),
        "sealed_factor_acceptance": acceptance,
        "acceptance_available": bool(
            sealed and (not acceptance) and (sealed_tested_total > 0)
        ),
        "runs": runs,
        "factor_library": deps.FACTOR_STORE.expression_library(),
        "composition_factors": [
            {"key": definition.key, "name": definition.name}
            for definition in deps.FACTOR_STORE.definitions(enabled_only=True)
            if definition.expression
            or definition.factor_type
            in {"momentum", "reversal", "volatility", "fundamental"}
        ],
        "expression_templates": list(EXPRESSION_TEMPLATES),
        "tested_individuals_total": deps.RESEARCH_LEDGER.factor_mining_trial_total(),
        "sealed_tested_individuals": sealed_tested_total,
        "factor_registry_lock": factor_lock,
        "promotion_block_reason": promotion_block_reason,
        "data_source_requirement": "使用 Tushare 封存日线与历史 PIT 数据；公告/收盘字段从下一交易日生效",
        "pit_quality": sealed_metadata.get("pit_quality")
        or {"size": "not_materialized", "industry": "unverified"},
        "pit_sources": sealed_metadata.get("pit_sources") or {},
        "research_universe": {
            "index_code": FACTOR_MINING_UNIVERSE_INDEX_CODE,
            "index_name": FACTOR_MINING_UNIVERSE_NAME,
            "selection": "pre_registered_historical_index_members",
            "snapshot_cadence": "monthly Tushare index_weight snapshots",
            "min_listing_days": FACTOR_MINING_MIN_LISTING_DAYS,
            "snapshot_start": universe_metadata.get("snapshot_start"),
            "snapshot_end": universe_metadata.get("snapshot_end"),
            "snapshot_count": universe_metadata.get("snapshot_count"),
            "member_data_version": universe_metadata.get("member_data_version"),
            "candidate_symbol_count": universe_metadata.get("candidate_symbol_count"),
            "sample_symbol_count": universe_metadata.get("sample_symbol_count"),
            "comparison_index_code": "000905.SH",
            "comparison_index_name": INDEX_UNIVERSE_NAMES.get("000905.SH", "中证500"),
            "comparison_role": "report_only_not_for_selection_or_acceptance",
            "locked": True,
            "status": "sealed_snapshot_frozen"
            if universe_metadata
            else "will_load_on_first_snapshot",
        },
    }


def _run_factor_mining_request(
    payload: dict[str, object], *, deps: MiningResearchDependencies
) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    token = MINING_REQUEST_ID.set(request_id)
    try:
        return _run_factor_mining_request_impl(payload, deps=deps)
    except Exception as error:
        LOGGER.exception("Counted mining request failed during postprocessing")
        run = deps.RESEARCH_LEDGER.factor_mining_run(request_id)
        if run and run["status"] == "evaluation_in_progress":
            result = dict(cast(Mapping[str, object], run["result"]))
            result.update(error=type(error).__name__, candidates=[])
            deps.RESEARCH_LEDGER.update_factor_mining_run(
                request_id, result, status="evaluation_failed"
            )
        raise
    finally:
        MINING_REQUEST_ID.reset(token)


def _run_factor_mining_request_impl(
    payload: dict[str, object], *, deps: MiningResearchDependencies
) -> dict[str, object]:
    deps._validate_factor_mining_universe_request(payload)
    seeds = validate_search_options(payload)
    if seeds:
        # Seal once before parallel readers start; no child may create its own snapshot.
        sealed, _, _ = deps._factor_mining_dataset()
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mining-seed"
        ) as pool:
            futures = []
            for seed in seeds:
                seed_context = contextvars.copy_context()
                child = {key: value for key, value in payload.items() if key != "seeds"}
                child["seed"] = seed
                futures.append(
                    pool.submit(
                        seed_context.run, _run_factor_mining_request, child, deps=deps
                    )
                )
            runs = [future.result() for future in futures]
        combined = aggregate_factor_mining_runs(runs)
        result: dict[str, object] = {
            "candidates": [],
            "multi_seed": {
                **combined,
                "seeds": seeds,
                "run_ids": [run["run_id"] for run in runs],
                "sealed_dates_used": False,
            },
        }
        report_id = uuid.uuid4().hex
        deps.RESEARCH_LEDGER.save_factor_mining_run(
            report_id,
            sealed_test_id=deps._as_int(sealed["id"]),
            data_version=str(sealed.get("data_version") or "unknown"),
            n_tested=0,
            result=result,
            status="multi_seed_report",
        )
        return {"run_id": report_id, "n_tested": combined["n_tested"], "result": result}
    seed = deps._bounded_int(payload.get("seed", 20260927), 0, 2147483647)
    generations = deps._bounded_int(payload.get("generations", 10), 1, 20)
    population_size = deps._bounded_int(payload.get("population_size", 50), 4, 100)
    top_n = deps._bounded_int(payload.get("top_n", 10), 1, 20)
    if generations * population_size > 2000:
        raise ValueError("单次最多评估 2,000 个 GP 个体")
    if not os.getenv("TUSHARE_TOKEN", "").strip():
        raise RuntimeError("未配置 TUSHARE_TOKEN，因子挖掘要求真实 Tushare 日线")
    sealed, sealed_bars, training_bars = deps._factor_mining_dataset()
    sealed_test_id = deps._as_int(sealed.get("id"))
    existing_acceptance = deps.RESEARCH_LEDGER.factor_mining_acceptance(sealed_test_id)
    if existing_acceptance is not None:
        raise PermissionError(
            "当前研究周期的 sealed 因子批次已打开；请先归档研究周期再开始新一轮挖掘"
        )
    training_start = min(bar.date for bar in training_bars)
    training_end = max(bar.date for bar in training_bars)
    feature_observations, industry_observations, pit_quality, _ = (
        deps._pit_observations_for_bars(sealed, sealed_bars)
    )
    training_dates = {bar.date for bar in training_bars}
    training_observations = [
        item for item in feature_observations if item.as_of in training_dates
    ]
    training_industry = [
        item for item in industry_observations if item.as_of in training_dates
    ]
    panel = build_factor_panel(
        training_bars,
        data_version=str(sealed.get("data_version") or "unknown"),
        observations=training_observations,
        industry_observations=training_industry,
        pit_quality=pit_quality,
    )
    existing_panels = [
        deps._factor_values_for_definition(item, training_bars, panel)
        for item in deps.FACTOR_STORE.definitions(enabled_only=True)
    ]
    for stored_factor in deps.FACTOR_STORE.expression_library():
        if stored_factor.get("status") not in {"accepted", "active"}:
            continue
        try:
            existing_panels.append(
                evaluate_expression(str(stored_factor["expression"]), panel).values
            )
        except (TypeError, ValueError):
            continue
    run_id = MINING_REQUEST_ID.get() or uuid.uuid4().hex
    context = MINING_JOB.get()
    parameters = {
        key: payload[key]
        for key in (
            "seed",
            "generations",
            "population_size",
            "top_n",
            "fitness_mode",
            "correlation_lambda",
            "complexity_mu",
            "temporal_cv",
            "field_subset",
            "operator_subset",
        )
        if key in payload
    }
    deps.RESEARCH_LEDGER.save_factor_mining_run(
        run_id,
        sealed_test_id=sealed_test_id,
        data_version=str(sealed.get("data_version") or "unknown"),
        n_tested=0,
        result={
            "candidates": [],
            "job_id": context.get("job_id") if context else None,
            "parameters": parameters,
            "protocol": {"sealed_dates_used": False},
        },
        status="evaluation_in_progress",
    )

    def count_attempts(total: int) -> None:
        report_mining_progress(
            "mining",
            progress_current=total,
            progress_total=generations * population_size,
        )
        deps.RESEARCH_LEDGER.record_factor_mining_attempts(run_id, total)

    result = run_factor_mining(
        training_bars,
        train_end=training_end,
        seed=seed,
        generations=generations,
        population_size=population_size,
        top_n=top_n,
        data_version=str(sealed.get("data_version") or "unknown"),
        observations=training_observations,
        industry_observations=training_industry,
        pit_quality=pit_quality,
        existing_factor_panels=[item for item in existing_panels if item],
        fitness_mode=str(payload.get("fitness_mode", "icir")),
        correlation_lambda=float(str(payload.get("correlation_lambda", 0.5))),
        complexity_mu=float(str(payload.get("complexity_mu", 0.0005))),
        temporal_cv=payload.get("temporal_cv", True) is not False,
        field_subset=cast(list[str], payload.get("field_subset"))
        if isinstance(payload.get("field_subset"), list)
        else None,
        attempt_callback=count_attempts,
        operator_subset=cast(list[str], payload.get("operator_subset"))
        if isinstance(payload.get("operator_subset"), list)
        else None,
    ).as_dict()
    n_tested = deps._as_int(result.get("n_tested"))
    candidate_rows = [
        deps._object_mapping(item)
        for item in deps._object_list(result.get("candidates"))
        if isinstance(item, Mapping)
    ]
    comparison_reference = deps._factor_mining_cross_universe_reference(
        [str(item.get("expression") or "") for item in candidate_rows],
        start_date=training_start,
        end_date=training_end,
    )
    comparison_expressions = comparison_reference.get("expressions")
    comparison_by_expression = (
        comparison_expressions if isinstance(comparison_expressions, dict) else {}
    )
    result["cross_universe_reference"] = {
        key: value
        for key, value in comparison_reference.items()
        if key != "expressions"
    }
    candidate_rows = [
        {
            **candidate,
            "cross_universe_reference": comparison_by_expression.get(
                str(candidate.get("expression") or "")
            ),
        }
        for candidate in candidate_rows
    ]
    result["candidates"] = candidate_rows
    for candidate in candidate_rows:
        expression = str(candidate.get("expression") or "")
        values = evaluate_expression(expression, panel).values
        evaluation = evaluate_factor_panel(
            panel,
            values,
            start=training_start,
            end=training_end,
            n_tested=deps.RESEARCH_LEDGER.factor_mining_trial_total(sealed_test_id),
            horizons=tuple(range(1, 21)),
            quantiles=10,
        )
        evaluation["ic_decay_curve"] = ic_half_life(
            deps._object_mapping(evaluation["horizons"])
        )
        candidate["evaluation"] = evaluation
        evaluation["portfolio"] = training_portfolios(
            panel,
            values,
            ledger=deps.RESEARCH_LEDGER,
            sealed_id=sealed_test_id,
        )
    code_version = os.getenv("A_PI_GIT_COMMIT", "uncommitted") or "uncommitted"
    n_tested = deps._as_int(result.get("n_tested"))
    candidates = [dict(item, n_tested=n_tested) for item in candidate_rows]
    stored = deps.FACTOR_STORE.save_mined_candidates(
        candidates,
        data_version=str(sealed.get("data_version") or "unknown"),
        code_version=code_version,
        sealed_test_id=sealed_test_id,
        run_id=run_id,
    )
    key_by_expression = {str(item["expression"]): str(item["key"]) for item in stored}
    candidate_rows = [
        {
            **candidate,
            "factor_key": key_by_expression.get(str(candidate.get("expression")), ""),
        }
        for candidate in candidate_rows
    ]
    result["candidates"] = candidate_rows
    result["code_version"] = code_version
    result["sealed_test_id"] = sealed_test_id
    result["sealed_start"] = sealed["sealed_start"]
    result["sealed_end"] = sealed["sealed_end"]
    saved = deps.RESEARCH_LEDGER.update_factor_mining_run(run_id, result)
    saved["notification_status"] = queue_decay_alerts(
        deps.RESEARCH_LEDGER.path, run_id, result
    )
    return saved


def _evaluate_factor_expression_request(
    payload: dict[str, object], *, deps: MiningResearchDependencies
) -> dict[str, object]:
    request_id = uuid.uuid4().hex
    token = MINING_REQUEST_ID.set(request_id)
    try:
        return _evaluate_factor_expression_request_impl(payload, deps=deps)
    except Exception as error:
        run = deps.RESEARCH_LEDGER.factor_mining_run(request_id)
        if run and run["status"] == "evaluation_in_progress":
            deps.RESEARCH_LEDGER.update_factor_mining_run(
                request_id,
                {
                    **cast(Mapping[str, object], run["result"]),
                    "error": type(error).__name__,
                    "candidates": [],
                },
                status="evaluation_failed",
            )
            # Built-in exceptions intentionally carry API metadata on this boundary.
            setattr(error, "run_id", request_id)  # noqa: B010
            if isinstance(error, (ValueError, ArithmeticError, IndexError)):
                message = str(error)
                code = (
                    "expression_syntax_error"
                    if "语法" in message
                    else "pit_not_unlocked"
                    if any(
                        word in message
                        for word in ("PIT 门禁", "PIT门禁", "行业中性化", "规模中性化")
                    )
                    else "field_unavailable"
                    if "字段" in message
                    else "expression_evaluation_failed"
                )
                setattr(error, "api_code", getattr(error, "api_code", code))  # noqa: B010
        raise
    finally:
        MINING_REQUEST_ID.reset(token)


def _evaluate_factor_expression_request_impl(
    payload: dict[str, object], *, deps: MiningResearchDependencies
) -> dict[str, object]:
    deps._validate_factor_mining_universe_request(payload)
    mode = payload.get("mode")
    if mode in {
        "evolution_create",
        "evolution_step",
        "evolution_history",
        "evolution_feedback",
    }:
        return evolution_request(
            payload, deps=deps, evaluate=_evaluate_factor_expression_request
        )
    if mode == "compose":
        return compose_request(
            payload, deps=deps, evaluate=_evaluate_factor_expression_request
        )
    if mode in {"template_batch", "hypothesis_batch"}:
        if mode == "template_batch":
            templates = list(EXPRESSION_TEMPLATES)
        else:
            expressions = payload.get("expressions")
            if (
                not isinstance(expressions, list)
                or not 1 <= len(expressions) <= 100
                or not all(
                    isinstance(item, str) and item.strip() and len(item) <= 500
                    for item in expressions
                )
            ):
                raise ValueError(
                    "hypothesis_batch requires 1–100 expressions of at most 500 characters"
                )
            templates = [
                {
                    "name": f"hypothesis-{index + 1}",
                    "category": "custom_hypothesis",
                    "expression": expression,
                }
                for index, expression in enumerate(expressions)
            ]
        batch_id = uuid.uuid4().hex

        def evaluate_one(expression: str) -> dict[str, object]:
            try:
                run = _evaluate_factor_expression_request(
                    {"expression": expression}, deps=deps
                )
            except (
                ValueError,
                ArithmeticError,
                RuntimeError,
                PermissionError,
            ) as error:
                failed_run_id = str(getattr(error, "run_id", "") or "")
                if failed_run_id:
                    recorded = deps.RESEARCH_LEDGER.factor_mining_run(failed_run_id)
                    if recorded is None:
                        raise
                    deps.RESEARCH_LEDGER.update_factor_mining_run(
                        failed_run_id,
                        {
                            **deps._object_mapping(recorded["result"]),
                            "census_batch_id": batch_id,
                        },
                        status="evaluation_failed",
                    )
                raise
            deps.RESEARCH_LEDGER.update_factor_mining_run(
                str(run["run_id"]),
                {**deps._object_mapping(run["result"]), "census_batch_id": batch_id},
            )
            return run

        result = template_census(templates, evaluate_one)
        result["census_batch_id"] = batch_id
        # Individual trials are the durable evidence. The aggregate is never counted twice.
        _, pipeline = deps._factor_mining_scope()
        sealed = deps.RESEARCH_LEDGER.get_sealed_test(
            "baseline", pipeline, include_bars=False
        )
        if sealed:
            deps.RESEARCH_LEDGER.save_factor_mining_run(
                batch_id,
                sealed_test_id=deps._as_int(sealed["id"]),
                data_version=str(sealed.get("data_version") or "unknown"),
                n_tested=0,
                result={"candidates": [], "census": result},
                status="census_report",
            )
        return {
            "run_id": batch_id,
            "n_tested": result["n_tested"],
            "result": result,
            "kind": str(mode),
        }
    expression = str(payload.get("expression", "")).strip()
    if not expression:
        raise ValueError("请输入因子表达式")
    label_horizons = payload.get("label_horizons", [5, 10, 20])
    if (
        not isinstance(label_horizons, list)
        or not 1 <= len(label_horizons) <= 20
        or not all(type(value) is int and 1 <= value <= 20 for value in label_horizons)
        or len(set(label_horizons)) != len(label_horizons)
    ):
        raise ValueError(
            "label_horizons requires unique integer horizons between 1 and 20"
        )
    sealed, sealed_bars, training_bars = deps._factor_mining_dataset()
    if (
        deps.RESEARCH_LEDGER.factor_mining_acceptance(deps._as_int(sealed.get("id")))
        is not None
    ):
        raise PermissionError("当前研究周期的 sealed 因子批次已打开；请先归档研究周期")
    data_version = str(sealed.get("data_version") or "unknown")
    feature_observations, industry_observations, pit_quality, _ = (
        deps._pit_observations_for_bars(sealed, sealed_bars)
    )
    training_dates = {bar.date for bar in training_bars}
    panel = build_factor_panel(
        training_bars,
        data_version=data_version,
        observations=[
            item for item in feature_observations if item.as_of in training_dates
        ],
        industry_observations=[
            item for item in industry_observations if item.as_of in training_dates
        ],
        pit_quality=pit_quality,
    )
    run_id = MINING_REQUEST_ID.get() or uuid.uuid4().hex
    deps.RESEARCH_LEDGER.save_factor_mining_run(
        run_id,
        sealed_test_id=deps._as_int(sealed.get("id")),
        data_version=data_version,
        n_tested=1,
        result={
            "candidates": [],
            "expression": expression,
            "protocol": {"sealed_dates_used": False, "failed_attempt_counted": True},
        },
        status="evaluation_in_progress",
    )
    evaluated = evaluate_expression(
        expression, panel, code_version=os.getenv("A_PI_GIT_COMMIT", "uncommitted")
    )
    if not any(
        value is not None and math.isfinite(value)
        for row in evaluated.values
        for value in row
    ):
        error = ValueError("表达式在训练区间没有可用字段观测值")
        setattr(error, "api_code", "field_unavailable")  # noqa: B010
        raise error
    train_start = min(bar.date for bar in training_bars)
    train_end = max(bar.date for bar in training_bars)
    report = evaluate_factor_panel(
        panel,
        evaluated.values,
        start=train_start,
        end=train_end,
        n_tested=1,
        horizons=tuple(range(1, 21)),
        quantiles=10,
    )
    report["ic_decay_curve"] = ic_half_life(deps._object_mapping(report["horizons"]))
    report["portfolio"] = training_portfolios(
        panel,
        evaluated.values,
        ledger=deps.RESEARCH_LEDGER,
        sealed_id=deps._as_int(sealed.get("id")),
        start=train_start,
        end=train_end,
    )
    report["layered_diagnostics"] = {
        str(quantiles): layered_diagnostics(
            panel, evaluated.values, quantiles=quantiles
        )
        for quantiles in (5, 10)
    }
    registry_panels = {}
    matrix_panels: dict[str, Panel | None] = {"__candidate__": evaluated.values}
    for definition in deps.FACTOR_STORE.definitions(enabled_only=True):
        reference = deps._factor_values_for_definition(definition, training_bars, panel)
        matrix_panels[definition.key] = reference
        if reference is not None:
            registry_panels[definition.key] = reference
    diagnostic = registry_diagnostics(panel, evaluated.values, registry_panels)
    report["registry_diagnostics"] = diagnostic
    report["registry_correlation_matrix"] = registry_correlation_matrix(
        panel, matrix_panels
    )
    comparison_reference = deps._factor_mining_cross_universe_reference(
        [expression], start_date=train_start, end_date=train_end
    )
    comparison_expressions = comparison_reference.get("expressions")
    comparison_by_expression = (
        comparison_expressions if isinstance(comparison_expressions, dict) else {}
    )
    code_version = os.getenv("A_PI_GIT_COMMIT", "uncommitted") or "uncommitted"
    horizons = deps._object_mapping(report.get("horizons"))
    selected_horizon = label_horizons[0] if ARENA_SESSION.get() else 5
    five_day_horizon = deps._object_mapping(horizons.get(str(selected_horizon)))
    candidate = {
        "expression": expression,
        "fitness": five_day_horizon.get("mean_ic"),
        "mean_rank_ic": five_day_horizon.get("mean_ic"),
        "min_history": evaluated.metadata["min_history"],
        "dependencies": evaluated.metadata["dependencies"],
        "evaluation": report,
        "cross_universe_reference": comparison_by_expression.get(expression),
        "n_tested": 1,
        "label_horizon": selected_horizon,
    }
    stored = deps.FACTOR_STORE.save_mined_candidates(
        [candidate],
        data_version=data_version,
        code_version=code_version,
        sealed_test_id=deps._as_int(sealed.get("id")),
        run_id=run_id,
    )
    candidate["factor_key"] = stored[0]["key"] if stored else ""
    result = {
        "seed": 0,
        "generations": 0,
        "population_size": 1,
        "n_tested": 1,
        "training_start": train_start.isoformat(),
        "training_end": train_end.isoformat(),
        "data_version": data_version,
        "code_version": code_version,
        "sealed_test_id": deps._as_int(sealed.get("id")),
        "sealed_start": sealed["sealed_start"],
        "sealed_end": sealed["sealed_end"],
        "candidates": [candidate],
        "cross_universe_reference": {
            key: value
            for key, value in comparison_reference.items()
            if key != "expressions"
        },
        "generation_log": [],
        "protocol": {
            "fitness": "training_mean_rank_ic_only",
            "sealed_dates_used": False,
            "manual_expression": True,
        },
        "feedback": {
            "expression_hash": canonical_expression_hash(expression),
            "train_rank_ic": {
                key: deps._object_mapping(value).get("mean_ic")
                for key, value in horizons.items()
                if int(key) in label_horizons
            },
            "train_icir": {
                key: deps._object_mapping(value).get("icir")
                for key, value in horizons.items()
                if int(key) in label_horizons
            },
            "max_corr_with_registry": diagnostic["max_corr_with_registry"],
            "incremental_rank_ic": diagnostic["incremental_rank_ic"],
            "coverage": report["coverage"],
            "n_tested_after": deps.RESEARCH_LEDGER.factor_mining_trial_total(
                deps._as_int(sealed.get("id"))
            ),
            "proposer_tag": str(payload.get("proposer_tag", "manual"))[:100],
            "pit_quality": dict(panel.pit_quality),
            "sealed_dates_used": False,
        },
    }
    saved = deps.RESEARCH_LEDGER.update_factor_mining_run(run_id, result)
    saved["notification_status"] = queue_decay_alerts(
        deps.RESEARCH_LEDGER.path, run_id, result
    )
    return saved
