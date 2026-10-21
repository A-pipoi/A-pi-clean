from datetime import timedelta

import pytest

from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.batch import aggregate_factor_mining_runs
from quant.mining.genetic import canonical_expression_hash, run_factor_mining
from tests.test_factor_mining import _bars


def test_canonical_identity_preserves_semantics_and_trial_count():
    assert canonical_expression_hash("close + volume") == canonical_expression_hash(" volume+close ")
    assert canonical_expression_hash("close+1") == canonical_expression_hash("1.0 + close")
    assert canonical_expression_hash("close-volume") != canonical_expression_hash("volume-close")
    batch = aggregate_factor_mining_runs([{"run_id":"one","n_tested":7,"result":{"candidates":[{"expression":"close + volume","fitness":1},{"expression":"volume+close","fitness":2}]}}])
    assert batch["n_tested"] == 7 and len(batch["candidates"]) == 1
    assert batch["candidates"][0]["expression"] == "volume+close"


def test_icir_fitness_is_training_only_cv_gated_and_reproducible():
    bars = _bars()
    end = bars[-1].date - timedelta(days=8)
    options = {"train_end": end, "seed": 41, "generations": 2, "population_size": 8, "fitness_mode": "icir", "correlation_lambda": .5, "complexity_mu": .0005, "temporal_cv": True}
    result = run_factor_mining(bars, **options)
    assert result.as_dict() == run_factor_mining([bar for bar in bars if bar.date<=end], **options).as_dict()
    assert result.n_tested == 16 and result.protocol["sealed_dates_used"] is False
    for candidate in result.candidates:
        assert candidate["cv_passed"] and len(candidate["cv_fold_mean_ic"]) == 3
        assert candidate["fitness"] == pytest.approx(candidate["icir"]-.5*candidate["max_abs_registry_correlation"]-candidate["complexity_penalty"])


def test_accepted_registry_correlation_is_hard_excluded_and_fields_are_bounded():
    bars=_bars()
    end=bars[-1].date
    panel=build_factor_panel(bars)
    close=evaluate_expression("close",panel).values
    result=run_factor_mining(bars,train_end=end,seed=1,generations=1,population_size=4,fitness_mode="icir",field_subset=["close"],existing_factor_panels=[close],temporal_cv=False)
    assert all(candidate["max_abs_registry_correlation"]<=.7 for candidate in result.candidates)
    with pytest.raises(ValueError, match="unavailable"):
        run_factor_mining(bars,train_end=end,field_subset=["future_return"])
    with pytest.raises(ValueError, match="finite"):
        run_factor_mining(bars,train_end=end,correlation_lambda=float("nan"))
