from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, cast

from quant.api.http_security import BoundedHTTPServer
from quant.api.mining_jobs import MINING_JOB, MiningJobManager
from quant.api.resource_limits import get_resource_limits
from quant.errors import InputTypeError
from quant.mining.search_options import validate_search_options
from quant.time_utils import china_today

from .api.arena_service import arena_request, participant_identity, participant_request
from .api.audit_metadata import (
    AuditMetadataDependencies,
)
from .api.audit_metadata import (
    _average as _average_service,
)
from .api.audit_metadata import (
    _cache_writable as _cache_writable_service,
)
from .api.audit_metadata import (
    _current_git_revision as _current_git_revision_service,
)
from .api.audit_metadata import (
    _frontend_source_revision as _frontend_source_revision_service,
)
from .api.audit_metadata import (
    _record_research_experiment as _record_research_experiment_service,
)
from .api.audit_metadata import (
    _sample_std as _sample_std_service,
)
from .api.auth import Auth
from .api.auth_store import AuthStore
from .api.backtest_jobs import BacktestJobDependencies, BacktestJobManager
from .api.backtest_service import BacktestDependencies, run_backtest_request
from .api.brinson_report import strict_pit_brinson_report as _strict_pit_brinson_report
from .api.data_probe import probe_tushare_data
from .api.deep_formal_trials import formal_trial_request
from .api.deep_scheduler import DeepFormalScheduler
from .api.evolution_package import package_request
from .api.execution_reports import backtest_operational_reports
from .api.external_deep_import import external_deep_request
from .api.factor_panels import (
    factor_mining_pit_quality as _compute_factor_mining_pit_quality,
)
from .api.factor_panels import (
    factor_panel_correlation as _compute_factor_panel_correlation,
)
from .api.factor_panels import (
    factor_values_for_definition as _compute_factor_values_for_definition,
)
from .api.factor_panels import (
    pit_observations_for_bars as _compute_pit_observations_for_bars,
)
from .api.feature_jobs import FeatureJobDependencies, FeatureMaterializationJobManager
from .api.feature_materialization import (
    FeatureMaterializationDependencies,
    materialize_pit_features,
)
from .api.http_handler import HttpApiHandler, HttpHandlerDependencies
from .api.ic_scheduler import DailyICScheduler
from .api.market_routes import (
    MarketRouteDependencies,
)
from .api.mining_acceptance import (
    MiningAcceptanceDependencies,
)
from .api.mining_acceptance import (
    _accept_factor_mining_run as _accept_factor_mining_run_service,
)
from .api.mining_acceptance import (
    _promote_mined_factor as _promote_mined_factor_service,
)
from .api.mining_data import (
    MiningDataDependencies,
)
from .api.mining_data import (
    _factor_mining_comparison_panel as _factor_mining_comparison_panel_service,
)
from .api.mining_data import (
    _factor_mining_cross_universe_reference as _factor_mining_cross_universe_reference_service,
)
from .api.mining_data import (
    _factor_mining_dataset as _factor_mining_dataset_service,
)
from .api.mining_data import (
    _fetch_factor_mining_pit_bundle as _fetch_factor_mining_pit_bundle_service,
)
from .api.mining_portfolio import (
    cycle_policy,
    factor_portfolio_request,
    set_cycle_policy,
)
from .api.mining_research import (
    MiningResearchDependencies,
)
from .api.mining_research import (
    _evaluate_factor_expression_request as _evaluate_factor_expression_request_service,
)
from .api.mining_research import (
    _factor_mining_scope as _factor_mining_scope_service,
)
from .api.mining_research import (
    _factor_mining_status as _factor_mining_status_service,
)
from .api.mining_research import (
    _run_factor_mining_request as _run_factor_mining_request_service,
)
from .api.paper_governance import (
    PaperGovernanceDependencies,
)
from .api.paper_governance import (
    _factor_registry_status as _factor_registry_status_service,
)
from .api.paper_governance import (
    _paper_cycle_sealed_gate_payload as _paper_cycle_sealed_gate_payload_service,
)
from .api.paper_governance import (
    _paper_sealed_gate_status as _paper_sealed_gate_status_service,
)
from .api.paper_governance import (
    _sealed_gate_payload as _sealed_gate_payload_service,
)
from .api.paper_governance import (
    _validation_gate as _validation_gate_service,
)
from .api.paper_quality import (
    PaperQualityDependencies,
)
from .api.paper_quality import (
    _enrich_paper_state as _enrich_paper_state_service,
)
from .api.paper_quality import (
    _limits_data_quality as _limits_data_quality_service,
)
from .api.paper_quality import (
    _require_complete_universe as _require_complete_universe_service,
)
from .api.paper_quality import (
    _risk_limits_payload as _risk_limits_payload_service,
)
from .api.paper_routes import (
    PaperApiDependencies,
)
from .api.paper_status import (
    PaperStatusDependencies,
)
from .api.parsing import (
    MAX_RESEARCH_SYMBOLS,
)
from .api.parsing import (
    bounded_int as _bounded_int,
)
from .api.parsing import (
    normalize_paper_symbol as _normalize_paper_symbol,
)
from .api.parsing import (
    object_list as _object_list,
)
from .api.parsing import (
    object_mapping as _object_mapping,
)
from .api.parsing import (
    optional_date as _optional_date,
)
from .api.parsing import (
    symbols as _symbols,
)
from .api.parsing import (
    symbols_from_query as _symbols_from_query,
)
from .api.portfolio_routes import PortfolioApiDependencies
from .api.research_config import (
    dataset_metadata_with_universe as _dataset_metadata_with_universe_values,
)
from .api.research_config import (
    scoped_dataset_version as _scoped_dataset_version,
)
from .api.research_config import (
    sealed_scope as _sealed_scope,
)
from .api.research_mutations import (
    ResearchMutationDependencies,
)
from .api.research_paper import research_paper_request
from .api.research_routes import (
    ResearchRouteDependencies,
)
from .api.runtime_cache import RuntimeCaches
from .api.sealed_service import SealedValidationDependencies
from .api.sealed_snapshot import (
    SealedSnapshotDependencies,
)
from .api.sealed_snapshot import (
    _ensure_sealed_snapshot as _ensure_sealed_snapshot_service,
)
from .api.sealed_snapshot import (
    _factor_snapshot as _factor_snapshot_service,
)
from .api.sealed_snapshot import (
    _factor_snapshot_hash as _factor_snapshot_hash_service,
)
from .api.sealed_snapshot import (
    _research_bars as _research_bars_service,
)
from .api.sealed_snapshot import (
    _sealed_pipeline_key as _sealed_pipeline_key_service,
)
from .api.sealed_snapshot import (
    _sealed_result_passed as _sealed_result_passed_service,
)
from .api.sensitivity_service import SensitivityDependencies
from .api.serialization import (
    bar_to_payload as _bar_to_payload,
)
from .api.serialization import (
    bars_from_payload as _bars_from_payload,
)
from .api.serialization import (
    sealed_public_payload as _sealed_public_payload,
)
from .api.shared_sealed import publish_sealed_data
from .api.universe import (
    ResolvedBacktestUniverse,
    filter_backtest_universe,
    resolve_backtest_universe,
)
from .api.user_scheduler import UserPaperScheduler
from .api.user_scope import (
    CURRENT_USERNAME,
    ScopedStoreProxy,
    UserScopedStores,
)
from .api.user_settings import UserPaperSettings
from .api.workspace_factors import build_live_factor_rows
from .api.workspace_snapshot import (
    WorkspaceSnapshotDependencies,
)
from .api.workspace_snapshot import (
    _build_workspace_snapshot as _build_workspace_snapshot_service,
)
from .backtest.queueing import get_redis_backtest_queue, queue_mode, redis_queue_counts
from .data import (
    DEFAULT_PAPER_UNIVERSE_INDEX,
    DEFAULT_SYMBOLS,
    DEFAULT_UNIVERSE_NOTE,
    FACTOR_MINING_MIN_LISTING_DAYS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    FACTOR_MINING_UNIVERSE_NAME,
    INDEX_UNIVERSE_NAMES,
    TushareDailyAdapter,
    fetch_tushare_adjustment_health,
    fetch_tushare_fundamental_factors,
    fetch_tushare_index_history,
    fetch_tushare_index_member_history,
    fetch_tushare_index_members,
    fetch_tushare_indices,
    fetch_tushare_listing_dates,
    fetch_tushare_realtime_indices,
    fetch_tushare_statements,
    flatten_statement_rows,
    get_tushare_a_share_universe,
    get_tushare_stock_metadata,
    resolve_paper_universe,
    search_tushare_stocks,
)
from .data.adapters import _tushare_http_query
from .data.schema import Bar, FeatureObservation, PITIndustryObservation
from .execution import PaperScheduler, PaperStore, run_strategy_cycle
from .execution.service import _data_freshness
from .factors import FACTOR_PIPELINE_VERSION, FactorRegistryStore
from .factors.analysis import evaluate_factor_panel
from .factors.expression import (
    FactorPanel,
    Panel,
    build_factor_panel,
    evaluate_expression,
)
from .factors.store import FactorDefinition
from .features import FeatureStore
from .mining.universe import (
    apply_factor_mining_universe as _apply_factor_mining_universe,
)
from .mining.universe import (
    factor_mining_universe_payload as _factor_mining_universe_payload,
)
from .mining.universe import (
    validate_factor_mining_universe_request as _validate_factor_mining_universe_request,
)
from .numeric import as_float as _as_float
from .numeric import as_int as _as_int
from .operations import (
    backup_sqlite_databases,
    configure_service_logging,
)
from .portfolio import PAPER_RISK_LIMITS, check_order_limits
from .reports.performance import (
    build_excess_return_report,
)
from .research import ResearchLedger
from .strategies import (
    STRATEGY_PRESETS,
    build_strategy,
    get_strategy_preset,
    resolve_strategy_config,
)
from .validation import run_sealed_test, validate_strategy

DATA_CACHE_DIR = Path(
    os.getenv("A_PI_DATA_CACHE", Path(__file__).resolve().parents[1] / "data_cache")
)
PAPER_DB_PATH = Path(
    os.getenv(
        "A_PI_PAPER_DB", Path(__file__).resolve().parents[1] / "data" / "paper.sqlite3"
    )
)
FACTOR_DB_PATH = Path(
    os.getenv(
        "A_PI_FACTOR_DB",
        Path(__file__).resolve().parents[1] / "data" / "factors.sqlite3",
    )
)
RESEARCH_DB_PATH = Path(
    os.getenv(
        "A_PI_RESEARCH_DB",
        Path(__file__).resolve().parents[1] / "data" / "research.sqlite3",
    )
)
FEATURE_DB_PATH = Path(
    os.getenv(
        "A_PI_FEATURE_DB",
        Path(__file__).resolve().parents[1] / "data" / "features.sqlite3",
    )
)
LOG_PATH = Path(
    os.getenv(
        "A_PI_LOG_PATH", Path(__file__).resolve().parents[1] / "data" / "a_pi.log"
    )
)
BACKUP_DIR = Path(
    os.getenv(
        "A_PI_BACKUP_DIR", Path(__file__).resolve().parents[1] / "data" / "backups"
    )
)
AUTH_DATA_ROOT = Path(
    os.getenv("A_PI_DATA_ROOT", str(Path(__file__).resolve().parents[1] / "data"))
)
AUTH_STORE = AuthStore(AUTH_DATA_ROOT / "auth" / "users.sqlite3")
AUTH = Auth(AUTH_STORE, AUTH_DATA_ROOT / "auth" / "auth_secret.key")
USER_STORES = UserScopedStores(AUTH_DATA_ROOT)
DEEP_SCHEDULER: DeepFormalScheduler | None = None
PAPER_STORE = cast(PaperStore, ScopedStoreProxy(USER_STORES, "paper_store"))
FACTOR_STORE = cast(FactorRegistryStore, ScopedStoreProxy(USER_STORES, "factor_store"))
RESEARCH_LEDGER = cast(ResearchLedger, ScopedStoreProxy(USER_STORES, "ledger"))
FEATURE_STORE = FeatureStore(FEATURE_DB_PATH)
PAPER_SCHEDULER: UserPaperScheduler | None = None


def _current_paper_scheduler() -> PaperScheduler | None:
    username = CURRENT_USERNAME.get()
    return PAPER_SCHEDULER.for_user(username) if PAPER_SCHEDULER and username else None


BACKTEST_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="a-pi-backtest"
)
BACKTEST_QUEUE_LIMIT = max(2, int(os.getenv("A_PI_BACKTEST_QUEUE_LIMIT", "4")))
MINING_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="a-pi-mining")
FEATURE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="a-pi-feature")
FEATURE_QUEUE_LIMIT = max(1, int(os.getenv("A_PI_FEATURE_QUEUE_LIMIT", "2")))
MINING_JOBS: MiningJobManager | None = None
MINING_JOBS_LOCK = threading.Lock()
DATA_PROBE_TTL_SECONDS = 300


from .api.errors import ApiConflictError

CHINA_TIMEZONE = timezone(timedelta(hours=8))
RUNTIME_CACHES = RuntimeCaches()
SEALED_TEST_DAYS = max(30, int(os.getenv("A_PI_SEALED_TEST_DAYS", "60")))
SEALED_MIN_OBSERVATIONS = max(10, int(os.getenv("A_PI_SEALED_MIN_OBSERVATIONS", "20")))
FACTOR_MINING_HISTORY_DAYS = 365 * 2
LAST_BACKUP: dict[str, object] | None = None
LOGGER = logging.getLogger("a_pi.api")


def _backtest_queue_status() -> dict[str, object]:
    mode = queue_mode()
    if mode == "local":
        active = sum(
            item.get("status") in {"queued", "running"}
            for item in BACKTEST_JOBS.values()
        )
        return {
            "mode": "local",
            "status": "available",
            "limit": BACKTEST_QUEUE_LIMIT,
            "active": active,
        }
    queue, connection = get_redis_backtest_queue()
    counts = redis_queue_counts(queue, connection)
    return {
        "mode": "redis",
        "name": queue.name,
        "status": "available",
        "limit": BACKTEST_QUEUE_LIMIT,
        "queued": counts["queued"],
        "running": counts["running"],
        "active": counts["queued"] + counts["running"],
    }


def _paper_api_dependencies() -> PaperApiDependencies:
    return PaperApiDependencies(
        store=PAPER_STORE,
        cache_dir=DATA_CACHE_DIR,
        timezone=CHINA_TIMEZONE,
        daily_adapter=TushareDailyAdapter,
        token_provider=lambda: os.getenv("TUSHARE_TOKEN"),
        normalize_symbol=_normalize_paper_symbol,
        bounded_int=_bounded_int,
        limits_data_quality=_limits_data_quality,
        data_freshness=_data_freshness,
        stock_metadata=get_tushare_stock_metadata,
        check_order_limits=check_order_limits,
        risk_limits=PAPER_RISK_LIMITS,
        enrich_state=_enrich_paper_state,
        risk_limits_payload=_risk_limits_payload,
        scheduler=_current_paper_scheduler,
        default_universe_index=DEFAULT_PAPER_UNIVERSE_INDEX,
        resolve_universe=resolve_paper_universe,
        symbols=_symbols,
        get_strategy_preset=get_strategy_preset,
        resolve_strategy_config=resolve_strategy_config,
        factor_store=FACTOR_STORE,
        sealed_gate=_paper_cycle_sealed_gate_payload,
        run_strategy_cycle=run_strategy_cycle,
    )


def _paper_status_dependencies() -> PaperStatusDependencies:
    return PaperStatusDependencies(
        store=PAPER_STORE,
        scheduler=_current_paper_scheduler,
        timezone=CHINA_TIMEZONE,
        logger=LOGGER,
        object_mapping=_object_mapping,
        optional_date=_optional_date,
        enrich_state=_enrich_paper_state,
        risk_limits_payload=_risk_limits_payload,
        factor_registry_status=_factor_registry_status,
        paper_sealed_gate_status=_paper_sealed_gate_status,
        sealed_public_payload=_sealed_public_payload,
        ledger_status=RESEARCH_LEDGER.status,
    )


def _portfolio_api_dependencies() -> PortfolioApiDependencies:
    return PortfolioApiDependencies(
        daily_adapter=TushareDailyAdapter,
        cache_dir=DATA_CACHE_DIR,
        token_provider=lambda: os.getenv("TUSHARE_TOKEN"),
        pit_panel_path=lambda: os.getenv("A_PI_PIT_BRINSON_PANEL", ""),
        symbols=_symbols,
        shanghai_timezone=CHINA_TIMEZONE,
    )


def _market_api_dependencies() -> MarketRouteDependencies:
    return MarketRouteDependencies(
        token=lambda: os.getenv("TUSHARE_TOKEN"),
        cache_dir=DATA_CACHE_DIR,
        default_symbols=DEFAULT_SYMBOLS,
        default_universe_note=DEFAULT_UNIVERSE_NOTE,
        index_universe_names=INDEX_UNIVERSE_NAMES,
        max_research_symbols=MAX_RESEARCH_SYMBOLS,
        bounded_int=_bounded_int,
        optional_date=_optional_date,
        symbols=_symbols,
        normalize_symbol=_normalize_paper_symbol,
        search_stocks=search_tushare_stocks,
        adjustment_health=fetch_tushare_adjustment_health,
        stock_metadata=get_tushare_stock_metadata,
        fundamental_factors=fetch_tushare_fundamental_factors,
        financial_statements=fetch_tushare_statements,
        flatten_statements=flatten_statement_rows,
        index_members=fetch_tushare_index_members,
        a_share_universe=get_tushare_a_share_universe,
        indices=fetch_tushare_indices,
        realtime_indices=fetch_tushare_realtime_indices,
    )


def _research_api_dependencies() -> ResearchRouteDependencies:
    return ResearchRouteDependencies(
        ledger=RESEARCH_LEDGER,
        factor_store=FACTOR_STORE,
        strategy_presets=STRATEGY_PRESETS,
        default_symbols=DEFAULT_SYMBOLS,
        token=lambda: os.getenv("TUSHARE_TOKEN"),
        cache_dir=DATA_CACHE_DIR,
        optional_date=_optional_date,
        as_int=_as_int,
        object_mapping=_object_mapping,
        object_list=_object_list,
        symbols_from_query=_symbols_from_query,
        sealed_scope=_sealed_scope,
        sealed_pipeline_key=_sealed_pipeline_key,
        sealed_public_payload=_sealed_public_payload,
        factor_mining_status=_factor_mining_status,
        fetch_benchmark=fetch_tushare_index_history,
        build_excess_report=build_excess_return_report,
    )


def _research_mutation_dependencies() -> ResearchMutationDependencies:
    return ResearchMutationDependencies(
        ledger=RESEARCH_LEDGER,
        factors=FACTOR_STORE,
        factor_lock=lambda: _factor_registry_status(
            PAPER_STORE.cycle_summary().get("equity_observations", 0)
        ),
        record_experiment=_record_research_experiment,
        factor_pipeline_version=FACTOR_PIPELINE_VERSION,
    )


def _sensitivity_dependencies() -> SensitivityDependencies:
    return SensitivityDependencies(
        require_strategy=_require_strategy_dependencies,
        resolve_universe=_resolve_backtest_universe,
        filter_universe=_filter_backtest_universe,
        make_adapter=TushareDailyAdapter,
        require_complete_universe=lambda metadata: _require_complete_universe(
            metadata, context="参数敏感性扫描"
        ),
        sealed_scope=_sealed_scope,
        sealed_pipeline_key=_sealed_pipeline_key,
        ensure_sealed_snapshot=_ensure_sealed_snapshot,
        research_bars=_research_bars,
        record_experiment=_record_research_experiment,
        as_float=_as_float,
        scoped_dataset_version=_scoped_dataset_version,
        validation_gate=_validation_gate,
        dataset_metadata=_dataset_metadata_with_universe,
        ledger=RESEARCH_LEDGER,
        factors=FACTOR_STORE,
        token=os.getenv("TUSHARE_TOKEN"),
        cache_dir=DATA_CACHE_DIR,
        factor_pipeline_version=FACTOR_PIPELINE_VERSION,
        sealed_test_days=SEALED_TEST_DAYS,
    )


def _sealed_validation_dependencies() -> SealedValidationDependencies:
    return SealedValidationDependencies(
        CHINA_TIMEZONE=CHINA_TIMEZONE,
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        FACTOR_STORE=FACTOR_STORE,
        FEATURE_STORE=FEATURE_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        SEALED_MIN_OBSERVATIONS=SEALED_MIN_OBSERVATIONS,
        SEALED_TEST_DAYS=SEALED_TEST_DAYS,
        TushareDailyAdapter=TushareDailyAdapter,
        _as_float=_as_float,
        _as_int=_as_int,
        _bars_from_payload=_bars_from_payload,
        _bounded_int=_bounded_int,
        _ensure_sealed_snapshot=_ensure_sealed_snapshot,
        _factor_snapshot=_factor_snapshot,
        _factor_snapshot_hash=_factor_snapshot_hash,
        _filter_backtest_universe=_filter_backtest_universe,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _require_complete_universe=_require_complete_universe,
        _require_strategy_dependencies=_require_strategy_dependencies,
        _resolve_backtest_universe=_resolve_backtest_universe,
        _sealed_pipeline_key=_sealed_pipeline_key,
        _sealed_public_payload=_sealed_public_payload,
        _sealed_scope=_sealed_scope,
        _symbols=_symbols,
        build_strategy=build_strategy,
        run_sealed_test=run_sealed_test,
    )


def _backtest_dependencies() -> BacktestDependencies:
    return BacktestDependencies(
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        FACTOR_STORE=FACTOR_STORE,
        FEATURE_STORE=FEATURE_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        SEALED_TEST_DAYS=SEALED_TEST_DAYS,
        TushareDailyAdapter=TushareDailyAdapter,
        _as_float=_as_float,
        _backtest_operational_reports=_backtest_operational_reports,
        _bounded_int=_bounded_int,
        _dataset_metadata_with_universe=_dataset_metadata_with_universe,
        _ensure_sealed_snapshot=_ensure_sealed_snapshot,
        _filter_backtest_universe=_filter_backtest_universe,
        _limits_data_quality=_limits_data_quality,
        _optional_date=_optional_date,
        _record_research_experiment=_record_research_experiment,
        _report_backtest_progress=_report_backtest_progress,
        _require_complete_universe=_require_complete_universe,
        _require_strategy_dependencies=_require_strategy_dependencies,
        _research_bars=_research_bars,
        _resolve_backtest_universe=_resolve_backtest_universe,
        _scoped_dataset_version=_scoped_dataset_version,
        _sealed_pipeline_key=_sealed_pipeline_key,
        _sealed_scope=_sealed_scope,
        _strict_pit_brinson_report=_strict_pit_brinson_report,
        _validation_gate=_validation_gate,
        build_strategy=build_strategy,
        validate_strategy=validate_strategy,
    )


def _mining_data_dependencies() -> MiningDataDependencies:
    return MiningDataDependencies(
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        FACTOR_MINING_HISTORY_DAYS=FACTOR_MINING_HISTORY_DAYS,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        SEALED_TEST_DAYS=SEALED_TEST_DAYS,
        TushareDailyAdapter=TushareDailyAdapter,
        _apply_factor_mining_universe=_apply_factor_mining_universe,
        _as_int=_as_int,
        _bars_from_payload=_bars_from_payload,
        _ensure_sealed_snapshot=_ensure_sealed_snapshot,
        _factor_mining_comparison_panel=_factor_mining_comparison_panel,
        _factor_mining_pit_quality=_factor_mining_pit_quality,
        _factor_mining_scope=_factor_mining_scope,
        _factor_mining_universe_payload=_factor_mining_universe_payload,
        _fetch_factor_mining_pit_bundle=_fetch_factor_mining_pit_bundle,
        _object_mapping=_object_mapping,
        _pit_observations_for_bars=_pit_observations_for_bars,
        _require_complete_universe=_require_complete_universe,
        _research_bars=_research_bars,
    )


def _mining_research_dependencies() -> MiningResearchDependencies:
    return MiningResearchDependencies(
        FACTOR_STORE=FACTOR_STORE,
        PAPER_STORE=PAPER_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        _as_int=_as_int,
        _bounded_int=_bounded_int,
        _factor_mining_cross_universe_reference=_factor_mining_cross_universe_reference,
        _factor_mining_dataset=_factor_mining_dataset,
        _factor_mining_scope=_factor_mining_scope,
        _factor_registry_status=_factor_registry_status,
        _factor_values_for_definition=_factor_values_for_definition,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _pit_observations_for_bars=_pit_observations_for_bars,
        _sealed_pipeline_key=_sealed_pipeline_key,
        _sealed_public_payload=_sealed_public_payload,
        _validate_factor_mining_universe_request=_validate_factor_mining_universe_request,
    )


def _mining_acceptance_dependencies() -> MiningAcceptanceDependencies:
    return MiningAcceptanceDependencies(
        build_factor_panel=build_factor_panel,
        evaluate_expression=evaluate_expression,
        evaluate_factor_panel=evaluate_factor_panel,
        ApiConflictError=ApiConflictError,
        FACTOR_STORE=FACTOR_STORE,
        PAPER_STORE=PAPER_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        _as_float=_as_float,
        _as_int=_as_int,
        _bars_from_payload=_bars_from_payload,
        _factor_mining_scope=_factor_mining_scope,
        _factor_panel_correlation=_factor_panel_correlation,
        _factor_registry_status=_factor_registry_status,
        _factor_values_for_definition=_factor_values_for_definition,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _pit_observations_for_bars=_pit_observations_for_bars,
        _record_research_experiment=_record_research_experiment,
    )


def _sealed_snapshot_dependencies() -> SealedSnapshotDependencies:
    return SealedSnapshotDependencies(
        FACTOR_STORE=FACTOR_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        _bar_to_payload=_bar_to_payload,
        _factor_snapshot=_factor_snapshot,
        _factor_snapshot_hash=_factor_snapshot_hash,
        _object_mapping=_object_mapping,
    )


def _paper_governance_dependencies() -> PaperGovernanceDependencies:
    return PaperGovernanceDependencies(
        PAPER_SCHEDULER=_current_paper_scheduler(),
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        _as_int=_as_int,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _paper_sealed_gate_status=_paper_sealed_gate_status,
        _sealed_gate_payload=_sealed_gate_payload,
        _sealed_pipeline_key=_sealed_pipeline_key,
        _sealed_public_payload=_sealed_public_payload,
        _sealed_result_passed=_sealed_result_passed,
        _sealed_scope=_sealed_scope,
    )


def _workspace_snapshot_dependencies() -> WorkspaceSnapshotDependencies:
    return WorkspaceSnapshotDependencies(
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        FACTOR_STORE=FACTOR_STORE,
        PAPER_SCHEDULER=_current_paper_scheduler(),
        PAPER_STORE=PAPER_STORE,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        SEALED_TEST_DAYS=SEALED_TEST_DAYS,
        TushareDailyAdapter=TushareDailyAdapter,
        _as_float=_as_float,
        _average=_average,
        _build_live_factor_rows=_build_live_factor_rows,
        _data_freshness=_data_freshness,
        _enrich_paper_state=_enrich_paper_state,
        _ensure_sealed_snapshot=_ensure_sealed_snapshot,
        _limits_data_quality=_limits_data_quality,
        _object_list=_object_list,
        _require_complete_universe=_require_complete_universe,
        _research_bars=_research_bars,
        _risk_limits_payload=_risk_limits_payload,
        _sealed_pipeline_key=_sealed_pipeline_key,
        _sealed_scope=_sealed_scope,
        _validation_gate=_validation_gate,
    )


def _paper_quality_dependencies() -> PaperQualityDependencies:
    return PaperQualityDependencies(
        stock_metadata=get_tushare_stock_metadata,
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        PAPER_STORE=PAPER_STORE,
        _as_float=_as_float,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _risk_limits_payload=_risk_limits_payload,
    )


def _audit_metadata_dependencies() -> AuditMetadataDependencies:
    return AuditMetadataDependencies(
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        __file__=__file__,
        _current_git_revision=_current_git_revision,
        _frontend_source_revision=_frontend_source_revision,
    )


def _http_dependencies() -> HttpHandlerDependencies:
    return HttpHandlerDependencies(
        BACKTEST_JOBS=BACKTEST_JOBS,
        BACKTEST_JOBS_LOCK=BACKTEST_JOBS_LOCK,
        BACKTEST_QUEUE_LIMIT=BACKTEST_QUEUE_LIMIT,
        BACKUP_DIR=USER_STORES.current().paper_store.path.parent / "backups",
        DATA_CACHE_DIR=DATA_CACHE_DIR,
        FACTOR_DB_PATH=USER_STORES.current().factor_store.path,
        FEATURE_DB_PATH=FEATURE_DB_PATH,
        FEATURE_STORE=FEATURE_STORE,
        LAST_BACKUP=None,
        LOGGER=LOGGER,
        LOG_PATH=LOG_PATH,
        PAPER_DB_PATH=USER_STORES.current().paper_store.path,
        PAPER_STORE=PAPER_STORE,
        RESEARCH_DB_PATH=USER_STORES.current().ledger.path,
        RESEARCH_LEDGER=RESEARCH_LEDGER,
        _accept_factor_mining_run=_accept_factor_mining_run,
        _as_float=_as_float,
        _backtest_dependencies=_backtest_dependencies,
        _backtest_job_view=_backtest_job_view,
        _backtest_queue_status=_backtest_queue_status,
        _cache_writable=_cache_writable,
        _data_probe_status=_data_probe_status,
        _evaluate_factor_expression_request=_evaluate_factor_expression_request,
        _factor_mining_status=_factor_mining_status,
        _market_api_dependencies=_market_api_dependencies,
        _materialize_pit_feature_request=_materialize_pit_feature_request,
        _object_list=_object_list,
        _object_mapping=_object_mapping,
        _paper_api_dependencies=_paper_api_dependencies,
        _paper_status_dependencies=_paper_status_dependencies,
        _portfolio_api_dependencies=_portfolio_api_dependencies,
        _promote_mined_factor=_promote_mined_factor,
        _research_api_dependencies=_research_api_dependencies,
        _research_mutation_dependencies=_research_mutation_dependencies,
        _run_factor_mining_request=_run_factor_mining_request,
        _sealed_validation_dependencies=_sealed_validation_dependencies,
        _sensitivity_dependencies=_sensitivity_dependencies,
        _submit_backtest_job=_submit_backtest_job,
        _workspace_catalog=_workspace_catalog,
        _workspace_response=_workspace_response,
        _research_paper_request=lambda action, payload=None: research_paper_request(
            action, USER_STORES, PAPER_SCHEDULER, payload
        ),
        _cancel_factor_mining_job=_cancel_factor_mining_job,
        _external_deep_request=_external_deep_request,
        _evolution_package_request=_evolution_package_request,
        _arena_request=_arena_request,
        _arena_participant_request=_arena_participant_request,
        _mining_portfolio_request=_mining_portfolio_request,
        _submit_feature_materialize_job=_submit_feature_materialize_job,
        _get_feature_materialize_job=_get_feature_materialize_job,
        _cancel_feature_materialize_job=_cancel_feature_materialize_job,
    )


def _require_strategy_dependencies(strategy_name: str) -> None:
    if strategy_name == "lightgbm" and importlib.util.find_spec("lightgbm") is None:
        raise ValueError(
            "LightGBM 尚未安装；在项目虚拟环境执行 `pip install -e .[ml]` 后再运行"
        )


def _probe_tushare_data() -> dict[str, object]:
    return probe_tushare_data(query=_tushare_http_query, china_timezone=CHINA_TIMEZONE)


def _data_probe_status() -> dict[str, object]:
    return RUNTIME_CACHES.probe.status(_probe_tushare_data)


def _workspace_response() -> tuple[int, dict[str, object]]:
    return USER_STORES.current().workspace_cache.response(_build_workspace_snapshot)


def _backup_after_paper_cycle(_result: dict[str, object]) -> None:
    stores = USER_STORES.current()
    backup_sqlite_databases(
        (stores.paper_store.path, stores.factor_store.path, stores.ledger.path),
        stores.paper_store.path.parent / "backups",
    )


def _workspace_catalog() -> dict[str, object]:
    """Return the workspaces actually configured for this service.

    The current deployment is intentionally single-workspace. Keeping this
    registry behind an API prevents the sidebar selector from presenting
    fabricated workspaces while leaving room for a future multi-workspace
    deployment.
    """

    workspace_id = (
        os.getenv("A_PI_WORKSPACE_ID", "research").strip().lower() or "research"
    )
    workspace_name = (
        os.getenv("A_PI_WORKSPACE_NAME", "研究工作区").strip() or "研究工作区"
    )
    workspace = {
        "id": workspace_id,
        "name": workspace_name,
        "description": "真实 Tushare 数据研究、回测与纸面交易",
        "status": "active",
        "active": True,
        "data_source": "tushare",
        "capabilities": [
            "factor_research",
            "backtest",
            "risk_control",
            "paper_trading",
        ],
    }
    return {
        "ok": True,
        "source": "a_pi_workspace_registry",
        "current_id": workspace_id,
        "can_switch": False,
        "workspaces": [workspace],
        "message": "当前服务仅配置一个研究工作区"
        if workspace_id == "research"
        else "当前服务仅配置一个工作区",
    }


def _backtest_job_view(job: dict[str, object]) -> dict[str, object]:
    return BACKTEST_JOB_MANAGER.view(job)


def _update_backtest_job(job_id: str, **changes: object) -> None:
    BACKTEST_JOB_MANAGER.update(job_id, **changes)


def _report_backtest_progress(progress: int, message: str) -> None:
    BACKTEST_JOB_MANAGER.report_progress(progress, message)


def _execute_backtest_payload(payload: dict[str, Any]) -> tuple[int, dict[str, object]]:
    username = CURRENT_USERNAME.get()
    user = AUTH_STORE.get_by_username(username) if username else None
    if not user or user.disabled:
        return 401, {"ok": False, "error": "unauthorized"}
    return run_backtest_request(payload, _backtest_dependencies())


def _run_backtest_job(job_id: str, payload: dict[str, object]) -> None:
    BACKTEST_JOB_MANAGER.run(job_id, payload)


def _submit_backtest_job(payload: dict[str, object]) -> str:
    username = CURRENT_USERNAME.get()
    if username is None:
        raise RuntimeError("authenticated owner required")
    with BACKTEST_JOBS_LOCK:
        return BACKTEST_JOB_MANAGER.submit({**payload, "_auth_owner": username})


BACKTEST_JOB_MANAGER = BacktestJobManager(
    BacktestJobDependencies(
        executor=BACKTEST_EXECUTOR,
        queue_limit=BACKTEST_QUEUE_LIMIT,
        ledger=lambda: RESEARCH_LEDGER,
        queue_mode=queue_mode,
        redis_queue=get_redis_backtest_queue,
        redis_counts=redis_queue_counts,
        execute=_execute_backtest_payload,
        remote_worker=_run_backtest_job,
    )
)
BACKTEST_JOBS = BACKTEST_JOB_MANAGER.jobs
BACKTEST_JOBS_LOCK = BACKTEST_JOB_MANAGER.lock


def _execute_feature_materialize_job(
    payload: dict[str, object],
    on_progress: object,
    check_cancelled: object,
) -> dict[str, object]:
    return materialize_pit_features(
        payload,
        FeatureMaterializationDependencies(
            today=lambda: datetime.now(CHINA_TIMEZONE).date(),
            resolve_universe=_resolve_backtest_universe,
            filter_universe=_filter_backtest_universe,
            make_adapter=TushareDailyAdapter,
            require_complete_universe=lambda metadata: _require_complete_universe(
                metadata, context="PIT特征库预热"
            ),
            data_probe=_data_probe_status,
            scoped_dataset_version=_scoped_dataset_version,
            feature_store=FEATURE_STORE,
            max_symbols=MAX_RESEARCH_SYMBOLS,
            index_code=FACTOR_MINING_UNIVERSE_INDEX_CODE,
            cache_dir=DATA_CACHE_DIR,
            token=os.getenv("TUSHARE_TOKEN"),
        ),
        on_progress=on_progress if callable(on_progress) else None,
        check_cancelled=check_cancelled if callable(check_cancelled) else None,
    )


FEATURE_JOB_MANAGER = FeatureMaterializationJobManager(
    FeatureJobDependencies(
        executor=FEATURE_EXECUTOR,
        queue_limit=FEATURE_QUEUE_LIMIT,
        execute=_execute_feature_materialize_job,
    )
)


def _submit_feature_materialize_job(payload: dict[str, object]) -> dict[str, object]:
    username = CURRENT_USERNAME.get()
    if not username:
        raise RuntimeError("特征预热需要登录")
    return FEATURE_JOB_MANAGER.submit({**payload, "_auth_owner": username})


def _get_feature_materialize_job(job_id: str) -> dict[str, object] | None:
    return FEATURE_JOB_MANAGER.get(job_id, owner=CURRENT_USERNAME.get())


def _cancel_feature_materialize_job(job_id: str) -> bool:
    return FEATURE_JOB_MANAGER.cancel(job_id, owner=CURRENT_USERNAME.get())



class ApiHandler(HttpApiHandler):
    def _auth(self) -> Auth:
        return AUTH

    def _services(self) -> HttpHandlerDependencies:
        return _http_dependencies()

    def _participant_identity_callback(self, token: str) -> dict:
        return participant_identity(AUTH, token)


def _record_research_experiment(
    strategy_version: str,
    data_version: str,
    parameters: dict[str, object],
    status: str,
    notes: str,
) -> int:
    return _record_research_experiment_service(
        strategy_version,
        data_version,
        parameters,
        status,
        notes,
        deps=_audit_metadata_dependencies(),
    )


def _current_git_revision() -> str:
    return _current_git_revision_service(deps=_audit_metadata_dependencies())


def _frontend_source_revision(site_dist: Path | None = None) -> str:
    return _frontend_source_revision_service(
        site_dist, deps=_audit_metadata_dependencies()
    )


def _backtest_operational_reports(
    result: object,
    bars: list[Bar],
    *,
    max_participation: float,
) -> dict[str, dict[str, object]]:
    """Inject application data access into the isolated report module."""

    return backtest_operational_reports(
        result,
        bars,
        max_participation=max_participation,
        stock_metadata_lookup=get_tushare_stock_metadata,
        cache_dir=str(DATA_CACHE_DIR),
    )


def _materialize_pit_feature_request(payload: dict[str, object]) -> dict[str, object]:
    return materialize_pit_features(
        payload,
        FeatureMaterializationDependencies(
            today=lambda: datetime.now(CHINA_TIMEZONE).date(),
            resolve_universe=_resolve_backtest_universe,
            filter_universe=_filter_backtest_universe,
            make_adapter=TushareDailyAdapter,
            require_complete_universe=lambda metadata: _require_complete_universe(
                metadata, context="PIT特征库预热"
            ),
            data_probe=_data_probe_status,
            scoped_dataset_version=_scoped_dataset_version,
            feature_store=FEATURE_STORE,
            max_symbols=MAX_RESEARCH_SYMBOLS,
            index_code=FACTOR_MINING_UNIVERSE_INDEX_CODE,
            cache_dir=DATA_CACHE_DIR,
            token=os.getenv("TUSHARE_TOKEN"),
        ),
    )


def _resolve_backtest_universe(
    payload: Mapping[str, Any],
    start_date: date | None,
    end_date: date | None,
    *,
    force_membership_refresh: bool = False,
) -> ResolvedBacktestUniverse:
    """Inject data-source dependencies into the isolated universe module."""

    return resolve_backtest_universe(
        payload,
        start_date,
        end_date,
        force_membership_refresh=force_membership_refresh,
        default_symbols=DEFAULT_SYMBOLS,
        index_code=FACTOR_MINING_UNIVERSE_INDEX_CODE,
        index_name=FACTOR_MINING_UNIVERSE_NAME,
        min_listing_days=FACTOR_MINING_MIN_LISTING_DAYS,
        max_symbols=MAX_RESEARCH_SYMBOLS,
        token=os.getenv("TUSHARE_TOKEN", "").strip(),
        cache_dir=str(DATA_CACHE_DIR),
        fetch_membership=fetch_tushare_index_member_history,
        fetch_listing_dates=fetch_tushare_listing_dates,
    )


def _filter_backtest_universe(
    bars: list[Bar], universe: ResolvedBacktestUniverse
) -> list[Bar]:
    return filter_backtest_universe(
        bars,
        universe,
        min_listing_days=FACTOR_MINING_MIN_LISTING_DAYS,
    )


def _dataset_metadata_with_universe(
    source_metadata: Mapping[str, object], universe: Mapping[str, object]
) -> dict[str, object]:
    universe_metadata = universe.get("metadata")
    if not isinstance(universe_metadata, dict):
        raise InputTypeError("研究股票池缺少可审计元数据")
    return _dataset_metadata_with_universe_values(source_metadata, universe_metadata)


def _sealed_pipeline_key(scope: str) -> str:
    return _sealed_pipeline_key_service(scope, deps=_sealed_snapshot_dependencies())


def _ensure_sealed_snapshot(
    strategy_name: str,
    pipeline_key: str,
    bars: list[Bar],
    snapshot: object,
    *,
    symbols: list[str],
    adjust: str,
    sealed_days: int,
    pit_data: dict[str, object] | None = None,
    pit_metadata: dict[str, object] | None = None,
    additional_parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    sealed = _ensure_sealed_snapshot_service(
        strategy_name,
        pipeline_key,
        bars,
        snapshot,
        symbols=symbols,
        adjust=adjust,
        sealed_days=sealed_days,
        pit_data=pit_data,
        pit_metadata=pit_metadata,
        additional_parameters=additional_parameters,
        deps=_sealed_snapshot_dependencies(),
    )
    publish_sealed_data(AUTH_DATA_ROOT, sealed)
    return sealed


def _research_bars(bars: list[Bar], sealed: dict[str, object]) -> list[Bar]:
    return _research_bars_service(bars, sealed, deps=_sealed_snapshot_dependencies())


def _fetch_factor_mining_pit_bundle(
    symbols: list[str],
    start_date: date,
    end_date: date,
    token: str,
    *,
    force_refresh: bool = True,
) -> dict[str, object]:
    return _fetch_factor_mining_pit_bundle_service(
        symbols,
        start_date,
        end_date,
        token,
        force_refresh=force_refresh,
        deps=_mining_data_dependencies(),
    )


def _factor_mining_comparison_panel(
    start_date: date, end_date: date
) -> tuple[FactorPanel, str]:
    return _factor_mining_comparison_panel_service(
        start_date, end_date, deps=_mining_data_dependencies()
    )


def _factor_mining_cross_universe_reference(
    expressions: list[str], *, start_date: date, end_date: date
) -> dict[str, object]:
    return _factor_mining_cross_universe_reference_service(
        expressions,
        start_date=start_date,
        end_date=end_date,
        deps=_mining_data_dependencies(),
    )


def _factor_mining_pit_quality(
    pit_data: dict[str, object], bars: list[Bar]
) -> dict[str, object]:
    return _compute_factor_mining_pit_quality(pit_data, bars)


def _pit_observations_for_bars(
    sealed: dict[str, object], bars: list[Bar]
) -> tuple[
    list[FeatureObservation],
    list[PITIndustryObservation],
    dict[str, str],
    dict[str, object],
]:
    return _compute_pit_observations_for_bars(sealed, bars)


def _factor_mining_scope() -> tuple[str, str]:
    return _factor_mining_scope_service(deps=_mining_research_dependencies())


def _factor_mining_status() -> dict[str, object]:
    result = _factor_mining_status_service(deps=_mining_research_dependencies())
    _, pipeline = _factor_mining_scope()
    latest = _mining_jobs().latest(_mining_owner(), pipeline)
    if latest and latest["status"] == "interrupted":
        for run in RESEARCH_LEDGER.factor_mining_runs(limit=100):
            if (
                run["status"] == "evaluation_in_progress"
                and _object_mapping(run.get("result")).get("job_id") == latest["job_id"]
            ):
                RESEARCH_LEDGER.update_factor_mining_run(
                    str(run["run_id"]),
                    {
                        **_object_mapping(run.get("result")),
                        "error": "worker_interrupted",
                        "candidates": [],
                    },
                    status="evaluation_failed",
                )
        result["runs"] = RESEARCH_LEDGER.factor_mining_runs(limit=20)
    result["active_job"] = (
        latest
        if latest and latest["status"] in {"queued", "running", "cancelling"}
        else None
    )
    result["latest_job"] = latest
    public_seal = _object_mapping(result.get("sealed_test"))
    result["dataset_status"] = {
        "sealed_ready": bool(public_seal.get("sealed")),
        "requested_start": latest.get("requested_start") if latest else None,
        "requested_end": latest.get("requested_end") if latest else None,
    }
    return result


def _mining_owner() -> str:
    owner = CURRENT_USERNAME.get()
    if not owner:
        raise PermissionError("authenticated user context is required")
    return owner


def _mining_jobs() -> MiningJobManager:
    global MINING_JOBS
    with MINING_JOBS_LOCK:
        if MINING_JOBS is None:
            MINING_JOBS = MiningJobManager(
                AUTH_DATA_ROOT / "mining_jobs.sqlite3",
                MINING_EXECUTOR,
                get_resource_limits(),
            )
        return MINING_JOBS


def _maybe_queue_mining(
    payload: dict[str, object], execute: Any
) -> dict[str, object] | None:
    if MINING_JOB.get():
        return None
    _validate_factor_mining_universe_request(payload)
    if not os.getenv("TUSHARE_TOKEN", "").strip():
        raise RuntimeError("未配置 TUSHARE_TOKEN，因子挖掘要求真实 Tushare 日线")
    owner = _mining_owner()
    _, pipeline = _factor_mining_scope()
    previous = _mining_jobs().latest(owner, pipeline)
    if previous and previous["status"] in {"queued", "running", "cancelling"}:
        return {"job_id": previous["job_id"], "stage": previous["stage"]}
    if RESEARCH_LEDGER.get_sealed_test("baseline", pipeline, include_bars=False):
        return None
    end = china_today()
    start = end - timedelta(days=FACTOR_MINING_HISTORY_DAYS)
    job = _mining_jobs().submit(
        owner, pipeline, (start.isoformat(), end.isoformat()), execute
    )
    return {"job_id": job["job_id"], "stage": "dataset_sealing"}


def _evolution_package_request(action: str, session_id: str, payload: dict) -> dict:
    def accept_completed_cycle(run_id: str) -> dict:
        if _factor_mining_status().get("active_job"):
            raise PermissionError(
                "Wait for background research before opening sealed data"
            )
        return _accept_factor_mining_run(run_id)

    return package_request(
        action,
        payload,
        owner=_mining_owner(),
        session_id=session_id,
        ledger_path=USER_STORES.current().ledger.path,
        accept=accept_completed_cycle,
    )


def _arena_request(action: str, arena_id: str, payload: dict) -> dict:
    # Read/list/archive do not reserve heavy compute. Actual creation/close/
    # acceptance retain existing per-owner limits.
    def request():
        return arena_request(
            action,
            arena_id,
            payload,
            deps=_mining_research_dependencies(),
            auth=AUTH,
            evaluate=lambda p: _evaluate_factor_expression_request_service(
                p, deps=_mining_research_dependencies()
            ),
            accept=_accept_factor_mining_run,
        )

    if action in {"list", "report", "archive"}:
        return request()
    limits = get_resource_limits()
    lease = limits.reserve(_mining_owner(), "arena-organizer-" + action)
    try:
        with limits.running_operation(_mining_owner()):
            return request()
    finally:
        limits.complete(lease)


def _arena_participant_request(
    action: str, session_id: str, payload: dict, claims: dict
) -> dict:
    def request():
        return participant_request(
            action,
            session_id,
            payload,
            claims=claims,
            deps=_mining_research_dependencies(),
            evaluate=lambda p: _evaluate_factor_expression_request_service(
                p, deps=_mining_research_dependencies()
            ),
        )

    if action != "step":
        return request()
    limits = get_resource_limits()
    identity = "arena:" + claims["owner"] + ":" + session_id
    lease = limits.reserve(identity, "arena-evaluation")
    try:
        with limits.running_operation(claims["owner"]):
            return request()
    finally:
        limits.complete(lease)


def _mining_portfolio_request(action: str, payload: dict) -> dict:
    from dataclasses import asdict

    from quant.mining.portfolio_metrics import PortfolioPolicy

    if action == "report":
        limits = get_resource_limits()
        lease = limits.reserve(_mining_owner(), "factor-portfolio-report")
        try:
            with limits.running_operation(_mining_owner()):
                return factor_portfolio_request(
                    payload, deps=_mining_research_dependencies()
                )
        finally:
            limits.complete(lease)
    _, pipeline = _factor_mining_scope()
    sealed = RESEARCH_LEDGER.get_sealed_test("baseline", pipeline, include_bars=False)
    if action == "get-policy" and not payload:
        policy = (
            cycle_policy(
                USER_STORES.current().ledger.path, int(cast(int | str, sealed["id"]))
            )
            if sealed
            else PortfolioPolicy()
        )
        return {
            "policy": asdict(policy),
            "sealed_id": sealed["id"] if sealed else None,
            "locked": bool(
                sealed
                and RESEARCH_LEDGER.factor_mining_trial_total(
                    int(cast(int | str, sealed["id"]))
                )
            ),
        }
    if action == "set-policy" and sealed:
        return {
            "policy": set_cycle_policy(
                USER_STORES.current().ledger.path,
                int(cast(int | str, sealed["id"])),
                payload,
            )
        }
    raise PermissionError(
        "Create the research snapshot before pre-registering a portfolio policy"
    )


def _external_deep_request(action: str, payload: dict) -> dict:
    stores = USER_STORES.current()
    if action.startswith("trial-"):
        return formal_trial_request(
            action,
            payload,
            owner=_mining_owner(),
            ledger=stores.ledger,
            execute=DEEP_SCHEDULER.request if DEEP_SCHEDULER else None,
        )
    base = Path(__file__).resolve().parents[1] / "deep_factor"
    destination = (stores.mining_dir / "external-deep.sqlite3").resolve()
    if destination.parent != stores.mining_dir.resolve():
        raise PermissionError("Invalid research destination")
    result = external_deep_request(
        action,
        payload,
        owner=_mining_owner(),
        inbox_root=base / "runs",
        destination=destination,
        ledger_path=stores.ledger.path,
        dataset_checksums=base
        / "datasets"
        / "csi300-price-20261011"
        / "checksums.json",
    )
    result["formal_worker"] = (
        DEEP_SCHEDULER.status(_mining_owner()) if DEEP_SCHEDULER else {"running": False}
    )
    return result


def _cancel_factor_mining_job(job_id: str) -> bool:
    _, pipeline = _factor_mining_scope()
    return _mining_jobs().cancel(_mining_owner(), pipeline, job_id)


def _run_factor_mining_request(payload: dict[str, object]) -> dict[str, object]:
    validate_search_options(payload)
    for name, default, minimum, maximum in (
        ("seed", 20260927, 0, 2147483647),
        ("generations", 10, 1, 20),
        ("population_size", 50, 4, 100),
        ("top_n", 10, 1, 20),
    ):
        _bounded_int(payload.get(name, default), minimum, maximum)
    queued = _maybe_queue_mining(
        payload, lambda: _run_factor_mining_request(dict(payload))
    )
    if queued:
        return queued
    result = _run_factor_mining_request_service(
        payload, deps=_mining_research_dependencies()
    )
    digest = hashlib.sha256(str(result.get("run_id") or "").encode()).hexdigest()
    artifact = USER_STORES.current().mining_dir / (digest + ".json")
    try:
        artifact.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        LOGGER.warning("Mining artifact unavailable; canonical ledger result retained")
    return result


def _factor_mining_dataset() -> tuple[dict[str, object], list[Bar], list[Bar]]:
    return _factor_mining_dataset_service(deps=_mining_data_dependencies())


def _evaluate_factor_expression_request(
    payload: dict[str, object],
) -> dict[str, object]:
    if payload.get("mode") in {"evolution_history", "evolution_feedback"}:
        return _evaluate_factor_expression_request_service(
            payload, deps=_mining_research_dependencies()
        )
    if (
        payload.get("mode")
        not in {
            "template_batch",
            "hypothesis_batch",
            "evolution_create",
            "evolution_step",
            "compose",
        }
        and not str(payload.get("expression", "")).strip()
    ):
        raise ValueError("请输入因子表达式")
    queued = _maybe_queue_mining(
        payload, lambda: _evaluate_factor_expression_request(dict(payload))
    )
    if queued:
        return queued
    return _evaluate_factor_expression_request_service(
        payload, deps=_mining_research_dependencies()
    )


def _factor_values_for_definition(
    definition: FactorDefinition, bars: Sequence[Bar], panel: FactorPanel
) -> Panel | None:
    return _compute_factor_values_for_definition(definition, bars, panel)


def _accept_factor_mining_run(run_id: str) -> dict[str, object]:
    return _accept_factor_mining_run_service(
        run_id, deps=_mining_acceptance_dependencies()
    )


def _factor_panel_correlation(
    left: Panel, right: Panel, panel: FactorPanel, start: date, end: date
) -> float | None:
    return _compute_factor_panel_correlation(left, right, panel, start, end)


def _promote_mined_factor(key: str) -> dict[str, object]:
    return _promote_mined_factor_service(key, deps=_mining_acceptance_dependencies())


def _factor_snapshot() -> list[dict[str, object]]:
    return _factor_snapshot_service(deps=_sealed_snapshot_dependencies())


def _factor_snapshot_hash() -> str:
    return _factor_snapshot_hash_service(deps=_sealed_snapshot_dependencies())


def _sealed_result_passed(sealed: dict[str, object] | None) -> bool:
    return _sealed_result_passed_service(sealed, deps=_sealed_snapshot_dependencies())


def _sealed_gate_payload(
    strategy_name: str, symbols: list[str] | None = None, adjust: str = ""
) -> dict[str, object]:
    return _sealed_gate_payload_service(
        strategy_name, symbols, adjust, deps=_paper_governance_dependencies()
    )


def _paper_sealed_gate_status(
    scheduler_status: dict[str, object] | None = None,
) -> dict[str, object]:
    return _paper_sealed_gate_status_service(
        scheduler_status, deps=_paper_governance_dependencies()
    )


def _paper_cycle_sealed_gate_payload(
    strategy_name: str,
    symbols: list[str],
    adjust: str,
    universe: dict[str, object],
    rebalance: str,
) -> dict[str, object]:
    return _paper_cycle_sealed_gate_payload_service(
        strategy_name,
        symbols,
        adjust,
        universe,
        rebalance,
        deps=_paper_governance_dependencies(),
    )


def _validation_gate(
    strategy_version: str,
    strategy_validation_passed: bool,
    *,
    sealed_pipeline_key: str | None = None,
    sealed_scope: str | None = None,
) -> dict[str, object]:
    return _validation_gate_service(
        strategy_version,
        strategy_validation_passed,
        sealed_pipeline_key=sealed_pipeline_key,
        sealed_scope=sealed_scope,
        deps=_paper_governance_dependencies(),
    )


def _cache_writable(path: Path) -> bool:
    return _cache_writable_service(path, deps=_audit_metadata_dependencies())


def _risk_limits_payload() -> dict[str, object]:
    return _risk_limits_payload_service(deps=_paper_quality_dependencies())


def _factor_registry_status(effective_samples: object) -> dict[str, object]:
    return _factor_registry_status_service(
        effective_samples, deps=_paper_governance_dependencies()
    )


def _limits_data_quality(
    metadata: dict[str, object], *, context: str
) -> dict[str, object]:
    return _limits_data_quality_service(
        metadata, context=context, deps=_paper_quality_dependencies()
    )


def _enrich_paper_state(state: dict[str, object]) -> dict[str, object]:
    return _enrich_paper_state_service(state, deps=_paper_quality_dependencies())


def _require_complete_universe(metadata: Mapping[str, object], *, context: str) -> None:
    return _require_complete_universe_service(
        metadata, context=context, deps=_paper_quality_dependencies()
    )


def _build_workspace_snapshot() -> dict[str, object]:
    snapshot = _build_workspace_snapshot_service(
        deps=_workspace_snapshot_dependencies()
    )
    path = USER_STORES.current().workspace_path
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return snapshot


def _build_live_factor_rows(
    bars: Sequence[Bar],
    token: str | None,
    definitions: Sequence[FactorDefinition],
    fundamental_rows: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return build_live_factor_rows(
        bars,
        token,
        definitions,
        fundamental_rows,
        search_stocks=search_tushare_stocks,
        cache_dir=DATA_CACHE_DIR,
    )


def _sample_std(values: list[float]) -> float:
    return _sample_std_service(values, deps=_audit_metadata_dependencies())


def _average(values: list[float]) -> float | None:
    return _average_service(values, deps=_audit_metadata_dependencies())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve A-pi static UI and its local Python backtest API"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--tls-cert",
        type=Path,
        default=None,
        help="local HTTPS certificate; defaults to A_PI_TLS_CERT, then local_tls/localhost.crt.pem beside the package",
    )
    parser.add_argument(
        "--tls-key",
        type=Path,
        default=None,
        help="local HTTPS private key; defaults to A_PI_TLS_KEY, then local_tls/localhost.key.pem beside the package",
    )
    parser.add_argument(
        "--site-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "a_pi_site" / "build",
    )
    parser.add_argument(
        "--no-paper-scheduler",
        action="store_true",
        help="disable the paper-only background cycle",
    )
    parser.add_argument(
        "--paper-interval-seconds",
        type=int,
        default=int(os.getenv("A_PI_PAPER_INTERVAL_SECONDS", "900")),
    )
    parser.add_argument(
        "--paper-strategy", default=os.getenv("A_PI_PAPER_STRATEGY", "baseline")
    )
    parser.add_argument(
        "--paper-rebalance", default=os.getenv("A_PI_PAPER_REBALANCE", "weekly")
    )
    parser.add_argument(
        "--paper-symbols",
        default=os.getenv("A_PI_PAPER_SYMBOLS", ""),
    )
    parser.add_argument(
        "--paper-universe-index",
        default=os.getenv("A_PI_PAPER_UNIVERSE_INDEX", DEFAULT_PAPER_UNIVERSE_INDEX),
        help="PIT index membership for the paper candidate pool; ignored when --paper-symbols is explicitly set",
    )
    args = parser.parse_args()
    if (AUTH_DATA_ROOT / ".user-migration.lock").exists():
        raise SystemExit("User migration is active; do not start the backend")
    configure_service_logging(LOG_PATH)
    if queue_mode() == "local":
        # Only the in-process mode loses queued tasks on restart. Redis-backed
        # jobs are durable and must be left to RQ's worker/registry recovery.
        for user in AUTH_STORE.list_users():
            if not user.disabled:
                USER_STORES.get(user.username).ledger.recover_backtest_jobs()
    else:
        try:
            _backtest_queue_status()
        except RuntimeError as error:
            raise SystemExit(f"Redis 队列模式已启用但队列不可用：{error}") from error
    site_root = args.site_root.resolve()
    if not (site_root / "index.html").is_file():
        raise SystemExit(f"A-pi static site not found: {site_root}")
    if site_root.name == "build":
        from quant.api.frontend_release import validate_frontend_release

        try:
            validate_frontend_release(site_root)
        except (OSError, ValueError, KeyError) as error:
            raise SystemExit(
                f"A-pi frontend release validation failed: {error}"
            ) from error
    handler = partial(ApiHandler, directory=str(site_root))
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError(
            "Bind A-pi to loopback; use the HTTPS reverse proxy for remote access"
        )
    server = BoundedHTTPServer((args.host, args.port), handler)
    _tls_root = Path(__file__).resolve().parents[1] / "local_tls"
    _default_cert = Path(
        os.getenv("A_PI_TLS_CERT") or (_tls_root / "localhost.crt.pem")
    )
    _default_key = Path(os.getenv("A_PI_TLS_KEY") or (_tls_root / "localhost.key.pem"))
    tls_cert = (args.tls_cert or _default_cert).resolve()
    tls_key = (args.tls_key or _default_key).resolve()
    if not tls_cert.is_file() or not tls_key.is_file():
        raise SystemExit("A-pi local HTTPS certificate or private key is missing")
    server.enable_tls(tls_cert, tls_key)
    ic_scheduler = DailyICScheduler(
        AUTH_STORE, USER_STORES, DATA_CACHE_DIR, lambda: os.getenv("TUSHARE_TOKEN")
    )
    ic_scheduler.start()
    global DEEP_SCHEDULER
    DEEP_SCHEDULER = DeepFormalScheduler(AUTH_STORE, USER_STORES, DATA_CACHE_DIR)
    DEEP_SCHEDULER.start()
    from quant.data.extension_scheduler import ExtensionScheduler
    from quant.data.extension_store import ExtensionStore
    from quant.notifications import NotificationOutbox

    def notify_shared_data(identity: str, body: str) -> None:
        for user in AUTH_STORE.list_users():
            if not user.disabled:
                NotificationOutbox(
                    USER_STORES.get(user.username).ledger.path.with_name(
                        "notifications.sqlite3"
                    )
                ).enqueue("data:" + identity, "A-pi 数据同步待复核", body)

    data_scheduler = ExtensionScheduler(
        ExtensionStore(FEATURE_DB_PATH), notify_shared_data
    )
    data_scheduler.start()
    global PAPER_SCHEDULER
    if not args.no_paper_scheduler and os.getenv(
        "A_PI_PAPER_AUTO", "1"
    ).strip().lower() not in {"0", "false", "no"}:
        if str(args.paper_strategy).strip().lower() in {
            "lightgbm",
            "multi_signal",
            "rulebook",
        }:
            raise SystemExit("所选策略仅用于隔离研究，不能自动用作纸面交易策略")
        explicit_paper_symbols = [
            item.strip()
            for item in str(args.paper_symbols).replace("，", ",").split(",")
            if item.strip()
        ]
        paper_symbols = explicit_paper_symbols or list(DEFAULT_SYMBOLS)
        paper_universe_provider = None
        paper_universe_index = None
        if not explicit_paper_symbols:
            paper_universe_index = str(args.paper_universe_index).strip().upper()
            paper_universe_provider = lambda as_of: resolve_paper_universe(
                paper_universe_index or DEFAULT_PAPER_UNIVERSE_INDEX,
                as_of=as_of,
                token=os.getenv("TUSHARE_TOKEN"),
                cache_dir=str(DATA_CACHE_DIR),
            )

        def scheduler_for_user(username: str) -> PaperScheduler:
            config = UserPaperSettings(USER_STORES.get(username).paper_store.path).get()
            strategy = config.get("strategy", args.paper_strategy)
            rebalance = config.get("rebalance", args.paper_rebalance)
            return PaperScheduler(
                PAPER_STORE,
                symbols=paper_symbols,
                strategy=strategy,
                rebalance=rebalance,
                cache_dir=str(DATA_CACHE_DIR),
                interval_seconds=args.paper_interval_seconds,
                factor_definitions_provider=lambda: FACTOR_STORE.definitions(
                    enabled_only=True
                ),
                sealed_test_provider=lambda selected_symbols, universe: (
                    _paper_sealed_gate_status(
                        {
                            "strategy": strategy,
                            "symbols": selected_symbols,
                            "universe_index": paper_universe_index,
                            "universe": universe,
                            "rebalance": rebalance,
                        }
                    )
                ),
                universe_provider=paper_universe_provider,
                universe_index=paper_universe_index,
                after_cycle=_backup_after_paper_cycle,
            )

        def research_scheduler_for_user(username: str) -> PaperScheduler:
            account = USER_STORES.research_account(username)
            config = account.configuration()
            index = str(config["universe_index"])
            ledger = USER_STORES.get(username).ledger
            return PaperScheduler(
                account.store,
                symbols=paper_symbols,
                strategy=str(config["strategy"]),
                rebalance=str(config["rebalance"]),
                cache_dir=str(DATA_CACHE_DIR),
                interval_seconds=args.paper_interval_seconds,
                factor_definitions_provider=account.factors,
                universe_index=index,
                universe_provider=lambda as_of: resolve_paper_universe(
                    index,
                    as_of=as_of,
                    token=os.getenv("TUSHARE_TOKEN"),
                    cache_dir=str(DATA_CACHE_DIR),
                ),
                execution_mode="research",
                research_observer=lambda day, version: ledger.record_paper_exposure(
                    day, str(config["config_hash"]), version
                ),
            )

        PAPER_SCHEDULER = UserPaperScheduler(
            AUTH_STORE, USER_STORES, scheduler_for_user, research_scheduler_for_user
        )
        PAPER_SCHEDULER.start()
    print(f"A-pi is running at https://{args.host}:{args.port}")
    print(f"Python backtest API: https://{args.host}:{args.port}/api/backtest")
    print(
        "Paper scheduler: "
        + ("enabled (paper only)" if PAPER_SCHEDULER is not None else "disabled")
    )
    try:
        AUTH.start_maintenance()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nA-pi stopped")
    finally:
        data_scheduler.stop()
        ic_scheduler.stop()
        if DEEP_SCHEDULER is not None:
            DEEP_SCHEDULER.stop()
        AUTH.stop_maintenance()
        if PAPER_SCHEDULER is not None:
            PAPER_SCHEDULER.stop()
        server.server_close()
        RUNTIME_CACHES.close()
        BACKTEST_EXECUTOR.shutdown(wait=True, cancel_futures=True)
        MINING_EXECUTOR.shutdown(wait=True, cancel_futures=True)
        USER_STORES.close()


if __name__ == "__main__":
    main()
