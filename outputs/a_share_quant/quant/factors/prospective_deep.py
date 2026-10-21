"""Strict score-to-panel adapter, not registration or permission to enable.

Receipt time and registered model metadata must be supplied by the trusted
service, never accepted from a client-supplied PIT readiness flag.
"""

from __future__ import annotations

import math
from datetime import datetime

from .expression import FactorPanel
from .live_ic import SHANGHAI


def prospective_score_panel(
    panel: FactorPanel,
    rows: list[dict],
    *,
    model_id: str,
    purpose: str,
    trained_at: datetime,
    registered_at: datetime,
    received_at: datetime,
    generated_at: datetime,
    feature_available_at: datetime,
    horizon: int,
):
    """Map one actual receipt-day batch without imputing missing scores.

    This adapter intentionally does not import historical replay, evaluate
    sealed prices, write a budget ledger or mutate a factor registry.
    """
    times = (trained_at, registered_at, feature_available_at, generated_at, received_at)
    if any(time.utcoffset() is None for time in times):
        raise ValueError("timezone-aware evidence required")
    if (
        purpose != "prospective"
        or type(horizon) is not int
        or horizon not in (5, 10, 20)
    ):
        raise ValueError("prospective model and supported horizon required")
    if not trained_at <= registered_at <= generated_at <= received_at:
        raise ValueError("invalid model/receipt evidence order")
    if not feature_available_at <= generated_at:
        raise ValueError("features unavailable at prediction time")
    local = received_at.astimezone(SHANGHAI)
    day = local.date()
    if (
        registered_at.astimezone(SHANGHAI).date() >= day
        or generated_at.astimezone(SHANGHAI).date() != day
        or local.hour < 17
        or day not in panel.dates
    ):
        raise ValueError("predeclare before receipt day; no retrospective scores")
    if not rows or len(rows) > len(panel.symbols):
        raise ValueError("invalid score batch size")
    values = {}
    symbols = set(panel.symbols)
    for row in rows:
        if set(row) != {"date", "instrument", "score", "model_id", "label_horizon"}:
            raise ValueError("unexpected score columns")
        score, symbol = row["score"], row["instrument"]
        if (
            row["date"] != day.isoformat()
            or row["model_id"] != model_id
            or type(row["label_horizon"]) is not int
            or row["label_horizon"] != horizon
            or symbol not in symbols
            or symbol in values
            or type(score) not in (int, float)
            or not math.isfinite(score)
        ):
            raise ValueError("invalid, duplicate, late or mismatched score")
        values[symbol] = float(score)
    return [
        [values.get(symbol) if session == day else None for symbol in panel.symbols]
        for session in panel.dates
    ]
