from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quant.api.mining_acceptance import _accept_factor_mining_run


@pytest.mark.parametrize(
    "candidate",
    [
        {"source": "external_deep", "model_id": "model"},
        {"factor_type": "external_deep", "expression": "close"},
        {"model_id": "model", "expression": "close"},
    ],
)
def test_unwired_deep_candidate_cannot_open_or_consume_sealed(candidate):
    ledger = Mock()
    ledger.factor_mining_run.return_value = {"sealed_test_id": 1}
    ledger.factor_mining_runs_for_sealed_test.return_value = [
        {"result": {"candidates": [{"expression": "close"}, candidate]}}
    ]
    dependencies = SimpleNamespace(RESEARCH_LEDGER=ledger, _as_int=int)
    with pytest.raises(PermissionError, match="not connected"):
        _accept_factor_mining_run("run", deps=dependencies)
    ledger.get_sealed_test.assert_not_called()
    ledger.claim_factor_mining_acceptance.assert_not_called()
