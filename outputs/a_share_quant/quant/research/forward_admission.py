"""Explicit operator-approved new cohort; legacy seals and budgets stay intact."""

from __future__ import annotations

from datetime import date, datetime

from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import HASH, aware, canonical, digest


def validate_scope(scope: dict, rows: list[dict], now: datetime) -> None:
    fields = {
        "protocol",
        "study_created_at",
        "code_hash",
        "training_start",
        "training_end",
        "sealed_test_id",
        "sealed_start",
        "sealed_end",
        "prior_seals_sha256",
    }
    if not isinstance(scope, dict) or set(scope) != fields:
        raise PermissionError("Exact independently reviewed forward scope required")
    if (
        scope["protocol"] != "forward-isolated-v1"
        or type(scope["sealed_test_id"]) is not int
        or scope["sealed_test_id"] <= 0
        or not isinstance(scope["code_hash"], str)
        or not HASH.fullmatch(scope["code_hash"])
        or not isinstance(scope["prior_seals_sha256"], str)
        or not HASH.fullmatch(scope["prior_seals_sha256"])
    ):
        raise PermissionError("Invalid forward study identity")
    created = aware(scope["study_created_at"])
    start, end, sealed_start, sealed_end = [
        date.fromisoformat(scope[key])
        for key in ("training_start", "training_end", "sealed_start", "sealed_end")
    ]
    if (
        now.utcoffset() is None
        or created > now
        or not created.astimezone(SHANGHAI).date()
        <= start
        <= end
        < sealed_start
        <= sealed_end
    ):
        raise PermissionError("Separate forward training and sealed intervals required")
    if any(
        not scope["training_start"]
        <= row["date"]
        <= row["label_end"]
        <= scope["training_end"]
        or aware(row["feature_available_at"]) < created
        for row in rows
    ):
        raise PermissionError(
            "Historical observations cannot enter the new forward cohort"
        )


def check_ledger_scope(
    db, scope: dict, rows: list[dict], sealed_id: int, now: datetime, code_hash: str
) -> None:
    validate_scope(scope, rows, now)
    target = db.execute(
        "SELECT sealed_start,sealed_end FROM sealed_tests WHERE id=?", (sealed_id,)
    ).fetchone()
    prior = [
        list(row)
        for row in db.execute(
            "SELECT id,sealed_start,sealed_end FROM sealed_tests WHERE id<>? ORDER BY id",
            (sealed_id,),
        )
    ]
    if (
        scope["sealed_test_id"] != sealed_id
        or scope["code_hash"] != code_hash
        or target is None
        or list(target) != [scope["sealed_start"], scope["sealed_end"]]
        or digest(canonical(prior)) != scope["prior_seals_sha256"]
        or any(
            row[2]
            >= aware(scope["study_created_at"]).astimezone(SHANGHAI).date().isoformat()
            for row in prior
        )
    ):
        raise PermissionError(
            "Forward scope does not preserve all prior protected intervals"
        )
