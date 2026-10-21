"""Deterministic IC decay diagnostics, never an automatic trading decision."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import date


def factor_decay_report(rows: Sequence[Mapping[str, object]], *, window: int = 20,
                        reference_window: int = 60, confirmations: int = 3) -> dict[str, object]:
    if min(window, reference_window, confirmations) < 2:
        raise ValueError('decay windows must be at least two')
    observations: dict[date, float] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row['date']))
            value = float(str(row['value']))
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(value) or not -1 <= value <= 1:
            continue
        if day in observations:
            raise ValueError('duplicate IC session')
        observations[day] = value
    ordered = sorted(observations.items())
    series = []
    streak = 0
    for stop in range(window + reference_window, len(ordered) + 1):
        recent = ordered[stop-window:stop]
        prior = ordered[stop-window-reference_window:stop-window]
        current = sum(v for _, v in recent) / window
        baseline = sum(v for _, v in prior) / reference_window
        decayed = baseline > 0 and (current <= 0 or current < baseline * 0.5)
        streak = streak + 1 if decayed else 0
        series.append({'date': ordered[stop-1][0].isoformat(), 'rolling_ic': current,
            'reference_ic': baseline, 'decayed': decayed, 'confirmation_count': streak})
    if not series:
        return {'status': 'insufficient_data', 'observations': len(ordered),
            'required': window + reference_window, 'series': [], 'alerts': []}
    last = series[-1]
    alerts = []
    if streak >= confirmations:
        event = hashlib.sha256(str(last['date']).encode()).hexdigest()[:16]
        alerts.append({'id': f'ic-decay:{event}', 'date': last['date'],
            'type': 'warning', 'code': 'sustained_ic_decay',
            'message': '近期滚动IC低于独立前段基线的一半或转负，且持续至少三个观测日；请人工复核，不自动停用策略'})
    status = 'alert' if alerts else ('unproven_baseline' if baseline <= 0 else 'normal')
    return {'status': status, 'observations': len(ordered),
        'window': window, 'reference_window': reference_window, 'confirmations': confirmations,
        'as_of': ordered[-1][0].isoformat(), 'series': series, 'alerts': alerts,
        'scope': 'saved mature-label research observations; not a live data freshness claim',
        'inference': 'diagnostic only; overlapping windows are not independent significance tests'}
