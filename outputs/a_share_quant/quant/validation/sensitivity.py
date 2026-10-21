from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median, pstdev
from typing import Any

from quant.errors import ProviderShapeError

from ..backtest.costs import CostModel
from ..backtest.engine import Strategy
from ..data.schema import Bar
from ..factors.store import FactorDefinition
from ..numeric import as_float as _as_float
from ..strategies import build_research_strategy
from .statistics import benjamini_hochberg, deflated_sharpe_p_value
from .strategy import validate_strategy


@dataclass(frozen=True)
class SensitivityResult:
    passed: bool
    trial_count: int
    delta: float
    isolated_peak: bool
    sign_stable: bool
    baseline: dict[str, Any]
    trials: tuple[dict[str, Any], ...]
    multiple_testing: dict[str, Any]
    multiple_testing_trials: int
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "trial_count": self.trial_count,
            "delta": self.delta,
            "isolated_peak": self.isolated_peak,
            "sign_stable": self.sign_stable,
            "baseline": self.baseline,
            "trials": list(self.trials),
            "multiple_testing": self.multiple_testing,
            "multiple_testing_trials": self.multiple_testing_trials,
            "note": self.note,
        }


def run_parameter_sensitivity(
    bars: Sequence[Bar],
    *,
    strategy_name: str,
    lookback: int,
    top_n: int,
    rebalance_every: int,
    initial_cash: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    delta: float = 0.30,
    factor_definitions: Sequence[FactorDefinition] | None = None,
    fundamental_values: dict[str, dict[str, float]] | None = None,
    extra_trials: int = 0,
) -> SensitivityResult:
    """Run a one-at-a-time ±delta robustness scan.

    One-at-a-time scanning intentionally keeps the research budget bounded:
    baseline + two variants for each of the three key parameters = seven
    trials.  It tests robustness without turning the endpoint into a hidden
    optimizer.
    """

    if not 0 < delta <= 0.5:
        raise ValueError("delta must be between 0 and 0.5")
    base = (int(lookback), int(top_n), int(rebalance_every))
    if min(base) <= 0:
        raise ValueError("sensitivity parameters must be positive")
    model = cost_model or CostModel(max_participation=0.10)
    variants = _variants(*base, delta=delta)
    statistical_trials = len(variants) + max(0, int(extra_trials))
    trial_rows: list[dict[str, Any]] = []
    for variant in variants:
        variant_lookback, variant_top_n, variant_rebalance = variant

        def strategy_factory(
            lb: int = variant_lookback,
            tn: int = variant_top_n,
            re: int = variant_rebalance,
        ) -> Strategy:
            return build_research_strategy(
                strategy_name,
                lookback=lb,
                top_n=tn,
                rebalance_every=re,
                factor_definitions=factor_definitions,
                fundamental_values=fundamental_values,
            )

        report = validate_strategy(
            bars,
            strategy_factory,
            initial_cash=initial_cash,
            cost_model=model,
            embargo=None,
            rebalance_every=variant_rebalance,
        )
        trial_rows.append(
            _trial_row(
                variant_lookback,
                variant_top_n,
                variant_rebalance,
                report,
                statistical_trials,
            )
        )

    p_values = [
        float(row["deflated_sharpe_p_value"])
        if row["deflated_sharpe_p_value"] is not None
        else 1.0
        for row in trial_rows
    ]
    correction = benjamini_hochberg(p_values)
    adjusted_values = correction.get("adjusted")
    rejected_values = correction.get("rejected")
    if not isinstance(adjusted_values, list) or not isinstance(rejected_values, list):
        raise ProviderShapeError("Benjamini-Hochberg correction returned an invalid payload")
    adjusted = [_as_float(value) for value in adjusted_values]
    rejected = [bool(value) for value in rejected_values]
    if len(adjusted) != len(trial_rows) or len(rejected) != len(trial_rows):
        raise RuntimeError("Benjamini-Hochberg correction length does not match trials")
    for index, row in enumerate(trial_rows):
        row["bh_adjusted_p_value"] = adjusted[index]
        row["bh_rejected_at_5pct"] = rejected[index]

    baseline_row = next(row for row in trial_rows if tuple(row["parameters"]) == base)
    annualized_values = [
        float(row["annualized_return"])
        for row in trial_rows
        if row["annualized_return"] is not None
    ]
    isolated_peak = _isolated_peak(annualized_values)
    sign_stable = _sign_stable(baseline_row.get("annualized_return"), annualized_values)
    all_validation_passed = all(bool(row["validation_passed"]) for row in trial_rows)
    passed = all_validation_passed and not isolated_peak and sign_stable
    note = (
        "参数 ±30% 扫描通过：相邻参数未出现孤立尖峰，收益方向稳定；结果仍需结合密封测试集。"
        if passed
        else "参数敏感性未通过：禁止把该策略结果当作上线结论，请检查参数尖峰、方向反转或样本外门槛。"
    )
    return SensitivityResult(
        passed=passed,
        trial_count=len(trial_rows),
        delta=delta,
        isolated_peak=isolated_peak,
        sign_stable=sign_stable,
        baseline=baseline_row,
        trials=tuple(trial_rows),
        multiple_testing={"method": "Benjamini-Hochberg", **correction},
        multiple_testing_trials=statistical_trials,
        note=note,
    )


def _variants(lookback: int, top_n: int, rebalance_every: int, *, delta: float) -> list[tuple[int, int, int]]:
    values = [(lookback, top_n, rebalance_every)]
    for index, base in enumerate((lookback, top_n, rebalance_every)):
        for multiplier in (1.0 - delta, 1.0 + delta):
            variant = max(1, round(base * multiplier))
            candidate = [lookback, top_n, rebalance_every]
            candidate[index] = variant
            item = (candidate[0], candidate[1], candidate[2])
            if item not in values:
                values.append(item)
    return values


def _trial_row(
    lookback: int,
    top_n: int,
    rebalance_every: int,
    report: Any,
    statistical_trials: int,
) -> dict[str, Any]:
    return {
        "parameters": [lookback, top_n, rebalance_every],
        "lookback": lookback,
        "top_n": top_n,
        "rebalance_every": rebalance_every,
        "validation_passed": report.passed,
        "folds": report.folds,
        "embargo": report.embargo,
        "oos_observations": report.oos_observations,
        "annualized_return": report.annualized_return,
        "sharpe": report.sharpe,
        "max_drawdown": report.max_drawdown,
        "deflated_sharpe_p_value": deflated_sharpe_p_value(
            report.sharpe,
            report.oos_observations,
            statistical_trials,
            effective_observations=getattr(report, "effective_observations", None),
        ),
        "checks": [
            {"name": item.name, "status": item.status, "evidence": item.evidence}
            for item in report.checks
        ],
    }


def _isolated_peak(values: list[float]) -> bool:
    if len(values) < 3:
        return True
    center = median(values)
    spread = pstdev(values)
    best = max(values)
    second = sorted(values)[-2]
    # Conservative guard: a large best-vs-second jump or a very wide scan is
    # treated as a tuning peak even if the absolute return is positive.
    return (best - second > max(0.10, 2.0 * spread)) or (best - center > max(0.15, 3.0 * spread))


def _sign_stable(baseline: float | None, values: list[float]) -> bool:
    if baseline is None or not values:
        return False
    if abs(baseline) < 1e-9:
        return max(values) - min(values) < 0.02
    baseline_sign = 1 if baseline > 0 else -1
    return all((value == 0 or (1 if value > 0 else -1) == baseline_sign) for value in values)
