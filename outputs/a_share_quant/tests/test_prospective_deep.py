from datetime import date, datetime

import pytest

from quant.factors.expression import FactorPanel
from quant.factors.live_ic import SHANGHAI
from quant.factors.prospective_deep import prospective_score_panel


def arguments():
    return {
        "model_id": "model",
        "purpose": "prospective",
        "horizon": 5,
        "trained_at": datetime(2026, 10, 11, 12, tzinfo=SHANGHAI),
        "registered_at": datetime(2026, 10, 11, 13, tzinfo=SHANGHAI),
        "feature_available_at": datetime(2026, 10, 12, 17, tzinfo=SHANGHAI),
        "generated_at": datetime(2026, 10, 12, 17, 1, tzinfo=SHANGHAI),
        "received_at": datetime(2026, 10, 12, 17, 2, tzinfo=SHANGHAI),
    }


def panel():
    return FactorPanel(
        (date(2026, 10, 9), date(2026, 10, 12)),
        ("SH600000", "SZ000001"),
        {},
        ((None, None), (None, None)),
        "fixture",
    )


def row():
    return {
        "date": "2026-10-12",
        "instrument": "SH600000",
        "score": 1.0,
        "model_id": "model",
        "label_horizon": 5,
    }


def test_future_batch_maps_only_actual_receipt_day_and_preserves_missing():
    assert prospective_score_panel(panel(), [row()], **arguments()) == [
        [None, None],
        [1.0, None],
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "historical_replay"},
        {"horizon": True},
        {"registered_at": datetime(2026, 10, 12, 9, tzinfo=SHANGHAI)},
        {"generated_at": datetime(2026, 10, 9, 18, tzinfo=SHANGHAI)},
        {"feature_available_at": datetime(2026, 10, 12, 18, tzinfo=SHANGHAI)},
        # Deliberately naive: verify that missing availability timezone is rejected.
        {"received_at": datetime(2026, 10, 12, 17)},
    ],
)
def test_bad_temporal_or_model_evidence_is_rejected(change):
    with pytest.raises(ValueError):
        prospective_score_panel(panel(), [row()], **{**arguments(), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"score": float("nan")},
        {"score": True},
        {"model_id": "other"},
        {"date": "2026-10-09"},
        {"instrument": "OUTSIDE"},
    ],
)
def test_invalid_scores_cannot_enter_panel(change):
    with pytest.raises(ValueError):
        prospective_score_panel(panel(), [{**row(), **change}], **arguments())


def test_duplicates_are_not_silently_overwritten():
    with pytest.raises(ValueError):
        prospective_score_panel(panel(), [row(), row()], **arguments())
