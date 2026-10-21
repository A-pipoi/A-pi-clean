from datetime import date

import pytest

from quant.api.parsing import (
    MAX_RESEARCH_SYMBOLS,
    bounded_int,
    normalize_paper_symbol,
    optional_date,
    symbols,
    symbols_from_query,
)


def test_bounded_int_accepts_integer_values_and_rejects_fractional_values() -> None:
    assert bounded_int("5", 1, 10) == 5
    assert bounded_int(5.0, 1, 10) == 5
    with pytest.raises(ValueError, match="integer"):
        bounded_int(5.2, 1, 10)
    with pytest.raises(ValueError, match="integer"):
        bounded_int(True, 1, 10)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_bounded_int_rejects_nonfinite_floats_with_validation_error(value) -> None:
    with pytest.raises(ValueError, match="finite integer"):
        bounded_int(value, 1, 10)


@pytest.mark.parametrize("value", [None, [], {}, True])
def test_bounded_int_keeps_value_error_contract_for_invalid_input(value) -> None:
    with pytest.raises(ValueError, match="integer"):
        bounded_int(value, 1, 10)


def test_symbols_enforce_non_empty_and_project_limit() -> None:
    assert symbols(["000001.SZ", " 600036.SH "]) == ["000001.SZ", "600036.SH"]
    with pytest.raises(ValueError, match="non-empty"):
        symbols([])
    with pytest.raises(ValueError, match=str(MAX_RESEARCH_SYMBOLS)):
        symbols([str(index) for index in range(MAX_RESEARCH_SYMBOLS + 1)])


def test_symbol_and_date_normalization() -> None:
    assert normalize_paper_symbol("600036") == "600036.SH"
    assert normalize_paper_symbol("000001.sz") == "000001.SZ"
    assert optional_date("2026-09-29T00:00:00") == date(2026, 9, 29)
    assert optional_date("") is None
    assert symbols_from_query(["000001.SZ，600036.SH"]) == ["000001.SZ", "600036.SH"]
