"""Non-overlapping, next-open layer diagnostics, explicitly not execution evidence."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, cast

from quant.backtest.costs import CostModel
from quant.factors.analysis import _correlation, _rank
from quant.factors.expression import FactorPanel, Panel


def layered_diagnostics(
    panel: FactorPanel, values: Panel, *, horizon: int = 5, quantiles: int = 5
) -> dict:
    if type(horizon) is not int or not 1 <= horizon <= 20 or quantiles not in {5, 10}:
        raise ValueError("layer diagnostics require horizon 1–20 and 5 or 10 layers")
    prices = panel.fields["open"]
    layers = [1.0] * quantiles
    gross_nav = 1.0
    net_nav = {rate: 1.0 for rate in (0.05, 0.10, 0.20)}
    curve: list[dict[str, Any]] = []
    layer_returns: list[list[float]] = [[] for _ in range(quantiles)]
    skipped = 0
    cost = CostModel()
    for signal in range(0, len(panel.dates) - horizon - 1, horizon):
        entry, exit_row = signal + 1, signal + 1 + horizon
        selected = [
            i
            for i, value in enumerate(values[signal])
            if value is not None and math.isfinite(value)
        ]
        selected.sort(key=lambda i: (values[signal][i], panel.symbols[i]))
        # Never silently drop symbols using future price availability (survivorship bias).
        if len(selected) < quantiles or any(
            prices[row][i] is None
            or not math.isfinite(cast(float, prices[row][i]))
            or cast(float, prices[row][i]) <= 0
            for row in (entry, exit_row)
            for i in selected
        ):
            skipped += 1
            continue
        returns: list[list[float]] = [[] for _ in range(quantiles)]
        for position, column in enumerate(selected):
            bucket = min(quantiles - 1, position * quantiles // len(selected))
            returns[bucket].append(
                cast(float, prices[exit_row][column])
                / cast(float, prices[entry][column])
                - 1
            )
        averages = [sum(bucket) / len(bucket) for bucket in returns]
        for i, value in enumerate(averages):
            layer_returns[i].append(value)
            layers[i] *= 1 + value
        spread = averages[-1] - averages[0]
        if spread <= -1:
            return {
                "status": "invalid_leveraged_nav",
                "observations": len(curve),
                "note": "The theoretical long-short NAV became nonpositive; annualization is disabled.",
            }
        gross_nav *= 1 + spread
        # Two legs, explicit one-way daily turnover scenarios, no zero-cost defaults.
        round_trip = (
            2 * cost.commission_rate
            + cost.stamp_rate(panel.dates[exit_row])
            + 2 * cost.transfer_fee_rate
            + 2 * cost.fixed_slippage_bps / 10000
        )
        for rate in net_nav:
            net_nav[rate] *= 1 + spread - 2 * rate * horizon * round_trip
        curve.append(
            {
                "signal_date": panel.dates[signal].isoformat(),
                "entry_date": panel.dates[entry].isoformat(),
                "exit_date": panel.dates[exit_row].isoformat(),
                "layers": list(layers),
                "long_short_gross_nav": gross_nav,
                "long_short_net_nav": {str(rate): nav for rate, nav in net_nav.items()},
            }
        )
    mean_returns = [
        sum(bucket) / len(bucket) if bucket else None for bucket in layer_returns
    ]
    periods = len(curve)
    elapsed = (
        panel.dates.index(date.fromisoformat(curve[-1]["exit_date"]))
        - panel.dates.index(date.fromisoformat(curve[0]["entry_date"]))
        if periods
        else 0
    )
    annualized = [
        nav ** (252 / elapsed) - 1 if periods >= 2 and elapsed > 0 and nav > 0 else None
        for nav in layers
    ]
    monotonicity = (
        _correlation(
            _rank(list(range(quantiles))), _rank(cast(list[float], mean_returns))
        )
        if periods
        else None
    )
    return {
        "status": "available" if periods else "insufficient_data",
        "horizon": horizon,
        "quantiles": quantiles,
        "observations": periods,
        "skipped_blocks": skipped,
        "layer_annualized_returns": annualized,
        "monotonicity_spearman": monotonicity,
        "curve": curve,
        "cost_assumptions": {
            "commission_rate": cost.commission_rate,
            "fixed_slippage_bps": cost.fixed_slippage_bps,
            "stamp_tax": "existing dated CostModel schedule",
            "daily_one_way_turnover": [0.05, 0.10, 0.20],
            "gross_exposure": 2,
        },
        "protocol": {
            "signal": "close-derived factor",
            "entry": "next session open",
            "exit": "open after horizon sessions",
            "overlapping_returns": False,
            "sealed_dates_used": False,
            "execution_backtest": False,
        },
        "note": "Signal-price theoretical layer diagnostic, not executable A-share shorting evidence. Minimum commission, lots, price limits, suspension, borrow fees, financing and market impact are not simulated. Cost scenarios are assumptions, not realized turnover. Skipped blocks are reported, not filled.",
    }
