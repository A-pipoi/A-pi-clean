import sqlite3
from contextlib import closing
from datetime import datetime

import pytest

from quant.research.deep_evidence import canonical
from quant.research.forward_draft import build_draft
from quant.research.forward_samples import ForwardSamples


def now(day, hour):
    return datetime.fromisoformat(day + f"T{hour:02d}:00:00+08:00")


def test_empty_cohort_cannot_be_supplemented_with_replay(tmp_path):
    store = ForwardSamples(
        tmp_path / "samples.sqlite3", version="fixture", now=now("2026-10-13", 8)
    )
    with pytest.raises(PermissionError, match="1000"):
        build_draft(store.path, 5, "2026-10-20", now("2026-10-20", 18))


@pytest.mark.parametrize("tamper", [False, True])
def test_mature_draft_remains_unapproved_and_checks_source_bytes(tmp_path, tamper):
    store = ForwardSamples(
        tmp_path / "samples.sqlite3", version="fixture", now=now("2026-10-13", 8)
    )
    sessions = [
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
        "2026-10-19",
        "2026-10-20",
    ]
    symbols = [f"{i:06d}.SZ" for i in range(1, 1001)]
    rows = [{"instrument": symbol, "features": [1.0] * 360} for symbol in symbols]
    # All synthetic, isolated source bytes; never production source evidence.
    for day, price in [(sessions[0], 10), (sessions[-1], 11)]:
        store.calendar(
            [s for s in sessions if s <= day],
            canonical({"fixture_calendar": day}),
            now(day, 9),
        )
        store.membership(
            day, symbols, canonical({"fixture_membership": day}), now(day, 9)
        )
        store.capture(
            day,
            rows,
            dict.fromkeys(symbols, price),
            canonical({"fixture_prices": day}),
            now(day, 17),
        )
    assert store.settle(sessions, now(sessions[-1], 18)) == 1000
    if tamper:
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute("UPDATE sources SET blob=?", (b"altered",))
        with pytest.raises(PermissionError, match="altered retained source"):
            build_draft(store.path, 5, sessions[-1], now(sessions[-1], 18))
    else:
        result = build_draft(store.path, 5, sessions[-1], now(sessions[-1], 18))
        assert len(result["dataset"]["rows"]) == 1000
        assert result["dataset"]["purpose"] == "forward_training_candidate"
        assert result["approved_for_training"] is False
        assert result["formal_acceptance"] is False
