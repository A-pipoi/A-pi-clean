"""Read-only factor, strategy, research-ledger and benchmark routes."""

from __future__ import annotations

import importlib.util
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quant.api.contracts import (
    BuildExcessReturnReportCallback,
    FetchTushareIndexHistoryCallback,
)

from ..data.observed_industry import ObservedIndustryStore
from ..factors.decay import factor_decay_report
from ..factors.live_ic import LiveICStore
from ..factors.store import FactorRegistryStore
from ..notifications import NotificationOutbox
from ..research import ResearchLedger
from ..strategies.rulebook import DEFAULT_RECIPE, RecipeStore

RESEARCH_GET_PATHS = frozenset(
    {
        "/api/reports/benchmark",
        "/api/factors",
        "/api/factor-mining",
        "/api/research/ledger",
        "/api/research/data-quality",
        "/api/research/sealed",
        "/api/strategies",
        "/api/factors/decay",
        "/api/notifications/outbox",
        "/api/factors/live-ic",
        "/api/factors/industry-pit",
        "/api/strategy-recipes",
        "/api/data/extensions",
    }
)


@dataclass(frozen=True)
class ResearchRouteDependencies:
    ledger: ResearchLedger
    factor_store: FactorRegistryStore
    strategy_presets: Mapping[str, Any]
    default_symbols: Sequence[str]
    token: Callable[[], str | None]
    cache_dir: Path
    optional_date: Callable[[object], date | None]
    as_int: Callable[[object], int]
    object_mapping: Callable[[object], dict[str, Any]]
    object_list: Callable[[object], list[Any]]
    symbols_from_query: Callable[[list[str]], list[str]]
    sealed_scope: Callable[[list[str], str], str]
    sealed_pipeline_key: Callable[[str], str]
    sealed_public_payload: Callable[..., dict[str, object]]
    factor_mining_status: Callable[[], dict[str, object]]
    fetch_benchmark: FetchTushareIndexHistoryCallback
    build_excess_report: BuildExcessReturnReportCallback


def _arg(query: Mapping[str, list[str]], key: str, default: str = "") -> str:
    return query.get(key, [default])[0]


def handle_research_get(
    path: str,
    query: Mapping[str, list[str]],
    deps: ResearchRouteDependencies,
) -> tuple[int, dict[str, object]]:
    if path not in RESEARCH_GET_PATHS:
        raise ValueError(f"unsupported research route: {path}")
    if path == '/api/data/extensions':
        from ..data.extension_analytics import diagnostics
        from ..data.extension_store import ExtensionStore, default_store_path
        shared_path = default_store_path()
        if not shared_path.exists():
            return 200, {'ok': True, 'status': 'not_initialized', 'shared_workspace_data': True}
        extension_store = ExtensionStore(shared_path)
        # GET is read-only: never build a missing schema or start provider I/O.
        from contextlib import closing
        with closing(extension_store.connect()) as connection:
            initialized = connection.execute("SELECT 1 FROM sqlite_master WHERE name='ts_sync'").fetchone()
        if not initialized:
            return 200, {'ok': True, 'status': 'not_initialized', 'shared_workspace_data': True}
        return 200, {'ok': True, 'status': 'sample_only', 'shared_workspace_data': True, **diagnostics(extension_store)}
    if path == '/api/research/data-quality':
        from ..data.extension_store import ExtensionStore, default_store_path
        digest = _arg(query, 'digest')
        shared_path = default_store_path()
        artifact = ExtensionStore(shared_path).quality_artifact(digest) if shared_path.exists() else None
        return (200, {'ok':True, **artifact}) if artifact is not None else (404, {'ok':False,'error':'quality_artifact_not_found'})
    if path == "/api/notifications/outbox":
        return 200, {"ok": True, **NotificationOutbox(deps.ledger.path.with_name('notifications.sqlite3')).status()}
    if path == '/api/strategy-recipes':
        return 200,{'ok':True,'recipes':RecipeStore(deps.ledger.path.with_name('strategy_recipes.sqlite3')).list(),
            'template':DEFAULT_RECIPE,'research_only':True,'budget_family':'rulebook'}
    if path == "/api/factors/live-ic":
        return 200, LiveICStore(deps.ledger.path.with_name('live_ic.sqlite3')).report()
    if path == "/api/factors/industry-pit":
        store=ObservedIndustryStore(deps.cache_dir/'live_ic'/'industry.sqlite3')
        symbols=deps.symbols_from_query(query.get('symbols',[])) or LiveICStore(deps.ledger.path.with_name('live_ic.sqlite3')).latest_symbols() or store.latest_symbols()
        cutoff=datetime.fromisoformat(_arg(query,'as_of') or datetime.now(UTC).isoformat())
        if cutoff.tzinfo is None or cutoff>datetime.now(UTC):
            raise ValueError('行业 PIT 查询必须提供不晚于当前时刻的带时区时间')
        return 200, {'ok':True,**store.at(cutoff,symbols)}
    if path == "/api/factors/decay":
        # Reuse the authorized, account-scoped saved research history. No provider
        # fetch or budget deduction and no evaluation of an unopened sealed set.
        monitors = []
        for run in deps.ledger.factor_mining_runs(limit=20):
            result = deps.object_mapping(run.get("result"))
            for candidate_raw in deps.object_list(result.get("candidates")):
                candidate = deps.object_mapping(candidate_raw)
                evaluation = deps.object_mapping(candidate.get("evaluation"))
                horizons = deps.object_mapping(evaluation.get("horizons"))
                for horizon, values_raw in horizons.items():
                    values = deps.object_mapping(values_raw)
                    rows = [deps.object_mapping(row) for row in deps.object_list(values.get("ic_series"))]
                    if not rows:
                        continue
                    monitors.append({"factor_key": candidate.get("factor_key"),
                        "run_id": run.get("run_id"), "horizon": horizon,
                        "data_version": run.get("data_version"),
                        **factor_decay_report(rows)})
        return 200, {"ok": True, "source": "saved_account_research", "monitors": monitors,
            "status": "available" if monitors else "no_saved_ic_series",
            "live_monitoring": False}
    if path == "/api/factors":
        return 200, {
            "ok": True,
            "definitions": [item.as_dict() for item in deps.factor_store.definitions()],
            "source": "a_pi_factor_registry",
        }
    if path == "/api/factor-mining":
        return 200, {"ok": True, **deps.factor_mining_status()}
    if path == "/api/research/ledger":
        strategy = _arg(query, "strategy", "baseline").strip().lower() or "baseline"
        ledger_status = deps.ledger.status(strategy)
        from ..data.extension_store import ExtensionStore, default_store_path
        quality_path=default_store_path()
        quality_artifacts=ExtensionStore(quality_path).quality_artifacts() if quality_path.exists() else []
        run_count = deps.as_int(ledger_status["used"])
        latest_experiment = deps.ledger.latest(strategy)
        latest_parameters = deps.object_mapping(
            (latest_experiment or {}).get("parameters")
        )
        latest_nested_parameters = deps.object_mapping(
            latest_parameters.get("parameters")
        )
        sealed_scope = latest_parameters.get(
            "sealed_scope"
        ) or latest_nested_parameters.get("sealed_scope")
        sealed_pipeline_key = (
            deps.sealed_pipeline_key(str(sealed_scope))
            if sealed_scope
            else deps.sealed_pipeline_key(
                deps.sealed_scope(list(deps.default_symbols), "")
            )
        )
        return 200, {
            "ok": True,
            "strategy": strategy,
            "runs": run_count,
            "run_count": run_count,
            "budget": deps.ledger.budget,
            "remaining": max(0, deps.ledger.budget - run_count),
            "kind_counts": ledger_status["kind_counts"],
            "archived_count": ledger_status["archived_count"],
            "can_record": ledger_status["can_record"],
            "latest": latest_experiment,
            "experiments": deps.ledger.experiments(strategy, limit=50),
            "latest_sensitivity": deps.ledger.latest(strategy, kind="sensitivity"),
            "sealed_test": deps.sealed_public_payload(
                deps.ledger.get_sealed_test(
                    strategy,
                    sealed_pipeline_key,
                    include_bars=False,
                )
            ),
            "source": "a_pi_research_ledger",
            "shared_data_quality_artifacts": quality_artifacts,
        }
    if path == "/api/research/sealed":
        strategy = _arg(query, "strategy", "baseline").strip().lower() or "baseline"
        symbols = deps.symbols_from_query(query.get("symbols", [])) or list(
            deps.default_symbols
        )
        adjust = _arg(query, "adjust").strip().lower()
        sealed = deps.ledger.get_sealed_test(
            strategy,
            deps.sealed_pipeline_key(deps.sealed_scope(symbols, adjust)),
            include_bars=False,
        )
        return 200, {
            "ok": True,
            "strategy": strategy,
            "sealed_test": deps.sealed_public_payload(sealed),
            "source": "a_pi_sealed_test_ledger",
        }
    if path == "/api/strategies":
        installed = importlib.util.find_spec("lightgbm") is not None
        return 200, {
            "ok": True,
            "source": "a_pi_strategy_registry",
            "strategies": [
                {
                    "key": item.key,
                    "label": item.label,
                    "lookback": item.lookback,
                    "top_n": item.top_n,
                    "description": item.description,
                    "available": item.key != "lightgbm" or installed,
                    "research_only": item.key in {"lightgbm", "multi_signal", "baseline_price_audited", "baseline_full_audited"},
                    "unavailable_reason": "请安装项目可选依赖 .[ml]"
                    if item.key == "lightgbm" and not installed
                    else None,
                }
                for item in deps.strategy_presets.values()
            ],
        }

    try:
        benchmark = deps.fetch_benchmark(
            _arg(query, "ts_code", "000300.SH"),
            token=deps.token(),
            start=deps.optional_date(_arg(query, "start_date")),
            end=deps.optional_date(_arg(query, "end_date")),
            cache_dir=deps.cache_dir,
            force_refresh=True,
        )
        report = None
        experiment_id_raw = _arg(query, "experiment_id")
        initial_raw = _arg(query, "strategy_initial")
        final_raw = _arg(query, "strategy_final")
        if experiment_id_raw and initial_raw and final_raw:
            initial_equity = float(initial_raw)
            final_equity = float(final_raw)
            if (
                not math.isfinite(initial_equity)
                or not math.isfinite(final_equity)
                or initial_equity <= 0
            ):
                raise ValueError("strategy equity must be a positive finite number")
            report = deps.build_excess_report(
                initial_equity,
                final_equity,
                [
                    row
                    for row in deps.object_list(benchmark.get("rows"))
                    if isinstance(row, dict)
                ],
            )
            report = {
                **report,
                "ts_code": benchmark.get("ts_code"),
                "name": benchmark.get("name"),
                "source": benchmark.get("source"),
            }
        return 200, {"ok": True, "benchmark": benchmark, "benchmark_report": report}
    except (TypeError, ValueError) as error:
        return 400, {"ok": False, "error": str(error)}
    except RuntimeError as error:
        return 502, {
            "ok": False,
            "code": "data_provider_unavailable",
            "error": str(error),
        }
