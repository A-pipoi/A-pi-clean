import random
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quant.api.factor_panels import factor_values_for_definition
from quant.api.mining_research import _evaluate_factor_expression_request
from quant.factors import FactorRegistryStore
from quant.research import ResearchLedger
from tests.test_factor_mining import _bars


@pytest.mark.parametrize("weighting", ["equal", "icir"])
def test_composition_counts_components_and_new_hypothesis_without_activation(
    tmp_path, weighting
):
    rng = random.Random(13)
    bars = []
    for bar in _bars():
        close = bar.close + rng.uniform(-0.4, 0.4)
        bars.append(
            replace(
                bar, close=close, open=close - 0.05, high=close + 0.2, low=close - 0.2
            )
        )
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    store = FactorRegistryStore(tmp_path / "factors.sqlite3")
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=store,
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: (
            {
                "id": 17,
                "data_version": "v1",
                "sealed_start": "2026-01-01",
                "sealed_end": "2026-01-31",
            },
            [],
            bars,
        ),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _factor_values_for_definition=factor_values_for_definition,
        _object_mapping=lambda item: item if isinstance(item, dict) else {},
        _factor_mining_cross_universe_reference=lambda *_, **__: {"expressions": {}},
    )
    active_before = [
        definition.key for definition in store.definitions(enabled_only=True)
    ]
    run = _evaluate_factor_expression_request(
        {
            "mode": "compose",
            "factor_keys": ["builtin_momentum_20", "builtin_volatility_20"],
            "weighting": weighting,
        },
        deps=deps,
    )
    assert run["result"]["composition"]["research_only"]
    assert ledger.factor_mining_trial_total(17) == 3
    assert [
        definition.key for definition in store.definitions(enabled_only=True)
    ] == active_before
