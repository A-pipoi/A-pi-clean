import pytest

from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.diagnostics import (
    ic_half_life,
    registry_correlation_matrix,
    registry_diagnostics,
)
from tests.test_factor_mining import _bars


def test_half_life_does_not_invent_a_crossing_or_missing_ic():
    assert ic_half_life({"1": {"mean_ic": None}})["status"] == "unavailable"
    observed = ic_half_life(
        {"1": {"mean_ic": -0.1}, "2": {"mean_ic": -0.08}, "3": {"mean_ic": -0.04}}
    )
    assert observed["half_life"] == 3
    assert (
        ic_half_life({"1": {"mean_ic": 0.1}, "20": {"mean_ic": 0.08}})["status"]
        == "right_censored"
    )


def test_duplicate_factor_has_full_correlation_and_no_incremental_signal():
    panel = build_factor_panel(_bars(), data_version="training-fixture")
    values = evaluate_expression("close", panel).values
    report = registry_diagnostics(panel, values, {"existing": values})
    assert report["max_corr_with_registry"] == pytest.approx(1)
    assert report["correlations"][0]["spearman"] == pytest.approx(1)
    assert report["incremental_rank_ic"] is None
    assert report["sealed_dates_used"] is False


def test_matrix_is_symmetric_and_missing_constant_cells_stay_null():
    from types import SimpleNamespace

    panel = SimpleNamespace(dates=["2025-01-03"], symbols=["a", "b", "c"])
    report = registry_correlation_matrix(
        panel,
        {
            "positive": [[1.0, 2.0, 3.0]],
            "negative": [[3.0, 2.0, 1.0]],
            "missing": None,
            "constant": [[1.0, 1.0, 1.0]],
        },
    )
    keys = report["keys"]
    cells = report["matrix"]
    a, b = keys.index("positive"), keys.index("negative")
    assert cells[a][b] == cells[b][a]
    assert cells[a][b]["pearson"] == pytest.approx(-1)
    assert cells[a][b]["spearman"] == pytest.approx(-1)
    assert cells[a][b]["paired_samples"] == 3
    for unavailable in ("missing", "constant"):
        assert cells[a][keys.index(unavailable)]["pearson"] is None
    assert report["sealed_dates_used"] is False
    with pytest.raises(ValueError, match="dimensions"):
        registry_correlation_matrix(panel, {"bad": [[1.0, 2.0]]})
