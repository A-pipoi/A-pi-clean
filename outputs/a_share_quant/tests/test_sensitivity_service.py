"""Sensitivity scans enforce governance before any market-data request."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from quant.api.sensitivity_service import (
    SensitivityDependencies,
    run_sensitivity_request,
)
from quant.research import ResearchBudgetExceeded


def _deps(*, can_record: bool) -> SensitivityDependencies:
    ledger = Mock()
    ledger.can_record.return_value = can_record
    return SensitivityDependencies(
        require_strategy=Mock(), resolve_universe=Mock(), filter_universe=Mock(),
        make_adapter=Mock(), require_complete_universe=Mock(), sealed_scope=Mock(),
        sealed_pipeline_key=Mock(), ensure_sealed_snapshot=Mock(), research_bars=Mock(),
        record_experiment=Mock(), as_float=float, scoped_dataset_version=Mock(),
        validation_gate=Mock(), dataset_metadata=Mock(), ledger=ledger, factors=Mock(),
        token=None, cache_dir=Mock(), factor_pipeline_version="v1", sealed_test_days=40,
    )


def test_budget_gate_precedes_universe_fetch() -> None:
    deps = _deps(can_record=False)
    with pytest.raises(ResearchBudgetExceeded):
        run_sensitivity_request({"strategy": "baseline"}, deps)
    deps.resolve_universe.assert_not_called()
    deps.make_adapter.assert_not_called()


def test_lightgbm_scan_is_rejected_without_market_fetch() -> None:
    deps = _deps(can_record=True)
    with pytest.raises(ValueError, match="LightGBM"):
        run_sensitivity_request({"strategy": "lightgbm"}, deps)
    deps.resolve_universe.assert_not_called()
