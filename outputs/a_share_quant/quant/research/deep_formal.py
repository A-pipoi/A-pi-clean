"""Transactional deep trials in the user's existing research ledger.

Only a predeclared *future* sealed window can receive a new trial. Historical
replay remains research-only. Registration requires a trusted worker verifier;
this module deliberately does not expose client-supplied PIT claims as proof.
Prospective scores join the whole-cycle sealed acceptance after an atomic
manifest freeze. Historical research imports remain outside this lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing, nullcontext
from datetime import UTC, date, datetime
from pathlib import Path

from quant.api.sqlite_support import connect_sqlite
from quant.factors.expression import FactorPanel, Panel
from quant.factors.live_ic import SHANGHAI
from quant.research.ledger import ResearchBudgetExceeded, ResearchLedger


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def deep_cycle_digest(db: sqlite3.Connection, sealed_id: int) -> str:
    """Bind models, evidence and receipts without reading sealed prices."""
    models = db.execute(
        "SELECT model_id,attempt_id,owner,version,metadata_json,registered_at "
        "FROM deep_formal_models WHERE sealed_test_id=? ORDER BY model_id",
        (sealed_id,),
    ).fetchall()
    scores = db.execute(
        "SELECT s.model_id,s.score_date,s.payload_hash,s.scores_json,"
        "s.generated_at,s.feature_available_at,s.received_at "
        "FROM deep_formal_score_batches s JOIN deep_formal_models m "
        "ON m.model_id=s.model_id WHERE m.sealed_test_id=? "
        "ORDER BY s.model_id,s.score_date",
        (sealed_id,),
    ).fetchall()
    runs = db.execute(
        "SELECT run_id,data_version,n_tested,result_json,created_at "
        "FROM factor_mining_runs WHERE sealed_test_id=? ORDER BY run_id",
        (sealed_id,),
    ).fetchall()
    payload = _json(
        {
            "models": [list(row) for row in models],
            "scores": [list(row) for row in scores],
            "runs": [list(row) for row in runs],
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class DeepFormalTrials:
    """Budget, immutable model registry and run accounting share one database."""

    def __init__(
        self,
        ledger: ResearchLedger,
        owner: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", owner):
            raise PermissionError("Authenticated owner required")
        self.ledger = ledger
        self.owner = owner
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Service clock must be timezone-aware")
        return now

    @staticmethod
    def _schema(db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS deep_formal_cycle_limits (
            sealed_test_id INTEGER PRIMARY KEY, trial_limit INTEGER NOT NULL
            CHECK(trial_limit>0))""")
        db.execute("""CREATE TABLE IF NOT EXISTS deep_formal_trials (
            attempt_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            sealed_test_id INTEGER NOT NULL, request_key TEXT NOT NULL,
            config_json TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            UNIQUE(owner,sealed_test_id,request_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS deep_formal_models (
            model_id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL,
            owner TEXT NOT NULL, sealed_test_id INTEGER NOT NULL,
            version TEXT NOT NULL, metadata_json TEXT NOT NULL,
            registered_at TEXT NOT NULL, status TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled=0))""")
        db.execute("""CREATE TABLE IF NOT EXISTS deep_formal_score_batches (
            model_id TEXT NOT NULL, score_date TEXT NOT NULL,
            payload_hash TEXT NOT NULL, scores_json TEXT NOT NULL,
            generated_at TEXT NOT NULL, feature_available_at TEXT NOT NULL,
            received_at TEXT NOT NULL, PRIMARY KEY(model_id,score_date),
            FOREIGN KEY(model_id) REFERENCES deep_formal_models(model_id))""")

    def _future_window(
        self, db: sqlite3.Connection, sealed_id: int, now: datetime
    ) -> str:
        if type(sealed_id) is not int or sealed_id <= 0:
            raise ValueError("Invalid sealed cycle")
        # Metadata only. Never read bars_json, PIT payloads or sealed outcomes.
        row = db.execute(
            "SELECT sealed_start,status,archived_at,"
            "result_json IS NOT NULL,data_version "
            "FROM sealed_tests WHERE id=?",
            (sealed_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Sealed cycle not found")
        if (
            row[1] != "sealed"
            or row[2] is not None
            or row[3]
            or date.fromisoformat(row[0]) <= now.astimezone(SHANGHAI).date()
            or db.execute(
                "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
                (sealed_id,),
            ).fetchone()
        ):
            raise PermissionError("Predeclared unopened future sealed cycle required")
        return str(row[4])

    @staticmethod
    def _configuration(config: dict) -> str:
        if not isinstance(config, dict) or set(config) != {
            "architecture",
            "dataset_hash",
            "feature_version",
            "label_horizon",
            "hyperparameters",
        }:
            raise ValueError("Exact training configuration required")
        if (
            config["architecture"] not in {"GRU", "MLP", "Linear"}
            or not isinstance(config["dataset_hash"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", config["dataset_hash"])
            or not isinstance(config["feature_version"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", config["feature_version"])
            or type(config["label_horizon"]) is not int
            or config["label_horizon"] not in (5, 10, 20)
            or not isinstance(config["hyperparameters"], dict)
        ):
            raise ValueError("Invalid training configuration")
        encoded = _json(config)
        if len(encoded.encode()) > 8192:
            raise ValueError("Training configuration too large")
        return encoded

    def reserve(self, sealed_id: int, request_key: str, config: dict) -> dict:
        """Persist N=1 before starting training; failure never refunds it."""
        if not isinstance(request_key, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,64}", request_key
        ):
            raise ValueError("Explicit idempotency key required")
        encoded = self._configuration(config)
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = self._now()
            data_version = self._future_window(db, sealed_id, now)
            self._schema(db)
            previous = db.execute(
                "SELECT attempt_id,config_json FROM deep_formal_trials "
                "WHERE owner=? AND sealed_test_id=? AND request_key=?",
                (self.owner, sealed_id, request_key),
            ).fetchone()
            if previous:
                if previous[1] != encoded:
                    raise ValueError("Idempotency key configuration mismatch")
                attempt_id = previous[0]
            else:
                db.execute(
                    "INSERT OR IGNORE INTO deep_formal_cycle_limits VALUES(?,?)",
                    (sealed_id, self.ledger.budget),
                )
                limit = db.execute(
                    "SELECT trial_limit FROM deep_formal_cycle_limits "
                    "WHERE sealed_test_id=?",
                    (sealed_id,),
                ).fetchone()[0]
                total = db.execute(
                    "SELECT COALESCE(SUM(n_tested),0) FROM factor_mining_runs "
                    "WHERE sealed_test_id=?",
                    (sealed_id,),
                ).fetchone()[0]
                if total >= limit:
                    raise ResearchBudgetExceeded("Whole-cycle trial budget exhausted")
                attempt_id = "deep-" + uuid.uuid4().hex
                stamp = now.isoformat()
                db.execute(
                    "INSERT INTO deep_formal_trials VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        self.owner,
                        sealed_id,
                        request_key,
                        encoded,
                        "reserved",
                        stamp,
                        None,
                        None,
                    ),
                )
                db.execute(
                    "INSERT INTO factor_mining_runs VALUES(?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        sealed_id,
                        data_version,
                        "evaluation_in_progress",
                        1,
                        _json(
                            {
                                "source": "external_deep",
                                "attempt_id": attempt_id,
                                "purpose": "prospective",
                                "candidates": [],
                            }
                        ),
                        stamp,
                    ),
                )
        return self.get(attempt_id)

    def _owned(self, db: sqlite3.Connection, attempt_id: str):
        row = db.execute(
            "SELECT * FROM deep_formal_trials WHERE attempt_id=? AND owner=?",
            (attempt_id, self.owner),
        ).fetchone()
        if row is None:
            raise LookupError("Training attempt not found for current user")
        return row

    def get(self, attempt_id: str) -> dict:
        with closing(connect_sqlite(self.ledger.path)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='deep_formal_trials'"
            ).fetchone():
                raise LookupError("Training attempt not found for current user")
            row = self._owned(db, attempt_id)
            return {
                "attempt_id": row["attempt_id"],
                "status": row["status"],
                "sealed_test_id": row["sealed_test_id"],
                "n_tested": 1,
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "enabled": False,
            }

    def start(self, attempt_id: str) -> dict:
        """One-use start permit. A retry must reserve a new counted attempt."""
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, attempt_id)
            now = self._now()
            self._future_window(db, row["sealed_test_id"], now)
            if row["status"] != "reserved":
                raise PermissionError("Training permit already used or closed")
            db.execute(
                "UPDATE deep_formal_trials SET status='running',started_at=? "
                "WHERE attempt_id=?",
                (now.isoformat(), attempt_id),
            )
        return self.get(attempt_id)

    def fail(self, attempt_id: str) -> dict:
        """Retain failed/abandoned attempts without sensitive error payloads."""
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, attempt_id)
            if row["status"] == "completed":
                raise PermissionError("Registered model cannot be erased")
            if row["status"] != "failed":
                db.execute(
                    "UPDATE deep_formal_trials SET status='failed',finished_at=? "
                    "WHERE attempt_id=?",
                    (self._now().isoformat(), attempt_id),
                )
                db.execute(
                    "UPDATE factor_mining_runs SET status='evaluation_failed' "
                    "WHERE run_id=?",
                    (attempt_id,),
                )
        return self.get(attempt_id)

    def register_verified(
        self, attempt_id: str, *, verify: Callable[[dict], dict]
    ) -> dict:
        """Trusted worker-only hook, not an HTTP metadata upload endpoint.

        The verifier must hash the actual fixed-owner weight file, verify a
        service-approved PIT dataset manifest and attest the issued start
        permit. Client flags/paths/timestamps cannot serve as this verifier.
        Verification failure leaves the counted attempt open for explicit fail.
        """
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = self._owned(db, attempt_id)
            now = self._now()
            self._future_window(db, row["sealed_test_id"], now)
            existing = db.execute(
                "SELECT model_id,version FROM deep_formal_models "
                "WHERE attempt_id=? AND owner=?",
                (attempt_id, self.owner),
            ).fetchone()
            if existing:
                return {
                    "model_id": existing[0],
                    "version": existing[1],
                    "status": "awaiting_acceptance",
                    "enabled": False,
                }
            if row["status"] != "running":
                raise PermissionError("Issued, started training attempt required")
            config = json.loads(row["config_json"])
            proof = verify(
                {
                    "attempt_id": attempt_id,
                    "owner": self.owner,
                    "config": config,
                    "started_at": row["started_at"],
                    "registered_at": now.isoformat(),
                }
            )
            now = self._now()
            self._future_window(db, row["sealed_test_id"], now)
            if not isinstance(proof, dict) or set(proof) != {
                "purpose",
                "weights_sha256",
                "dataset_hash",
                "feature_version",
                "start_permit_verified",
                "pit_evidence_verified",
                "metadata_hash",
            }:
                raise PermissionError("Trusted training evidence required")
            if (
                proof["purpose"] != "prospective"
                or proof["start_permit_verified"] is not True
                or proof["pit_evidence_verified"] is not True
                or proof["dataset_hash"] != config["dataset_hash"]
                or proof["feature_version"] != config["feature_version"]
                or any(
                    not isinstance(proof[key], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", proof[key])
                    for key in ("weights_sha256", "metadata_hash")
                )
            ):
                raise PermissionError("Historical replay or unverified model rejected")
            model_id = attempt_id.replace("deep-", "deep_")
            metadata = {
                **proof,
                "config": config,
                "attempt_id": attempt_id,
                "owner": self.owner,
                "started_at": row["started_at"],
                "registered_at": now.isoformat(),
            }
            encoded = _json(metadata)
            version = hashlib.sha256(encoded.encode()).hexdigest()
            candidate = {
                "model_id": model_id,
                "version": version,
                "source": "external_deep",
                "factor_type": "external_deep",
                "label_horizon": config["label_horizon"],
            }
            db.execute(
                "INSERT INTO deep_formal_models VALUES(?,?,?,?,?,?,?,?,0)",
                (
                    model_id,
                    attempt_id,
                    self.owner,
                    row["sealed_test_id"],
                    version,
                    encoded,
                    now.isoformat(),
                    "awaiting_acceptance",
                ),
            )
            db.execute(
                "UPDATE deep_formal_trials SET status='completed',finished_at=? "
                "WHERE attempt_id=?",
                (now.isoformat(), attempt_id),
            )
            db.execute(
                "UPDATE factor_mining_runs SET "
                "status='awaiting_acceptance',result_json=? "
                "WHERE run_id=?",
                (
                    _json(
                        {
                            "source": "external_deep",
                            "purpose": "prospective",
                            "candidates": [candidate],
                        }
                    ),
                    attempt_id,
                ),
            )
        return {**candidate, "status": "awaiting_acceptance", "enabled": False}

    def receive_verified_scores(
        self,
        model_id: str,
        rows: list[dict],
        *,
        generated_at: datetime,
        feature_available_at: datetime,
        received_at: datetime | None = None,
    ) -> dict:
        """Trusted worker receipt; intentionally not exposed as an HTTP upload."""
        received = received_at or self._now()
        times = (generated_at, feature_available_at, received)
        if any(item.utcoffset() is None for item in times):
            raise ValueError("Timezone-aware score evidence required")
        local = received.astimezone(SHANGHAI)
        score_date = local.date()
        if (
            local.hour < 17
            or generated_at.astimezone(SHANGHAI).date() != score_date
            or generated_at > received
            or feature_available_at > generated_at
            or not rows
        ):
            raise ValueError("Late, premature or unavailable score batch")
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"instrument", "score"}:
                raise ValueError("Exact score columns required")
            symbol, score = row["instrument"], row["score"]
            if (
                not isinstance(symbol, str)
                or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol)
                or symbol in seen
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ValueError("Invalid or duplicate score")
            seen.add(symbol)
            normalized.append({"instrument": symbol, "score": float(score)})
        normalized.sort(key=lambda item: str(item["instrument"]))
        encoded = _json(normalized)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            self._schema(db)
            model = db.execute(
                "SELECT sealed_test_id,registered_at,status,owner "
                "FROM deep_formal_models "
                "WHERE model_id=?",
                (model_id,),
            ).fetchone()
            if model is None or model["owner"] != self.owner:
                raise LookupError("Registered model not found for current user")
            if model["status"] != "awaiting_acceptance":
                raise PermissionError("Model score receipt is closed")
            sealed = db.execute(
                "SELECT sealed_start,sealed_end,metadata_json,"
                "status,archived_at,result_json "
                "FROM sealed_tests WHERE id=?",
                (model["sealed_test_id"],),
            ).fetchone()
            metadata = json.loads(sealed[2]) if sealed and sealed[2] else {}
            universe = metadata.get("symbols") if isinstance(metadata, dict) else None
            if (
                sealed is None
                or sealed[3] != "sealed"
                or sealed[4] is not None
                or sealed[5] is not None
                or not date.fromisoformat(sealed[0])
                <= score_date
                <= date.fromisoformat(sealed[1])
                or datetime.fromisoformat(model["registered_at"])
                .astimezone(SHANGHAI)
                .date()
                >= score_date
                or not isinstance(universe, list)
                or any(symbol not in set(universe) for symbol in seen)
                or db.execute(
                    "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
                    (model["sealed_test_id"],),
                ).fetchone()
            ):
                raise PermissionError("Score date or predeclared universe mismatch")
            previous = db.execute(
                "SELECT payload_hash FROM deep_formal_score_batches "
                "WHERE model_id=? AND score_date=?",
                (model_id, score_date.isoformat()),
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise ValueError("Immutable score batch mismatch")
            else:
                db.execute(
                    "INSERT INTO deep_formal_score_batches VALUES(?,?,?,?,?,?,?)",
                    (
                        model_id,
                        score_date.isoformat(),
                        digest,
                        encoded,
                        generated_at.isoformat(),
                        feature_available_at.isoformat(),
                        received.isoformat(),
                    ),
                )
        return {
            "model_id": model_id,
            "score_date": score_date.isoformat(),
            "payload_hash": digest,
            "rows": len(normalized),
            "enabled": False,
        }


def deep_candidates_ready(
    ledger: ResearchLedger,
    runs: list[dict],
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Metadata-only preflight before the unique sealed claim."""
    candidates = [
        candidate
        for run in runs
        for candidate in (
            run.get("result", {}).get("candidates", [])
            if isinstance(run.get("result"), dict)
            else []
        )
        if isinstance(candidate, dict)
        and (
            candidate.get("model_id")
            or candidate.get("source") == "external_deep"
            or candidate.get("factor_type") == "external_deep"
        )
    ]
    if not candidates:
        return
    if any(not candidate.get("model_id") for candidate in candidates):
        raise PermissionError("Deep score acceptance adapter is not connected")
    if not isinstance(getattr(ledger, "path", None), (str, Path)):
        raise PermissionError("Deep score acceptance adapter is not connected")
    context = (
        nullcontext(connection) if connection else closing(sqlite3.connect(ledger.path))
    )
    with context as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='deep_formal_models'"
        ).fetchone():
            raise PermissionError("Deep score acceptance adapter is not connected")
        for candidate in candidates:
            row = db.execute(
                "SELECT m.version,m.status,COUNT(s.score_date),m.sealed_test_id "
                "FROM deep_formal_models m "
                "LEFT JOIN deep_formal_score_batches s ON s.model_id=m.model_id "
                "WHERE m.model_id=? GROUP BY m.model_id",
                (candidate["model_id"],),
            ).fetchone()
            if (
                row is None
                or row[0] != candidate.get("version")
                or row[1] != "awaiting_acceptance"
                or row[2] < 1
                or row[3] not in {run.get("sealed_test_id") for run in runs}
            ):
                raise PermissionError(
                    "Deep candidate has no immutable prospective score receipt"
                )


def deep_candidate_panel(
    ledger: ResearchLedger, panel: FactorPanel, candidate: dict
) -> Panel:
    """Load only pre-received scores after the sealed opportunity is claimed."""
    model_id = str(candidate.get("model_id") or "")
    with closing(sqlite3.connect(ledger.path)) as db:
        model = db.execute(
            "SELECT version,status,sealed_test_id "
            "FROM deep_formal_models WHERE model_id=?",
            (model_id,),
        ).fetchone()
        if (
            model is None
            or model[0] != candidate.get("version")
            or model[1] != "acceptance_claimed"
        ):
            raise PermissionError("Deep acceptance manifest is not frozen")
        manifest = db.execute(
            "SELECT digest FROM deep_formal_acceptance_manifests "
            "WHERE sealed_test_id=?",
            (model[2],),
        ).fetchone()
        if manifest is None or manifest[0] != deep_cycle_digest(db, model[2]):
            raise ValueError("Frozen deep acceptance manifest was modified")
        rows = db.execute(
            "SELECT score_date,payload_hash,scores_json FROM deep_formal_score_batches "
            "WHERE model_id=? ORDER BY score_date",
            (model_id,),
        ).fetchall()
    by_date = {}
    for day, digest, payload in rows:
        if hashlib.sha256(payload.encode()).hexdigest() != digest:
            raise ValueError("Immutable deep score payload was modified")
        by_date[date.fromisoformat(day)] = {
            item["instrument"]: float(item["score"]) for item in json.loads(payload)
        }
    return [
        [by_date.get(day, {}).get(symbol) for symbol in panel.symbols]
        for day in panel.dates
    ]
