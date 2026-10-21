"""Factor registration cannot bypass governance or leave orphan definitions."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from quant.api.research_mutations import ResearchMutationDependencies, register_factor


def _deps(*, locked: bool = False, sealed: bool = False, can_record: bool = True, record=None):
    ledger = Mock()
    ledger.has_active_sealed_result.return_value = sealed
    ledger.can_record.return_value = can_record
    factors = Mock()
    factors.register.return_value = Mock(key="custom-test", factor_type="momentum", window=20, metric="", as_dict=lambda: {"key": "custom-test"})
    factors.custom_count.return_value = 1
    factors.definitions.return_value = []
    deps = ResearchMutationDependencies(
        ledger=ledger,
        factors=factors,
        factor_lock=lambda: {"locked": locked, "reason": "锁定"},
        record_experiment=record or Mock(return_value=12),
        factor_pipeline_version="v1",
    )
    return deps


@pytest.mark.parametrize("locked,sealed", [(True, False), (False, True)])
def test_registration_respects_factor_gate(locked: bool, sealed: bool) -> None:
    deps = _deps(locked=locked, sealed=sealed)
    status, payload = register_factor({"name": "测试", "factor_type": "momentum"}, deps)
    assert status == 409
    assert payload["code"] == "factor_registry_locked"
    deps.factors.register.assert_not_called()


def test_registration_rolls_back_definition_when_ledger_record_fails() -> None:
    def fail(*_args: object) -> int:
        raise RuntimeError("ledger unavailable")

    deps = _deps(record=fail)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        register_factor({"name": "测试", "factor_type": "momentum"}, deps)
    deps.factors.unregister.assert_called_once_with("custom-test")
