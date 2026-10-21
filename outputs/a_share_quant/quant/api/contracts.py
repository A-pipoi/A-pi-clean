"""Typed callback contracts shared by API services and their composition root."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import date
    from pathlib import Path
    from typing import Any

    from quant.api.backtest_service import BacktestDependencies
    from quant.api.market_routes import MarketRouteDependencies
    from quant.api.paper_routes import PaperApiDependencies
    from quant.api.paper_status import PaperStatusDependencies
    from quant.api.portfolio_routes import PortfolioApiDependencies
    from quant.api.research_mutations import ResearchMutationDependencies
    from quant.api.research_routes import ResearchRouteDependencies
    from quant.api.sealed_service import SealedValidationDependencies
    from quant.api.sensitivity_service import SensitivityDependencies
    from quant.api.universe import ResolvedBacktestUniverse
    from quant.backtest.costs import CostModel
    from quant.backtest.engine import BacktestResult, Strategy
    from quant.data.schema import Bar, FeatureObservation, PITIndustryObservation
    from quant.data.universe import HistoricalUniverse
    from quant.factors.expression import (
        CompiledExpression,
        ExpressionResult,
        FactorPanel,
        Panel,
    )
    from quant.factors.store import FactorDefinition
    from quant.features.store import FeatureStore
    from quant.portfolio.risk import RiskLimits, RiskViolation
    from quant.strategies.registry import ResearchStrategy, StrategyPreset
    from quant.validation.sealed import SealedTestReport
    from quant.validation.strategy import StrategyValidationReport


class AcceptFactorMiningRunCallback(Protocol):
    def __call__(self, run_id: str) -> dict[str, object]: ...


class ApplyFactorMiningUniverseCallback(Protocol):
    def __call__(
        self, bars: list[Bar], universe_payload: Mapping[str, object]
    ) -> list[Bar]: ...


class AsFloatCallback(Protocol):
    def __call__(self, value: object) -> float: ...


class AsIntCallback(Protocol):
    def __call__(self, value: object) -> int: ...


class AverageCallback(Protocol):
    def __call__(self, values: list[float]) -> float | None: ...


class BacktestDependenciesCallback(Protocol):
    def __call__(self) -> BacktestDependencies: ...


class BacktestJobViewCallback(Protocol):
    def __call__(self, job: dict[str, object]) -> dict[str, object]: ...


class BacktestOperationalReportsCallback(Protocol):
    def __call__(
        self, result: object, bars: list[Bar], *, max_participation: float
    ) -> dict[str, dict[str, object]]: ...


class BacktestQueueStatusCallback(Protocol):
    def __call__(self) -> dict[str, object]: ...


class BarToPayloadCallback(Protocol):
    def __call__(self, bar: Bar) -> dict[str, object]: ...


class BarsFromPayloadCallback(Protocol):
    def __call__(self, rows: object) -> list[Bar]: ...


class BoundedIntCallback(Protocol):
    def __call__(self, value: object, minimum: int, maximum: int) -> int: ...


class BuildExcessReturnReportCallback(Protocol):
    def __call__(
        self,
        strategy_initial: float,
        strategy_final: float,
        benchmark_rows: Sequence[dict[str, object]],
    ) -> dict[str, object]: ...


class BuildFactorPanelCallback(Protocol):
    def __call__(
        self,
        bars: Sequence[Bar],
        *,
        data_version: str = ...,
        observations: Sequence[FeatureObservation] = ...,
        industry_observations: Sequence[PITIndustryObservation] = ...,
        pit_quality: Mapping[str, str] | None = ...,
    ) -> FactorPanel: ...


class BuildLiveFactorRowsCallback(Protocol):
    def __call__(
        self,
        bars: Sequence[Bar],
        token: str | None,
        definitions: Sequence[FactorDefinition],
        fundamental_rows: list[dict[str, object]] | None = ...,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...


class BuildStrategyCallback(Protocol):
    def __call__(
        self,
        name: str | None,
        *,
        rebalance_every: int,
        factor_definitions: Sequence[FactorDefinition] | None = ...,
        fundamental_values: Mapping[str, Mapping[str, float]] | None = ...,
        feature_store: FeatureStore | None = ...,
        data_version: str | None = ...,
    ) -> ResearchStrategy: ...


class CacheWritableCallback(Protocol):
    def __call__(self, path: Path) -> bool: ...


class CheckOrderLimitsCallback(Protocol):
    def __call__(
        self,
        positions: dict[str, int | float],
        prices: dict[str, float],
        industries: dict[str, str | None],
        equity: float,
        symbol: str,
        side: str,
        quantity: int,
        limits: RiskLimits = ...,
        current_drawdown: float | None = ...,
        risk_estimate: Mapping[str, object] | None = ...,
    ) -> list[RiskViolation]: ...


class CurrentGitRevisionCallback(Protocol):
    def __call__(self) -> str: ...


class DataFreshnessCallback(Protocol):
    def __call__(
        self, metadata: dict[str, Any], cycle_date: date
    ) -> dict[str, Any]: ...


class DataProbeStatusCallback(Protocol):
    def __call__(self) -> dict[str, object]: ...


class DatasetMetadataWithUniverseCallback(Protocol):
    def __call__(
        self, source_metadata: Mapping[str, object], universe: Mapping[str, object]
    ) -> dict[str, object]: ...


class EnrichPaperStateCallback(Protocol):
    def __call__(self, state: dict[str, object]) -> dict[str, object]: ...


class EnsureSealedSnapshotCallback(Protocol):
    def __call__(
        self,
        strategy_name: str,
        pipeline_key: str,
        bars: list[Bar],
        snapshot: object,
        *,
        symbols: list[str],
        adjust: str,
        sealed_days: int,
        pit_data: dict[str, object] | None = ...,
        pit_metadata: dict[str, object] | None = ...,
        additional_parameters: dict[str, object] | None = ...,
    ) -> dict[str, object]: ...


class EvaluateExpressionCallback(Protocol):
    def __call__(
        self,
        expression: str | CompiledExpression,
        panel: FactorPanel,
        *,
        code_version: str = ...,
    ) -> ExpressionResult: ...


class EvaluateFactorExpressionRequestCallback(Protocol):
    def __call__(self, payload: dict[str, object]) -> dict[str, object]: ...


class EvaluateFactorPanelCallback(Protocol):
    def __call__(
        self,
        panel: FactorPanel,
        values: list[list[float | None]],
        *,
        start: date | None = ...,
        end: date | None = ...,
        n_tested: int = ...,
        horizons: Sequence[int] = ...,
        quantiles: int = ...,
    ) -> dict[str, object]: ...


class FactorMiningComparisonPanelCallback(Protocol):
    def __call__(self, start_date: date, end_date: date) -> tuple[FactorPanel, str]: ...


class FactorMiningCrossUniverseReferenceCallback(Protocol):
    def __call__(
        self, expressions: list[str], *, start_date: date, end_date: date
    ) -> dict[str, object]: ...


class FactorMiningDatasetCallback(Protocol):
    def __call__(self) -> tuple[dict[str, object], list[Bar], list[Bar]]: ...


class FactorMiningPitQualityCallback(Protocol):
    def __call__(
        self, pit_data: dict[str, object], bars: list[Bar]
    ) -> dict[str, object]: ...


class FactorMiningScopeCallback(Protocol):
    def __call__(self) -> tuple[str, str]: ...


class FactorMiningStatusCallback(Protocol):
    def __call__(self) -> dict[str, object]: ...


class FactorMiningUniversePayloadCallback(Protocol):
    def __call__(
        self,
        membership_history: Mapping[str, object],
        listing_dates: Mapping[str, date],
        listing_metadata: Mapping[str, object],
        symbols: list[str],
        included_symbols: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, object]: ...


class FactorPanelCorrelationCallback(Protocol):
    def __call__(
        self, left: Panel, right: Panel, panel: FactorPanel, start: date, end: date
    ) -> float | None: ...


class FactorRegistryStatusCallback(Protocol):
    def __call__(self, effective_samples: object) -> dict[str, object]: ...


class FactorSnapshotCallback(Protocol):
    def __call__(self) -> list[dict[str, object]]: ...


class FactorSnapshotHashCallback(Protocol):
    def __call__(self) -> str: ...


class FactorValuesForDefinitionCallback(Protocol):
    def __call__(
        self, definition: FactorDefinition, bars: Sequence[Bar], panel: FactorPanel
    ) -> Panel | None: ...


class FetchFactorMiningPitBundleCallback(Protocol):
    def __call__(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        token: str,
        *,
        force_refresh: bool = ...,
    ) -> dict[str, object]: ...


class FetchTushareAdjustmentHealthCallback(Protocol):
    def __call__(
        self,
        symbols: Iterable[str],
        *,
        token: str | None = ...,
        start: date | None = ...,
        end: date | None = ...,
    ) -> dict[str, object]: ...


class FetchTushareFundamentalFactorsCallback(Protocol):
    def __call__(
        self,
        symbols: Iterable[str],
        *,
        as_of: date | None = ...,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
        cache_ttl_seconds: int = ...,
        force_refresh: bool = ...,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


class FetchTushareIndexHistoryCallback(Protocol):
    def __call__(
        self,
        ts_code: str = ...,
        *,
        token: str | None = ...,
        start: date | None = ...,
        end: date | None = ...,
        cache_dir: str | Path | None = ...,
        force_refresh: bool = ...,
    ) -> dict[str, object]: ...


class FetchTushareIndexMembersCallback(Protocol):
    def __call__(
        self,
        index_code: str,
        *,
        as_of: date | None = ...,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
        cache_ttl_seconds: int = ...,
    ) -> dict[str, Any]: ...


class FetchTushareIndicesCallback(Protocol):
    def __call__(
        self,
        ts_codes: Iterable[str] | None = ...,
        *,
        token: str | None = ...,
        end: date | None = ...,
        lookback_days: int = ...,
        cache_dir: str | Path | None = ...,
        cache_ttl_seconds: int = ...,
        force_refresh: bool = ...,
    ) -> tuple[list[dict[str, str | float]], list[dict[str, str]]]: ...


class FetchTushareRealtimeIndicesCallback(Protocol):
    def __call__(self, token: str | None = ...) -> list[dict[str, str | float]]: ...


class FetchTushareStatementsCallback(Protocol):
    def __call__(
        self,
        symbols: Iterable[str],
        *,
        as_of: date | None = ...,
        statement: str = ...,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
        cache_ttl_seconds: int = ...,
        force_refresh: bool = ...,
    ) -> tuple[dict[str, dict[str, dict[str, Any] | None]], dict[str, Any]]: ...


class FilterBacktestUniverseCallback(Protocol):
    def __call__(
        self, bars: list[Bar], universe: ResolvedBacktestUniverse
    ) -> list[Bar]: ...


class FlattenStatementRowsCallback(Protocol):
    def __call__(
        self, statements: dict[str, dict[str, dict[str, Any] | None]]
    ) -> list[dict[str, Any]]: ...


class FrontendSourceRevisionCallback(Protocol):
    def __call__(self, site_dist: Path | None = ...) -> str: ...


class GetTushareAShareUniverseCallback(Protocol):
    def __call__(
        self,
        *,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
        offset: int = ...,
        limit: int = ...,
        cache_ttl_seconds: int = ...,
    ) -> tuple[list[dict[str, str]], int, bool]: ...


class GetTushareStockMetadataCallback(Protocol):
    def __call__(
        self,
        symbols: list[str],
        *,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
    ) -> tuple[dict[str, dict[str, str]], bool]: ...


class LimitsDataQualityCallback(Protocol):
    def __call__(
        self, metadata: dict[str, object], *, context: str
    ) -> dict[str, object]: ...


class MarketApiDependenciesCallback(Protocol):
    def __call__(self) -> MarketRouteDependencies: ...


class MaterializePitFeatureRequestCallback(Protocol):
    def __call__(self, payload: dict[str, object]) -> dict[str, object]: ...


class ObjectListCallback(Protocol):
    def __call__(self, value: object) -> list[object]: ...


class ObjectMappingCallback(Protocol):
    def __call__(self, value: object) -> dict[str, object]: ...


class OptionalDateCallback(Protocol):
    def __call__(self, value: object) -> date | None: ...


class PaperApiDependenciesCallback(Protocol):
    def __call__(self) -> PaperApiDependencies: ...


class PaperSealedGateStatusCallback(Protocol):
    def __call__(
        self, scheduler_status: dict[str, object] | None = ...
    ) -> dict[str, object]: ...


class PaperStatusDependenciesCallback(Protocol):
    def __call__(self) -> PaperStatusDependencies: ...


class PitObservationsForBarsCallback(Protocol):
    def __call__(
        self, sealed: dict[str, object], bars: list[Bar]
    ) -> tuple[
        list[FeatureObservation],
        list[PITIndustryObservation],
        dict[str, str],
        dict[str, object],
    ]: ...


class PortfolioApiDependenciesCallback(Protocol):
    def __call__(self) -> PortfolioApiDependencies: ...


class PromoteMinedFactorCallback(Protocol):
    def __call__(self, key: str) -> dict[str, object]: ...


class RecordResearchExperimentCallback(Protocol):
    def __call__(
        self,
        strategy_version: str,
        data_version: str,
        parameters: dict[str, object],
        status: str,
        notes: str,
    ) -> int: ...


class ReportBacktestProgressCallback(Protocol):
    def __call__(self, progress: int, message: str) -> None: ...


class RequireCompleteUniverseCallback(Protocol):
    def __call__(self, metadata: Mapping[str, object], *, context: str) -> None: ...


class RequireStrategyDependenciesCallback(Protocol):
    def __call__(self, strategy_name: str) -> None: ...


class ResearchApiDependenciesCallback(Protocol):
    def __call__(self) -> ResearchRouteDependencies: ...


class ResearchBarsCallback(Protocol):
    def __call__(self, bars: list[Bar], sealed: dict[str, object]) -> list[Bar]: ...


class ResearchMutationDependenciesCallback(Protocol):
    def __call__(self) -> ResearchMutationDependencies: ...


class ResolveBacktestUniverseCallback(Protocol):
    def __call__(
        self,
        payload: Mapping[str, Any],
        start_date: date | None,
        end_date: date | None,
        *,
        force_membership_refresh: bool = ...,
    ) -> ResolvedBacktestUniverse: ...


class ResolveStrategyConfigCallback(Protocol):
    def __call__(
        self, name: str | None, lookback: int | None = ..., top_n: int | None = ...
    ) -> tuple[StrategyPreset, int, int]: ...


class RiskLimitsPayloadCallback(Protocol):
    def __call__(self) -> dict[str, object]: ...


class RunFactorMiningRequestCallback(Protocol):
    def __call__(self, payload: dict[str, object]) -> dict[str, object]: ...


class RunSealedTestCallback(Protocol):
    def __call__(
        self,
        bars: Sequence[Bar],
        strategy_factory: Callable[[], Strategy],
        *,
        sealed_start: date,
        sealed_end: date,
        initial_cash: float = ...,
        cost_model: CostModel | None = ...,
        minimum_observations: int = ...,
        pit_universe: HistoricalUniverse | None = ...,
        pit_index_code: str | None = ...,
    ) -> SealedTestReport: ...


class ScopedDatasetVersionCallback(Protocol):
    def __call__(self, base_version: str, universe_version: str) -> str: ...


class SealedGatePayloadCallback(Protocol):
    def __call__(
        self, strategy_name: str, symbols: list[str] | None = ..., adjust: str = ...
    ) -> dict[str, object]: ...


class SealedPipelineKeyCallback(Protocol):
    def __call__(self, scope: str) -> str: ...


class SealedPublicPayloadCallback(Protocol):
    def __call__(self, sealed: Mapping[str, object] | None) -> dict[str, object]: ...


class SealedResultPassedCallback(Protocol):
    def __call__(self, sealed: dict[str, object] | None) -> bool: ...


class SealedScopeCallback(Protocol):
    def __call__(
        self, symbols: Sequence[str], adjust: str, *, universe_version: str = ..., signal_adjust: str = ...
    ) -> str: ...


class SealedValidationDependenciesCallback(Protocol):
    def __call__(self) -> SealedValidationDependencies: ...


class SearchTushareStocksCallback(Protocol):
    def __call__(
        self,
        query: str = ...,
        *,
        token: str | None = ...,
        cache_dir: str | Path | None = ...,
        limit: int = ...,
        cache_ttl_seconds: int = ...,
    ) -> tuple[list[dict[str, str]], int, bool]: ...


class SensitivityDependenciesCallback(Protocol):
    def __call__(self) -> SensitivityDependencies: ...


class StrictPitBrinsonReportCallback(Protocol):
    def __call__(
        self, result: BacktestResult, bars: list[Bar]
    ) -> dict[str, object]: ...


class SubmitBacktestJobCallback(Protocol):
    def __call__(self, payload: dict[str, object]) -> str: ...


class SymbolsCallback(Protocol):
    def __call__(self, value: object) -> list[str]: ...


class ValidateFactorMiningUniverseRequestCallback(Protocol):
    def __call__(self, payload: Mapping[str, object]) -> None: ...


class ValidateStrategyCallback(Protocol):
    def __call__(
        self,
        bars: Sequence[Bar],
        strategy_factory: Callable[[], Strategy],
        *,
        initial_cash: float = ...,
        cost_model: CostModel | None = ...,
        train_window: int = ...,
        test_window: int = ...,
        embargo: int | None = ...,
        rebalance_every: int = ...,
        minimum_oos_observations: int = ...,
    ) -> StrategyValidationReport: ...


class ValidationGateCallback(Protocol):
    def __call__(
        self,
        strategy_version: str,
        strategy_validation_passed: bool,
        *,
        sealed_pipeline_key: str | None = ...,
        sealed_scope: str | None = ...,
    ) -> dict[str, object]: ...


class WorkspaceCatalogCallback(Protocol):
    def __call__(self) -> dict[str, object]: ...


class WorkspaceResponseCallback(Protocol):
    def __call__(self) -> tuple[int, dict[str, object]]: ...
