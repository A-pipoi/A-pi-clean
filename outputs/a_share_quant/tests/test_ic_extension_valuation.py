from datetime import date, datetime

from quant.api.ic_scheduler import valued_extension_signals
from quant.factors.live_ic import SHANGHAI, LiveICStore


def test_missing_valuation_is_missing_coverage_not_scheduler_crash(tmp_path):
    values = valued_extension_signals(
        {"A": 1.0, "B": 2.0, "OUTSIDE": 3.0}, {"A", "B"}, {"A": 10.0}
    )
    assert values == {"A": 1.0}
    store = LiveICStore(tmp_path / "live.sqlite3")
    assert store.freeze(
        date(2026, 10, 9),
        "version",
        "field",
        values,
        {"A": 10.0},
        2,
        "real-observation-hash",
        now=datetime(2026, 10, 9, 17, tzinfo=SHANGHAI),
    )
    monitor = store.report()["monitors"][0]
    assert monitor["latest_factor_coverage"] == 0.5
    assert monitor["signal_status"] == "insufficient_factor_coverage"
    assert monitor["ic_series"] == []
