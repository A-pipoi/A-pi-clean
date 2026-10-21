from datetime import date
from unittest.mock import patch

import pytest

from quant.api.resource_limits import ResourceLimitExceeded
from quant.api.serialization import bar_to_payload, bars_from_payload
from quant.data import adapters
from quant.data.schema import Bar
from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.genetic import _parse_generated_expression, _render


def valid_row():
    return {
        "symbol": "000001.SZ",
        "date": "2024-01-02",
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10,
        "volume": 100,
    }


@pytest.mark.parametrize(
    "field",
    [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "limit_up",
        "limit_down",
        "signal_open",
        "signal_high",
        "signal_low",
        "signal_close",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_snapshot_rejects_nonfinite_required_and_optional_numbers(field, value):
    row = valid_row()
    row[field] = value
    with pytest.raises(ValueError, match=field):
        bars_from_payload([row])


@pytest.mark.parametrize("field", ["paused", "listed", "is_st", "is_delisted"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_snapshot_flags_cannot_be_truthiness_converted(field, value):
    row = valid_row()
    row[field] = value
    with pytest.raises(ValueError, match=field):
        bars_from_payload([row])


@pytest.mark.parametrize(
    "field", ["symbol", "date", "open", "high", "low", "close", "volume"]
)
def test_missing_snapshot_fields_raise_validation_error_not_key_error(field):
    row = valid_row()
    del row[field]
    with pytest.raises(ValueError, match="必要"):
        bars_from_payload([row])


def test_snapshot_valid_defaults_numeric_strings_and_roundtrip_remain_supported():
    row = valid_row()
    row["open"] = "10"
    bar = bars_from_payload([row])[0]
    assert bar.listed is True and bar.paused is False
    assert bar.is_st is False and bar.is_delisted is False
    assert bars_from_payload([bar_to_payload(bar)]) == [bar]
    assert bars_from_payload([]) == []


@pytest.mark.parametrize("field", ["open", "volume", "limit_up"])
def test_snapshot_boolean_is_not_a_number(field):
    row = valid_row()
    row[field] = True
    with pytest.raises(ValueError, match=field):
        bars_from_payload([row])


@pytest.mark.parametrize(
    "expression",
    ["close > open", "not (close > open)", "(close > open) and (volume > 0)"],
)
def test_gp_unsupported_nodes_are_rejected_not_replaced_with_close(expression):
    with pytest.raises(ValueError, match="GP 转换不支持"):
        _parse_generated_expression(expression)


@pytest.mark.parametrize(
    "expression", ["close", "close / open - 1", "-close", "+close", "ts_mean(close, 5)"]
)
def test_gp_supported_expression_roundtrip_is_stable(expression):
    rendered = _render(_parse_generated_expression(expression))
    assert _render(_parse_generated_expression(rendered)) == rendered
    if expression == "+close":
        assert rendered == "close"
    elif expression != "close":
        assert rendered != "close"


@pytest.mark.parametrize("expression", ["-1", "-0.5", "-close", "-(-close)"])
def test_negative_constant_does_not_expand_each_generation(expression):
    rendered = _render(_parse_generated_expression(expression))
    for _ in range(5):
        assert _render(_parse_generated_expression(rendered)) == rendered


@pytest.mark.parametrize(
    "expression",
    [
        "-1",
        "-0.5",
        "-close",
        "-(-close)",
        "+close",
        "close / open - 1",
        "ts_mean(close, 2)",
    ],
)
def test_gp_roundtrip_preserves_values_on_isolated_prices(expression):
    panel = build_factor_panel(
        [
            Bar("000001.SZ", date(2024, 1, 2), 10, 11, 9, 10, 100),
            Bar("000001.SZ", date(2024, 1, 3), 10, 12, 9, 11, 200),
        ]
    )
    converted = _render(_parse_generated_expression(expression))
    actual = evaluate_expression(converted, panel)
    expected = evaluate_expression(expression, panel)
    assert actual.values == expected.values
    assert actual.dates == expected.dates
    assert actual.symbols == expected.symbols


def test_adjustment_quota_stops_before_next_symbol_and_sleep():
    error = ResourceLimitExceeded("tushare_hourly_budget", 900)
    with (
        patch.object(adapters, "_tushare_http_query", side_effect=error) as query,
        patch.object(adapters.time, "sleep") as sleep,
        pytest.raises(ResourceLimitExceeded) as raised,
    ):
        adapters.fetch_tushare_adjustment_health(
            ["000001.SZ", "600036.SH"],
            token="offline-test-token",
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
        )
    assert raised.value is error and raised.value.retry_after == 900
    query.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("quota_stage", ["batch", "fallback"])
def test_index_quota_is_never_retried_as_per_index_fallback(quota_stage):
    error = ResourceLimitExceeded("tushare_hourly_budget", 900)
    batch_error = (
        error if quota_stage == "batch" else RuntimeError("offline-batch-unavailable")
    )
    with (
        patch.object(adapters, "_tushare_http_query", side_effect=batch_error) as query,
        patch.object(adapters, "fetch_tushare_index", side_effect=error) as fallback,
        pytest.raises(ResourceLimitExceeded) as raised,
    ):
        adapters.fetch_tushare_indices(
            ["000001.SH", "399001.SZ"], token="offline-test-token", force_refresh=True
        )
    assert raised.value is error and raised.value.retry_after == 900
    query.assert_called_once()
    if quota_stage == "batch":
        fallback.assert_not_called()
    else:
        fallback.assert_called_once()


def test_adjustment_provider_error_remains_unavailable_and_redacted(caplog):
    with patch.object(
        adapters, "_tushare_http_query", side_effect=RuntimeError("token=never-log")
    ):
        result = adapters.fetch_tushare_adjustment_health(
            ["000001.SZ"], token="offline-test-token"
        )
    assert result["symbols"][0]["available"] is False
    assert result["symbols"][0]["error"] == "RuntimeError"
    assert "never-log" not in str(result) + caplog.text
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
