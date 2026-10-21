from datetime import date, datetime, timedelta

from quant.data.observed_industry import ObservedIndustryStore
from quant.data.pit import SHANGHAI, materialize_pit_industry_observations


def test_first_observation_only_enters_next_exchange_session(tmp_path):
    store = ObservedIndustryStore(tmp_path / "industry.sqlite3")
    first_seen = datetime(2026, 10, 9, 10, 0, tzinfo=SHANGHAI)
    store.record({"A": "银行"}, first_seen, "first-response")
    sessions = [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]

    assert store.records(["A"], date(2026, 10, 8)) == []
    assert store.at(first_seen - timedelta(microseconds=1), ["A"])["rows"] == {}
    records = store.records(["A"], sessions[-1])
    rows = materialize_pit_industry_observations(records, sessions)

    assert len(rows) == 1
    assert rows[0].as_of == date(2026, 10, 12)
    assert rows[0].available_at == first_seen
    assert store.at(first_seen, ["A"])["historical_backfill"] is False


def test_later_observation_never_rewrites_earlier_classification(tmp_path):
    store = ObservedIndustryStore(tmp_path / "industry.sqlite3")
    first_seen = datetime(2026, 10, 9, 17, 0, tzinfo=SHANGHAI)
    changed = datetime(2026, 10, 12, 17, 0, tzinfo=SHANGHAI)
    store.record({"A": "银行"}, first_seen, "initial")
    before = store.records(["A"], first_seen.date())
    store.record({"A": "房地产", "B": "电子"}, changed, "later-response")

    assert store.records(["A"], first_seen.date()) == before
    assert store.at(first_seen, ["A", "B"])["missing_symbols"] == ["B"]
    rows = materialize_pit_industry_observations(
        store.records(["A", "B"], date(2026, 10, 13)),
        [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)],
    )
    assert [(row.as_of, row.symbol, row.industry) for row in rows] == [
        (date(2026, 10, 12), "A", "银行"),
        (date(2026, 10, 13), "A", "房地产"),
        (date(2026, 10, 13), "B", "电子"),
    ]
