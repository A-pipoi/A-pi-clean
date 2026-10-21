from datetime import date, datetime, timedelta

import pytest

from quant.factors.live_ic import SHANGHAI
from quant.research.forward_samples import ForwardSamples


def moment(day, hour):
    return datetime.fromisoformat(day + f"T{hour:02d}:00:00+08:00")


@pytest.fixture
def store(tmp_path):
    return ForwardSamples(
        tmp_path / "samples.sqlite3", version="new", now=moment("2026-10-13", 8)
    )


def rows():
    return [{"instrument": "000001.SZ", "features": [1.0] * 360}]


def capture(store, day, price):
    store.membership(day, ["000001.SZ"], b"actual membership fixture", moment(day, 9))
    store.capture(
        day, rows(), {"000001.SZ": price}, b"actual price fixture", moment(day, 17)
    )


def test_creation_and_unapproved_status(store):
    assert store.status()["samples"] == 0
    assert store.status()["approved_for_training"] is False
    assert store.status()["formal_acceptance"] is False


def test_version_cannot_be_overwritten(store):
    with pytest.raises(PermissionError, match="version changed"):
        ForwardSamples(store.path, version="other", now=moment("2026-10-13", 8))
    assert store.status()["version"] == "new"


@pytest.mark.parametrize(
    "day,hour", [("2026-10-12", 9), ("2026-10-13", 15), ("2026-10-13", 17)]
)
def test_no_backdated_or_after_close_membership(store, day, hour):
    with pytest.raises(PermissionError):
        store.membership(day, ["000001.SZ"], b"fixture", moment(day, hour))
    assert store.status()["membership"] == 0


def test_features_require_prior_membership_and_close(store):
    with pytest.raises(PermissionError, match="membership"):
        store.capture(
            "2026-10-13",
            rows(),
            {"000001.SZ": 10},
            b"fixture",
            moment("2026-10-13", 17),
        )
    store.membership("2026-10-13", ["000001.SZ"], b"fixture", moment("2026-10-13", 9))
    with pytest.raises(PermissionError, match="close"):
        store.capture(
            "2026-10-13",
            rows(),
            {"000001.SZ": 10},
            b"fixture",
            moment("2026-10-13", 16),
        )


def test_first_observation_is_immutable(store):
    capture(store, "2026-10-13", 10)
    capture(store, "2026-10-13", 20)
    assert store.status()["samples"] == 1


def test_only_real_observed_endpoints_mature_and_no_repeat(store):
    sessions = [(date(2026, 10, 13) + timedelta(days=i)).isoformat() for i in range(6)]
    # Deliberate synthetic session fixture, not production exchange evidence.
    capture(store, sessions[0], 10)
    assert store.settle(sessions, moment(sessions[-1], 17)) == 0
    capture(store, sessions[-1], 11)
    assert store.settle(sessions, moment(sessions[-1], 17)) == 1
    assert store.settle(sessions, moment(sessions[-1], 18)) == 0
    assert store.status()["labels"] == 1


def test_future_or_duplicate_calendar_rejected(store):
    with pytest.raises(PermissionError, match="Future"):
        store.settle(["2026-10-14"], moment("2026-10-13", 17))
    with pytest.raises(ValueError, match="calendar"):
        store.settle(["2026-10-13", "2026-10-13"], moment("2026-10-13", 17))


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_endpoint_prices_rejected(store, price):
    store.membership("2026-10-13", ["000001.SZ"], b"fixture", moment("2026-10-13", 9))
    with pytest.raises(ValueError):
        store.capture(
            "2026-10-13",
            rows(),
            {"000001.SZ": price},
            b"fixture",
            moment("2026-10-13", 17),
        )
    assert store.status()["samples"] == 0


def test_naive_creation_time_rejected(tmp_path):
    with pytest.raises(ValueError):
        ForwardSamples(
            tmp_path / "samples.sqlite3",
            version="new",
            now=datetime(2026, 10, 13, tzinfo=SHANGHAI).replace(tzinfo=None),
        )
