from typing import Any

from .sealed import SealedTestReport, run_sealed_test
from .strategy import StrategyValidationReport, ValidationCheck, validate_strategy
from .walk_forward import Fold, PurgedWalkForward

__all__ = [
    "Fold",
    "PurgedWalkForward",
    "SealedTestReport",
    "SensitivityResult",
    "StrategyValidationReport",
    "ValidationCheck",
    "run_parameter_sensitivity",
    "run_sealed_test",
    "validate_strategy",
]


def __getattr__(name: str) -> Any:
    # Sensitivity imports the strategy registry. Keep it lazy so importing a
    # factor statistic (which imports validation.statistics) does not cycle
    # through strategies -> factors -> validation -> sensitivity -> strategies.
    if name in {"SensitivityResult", "run_parameter_sensitivity"}:
        from .sensitivity import SensitivityResult, run_parameter_sensitivity

        return {
            "SensitivityResult": SensitivityResult,
            "run_parameter_sensitivity": run_parameter_sensitivity,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
