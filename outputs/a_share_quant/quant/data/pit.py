from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .schema import FeatureObservation, PITIndustryObservation

SHANGHAI = timezone(timedelta(hours=8))
SIGNAL_CUTOFF = time(15, 0)


class FutureInformationError(ValueError):
    """Raised when a feature is used before it was publicly available."""


class FeatureAvailabilityChecker:
    def check(self, observation: FeatureObservation) -> None:
        available_at = _in_shanghai(observation.available_at)
        cutoff = datetime.combine(observation.as_of, SIGNAL_CUTOFF, tzinfo=SHANGHAI)
        if available_at > cutoff:
            raise FutureInformationError(
                f"feature={observation.name!r} for {observation.symbol} is available at "
                f"{available_at.isoformat()}, after signal cutoff={cutoff.isoformat()}"
            )

    def check_many(self, observations: list[FeatureObservation]) -> None:
        for observation in observations:
            self.check(observation)


def materialize_pit_feature_observations(
    records: Iterable[dict[str, Any]], trading_dates: Iterable[date]
) -> list[FeatureObservation]:
    """Expand provider records into an as-of panel, only from the next eligible session.

    Provider rows must carry an availability timestamp. This function does not infer
    availability from a report period or an effective date. A provider event is made
    usable only on the first supplied trading date strictly after its local calendar
    date, a conservative rule for close-derived data and date-only announcements.
    """

    sessions = sorted(set(trading_dates))
    if not sessions:
        return []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or raw.get("ts_code") or "").strip().upper()
        name = str(raw.get("metric") or raw.get("name") or "").strip().lower()
        available_at = _parse_datetime(raw.get("available_at"))
        if not symbol or not name or available_at is None:
            continue
        record = dict(raw)
        record["symbol"] = symbol
        record["metric"] = name
        record["_available_at"] = available_at
        record["_report_date"] = _parse_date(raw.get("report_date") or raw.get("trade_date"))
        record["_announcement_date"] = _parse_date(raw.get("announcement_date"))
        record["_value"] = _finite_number(raw.get("value"))
        record["_source_version"] = str(raw.get("source_version") or _record_version(raw))
        grouped.setdefault((symbol, name), []).append(record)

    output: list[FeatureObservation] = []
    checker = FeatureAvailabilityChecker()
    for (symbol, name), history in grouped.items():
        history.sort(key=lambda item: (item["_available_at"], item["_report_date"] or date.min))
        cursor = 0
        active: dict[str, Any] | None = None
        for as_of in sessions:
            while cursor < len(history) and as_of > history[cursor]["_available_at"].date():
                row = history[cursor]
                candidate = FeatureObservation(
                    symbol=symbol,
                    as_of=as_of,
                    available_at=row["_available_at"],
                    name=name,
                    value=row["_value"],
                    source_version=row["_source_version"],
                )
                try:
                    checker.check(candidate)
                except FutureInformationError:
                    cursor += 1
                    continue
                candidate_key = (row["_report_date"] or row["_available_at"].date(), row["_available_at"])
                active_key = (
                    (active["_report_date"] or active["_available_at"].date(), active["_available_at"])
                    if active is not None else None
                )
                if active_key is None or candidate_key >= active_key:
                    active = row
                cursor += 1
            if active is None:
                continue
            output.append(
                FeatureObservation(
                    symbol=symbol,
                    as_of=as_of,
                    available_at=active["_available_at"],
                    name=name,
                    value=active["_value"],
                    source_version=active["_source_version"],
                    report_date=active["_report_date"],
                    announcement_date=active["_announcement_date"],
                    availability_basis=str(active.get("availability_basis") or "provider_timestamp"),
                )
            )
    return output


def materialize_pit_industry_observations(
    records: Iterable[dict[str, Any]], trading_dates: Iterable[date]
) -> list[PITIndustryObservation]:
    """Materialize industry rows only when the provider proves first availability.

    Effective dates (in_date/out_date) alone are deliberately insufficient. A real
    ``available_at`` or ``first_seen_at`` timestamp is required; this keeps Tushare's
    current effective-date-only history out of strict-PIT industry neutralization.
    """

    sessions = sorted(set(trading_dates))
    output: list[PITIndustryObservation] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        available_at = _parse_datetime(raw.get("available_at") or raw.get("first_seen_at"))
        if available_at is None:
            continue
        availability_basis = "provider_available_at" if raw.get("available_at") else "first_seen_at"
        symbol = str(raw.get("symbol") or raw.get("ts_code") or "").strip().upper()
        industry = str(raw.get("industry") or raw.get("l1_name") or "").strip()
        start = _parse_date(raw.get("in_date") or raw.get("valid_from"))
        end = _parse_date(raw.get("out_date") or raw.get("valid_to"))
        if not symbol or not industry or start is None:
            continue
        source_version = str(raw.get("source_version") or _record_version(raw))
        local_available = _in_shanghai(available_at)
        for as_of in sessions:
            cutoff = datetime.combine(as_of, SIGNAL_CUTOFF, tzinfo=SHANGHAI)
            if as_of <= local_available.date() or local_available > cutoff or as_of < start or (end is not None and as_of >= end):
                continue
            output.append(
                PITIndustryObservation(
                    symbol=symbol,
                    as_of=as_of,
                    available_at=available_at,
                    industry=industry,
                    source_version=source_version,
                    availability_basis=availability_basis,
                )
            )
    # Daily first-seen snapshots can overlap. Count at most one proven industry
    # per symbol/session; repeated snapshots must not inflate PIT coverage.
    unique: dict[tuple[str,date],PITIndustryObservation] = {}
    for item in output:
        key=(item.symbol,item.as_of)
        previous=unique.get(key)
        if previous is None or item.available_at>previous.available_at:
            unique[key]=item
    return sorted(unique.values(),key=lambda item:(item.as_of,item.symbol))


def _in_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _in_shanghai(value)
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    if "T" not in text and " " not in text:
        parsed = _parse_date(text)
        if parsed:
            return datetime.combine(parsed, time(16, 0), tzinfo=SHANGHAI)
    try:
        return _in_shanghai(datetime.fromisoformat(text))
    except ValueError:
        parsed = _parse_date(text)
        return datetime.combine(parsed, time(16, 0), tzinfo=SHANGHAI) if parsed else None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _record_version(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
