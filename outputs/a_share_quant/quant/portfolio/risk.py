from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RiskLimits:
    max_single_weight: float = 0.10
    max_industry_weight: float = 0.30
    max_gross_leverage: float = 1.0
    max_drawdown: float = 0.15
    max_annualized_volatility: float | None = None
    max_var_95: float | None = None
    max_es_95: float | None = None
    max_daily_variance: float | None = None
    max_pairwise_correlation: float | None = None


@dataclass(frozen=True)
class RiskViolation:
    rule: str
    message: str


PAPER_RISK_LIMITS = RiskLimits(
    max_single_weight=0.25,
    max_industry_weight=0.50,
    max_gross_leverage=1.0,
    max_drawdown=0.15,
    # These two daily pre-trade caps are mathematically tied to the existing
    # 15% annual volatility budget: 1.645 * 15% / sqrt(252).
    max_annualized_volatility=0.15,
    max_var_95=1.645 * 0.15 / math.sqrt(252.0),
    max_es_95=2.0627 * 0.15 / math.sqrt(252.0),
    max_daily_variance=(0.15 / math.sqrt(252.0)) ** 2,
    max_pairwise_correlation=0.95,
)


def estimate_portfolio_risk(
    weights: Mapping[str, float],
    daily_returns: Mapping[str, Mapping[date, float]],
    *,
    window: int = 60,
    min_observations: int = 20,
) -> dict[str, object]:
    """Estimate rolling full-covariance portfolio volatility and normal VaR.

    The as-of caller must provide only returns known before its order cutoff.
    This is a pre-trade daily risk estimate, not intraday VaR or a tail-loss
    guarantee. Missing common history is explicit and cannot be treated as zero.
    """

    active = {str(symbol): float(weight) for symbol, weight in weights.items() if float(weight) > 0}
    if not active:
        return {"status": "insufficient_data", "observations": 0, "annualized_volatility": None, "var_95": None}
    if any(not math.isfinite(weight) for weight in active.values()):
        raise ValueError("portfolio weights must be finite")
    symbols = sorted(active)
    if any(symbol not in daily_returns for symbol in symbols):
        common_dates: set[date] = set()
    else:
        common_dates = set(daily_returns[symbols[0]])
        for symbol in symbols[1:]:
            common_dates.intersection_update(daily_returns[symbol])
    dates = sorted(common_dates)[-max(2, int(window)):]
    if len(dates) < min_observations:
        return {
            "status": "insufficient_data",
            "observations": len(dates),
            "annualized_volatility": None,
            "var_95": None,
        }
    portfolio_returns = [
        sum(active[symbol] * float(daily_returns[symbol][day]) for symbol in symbols)
        for day in dates
    ]
    if any(not math.isfinite(value) for value in portfolio_returns):
        return {
            "status": "invalid_data",
            "observations": len(dates),
            "annualized_volatility": None,
            "var_95": None,
        }
    mean = sum(portfolio_returns) / len(portfolio_returns)
    variance = sum((value - mean) ** 2 for value in portfolio_returns) / max(1, len(portfolio_returns) - 1)
    daily_volatility = math.sqrt(max(0.0, variance))
    asset_means = {
        symbol: sum(float(daily_returns[symbol][day]) for day in dates) / len(dates)
        for symbol in symbols
    }
    asset_variances = {
        symbol: sum(
            (float(daily_returns[symbol][day]) - asset_means[symbol]) ** 2
            for day in dates
        ) / max(1, len(dates) - 1)
        for symbol in symbols
    }
    max_pairwise_correlation: float | None = None
    correlations: list[float] = []
    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1:]:
            denominator = math.sqrt(max(0.0, asset_variances[left] * asset_variances[right]))
            if denominator <= 1e-15:
                continue
            covariance = sum(
                (float(daily_returns[left][day]) - asset_means[left])
                * (float(daily_returns[right][day]) - asset_means[right])
                for day in dates
            ) / max(1, len(dates) - 1)
            correlations.append(max(-1.0, min(1.0, covariance / denominator)))
    if correlations:
        max_pairwise_correlation = max(abs(value) for value in correlations)
    return {
        "status": "available",
        "observations": len(dates),
        "window": window,
        "annualized_volatility": daily_volatility * math.sqrt(252.0),
        "var_95": 1.645 * daily_volatility,
        "es_95": 2.0627 * daily_volatility,
        "daily_variance": variance,
        "max_pairwise_correlation": max_pairwise_correlation,
        "basis": "pre_trade_daily_full_covariance_normal_approximation",
        "as_of": max(dates).isoformat(),
    }


def check_portfolio_limits(
    positions: dict[str, float],
    prices: dict[str, float],
    industries: dict[str, str | None],
    equity: float,
    limits: RiskLimits | None = None,
) -> list[RiskViolation]:
    limits = limits or RiskLimits()
    if equity <= 0:
        return [RiskViolation("equity", "equity must be positive")]
    values = {symbol: quantity * prices.get(symbol, 0.0) for symbol, quantity in positions.items()}
    gross = sum(max(value, 0.0) for value in values.values())
    violations: list[RiskViolation] = []
    if gross / equity > limits.max_gross_leverage + 1e-12:
        violations.append(RiskViolation("gross_leverage", f"gross leverage {gross / equity:.3f} exceeds limit"))
    for symbol, value in values.items():
        if value / equity > limits.max_single_weight + 1e-12:
            violations.append(RiskViolation("single_name", f"{symbol} weight {value / equity:.3f} exceeds limit"))
    by_industry: dict[str, float] = {}
    for symbol, value in values.items():
        group = industries.get(symbol) or "UNKNOWN"
        by_industry[group] = by_industry.get(group, 0.0) + value
    for group, value in by_industry.items():
        if value / equity > limits.max_industry_weight + 1e-12:
            violations.append(RiskViolation("industry", f"{group} weight {value / equity:.3f} exceeds limit"))
    return violations


def check_order_limits(
    positions: dict[str, int | float],
    prices: dict[str, float],
    industries: dict[str, str | None],
    equity: float,
    symbol: str,
    side: str,
    quantity: int,
    limits: RiskLimits = PAPER_RISK_LIMITS,
    current_drawdown: float | None = None,
    risk_estimate: Mapping[str, object] | None = None,
) -> list[RiskViolation]:
    """Check the projected portfolio before a new paper order is accepted.

    Sell orders are always allowed to reduce an existing exposure. Buy orders
    must satisfy the configured single-name, industry and gross-exposure caps.
    The limits are intentionally fixed for paper trading and are not learned
    from the historical sample.
    """
    side = str(side).upper().strip()
    if quantity <= 0 or side not in {"BUY", "SELL"}:
        return [RiskViolation("order", "invalid side or quantity")]
    if side == "SELL":
        return []
    projected = {key: float(value) for key, value in positions.items()}
    projected[symbol] = projected.get(symbol, 0.0) + quantity
    violations = check_portfolio_limits(projected, prices, industries, equity, limits)
    if current_drawdown is not None and current_drawdown > limits.max_drawdown + 1e-12:
        violations.append(
            RiskViolation(
                "drawdown",
                f"current drawdown {current_drawdown:.3f} exceeds limit {limits.max_drawdown:.3f}",
            )
        )
    if risk_estimate is not None and any((
        limits.max_annualized_volatility is not None,
        limits.max_var_95 is not None,
        limits.max_es_95 is not None,
        limits.max_daily_variance is not None,
        limits.max_pairwise_correlation is not None,
    )):
        if risk_estimate.get("status") != "available":
            violations.append(
                RiskViolation("risk_data", "pre-trade covariance risk estimate unavailable; buy blocked")
            )
        else:
            annualized = risk_estimate.get("annualized_volatility")
            var_95 = risk_estimate.get("var_95")
            es_95 = risk_estimate.get("es_95")
            daily_variance = risk_estimate.get("daily_variance")
            max_correlation = risk_estimate.get("max_pairwise_correlation")
            if (
                limits.max_annualized_volatility is not None
                and isinstance(annualized, (int, float))
                and annualized > limits.max_annualized_volatility + 1e-12
            ):
                violations.append(
                    RiskViolation(
                        "volatility_circuit_breaker",
                        f"projected annualized volatility {annualized:.3f} exceeds {limits.max_annualized_volatility:.3f}; buy blocked",
                    )
                )
            if (
                limits.max_var_95 is not None
                and isinstance(var_95, (int, float))
                and var_95 > limits.max_var_95 + 1e-12
            ):
                violations.append(
                    RiskViolation(
                        "var_95",
                        f"projected daily VaR 95% {var_95:.4f} exceeds {limits.max_var_95:.4f}; buy blocked",
                    )
                )
            for rule, actual, threshold, label in (
                ("expected_shortfall_95", es_95, limits.max_es_95, "projected daily ES 95%"),
                ("covariance_limit", daily_variance, limits.max_daily_variance, "projected portfolio daily variance"),
                ("correlation_limit", max_correlation, limits.max_pairwise_correlation, "maximum absolute pairwise correlation"),
            ):
                if (
                    threshold is not None
                    and isinstance(actual, (int, float))
                    and actual > threshold + 1e-12
                ):
                    violations.append(
                        RiskViolation(
                            rule,
                            f"{label} {actual:.4f} exceeds {threshold:.4f}; buy blocked",
                        )
                    )
    return violations
