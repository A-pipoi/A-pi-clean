from dataclasses import fields
from datetime import date
from unittest.mock import Mock

import pytest

from quant.api import mining_data
from quant.api.mining_data import MiningDataDependencies

SECRET = "synthetic-reference-secret"
START, END = date(2024, 1, 1), date(2024, 6, 1)


def dependencies():
    deps = Mock(spec=[field.name for field in fields(MiningDataDependencies)])
    deps._object_mapping.side_effect = lambda value: value
    return deps


@pytest.mark.parametrize("error_type", [RuntimeError, OSError, ArithmeticError])
def test_reference_panel_failure_is_redacted(error_type, caplog):
    deps = dependencies()

    def fail(*_args):
        raise error_type(SECRET) from RuntimeError(SECRET)

    deps._factor_mining_comparison_panel.side_effect = fail
    result = mining_data._factor_mining_cross_universe_reference(
        ["close"], start_date=START, end_date=END, deps=deps
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == error_type.__name__
    assert result["data_version"] is None
    assert result["expressions"] == {}
    assert result["role"] == "report_only_not_for_selection_or_acceptance"
    assert result["significance_used_for_decision"] is False
    assert SECRET not in str(result)
    assert SECRET not in caplog.text
    assert error_type.__name__ in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("stage", ["expression", "analysis"])
def test_candidate_failure_does_not_block_other_reference_results(
    stage, monkeypatch, caplog
):
    deps = dependencies()
    panel = object()
    deps._factor_mining_comparison_panel.return_value = panel, "fixture-version"
    evaluated = Mock(values={})

    def evaluate(expression, _panel):
        if stage == "expression" and expression == "close":
            raise RuntimeError(SECRET) from OSError(SECRET)
        return evaluated

    def analyze(*_args, **_kwargs):
        if stage == "analysis" and analyze.calls == 0:
            analyze.calls += 1
            raise RuntimeError(SECRET) from OSError(SECRET)
        analyze.calls += 1
        return {"horizons": {"5": {"mean_ic": 0.2, "icir": 0.3, "observations": 20}}}

    analyze.calls = 0
    monkeypatch.setattr(mining_data, "evaluate_expression", evaluate)
    monkeypatch.setattr(mining_data, "evaluate_factor_panel", analyze)
    result = mining_data._factor_mining_cross_universe_reference(
        ["close", "volume", "volume"], start_date=START, end_date=END, deps=deps
    )
    assert result["status"] == "available"
    assert result["data_version"] == "fixture-version"
    assert result["expressions"] == {
        "close": {"status": "unavailable", "reason": "RuntimeError", "horizons": {}},
        "volume": {
            "status": "available",
            "horizons": {"5": {"mean_rank_ic": 0.2, "icir": 0.3, "observations": 20}},
        },
    }
    assert result["significance_used_for_decision"] is False
    assert SECRET not in str(result)
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_empty_reference_candidates_do_not_fetch_data():
    deps = dependencies()
    result = mining_data._factor_mining_cross_universe_reference(
        ["", " "], start_date=START, end_date=END, deps=deps
    )
    assert result["status"] == "not_needed"
    assert result["expressions"] == {}
    deps._factor_mining_comparison_panel.assert_not_called()
