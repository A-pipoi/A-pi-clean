"""Mining data services with explicit runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from quant.api.contracts import (
    ApplyFactorMiningUniverseCallback,
    AsIntCallback,
    BarsFromPayloadCallback,
    EnsureSealedSnapshotCallback,
    FactorMiningComparisonPanelCallback,
    FactorMiningPitQualityCallback,
    FactorMiningScopeCallback,
    FactorMiningUniversePayloadCallback,
    FetchFactorMiningPitBundleCallback,
    ObjectMappingCallback,
    PitObservationsForBarsCallback,
    RequireCompleteUniverseCallback,
    ResearchBarsCallback,
)
from quant.data import (
    FACTOR_MINING_MIN_LISTING_DAYS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    INDEX_UNIVERSE_NAMES,
    TushareDailyAdapter,
    fetch_tushare_daily_basic_history,
    fetch_tushare_fundamental_history,
    fetch_tushare_index_member_history,
    fetch_tushare_industry_history,
    fetch_tushare_listing_dates,
)
from quant.data.filters import UniverseRules, filter_universe
from quant.data.observed_industry import ObservedIndustryStore
from quant.data.schema import Bar
from quant.data.universe import HistoricalUniverse
from quant.factors.analysis import evaluate_factor_panel
from quant.factors.expression import (
    FactorPanel,
    build_factor_panel,
    evaluate_expression,
)
from quant.mining.universe import FACTOR_MINING_UNIVERSE_VERSION
from quant.research import ResearchLedger
from quant.time_utils import china_today

from .mining_jobs import mining_requested_range, report_mining_progress

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MiningDataDependencies:
    DATA_CACHE_DIR: Path
    FACTOR_MINING_HISTORY_DAYS: int
    RESEARCH_LEDGER: ResearchLedger
    SEALED_TEST_DAYS: int
    TushareDailyAdapter: type[TushareDailyAdapter]
    _apply_factor_mining_universe: ApplyFactorMiningUniverseCallback
    _as_int: AsIntCallback
    _bars_from_payload: BarsFromPayloadCallback
    _ensure_sealed_snapshot: EnsureSealedSnapshotCallback
    _factor_mining_comparison_panel: FactorMiningComparisonPanelCallback
    _factor_mining_pit_quality: FactorMiningPitQualityCallback
    _factor_mining_scope: FactorMiningScopeCallback
    _factor_mining_universe_payload: FactorMiningUniversePayloadCallback
    _fetch_factor_mining_pit_bundle: FetchFactorMiningPitBundleCallback
    _object_mapping: ObjectMappingCallback
    _pit_observations_for_bars: PitObservationsForBarsCallback
    _require_complete_universe: RequireCompleteUniverseCallback
    _research_bars: ResearchBarsCallback


def _fetch_factor_mining_pit_bundle(
    symbols: list[str],
    start_date: date,
    end_date: date,
    token: str,
    *,
    force_refresh: bool = True,
    deps: MiningDataDependencies,
) -> dict[str, object]:
    financial_start = max(date(2010, 1, 1), start_date - timedelta(days=365 * 4))
    financial_rows, financial_meta = fetch_tushare_fundamental_history(
        symbols,
        start_date=financial_start,
        end_date=end_date,
        token=token,
        cache_dir=deps.DATA_CACHE_DIR,
        force_refresh=force_refresh,
    )
    daily_rows, daily_meta = fetch_tushare_daily_basic_history(
        symbols,
        start_date=start_date,
        end_date=end_date,
        token=token,
        cache_dir=deps.DATA_CACHE_DIR,
        force_refresh=force_refresh,
    )
    industry_rows, industry_meta = fetch_tushare_industry_history(
        symbols, token=token, cache_dir=deps.DATA_CACHE_DIR, force_refresh=force_refresh
    )
    observed=ObservedIndustryStore(deps.DATA_CACHE_DIR/'live_ic'/'industry.sqlite3').records(symbols,end_date)
    if observed:
        industry_rows.extend(observed)
        industry_meta={**industry_meta,'availability_quality':'verified',
            'availability_basis':'actual_first_seen','historical_backfill':False,
            'forward_observation_records':len(observed)}
    return {
        "schema_version": "pit-panel-v1",
        "fundamental_records": financial_rows,
        "daily_basic_records": daily_rows,
        "industry_records": industry_rows,
        "source_metadata": {
            "fundamentals": financial_meta,
            "daily_basic": daily_meta,
            "industry": industry_meta,
        },
    }


def _factor_mining_comparison_panel(
    start_date: date, end_date: date, *, deps: MiningDataDependencies
) -> tuple[FactorPanel, str]:
    """Build a non-sealed CSI 500 panel for report-only robustness checks."""
    comparison_code = "000905.SH"
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    history = fetch_tushare_index_member_history(
        comparison_code,
        start_date=start_date,
        end_date=end_date,
        token=token,
        cache_dir=deps.DATA_CACHE_DIR,
    )
    universe = HistoricalUniverse.from_index_weight_history(history)
    symbols = sorted(universe.symbols_for_range(start_date, end_date))
    if not symbols:
        raise RuntimeError("中证500历史快照在参考区间内没有成分股")
    listing_dates, listing_metadata = fetch_tushare_listing_dates(
        symbols, token=token, cache_dir=deps.DATA_CACHE_DIR
    )
    adapter = deps.TushareDailyAdapter(
        symbols=symbols,
        token=token,
        adjust="",
        cache_dir=deps.DATA_CACHE_DIR,
        fetch_limits=False,
        force_refresh=False,
    )
    raw_bars = adapter.daily_bars(start=start_date, end=end_date)
    deps._require_complete_universe(
        adapter.snapshot().metadata, context="中证500跨宇宙参考"
    )
    bars = filter_universe(
        raw_bars,
        None,
        UniverseRules(
            min_listing_days=FACTOR_MINING_MIN_LISTING_DAYS,
            exclude_st=False,
            exclude_paused=False,
            exclude_delisted=False,
        ),
        listing_dates=listing_dates,
        historical_universe=universe,
    )
    if not bars:
        raise RuntimeError("中证500参考样本经过历史成分和上市年龄过滤后为空")
    pit_bundle = deps._fetch_factor_mining_pit_bundle(
        symbols,
        min(bar.date for bar in bars),
        max(bar.date for bar in bars),
        token,
        force_refresh=False,
    )
    features, industries, pit_quality, _ = deps._pit_observations_for_bars(
        {"pit_data": pit_bundle}, bars
    )
    sessions = {bar.date for bar in bars}
    panel = build_factor_panel(
        bars,
        data_version=str(adapter.snapshot().version),
        observations=[item for item in features if item.as_of in sessions],
        industry_observations=[item for item in industries if item.as_of in sessions],
        pit_quality=pit_quality,
    )
    reference_version = hashlib.sha256(
        json.dumps(
            {
                "bars": adapter.snapshot().version,
                "members": history.get("data_version"),
                "listing_dates": listing_metadata.get("data_version"),
                "pit_data_hash": hashlib.sha256(
                    json.dumps(
                        pit_bundle, ensure_ascii=False, sort_keys=True, default=str
                    ).encode("utf-8")
                ).hexdigest()[:20],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    return (panel, reference_version)


def _factor_mining_cross_universe_reference(
    expressions: list[str],
    *,
    start_date: date,
    end_date: date,
    deps: MiningDataDependencies,
) -> dict[str, object]:
    """Report CSI 500 Rank IC as a reference; it never affects search or gates."""
    base: dict[str, object] = {
        "index_code": "000905.SH",
        "index_name": INDEX_UNIVERSE_NAMES.get("000905.SH", "中证500"),
        "role": "report_only_not_for_selection_or_acceptance",
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "significance_used_for_decision": False,
        "note": "跨宇宙 IC 仅供稳健性参考，不参与 GP 适应度、候选筛选、sealed 验收或入库门槛。",
    }
    normalized_expressions = list(
        dict.fromkeys(str(item).strip() for item in expressions if str(item).strip())
    )
    if not normalized_expressions:
        return {
            **base,
            "status": "not_needed",
            "reason": "本批次没有候选表达式",
            "data_version": None,
            "expressions": {},
        }
    try:
        panel, data_version = deps._factor_mining_comparison_panel(start_date, end_date)
    except Exception as error:
        LOGGER.exception("Cross-universe reference panel unavailable: %s", type(error).__name__, exc_info=None)
        return {
            **base,
            "status": "unavailable",
            "reason": type(error).__name__,
            "data_version": None,
            "expressions": {},
        }
    by_expression: dict[str, object] = {}
    for expression in normalized_expressions:
        try:
            evaluated = evaluate_expression(expression, panel)
            report = evaluate_factor_panel(
                panel,
                evaluated.values,
                start=start_date,
                end=end_date,
                n_tested=1,
                horizons=(1, 5, 10, 20),
                quantiles=10,
            )
            horizons = deps._object_mapping(report.get("horizons"))
            by_expression[expression] = {
                "status": "available",
                "horizons": {
                    str(horizon): {
                        "mean_rank_ic": values.get("mean_ic"),
                        "icir": values.get("icir"),
                        "observations": values.get("observations", 0),
                    }
                    for horizon, item in horizons.items()
                    for values in [deps._object_mapping(item)]
                    if values
                },
            }
        except Exception as error:
            LOGGER.exception("Cross-universe reference evaluation unavailable: %s", type(error).__name__, exc_info=None)
            by_expression[expression] = {
                "status": "unavailable",
                "reason": type(error).__name__,
                "horizons": {},
            }
    return {
        **base,
        "status": "available",
        "data_version": data_version,
        "expressions": by_expression,
    }


def _factor_mining_dataset(
    *, deps: MiningDataDependencies
) -> tuple[dict[str, object], list[Bar], list[Bar]]:
    if not os.getenv("TUSHARE_TOKEN", "").strip():
        raise RuntimeError("未配置 TUSHARE_TOKEN，因子挖掘要求真实 Tushare 日线")
    _, pipeline_key = deps._factor_mining_scope()
    sealed = deps.RESEARCH_LEDGER.get_sealed_test(
        "baseline", pipeline_key, include_bars=True
    )
    if sealed is None:
        frozen_range = mining_requested_range()
        end_date = date.fromisoformat(frozen_range[1]) if frozen_range else china_today()
        start_date = date.fromisoformat(frozen_range[0]) if frozen_range else end_date - timedelta(days=deps.FACTOR_MINING_HISTORY_DAYS)
        report_mining_progress("members", requested_start=start_date.isoformat(), requested_end=end_date.isoformat())
        membership_history = fetch_tushare_index_member_history(
            FACTOR_MINING_UNIVERSE_INDEX_CODE,
            start_date=start_date,
            end_date=end_date,
            token=os.getenv("TUSHARE_TOKEN"),
            cache_dir=deps.DATA_CACHE_DIR,
        )
        historical_universe = HistoricalUniverse.from_index_weight_history(
            membership_history
        )
        symbols = sorted(historical_universe.symbols_for_range(start_date, end_date))
        if not symbols:
            raise RuntimeError("沪深300历史快照在研究区间内没有成分股")
        report_mining_progress("listing", progress_current=0, progress_total=len(symbols))
        listing_dates, listing_metadata = fetch_tushare_listing_dates(
            symbols, token=os.getenv("TUSHARE_TOKEN"), cache_dir=deps.DATA_CACHE_DIR
        )
        adapter = deps.TushareDailyAdapter(
            symbols=symbols,
            token=os.getenv("TUSHARE_TOKEN"),
            adjust="",
            cache_dir=deps.DATA_CACHE_DIR,
            fetch_limits=False,
            force_refresh=True,
        )
        report_mining_progress("daily_bars", progress_current=0, progress_total=len(symbols))
        raw_bars = adapter.daily_bars(start=start_date, end=end_date)
        snapshot = adapter.snapshot()
        deps._require_complete_universe(snapshot.metadata, context="因子挖掘")
        if snapshot.metadata.get("data_source") != "tushare_live":
            raise RuntimeError(
                "因子挖掘要求强制刷新的 Tushare 实时日线；当前来源不是 tushare_live"
            )
        universe_payload = deps._factor_mining_universe_payload(
            membership_history,
            listing_dates,
            listing_metadata,
            symbols,
            {bar.symbol for bar in raw_bars},
            start_date,
            end_date,
        )
        fresh_bars = deps._apply_factor_mining_universe(raw_bars, universe_payload)
        universe_payload["sample_symbol_count"] = len(
            {bar.symbol for bar in fresh_bars}
        )
        bar_dates = sorted({bar.date for bar in fresh_bars})
        if not bar_dates:
            raise RuntimeError("因子挖掘没有可用于 PIT 对齐的交易日")
        report_mining_progress("pit_bundle", progress_current=0, progress_total=len(symbols))
        pit_bundle = deps._fetch_factor_mining_pit_bundle(
            symbols, min(bar_dates), max(bar_dates), os.getenv("TUSHARE_TOKEN", "")
        )
        pit_bundle["research_universe"] = universe_payload
        pit_quality = deps._factor_mining_pit_quality(pit_bundle, fresh_bars)
        report_mining_progress("snapshot", progress_current=len(symbols), progress_total=len(symbols))
        sealed = deps._ensure_sealed_snapshot(
            "baseline",
            pipeline_key,
            fresh_bars,
            snapshot,
            symbols=symbols,
            adjust="",
            sealed_days=deps.SEALED_TEST_DAYS,
            pit_data=pit_bundle,
            pit_metadata={
                "pit_quality": pit_quality,
                "sources": pit_bundle["source_metadata"],
                "research_universe": {
                    key: universe_payload[key]
                    for key in (
                        "schema_version",
                        "index_code",
                        "index_name",
                        "requested_start",
                        "requested_end",
                        "snapshot_start",
                        "snapshot_end",
                        "snapshot_count",
                        "member_data_version",
                        "listing_data_version",
                        "listing_data_stale",
                        "min_listing_days",
                        "candidate_symbol_count",
                        "sample_symbol_count",
                    )
                },
            },
        )
    sealed_id = deps._as_int(sealed.get("id") or 0)
    if sealed_id <= 0:
        raise RuntimeError("未能读取或创建共享的 Tushare 密封快照")
    metadata = deps._object_mapping(sealed.get("metadata"))
    if metadata.get("source") != "tushare" or metadata.get("data_source") not in {
        "tushare_live",
        "tushare_mixed",
    }:
        raise PermissionError("现有密封快照不是可信的 Tushare 数据，已阻止因子挖掘")
    sealed_bars = deps._bars_from_payload(sealed.get("bars", []))
    pit_data = deps._object_mapping(sealed.get("pit_data"))
    universe_payload = deps._object_mapping(pit_data.get("research_universe"))
    stored_universe = deps._object_mapping(metadata.get("research_universe"))
    if (
        not universe_payload
        or stored_universe.get("schema_version") != FACTOR_MINING_UNIVERSE_VERSION
    ):
        raise PermissionError(
            "当前 sealed 快照没有预注册沪深300宇宙与上市日期版本，需归档后新建研究周期"
        )
    if str(stored_universe.get("member_data_version") or "") != str(
        universe_payload.get("member_data_version") or ""
    ):
        raise PermissionError("sealed 快照的沪深300成员版本与数据包不一致")
    sealed_bars = deps._apply_factor_mining_universe(sealed_bars, universe_payload)
    training_bars = deps._research_bars(sealed_bars, sealed)
    if not training_bars:
        raise ValueError("密封快照不包含非密封训练区间")
    report_mining_progress("mining", progress_current=0, progress_total=None)
    return (sealed, sealed_bars, training_bars)
