from datetime import UTC, datetime

from quant.api.market_routes import SHANGHAI_TZ, _market_phase, _parse_quote_time


def test_market_phase_uses_a_share_local_clock() -> None:
    monday_open_utc = datetime(2026, 10, 12, 1, 30, tzinfo=UTC)
    local_now = monday_open_utc.astimezone(SHANGHAI_TZ)

    assert _market_phase(local_now) == ("交易中", True)


def test_naive_provider_quote_time_is_interpreted_as_shanghai_local() -> None:
    parsed = _parse_quote_time("2026-10-12 09:30:00")

    assert parsed == datetime(2026, 10, 12, 9, 30, tzinfo=SHANGHAI_TZ)


def test_offset_provider_quote_time_is_normalized_to_shanghai() -> None:
    parsed = _parse_quote_time("2026-10-12T01:30:00+00:00")

    assert parsed == datetime(2026, 10, 12, 9, 30, tzinfo=SHANGHAI_TZ)
