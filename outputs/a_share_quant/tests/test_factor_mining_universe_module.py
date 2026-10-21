from datetime import date

import pytest

from quant.data.schema import Bar
from quant.mining.universe import (
    FACTOR_MINING_UNIVERSE_VERSION,
    apply_factor_mining_universe,
    factor_mining_universe_payload,
    validate_factor_mining_universe_request,
)


def test_factor_mining_universe_payload_records_fixed_pit_sources() -> None:
    payload = factor_mining_universe_payload(
        {
            "snapshots": [{"trade_date": "2024-01-31", "members": [{"ts_code": "000001.SZ"}]}],
            "snapshot_count": 1,
            "first_snapshot": "2024-01-31",
            "last_snapshot": "2024-01-31",
            "data_version": "membership-v1",
        },
        {"000001.SZ": date(1991, 4, 3)},
        {"data_version": "listing-v2", "stale": False},
        ["000001.SZ", "600000.SH"],
        {"000001.SZ"},
        date(2024, 2, 1),
        date(2024, 3, 1),
    )

    assert payload["schema_version"] == FACTOR_MINING_UNIVERSE_VERSION
    assert payload["index_code"] == "000300.SH"
    assert payload["member_data_version"] == "membership-v1"
    assert payload["listing_data_version"] == "listing-v2"
    assert payload["candidate_symbol_count"] == 2
    assert payload["sample_symbol_count"] == 1
    assert payload["listing_dates"] == {"000001.SZ": "1991-04-03"}


def test_apply_factor_mining_universe_uses_membership_and_listing_age_per_day() -> None:
    bars = [
        Bar("000001.SZ", date(2024, 3, 1), 10, 10, 10, 10, 100),
        Bar("600000.SH", date(2024, 3, 1), 10, 10, 10, 10, 100),
        Bar("000001.SZ", date(2024, 6, 30), 10, 10, 10, 10, 100),
        Bar("600000.SH", date(2024, 6, 30), 10, 10, 10, 10, 100),
    ]
    payload = {
        "schema_version": FACTOR_MINING_UNIVERSE_VERSION,
        "index_code": "000300.SH",
        "snapshots": [
            {"trade_date": "2024-01-31", "members": [{"ts_code": "000001.SZ"}]},
            {"trade_date": "2024-04-30", "members": [{"ts_code": "000001.SZ"}, {"ts_code": "600000.SH"}]},
        ],
        "listing_dates": {"000001.SZ": "1991-04-03", "600000.SH": "2024-04-01"},
    }

    filtered = apply_factor_mining_universe(bars, payload)

    assert [(bar.date, bar.symbol) for bar in filtered] == [
        (date(2024, 3, 1), "000001.SZ"),
        (date(2024, 6, 30), "000001.SZ"),
        (date(2024, 6, 30), "600000.SH"),
    ]


def test_factor_mining_universe_fails_closed_on_wrong_schema_or_custom_universe() -> None:
    with pytest.raises(PermissionError, match="版本不匹配"):
        apply_factor_mining_universe([], {"schema_version": "unknown"})
    with pytest.raises(ValueError, match="不能切换指数"):
        validate_factor_mining_universe_request({"universe_index_code": "000905.SH"})
    with pytest.raises(ValueError, match="自选标的"):
        validate_factor_mining_universe_request({"symbols": ["000001.SZ"]})
