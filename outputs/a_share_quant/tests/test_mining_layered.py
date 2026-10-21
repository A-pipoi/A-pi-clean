from dataclasses import replace

import pytest

from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.layered import layered_diagnostics
from tests.test_factor_mining import _bars


def test_layers_are_next_open_nonoverlapping_and_higher_cost_lowers_nav():
    panel = build_factor_panel(_bars(), data_version="test")
    values = evaluate_expression("close", panel).values
    result = layered_diagnostics(panel, values)
    assert result["status"] == "available"
    curve = result["curve"]
    assert curve[0]["signal_date"] < curve[0]["entry_date"] < curve[0]["exit_date"]
    assert curve[0]["exit_date"] <= curve[1]["entry_date"]
    last = curve[-1]
    assert (
        last["long_short_gross_nav"]
        > last["long_short_net_nav"]["0.05"]
        > last["long_short_net_nav"]["0.1"]
        > last["long_short_net_nav"]["0.2"]
    )
    assert result["protocol"]["execution_backtest"] is False


def test_missing_future_price_skips_entire_block_not_symbol():
    panel = build_factor_panel(_bars(), data_version="test")
    prices = [list(row) for row in panel.fields["open"]]
    prices[6][0] = None
    panel = replace(panel, fields={**panel.fields, "open": prices})
    result = layered_diagnostics(panel, evaluate_expression("close", panel).values)
    assert result["skipped_blocks"] >= 1
    assert result["curve"][0]["signal_date"] != panel.dates[0].isoformat()


def test_insufficient_layers_or_bad_horizon_do_not_invent_returns():
    panel = build_factor_panel(_bars(6), data_version="test")
    values = evaluate_expression("close", panel).values
    result = layered_diagnostics(panel, values, quantiles=10)
    assert result["status"] == "insufficient_data"
    assert result["layer_annualized_returns"] == [None] * 10
    with pytest.raises(ValueError):
        layered_diagnostics(panel, values, horizon=0)
