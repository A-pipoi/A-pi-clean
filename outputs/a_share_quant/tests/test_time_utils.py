from datetime import UTC, date, datetime

import pytest

from quant.time_utils import SHANGHAI_TZ, china_today, parse_yyyymmdd


def test_missing_zoneinfo_has_current_market_utc8_fallback():
    import runpy
    from datetime import timedelta
    from pathlib import Path
    from unittest.mock import patch
    from zoneinfo import ZoneInfoNotFoundError

    with patch("zoneinfo.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing")):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "quant/time_utils.py"))
    assert namespace["SHANGHAI_TZ"].utcoffset(None) == timedelta(hours=8)
    assert namespace["SHANGHAI_TZ"].tzname(None) == "CST"


def test_china_today_uses_shanghai_calendar_date() -> None:
    assert china_today() == datetime.now(UTC).astimezone(SHANGHAI_TZ).date()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20261010", date(2026, 10, 10)),
        ("20240229", date(2024, 2, 29)),
    ],
)
def test_parse_yyyymmdd_returns_a_calendar_date(text: str, expected: date) -> None:
    assert parse_yyyymmdd(text) == expected


@pytest.mark.parametrize("text", ["20230229", "2026-10-10", "2026101", "２０２６１０１０"])
def test_parse_yyyymmdd_rejects_invalid_or_noncanonical_dates(text: str) -> None:
    with pytest.raises(ValueError):
        parse_yyyymmdd(text)
