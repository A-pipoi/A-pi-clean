"""Persistent, bounded historical reuse for forced raw daily refreshes."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from .schema import Bar

FULL_REFRESH_SECONDS = 30 * 24 * 60 * 60
OVERLAP_DAYS = 7


def load_completed_refresh(directory: Path | None, code: str, start: date,
                           end: date) -> tuple[Bar, ...] | None:
    """Resume a recent confirmed closing-day response, never a stale tail.

    Only explicit paper retries use this. Preserve the original fetch time;
    reading a cache must not prolong its ten-minute validity.
    """
    from .adapters import _bar_from_json, _disk_cache_path

    path = _disk_cache_path(directory, (code, start.isoformat(), end.isoformat(), ""), prefix="tushare")
    if path is None:
        return None
    try:
        from datetime import datetime
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(payload["fetched_at"])
        if stamp.tzinfo is None or not 0 <= time.time() - stamp.timestamp() <= 600:
            return None
        bars = tuple(_bar_from_json(row) for row in payload["bars"])
        if (not bars or max(bar.date for bar in bars) != end
                or len({bar.date for bar in bars}) != len(bars)
                or any(bar.symbol != code or not start <= bar.date <= end
                       or not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close, bar.volume))
                       or min(bar.open, bar.high, bar.low, bar.close) <= 0
                       or bar.limit_down is None or bar.limit_up is None
                       or not math.isfinite(bar.limit_down) or not math.isfinite(bar.limit_up)
                       or not 0 < bar.limit_down <= bar.limit_up for bar in bars)):
            return None
        return bars
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _path(directory: Path, code: str) -> Path:
    return directory / f"tushare_incremental_{code.replace('.', '_')}_raw.json"


def load_history(directory: Path | None, code: str, start: date, end: date
                 ) -> tuple[tuple[Bar, ...], date, float] | None:
    """Return only a previously fetched prefix; the caller must refresh its tail.

    Legacy interval caches intentionally cannot establish coverage provenance.
    A wider/earlier request, corrupt cache, or expired baseline needs a full fetch.
    """
    from .adapters import _bar_from_json

    if directory is None:
        return None
    try:
        payload = json.loads(_path(directory, code).read_text(encoding="utf-8"))
        baseline = float(payload["full_fetched_at"])
        covered_start = date.fromisoformat(payload["start"])
        covered_end = date.fromisoformat(payload["end"])
        bars = tuple(_bar_from_json(row) for row in payload["bars"])
        if (
            payload["schema_version"] != 1
            or payload["symbol"] != code
            or not 0 <= time.time() - baseline < FULL_REFRESH_SECONDS
            or covered_start > start
            or covered_end > end
            or covered_end < start
            or not bars
            or any(bar.symbol != code or not covered_start <= bar.date <= covered_end for bar in bars)
            or any(not all(math.isfinite(value) for value in
                           (bar.open, bar.high, bar.low, bar.close, bar.volume))
                   or min(bar.open, bar.high, bar.low, bar.close) <= 0 for bar in bars)
            or len({bar.date for bar in bars}) != len(bars)
        ):
            return None
        tail_start = max(start, max(bar.date for bar in bars) - timedelta(days=OVERLAP_DAYS))
        prefix = tuple(bar for bar in bars if start <= bar.date < tail_start)
        return (prefix, tail_start, baseline) if prefix else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def save_history(directory: Path | None, code: str, start: date, end: date,
                 bars: tuple[Bar, ...], baseline: float) -> None:
    from .adapters import _bar_to_json

    if directory is None:
        return
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "symbol": code, "start": start.isoformat(),
                   "end": end.isoformat(), "full_fetched_at": baseline,
                   "bars": [_bar_to_json(bar) for bar in bars]}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".tushare_incremental_", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, _path(directory, code))
    except OSError:
        # Persistence failure cannot invalidate a successful live response.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
