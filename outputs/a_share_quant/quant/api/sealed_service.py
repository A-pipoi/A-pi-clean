"""One-shot sealed PIT acceptance."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from quant.api.contracts import (
    AsFloatCallback,
    AsIntCallback,
    BarsFromPayloadCallback,
    BoundedIntCallback,
    BuildStrategyCallback,
    EnsureSealedSnapshotCallback,
    FactorSnapshotCallback,
    FactorSnapshotHashCallback,
    FilterBacktestUniverseCallback,
    ObjectListCallback,
    ObjectMappingCallback,
    RequireCompleteUniverseCallback,
    RequireStrategyDependenciesCallback,
    ResolveBacktestUniverseCallback,
    RunSealedTestCallback,
    SealedPipelineKeyCallback,
    SealedPublicPayloadCallback,
    SealedScopeCallback,
    SymbolsCallback,
)
from quant.backtest import CostModel
from quant.data import (
    DEFAULT_SYMBOLS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    TushareDailyAdapter,
)
from quant.data.universe import HistoricalUniverse
from quant.factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from quant.features import FeatureStore
from quant.research import ResearchLedger
from quant.strategies import get_strategy_preset, resolve_strategy_config

from ..strategies.rulebook import RecipeStore, RulebookStrategy


@dataclass(frozen=True)
class SealedValidationDependencies:
    CHINA_TIMEZONE: tzinfo
    DATA_CACHE_DIR: Path
    FACTOR_STORE: FactorRegistryStore
    FEATURE_STORE: FeatureStore
    RESEARCH_LEDGER: ResearchLedger
    SEALED_MIN_OBSERVATIONS: int
    SEALED_TEST_DAYS: int
    TushareDailyAdapter: type[TushareDailyAdapter]
    _as_float: AsFloatCallback
    _as_int: AsIntCallback
    _bars_from_payload: BarsFromPayloadCallback
    _bounded_int: BoundedIntCallback
    _ensure_sealed_snapshot: EnsureSealedSnapshotCallback
    _factor_snapshot: FactorSnapshotCallback
    _factor_snapshot_hash: FactorSnapshotHashCallback
    _filter_backtest_universe: FilterBacktestUniverseCallback
    _object_list: ObjectListCallback
    _object_mapping: ObjectMappingCallback
    _require_complete_universe: RequireCompleteUniverseCallback
    _require_strategy_dependencies: RequireStrategyDependenciesCallback
    _resolve_backtest_universe: ResolveBacktestUniverseCallback
    _sealed_pipeline_key: SealedPipelineKeyCallback
    _sealed_public_payload: SealedPublicPayloadCallback
    _sealed_scope: SealedScopeCallback
    _symbols: SymbolsCallback
    build_strategy: BuildStrategyCallback
    run_sealed_test: RunSealedTestCallback


def run_sealed_request(
    payload: dict[str, Any], deps: SealedValidationDependencies
) -> tuple[int, dict[str, object]]:
    try:
        strategy_name = str(payload.get("strategy", "baseline")).strip().lower()
        strategy_preset = get_strategy_preset(strategy_name)
        recipe_version=str(payload.get('recipe_version') or '')
        recipe=RecipeStore(deps.RESEARCH_LEDGER.path.with_name('strategy_recipes.sqlite3')).get(recipe_version) if strategy_name=='rulebook' else None
        if recipe:
            latest=deps.RESEARCH_LEDGER.latest(strategy_name,kind='backtest')
            params=deps._object_mapping((latest or {}).get('parameters'))
            params=deps._object_mapping(params.get('parameters')) or params
            if params.get('recipe_version')!=recipe_version:
                raise ValueError('请先对所选规则版本运行非密封研究回测，再执行一次性验收')
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
        rebalance = str(payload.get("rebalance", "weekly")).strip().lower()
        if rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance must be 'daily', 'weekly', or 'monthly'")
        universe_mode = str(payload.get("universe_mode", "fixed")).strip().lower()
        pit_mode = universe_mode in {"pit_index", "pit_hs300"}
        pit_existing = (
            deps.RESEARCH_LEDGER.latest_sealed_test_for_universe(
                strategy_name,
                index_code=FACTOR_MINING_UNIVERSE_INDEX_CODE,
                protocol="pit_historical_index_v1",
                include_bars=True,
            )
            if pit_mode
            else None
        )
        historical_universe: HistoricalUniverse | None = None
        if pit_existing and pit_existing.get("result") is not None:
            existing_parameters = deps._object_mapping(pit_existing.get("parameters"))
            if recipe:existing_parameters={**existing_parameters,'recipe_version':deps._object_mapping(pit_existing.get('result')).get('recipe_version')}
            expected_parameters = {
                **({'recipe_version':recipe_version} if recipe else {}),
                "factor_snapshot_hash": deps._factor_snapshot_hash(),
                "rebalance": rebalance,
                "lookback": lookback,
                "top_n": top_n,
            }
            changed_parameters = [
                key
                for key, value in expected_parameters.items()
                if existing_parameters.get(key) != value
            ]
            if changed_parameters:
                raise ValueError(
                    "当前设置与既有 PIT 密封结果不一致（"
                    + ", ".join(changed_parameters)
                    + "）；结果保持只读，请先归档研究周期后再建立新验收"
                )
            return (
                200,
                {
                    "ok": True,
                    "strategy": strategy_name,
                    "sealed_test": deps._sealed_public_payload(pit_existing),
                    "sealed_already_evaluated": True,
                    "note": "PIT 密封结果已封存；重复请求只返回原结果，不重新拉取行情或计算",
                },
            )
        if pit_existing:
            existing_parameters = deps._object_mapping(pit_existing.get("parameters"))
            symbols = [
                str(item)
                for item in deps._object_list(existing_parameters.get("symbols"))
            ]
            if not symbols:
                raise ValueError(
                    "现有 PIT 密封快照缺少股票池审计信息；请归档后重新建立验收"
                )
            adjust = ""
            pipeline_key = str(pit_existing.get("factor_pipeline_version") or "")
            pit_data = pit_existing.get("pit_data")
            if not isinstance(pit_data, dict):
                raise ValueError(
                    "现有 PIT 密封快照缺少历史成分数据；请归档后重新建立验收"
                )
            historical_universe = HistoricalUniverse.from_index_weight_history(
                deps._object_mapping(pit_data)
            )
            universe = None
            pit_metadata = None
        elif pit_mode:
            sealed_end = datetime.now(deps.CHINA_TIMEZONE).date()
            sealed_history_start = sealed_end - timedelta(days=365)
            universe = deps._resolve_backtest_universe(
                {
                    "universe_mode": "pit_index",
                    "universe_index_code": FACTOR_MINING_UNIVERSE_INDEX_CODE,
                },
                sealed_history_start,
                sealed_end,
                force_membership_refresh=True,
            )
            symbols = list(universe["symbols"])
            adjust = ""
            scope = deps._sealed_scope(
                symbols, adjust, universe_version=str(universe["version"])
            )
            resolved_historical = universe.get("historical_universe")
            if not isinstance(resolved_historical, HistoricalUniverse):
                raise RuntimeError("PIT 密封验收缺少可验证的历史成分映射")
            historical_universe = resolved_historical
            pit_data = universe.get("pit_data")
            pit_metadata = universe.get("pit_metadata")
        else:
            if universe_mode not in {"fixed", "custom", "seed"}:
                raise ValueError("universe_mode must be 'pit_index' or 'fixed'")
            symbols = deps._symbols(payload.get("symbols", DEFAULT_SYMBOLS))
            adjust = str(payload.get("adjust", "")).strip().lower()
            scope = deps._sealed_scope(symbols, adjust)
            universe = None
            historical_universe = None
            pit_data = None
            pit_metadata = None
        if not pit_existing:
            pipeline_key = deps._sealed_pipeline_key(scope)
        existing = (
            pit_existing
            if pit_mode
            else deps.RESEARCH_LEDGER.get_sealed_test(
                strategy_name, pipeline_key, include_bars=True
            )
        )
        if existing and existing.get("result") is not None:
            return (
                200,
                {
                    "ok": True,
                    "strategy": strategy_name,
                    "sealed_test": deps._sealed_public_payload(existing),
                    "sealed_already_evaluated": True,
                    "note": "密封测试结果已封存，重复请求只返回原始结果，不会重新计算",
                },
            )
        if existing:
            sealed = existing
            pit_data = sealed.get("pit_data") if pit_mode else None
            if pit_mode:
                historical_universe = HistoricalUniverse.from_index_weight_history(
                    deps._object_mapping(pit_data)
                )
                snapshot_parameters = deps._object_mapping(sealed.get("parameters"))
                if recipe:snapshot_parameters={**snapshot_parameters,'recipe_version':deps._object_mapping(sealed.get('result')).get('recipe_version')}
                expected_parameters = {
                    **({'recipe_version':recipe_version} if recipe else {}),
                    "factor_snapshot_hash": deps._factor_snapshot_hash(),
                    "rebalance": rebalance,
                    "lookback": lookback,
                    "top_n": top_n,
                }
                changed_parameters = [
                    key
                    for key, value in expected_parameters.items()
                    if snapshot_parameters.get(key) != value
                ]
                if changed_parameters:
                    raise ValueError(
                        "现有 PIT 密封快照参数与当前设置不一致（"
                        + ", ".join(changed_parameters)
                        + "）；请先归档研究周期，不能覆盖或重跑已封存样本"
                    )
                stored_symbols = snapshot_parameters.get("symbols")
                if isinstance(stored_symbols, list):
                    symbols = [str(item) for item in stored_symbols]
        else:
            adapter = deps.TushareDailyAdapter(
                symbols=symbols,
                token=os.getenv("TUSHARE_TOKEN"),
                adjust=adjust,
                cache_dir=deps.DATA_CACHE_DIR,
                fetch_limits=True,
                force_refresh=True,
            )
            start = universe["start_date"] if pit_mode and universe else None
            end = universe["end_date"] if pit_mode and universe else None
            bars = adapter.daily_bars(start=start, end=end)
            snapshot = adapter.snapshot()
            deps._require_complete_universe(snapshot.metadata, context="密封测试")
            if not bars:
                raise ValueError("没有可用于密封测试的行情数据")
            if pit_mode:
                if universe is None or historical_universe is None:
                    raise RuntimeError("PIT 密封验收缺少历史成分快照")
                if not bool(snapshot.metadata.get("limits_available")):
                    raise RuntimeError(
                        f"严格 PIT 密封验收要求完整 stk_limit 涨跌停数据；当前状态：{snapshot.metadata.get('limit_data_status', 'unknown')}。未创建密封快照。"
                    )
                if snapshot.metadata.get("data_source") != "tushare_live":
                    raise RuntimeError(
                        "严格 PIT 密封验收只接受本次实时 Tushare 日线，未创建密封快照"
                    )
                latest_bar_date = max(bar.date for bar in bars)
                if latest_bar_date != datetime.now(deps.CHINA_TIMEZONE).date():
                    raise RuntimeError(
                        f"严格 PIT 密封验收需要北京时间今日收盘日线；最新可用为 {latest_bar_date.isoformat()}，未创建密封快照"
                    )
                bars = deps._filter_backtest_universe(bars, universe)
                if not bars:
                    raise ValueError("真实日线经逐日 PIT 成分筛选后为空")
                pit_bar_dates = sorted({bar.date for bar in bars})
                if len(pit_bar_dates) < deps.SEALED_TEST_DAYS + 40:
                    raise ValueError(
                        f"严格 PIT 密封至少需要 {deps.SEALED_TEST_DAYS + 40} 个有效交易日；当前仅 {len(pit_bar_dates)} 个，未创建密封快照"
                    )
                pit_changes = historical_universe.membership_changes(
                    pit_bar_dates[0], pit_bar_dates[-1]
                )
                if not pit_changes:
                    raise RuntimeError(
                        "所取沪深300历史范围没有可验证的真实成分变更；未创建密封快照"
                    )
            sealed = deps._ensure_sealed_snapshot(
                strategy_name,
                pipeline_key,
                bars,
                snapshot,
                symbols=symbols,
                adjust=adjust,
                sealed_days=deps.SEALED_TEST_DAYS,
                pit_data=pit_data if isinstance(pit_data, dict) else None,
                pit_metadata=pit_metadata if isinstance(pit_metadata, dict) else None,
                additional_parameters={
                    **({'recipe_version':recipe_version,'recipe':recipe} if recipe else {}),
                    "universe_mode": "pit_historical_index" if pit_mode else "fixed",
                    "sealed_acceptance_protocol": "pit_historical_index_v1"
                    if pit_mode
                    else None,
                    "factor_snapshot_hash": deps._factor_snapshot_hash(),
                    "rebalance": rebalance,
                    "lookback": lookback,
                    "top_n": top_n,
                },
            )
        if sealed.get("result") is not None:
            return (
                200,
                {
                    "ok": True,
                    "strategy": strategy_name,
                    "sealed_test": deps._sealed_public_payload(sealed),
                    "sealed_already_evaluated": True,
                },
            )
        deps.RESEARCH_LEDGER.assert_unexposed_interval(str(sealed["sealed_start"]), str(sealed["sealed_end"]))
        # Backtests reserve the data snapshot before the chosen recipe is frozen
        # for one-shot acceptance. Once evaluated, changing a recipe is rejected.
        stored=deps._object_mapping(sealed.get('parameters'))
        if recipe and stored.get('recipe_version') not in {None,recipe_version}:
            raise ValueError('密封快照绑定的规则版本不一致，请先归档研究周期')
        sealed_bars = deps._bars_from_payload(sealed.get("bars", []))
        rebalance_every = {"daily": 1, "weekly": 5, "monthly": 21}[rebalance]
        factor_definitions = deps.FACTOR_STORE.definitions(enabled_only=True)
        feature_version = str(sealed.get("data_version") or "sealed-snapshot")
        feature_store_info = (
            deps.FEATURE_STORE.materialize(sealed_bars, feature_version)
            if strategy_name == "lightgbm"
            else None
        )
        cost_model = CostModel(max_participation=0.1)
        strategy_factory = lambda: RulebookStrategy(recipe,rebalance_every) if recipe is not None else deps.build_strategy(
            strategy_name,
            rebalance_every=rebalance_every,
            factor_definitions=factor_definitions,
            feature_store=deps.FEATURE_STORE if strategy_name == "lightgbm" else None,
            data_version=feature_version if strategy_name == "lightgbm" else None,
        )
        sealed_report = deps.run_sealed_test(
            sealed_bars,
            strategy_factory,
            sealed_start=date.fromisoformat(str(sealed["sealed_start"])),
            sealed_end=date.fromisoformat(str(sealed["sealed_end"])),
            initial_cash=deps._as_float(payload.get("initial_cash", 1000000)),
            cost_model=cost_model,
            minimum_observations=deps.SEALED_MIN_OBSERVATIONS,
            pit_universe=historical_universe
            if isinstance(historical_universe, HistoricalUniverse)
            else None,
            pit_index_code=FACTOR_MINING_UNIVERSE_INDEX_CODE if pit_mode else None,
        )
        evaluated = deps.RESEARCH_LEDGER.record_sealed_result(
            deps._as_int(sealed["id"]),
            status="passed" if sealed_report.passed else "needs_review",
            result={
                **({'recipe_version':recipe_version,'recipe':recipe} if recipe else {}),
                **sealed_report.as_dict(),
                "strategy": strategy_name,
                "lookback": lookback,
                "top_n": top_n,
                "rebalance": rebalance,
                "rebalance_every": rebalance_every,
                "factor_pipeline_version": FACTOR_PIPELINE_VERSION,
                "factor_snapshot_hash": deps._factor_snapshot_hash(),
                "factor_definitions": deps._factor_snapshot(),
                "feature_store": feature_store_info,
                "symbols": symbols,
                "adjust": adjust,
                "universe_mode": "pit_historical_index" if pit_mode else "fixed",
            },
        )
        return (
            200,
            {
                "ok": True,
                "strategy": strategy_name,
                "sealed_test": deps._sealed_public_payload(evaluated),
                "sealed_already_evaluated": False,
            },
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return (400, {"ok": False, "error": str(error)})
    except RuntimeError as error:
        return (
            502,
            {"ok": False, "code": "data_provider_unavailable", "error": str(error)},
        )
    except Exception as error:
        logging.getLogger(__name__).exception("quant.api.sealed_service failed: %s", type(error).__name__, exc_info=None)
        return (500, {"ok": False, "error": f"sealed_validation_failed: {type(error).__name__}"})
