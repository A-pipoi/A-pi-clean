"""Owned, counted formal training and prospective inference orchestration.

No heavy dependencies, client evidence upload, replay promotion, sealed-price
read, sealed opening or factor enablement. A separate fixed Python process does
the CPU work; this supervisor timestamps and verifies its actual artifacts.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from quant.api.private_files import protect_private_path
from quant.api.research_paper import execution_code_hash
from quant.api.sqlite_support import connect_sqlite
from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import (
    FEATURE_VERSION,
    EvidenceAuthority,
    aware,
    canonical,
    digest,
    regular,
)
from quant.research.deep_formal import DeepFormalTrials
from quant.research.forward_admission import check_ledger_scope
from quant.research.ledger import ResearchLedger


def training_options(config: dict) -> None:
    if config.get("feature_version") != FEATURE_VERSION:
        raise ValueError("Fixed Alpha360 feature contract required")
    options = config.get("hyperparameters")
    bounds = {
        "seed": (0, 2147483647),
        "epochs": (1, 100),
        "batch_size": (1, 4096),
        "hidden": (1, 256),
    }
    if not isinstance(options, dict) or set(options) != {*bounds, "learning_rate"}:
        raise ValueError("Exact bounded training hyperparameters required")
    for key, (low, high) in bounds.items():
        if type(options[key]) is not int or not low <= options[key] <= high:
            raise ValueError("Training resource limits exceeded")
    rate = options["learning_rate"]
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not math.isfinite(rate)
        or not 0 < rate <= 0.1
    ):
        raise ValueError("Invalid learning rate")


class DeepFormalWorker:
    def __init__(
        self,
        ledger: ResearchLedger,
        owner: str,
        *,
        authority: EvidenceAuthority,
        artifacts: Path,
        runtime: Path,
        script: Path,
        clock: Callable[[], datetime] | None = None,
        stop: threading.Event | None = None,
    ):
        self.ledger, self.owner = ledger, owner
        self.clock = clock or (lambda: datetime.now(UTC))
        self.trials = DeepFormalTrials(ledger, owner, clock=self.clock)
        self.authority, self.artifacts = authority, artifacts.absolute()
        self.runtime, self.script = runtime.absolute(), script.absolute()
        self.stop_event = stop or threading.Event()

    @staticmethod
    def schema(db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS deep_worker_jobs (
            attempt_id TEXT PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL,
            queued_at TEXT NOT NULL,finished_at TEXT,error_code TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS deep_worker_scores (
            model_id TEXT NOT NULL,score_date TEXT NOT NULL,status TEXT NOT NULL,
            error_code TEXT,PRIMARY KEY(model_id,score_date))""")

    def _contract(self, db: sqlite3.Connection, attempt: str) -> dict:
        db.row_factory = sqlite3.Row
        row = self.trials._owned(db, attempt)
        sealed = db.execute(
            "SELECT data_version,sealed_start,metadata_json "
            "FROM sealed_tests WHERE id=?",
            (row["sealed_test_id"],),
        ).fetchone()
        # Metadata only, including archived protected boundaries.
        earliest = db.execute("SELECT MIN(sealed_start) FROM sealed_tests").fetchone()[
            0
        ]
        config = json.loads(row["config_json"])
        training_options(config)
        try:
            dataset, approval = self.authority.dataset(config, self.owner, sealed[0])
        except FileNotFoundError as error:
            raise PermissionError("Trusted dataset approval missing") from error
        now = self.clock()
        forward_scope = approval.get("forward_scope")
        if forward_scope is not None:
            study_path = (
                self.authority.root.parent
                / "forward-training"
                / self.owner
                / "samples.sqlite3"
            )
            try:
                with closing(
                    sqlite3.connect(
                        study_path.resolve().as_uri() + "?mode=ro", uri=True
                    )
                ) as study_db:
                    study = study_db.execute(
                        "SELECT version,created_at FROM plan WHERE id=1"
                    ).fetchone()
            except sqlite3.OperationalError as error:
                raise PermissionError(
                    "Actual forward study registration missing"
                ) from error
            if (
                not study
                or study[0] != forward_scope.get("code_hash")
                or aware(study[1]) != aware(forward_scope.get("study_created_at", ""))
            ):
                raise PermissionError(
                    "Forward study identity or first observation time mismatch"
                )
            check_ledger_scope(
                db,
                forward_scope,
                dataset["rows"],
                row["sealed_test_id"],
                now,
                execution_code_hash(),
            )
        if (
            now.utcoffset() is None
            or aware(approval["approved_at"]) > now
            or (
                forward_scope is None
                and any(r["label_end"] >= earliest for r in dataset["rows"])
            )
            or len(dataset["rows"]) < 1000
        ):
            raise PermissionError(
                "Protected boundary or approved training sample mismatch"
            )
        symbols = json.loads(sealed[2])["symbols"]
        if not isinstance(symbols, list) or any(
            r["instrument"] not in symbols for r in dataset["rows"]
        ):
            raise PermissionError("Predeclared training universe mismatch")
        return {
            "config": config,
            "rows": dataset["rows"],
            "approval": approval,
            "status": row["status"],
            "started_at": row["started_at"],
        }

    def enqueue(self, attempt: str) -> dict:
        # Admission cannot begin training on replay/self-attested manifests.
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            contract = self._contract(db, attempt)
            existing = db.execute(
                "SELECT status,error_code FROM deep_worker_jobs "
                "WHERE attempt_id=? AND owner=?",
                (attempt, self.owner),
            ).fetchone()
            if existing:
                return {
                    "attempt_id": attempt,
                    "worker_status": existing[0],
                    "error_code": existing[1],
                    "enabled": False,
                }
            if contract["status"] != "reserved":
                raise PermissionError(
                    "Unused reserved permit required; no automatic replay"
                )
            self.trials._future_window(
                db, self.trials._owned(db, attempt)["sealed_test_id"], self.clock()
            )
            db.execute(
                "INSERT INTO deep_worker_jobs VALUES(?,?,?, ?,NULL,NULL)",
                (attempt, self.owner, "queued", self.clock().isoformat()),
            )
        return {"attempt_id": attempt, "worker_status": "queued", "enabled": False}

    def job(self, attempt: str) -> dict:
        self.trials.get(attempt)  # Owned query before touching worker state.
        with closing(connect_sqlite(self.ledger.path)) as db:
            row = None
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='deep_worker_jobs'"
            ).fetchone():
                row = db.execute(
                    "SELECT status,error_code FROM deep_worker_jobs "
                    "WHERE attempt_id=? AND owner=?",
                    (attempt, self.owner),
                ).fetchone()
        return {
            "attempt_id": attempt,
            "worker_status": row[0] if row else "not_queued",
            "error_code": row[1] if row else None,
            "enabled": False,
        }

    def _directory(self, identity: str) -> Path:
        # Identities originate from the ledger, never from supplied file paths.
        if not identity or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in identity
        ):
            raise ValueError("Invalid worker identity")
        self.artifacts.mkdir(parents=True, exist_ok=True)
        protect_private_path(self.artifacts)
        path = self.artifacts / identity
        path.mkdir()  # Exclusive: never overwrite/resume an old training output.
        protect_private_path(path)
        return path

    def _execute(self, mode: str, directory: Path, payload: dict) -> dict:
        # Verify fixed executable/script paths (no shell or request overrides).
        regular(self.script)
        if not self.runtime.is_file() or self.runtime.is_symlink():
            raise RuntimeError("independent_runtime_unavailable")
        input_path = directory / "input.json"
        blob = canonical(payload)
        with input_path.open("xb") as stream:
            protect_private_path(input_path)
            stream.write(blob)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        # No Tushare token, model credentials, signing key or ledger is sent.
        with subprocess.Popen(
            [
                str(self.runtime),
                "-I",
                "-S",
                str(self.script),
                mode,
                "--input",
                str(input_path),
                "--output",
                str(directory),
            ],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            deadline = time.monotonic() + (1800 if mode == "train" else 120)
            while process.poll() is None:
                if self.stop_event.wait(0.1) or time.monotonic() >= deadline:
                    process.kill()
                    process.wait()
                    raise RuntimeError("worker_interrupted_or_timed_out")
            if process.returncode:
                raise RuntimeError("independent_runtime_failed")
        if regular(input_path) != blob:
            raise PermissionError("Worker input changed during execution")
        return json.loads(regular(directory / "result.json"))

    def train_one(self) -> bool:
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.schema(db)
            row = db.execute(
                "SELECT attempt_id FROM deep_worker_jobs "
                "WHERE owner=? AND status='queued' "
                "ORDER BY queued_at LIMIT 1",
                (self.owner,),
            ).fetchone()
            if not row:
                return False
            attempt = row[0]
            db.execute(
                "UPDATE deep_worker_jobs SET status='running' WHERE attempt_id=?",
                (attempt,),
            )
        try:
            self.trials.start(attempt)
            with closing(connect_sqlite(self.ledger.path)) as db:
                contract = self._contract(db, attempt)
            directory = self._directory(attempt)
            payload = {"config": contract["config"], "rows": contract["rows"]}
            started_at = contract["started_at"]
            result = self._execute("train", directory, payload)
            losses = result.get("losses")
            if (
                result.get("config") != contract["config"]
                or result.get("train_samples") != len(contract["rows"])
                or not isinstance(losses, list)
                or len(losses) != contract["config"]["hyperparameters"]["epochs"]
                or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in losses
                )
            ):
                raise PermissionError("Incomplete or mismatched training output")
            weights = directory / "model.pt"
            protect_private_path(weights)
            receipt = {
                "attempt_id": attempt,
                "owner": self.owner,
                "purpose": "prospective",
                "config": contract["config"],
                "started_at": started_at,
                "finished_at": self.clock().isoformat(),
                "weights_sha256": digest(regular(weights)),
                "input_sha256": digest(canonical(payload)),
                "result_sha256": digest(regular(directory / "result.json")),
                "approval_sha256": digest(canonical(contract["approval"])),
                "runtime_code_sha256": self._code_hash(),
            }
            self.authority.put(attempt + ".receipt", receipt)
            self.trials.register_verified(attempt, verify=self.verify)
            self._finish(attempt, "completed", None)
        except Exception as error:
            logging.getLogger(__name__).exception(
                "quant.research.deep_worker failed: %s",
                type(error).__name__,
                exc_info=None,
            )
            # Do not persist exception strings that could expose paths/credentials.
            # A completed model cannot be erased if completion was interrupted.
            if self.trials.get(attempt)["status"] != "completed":
                self.trials.fail(attempt)
            self._finish(attempt, "failed", type(error).__name__)
        return True

    def _finish(self, attempt: str, status: str, error: str | None) -> None:
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            db.execute(
                "UPDATE deep_worker_jobs SET status=?,finished_at=?,error_code=? "
                "WHERE attempt_id=? AND owner=?",
                (status, self.clock().isoformat(), error, attempt, self.owner),
            )

    def verify(self, permit: dict) -> dict:
        attempt = permit["attempt_id"]
        receipt = self.authority.load(attempt + ".receipt")
        with closing(connect_sqlite(self.ledger.path)) as db:
            contract = self._contract(db, attempt)
        directory = self.artifacts / attempt
        payload = {"config": contract["config"], "rows": contract["rows"]}
        weights_hash = digest(regular(directory / "model.pt"))
        if (
            receipt.get("owner") != self.owner
            or permit["owner"] != self.owner
            or receipt.get("attempt_id") != attempt
            or receipt.get("purpose") != "prospective"
            or receipt.get("config") != permit["config"]
            or receipt.get("started_at") != permit["started_at"]
            or not aware(permit["started_at"])
            <= aware(receipt["finished_at"])
            <= aware(permit["registered_at"])
            or receipt.get("weights_sha256") != weights_hash
            or receipt.get("input_sha256") != digest(canonical(payload))
            or regular(directory / "input.json") != canonical(payload)
            or receipt.get("result_sha256")
            != digest(regular(directory / "result.json"))
            or receipt.get("approval_sha256") != digest(canonical(contract["approval"]))
            or receipt.get("runtime_code_sha256") != self._code_hash()
        ):
            raise PermissionError("Trusted training receipt or artifact mismatch")
        return {
            "purpose": "prospective",
            "weights_sha256": weights_hash,
            "dataset_hash": permit["config"]["dataset_hash"],
            "feature_version": permit["config"]["feature_version"],
            "start_permit_verified": True,
            "pit_evidence_verified": True,
            "metadata_hash": digest(canonical(receipt)),
        }

    def _code_hash(self) -> str:
        return digest(
            canonical(
                {
                    "runtime": digest(regular(self.script)),
                    "model_definition": digest(
                        regular(self.script.with_name("training.py"))
                    ),
                }
            )
        )

    def score_today(self) -> int:
        now = self.clock()
        day = now.astimezone(SHANGHAI).date().isoformat()
        if not 17 <= now.astimezone(SHANGHAI).hour < 20:
            return 0
        try:
            features = self.authority.features(self.owner, day)
        except FileNotFoundError:
            return 0  # No trusted collector input: wait, never replay old scores.
        available = aware(features["feature_available_at"])
        if available > now or available.astimezone(SHANGHAI).date().isoformat() != day:
            raise PermissionError("Future or backdated feature receipt")
        with closing(connect_sqlite(self.ledger.path)) as db:
            self.schema(db)
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='deep_formal_models'"
            ).fetchone():
                return 0
            models = db.execute(
                "SELECT m.model_id,m.attempt_id,m.metadata_json,s.metadata_json "
                "FROM deep_formal_models m "
                "JOIN sealed_tests s ON s.id=m.sealed_test_id "
                "WHERE m.owner=? AND m.status='awaiting_acceptance' "
                "AND s.status='sealed' "
                "AND s.archived_at IS NULL AND s.result_json IS NULL "
                "AND s.sealed_start<=? AND s.sealed_end>=?",
                (self.owner, day, day),
            ).fetchall()
        completed = 0
        for model_id, attempt, encoded, universe_json in models:
            if self.stop_event.is_set():
                break
            metadata = json.loads(encoded)
            if (
                aware(metadata["registered_at"]).astimezone(SHANGHAI).date().isoformat()
                >= day
            ):
                continue
            with closing(connect_sqlite(self.ledger.path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                inserted = db.execute(
                    "INSERT OR IGNORE INTO deep_worker_scores "
                    "VALUES(?,?,'running',NULL)",
                    (model_id, day),
                ).rowcount
            if not inserted:
                continue  # No automatic second inference after ambiguous/crashed work.
            status, error_code = "failed", None
            try:
                receipt = self.authority.load(attempt + ".receipt")
                weights = self.artifacts / attempt / "model.pt"
                weights_hash = digest(regular(weights))
                if (
                    receipt["weights_sha256"] != weights_hash
                    or metadata["weights_sha256"] != weights_hash
                    or receipt["config"] != metadata["config"]
                    or metadata["metadata_hash"] != digest(canonical(receipt))
                    or receipt.get("runtime_code_sha256") != self._code_hash()
                ):
                    raise PermissionError("Registered weights or receipt changed")
                symbols = set(json.loads(universe_json)["symbols"])
                rows = [row for row in features["rows"] if row["instrument"] in symbols]
                if not symbols or len(rows) / len(symbols) < 0.9:
                    raise PermissionError("Predeclared feature coverage below 90%")
                directory = self._directory(model_id + "-" + day)
                # Infer from an exclusive verified copy, not a mutable model
                # path that could change between hash-check and torch.load.
                weight_copy = directory / "weights.pt"
                weight_bytes = regular(weights)
                if digest(weight_bytes) != weights_hash:
                    raise PermissionError("Weights changed before inference copy")
                with weight_copy.open("xb") as stream:
                    protect_private_path(weight_copy)
                    stream.write(weight_bytes)
                payload = {
                    "config": metadata["config"],
                    "rows": rows,
                    "weights": str(weight_copy),
                }
                result = self._execute("score", directory, payload)
                generated_at = self.clock()  # Service timestamp, not child-supplied.
                if (
                    digest(regular(weights)) != weights_hash
                    or digest(regular(weight_copy)) != weights_hash
                    or self.authority.features(self.owner, day) != features
                    or sorted(r["instrument"] for r in result["rows"])
                    != sorted(r["instrument"] for r in rows)
                ):
                    raise PermissionError(
                        "Inference input/weight/output binding mismatch"
                    )
                self.trials.receive_verified_scores(
                    model_id,
                    result["rows"],
                    generated_at=generated_at,
                    feature_available_at=available,
                )
                status = "received"
                completed += 1
            except Exception as error:
                logging.getLogger(__name__).exception(
                    "quant.research.deep_worker failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )
                error_code = type(error).__name__
            with closing(connect_sqlite(self.ledger.path)) as db, db:
                db.execute(
                    "UPDATE deep_worker_scores SET status=?,error_code=? "
                    "WHERE model_id=? AND score_date=?",
                    (status, error_code, model_id, day),
                )
        return completed

    def recover(self) -> None:
        """After supervisor restart, unknown work is retained, never rerun/refunded."""
        with closing(connect_sqlite(self.ledger.path)) as db, db:
            self.schema(db)
            attempts = db.execute(
                "SELECT attempt_id FROM deep_worker_jobs "
                "WHERE owner=? AND status='running'",
                (self.owner,),
            ).fetchall()
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='deep_formal_models'"
            ).fetchone():
                db.execute(
                    "UPDATE deep_worker_scores SET status='interrupted',"
                    "error_code='worker_interrupted' WHERE status='running' "
                    "AND model_id IN (SELECT model_id FROM deep_formal_models "
                    "WHERE owner=?)",
                    (self.owner,),
                )
        for (attempt,) in attempts:
            completed = self.trials.get(attempt)["status"] == "completed"
            if not completed:
                self.trials.fail(attempt)
            self._finish(
                attempt,
                "completed" if completed else "interrupted",
                None if completed else "worker_interrupted",
            )
