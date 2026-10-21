"""One leased supervisor: explicitly queued training and daily forward inference."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from quant.api.auth_store import AuthStore
from quant.api.private_files import protect_private_path
from quant.api.research_paper import execution_code_hash
from quant.api.user_scope import UserScopedStores, user_context
from quant.data.deep_features import FormalFeatureCollector
from quant.factors.live_ic import SHANGHAI
from quant.research.deep_evidence import EvidenceAuthority, regular_path
from quant.research.deep_worker import DeepFormalWorker
from quant.research.forward_capture import collect_forward

LOGGER = logging.getLogger(__name__)


def independent_runtime(project: Path) -> Path:
    configured = os.getenv("A_PI_DEEP_PYTHON")
    if configured:
        return Path(configured)
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return project / "deep_factor" / ".venv" / executable


class DeepFormalScheduler:
    def __init__(self, users: AuthStore, stores: UserScopedStores, cache: Path):
        self.users, self.stores, self.cache = users, stores, cache
        self.root = stores.root.parent / ".deep-formal"
        self.authority = EvidenceAuthority(self.root / "approvals")
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lease: BinaryIO | None = None
        self.recovered: set[str] = set()
        self.last_collection: dict[str, float] = {}
        self.last_forward_collection: dict[str, float] = {}
        self.user_locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()

    def worker(self, owner: str) -> DeepFormalWorker:
        scoped = self.stores.get(owner)
        project = Path(__file__).resolve().parents[2]
        runtime = independent_runtime(project)
        return DeepFormalWorker(
            scoped.ledger,
            owner,
            authority=self.authority,
            artifacts=self.root / "artifacts" / owner,
            runtime=runtime,
            script=project / "deep_factor/formal_runtime.py",
            stop=self.stop_event,
        )

    def request(self, action: str, payload: dict, owner: str) -> dict:
        worker = self.worker(owner)
        if action == "trial-status":
            return worker.job(payload["attempt_id"])
        if action != "trial-execute" or self.lease is None or self.stop_event.is_set():
            raise RuntimeError("formal_worker_unavailable")
        if not worker.runtime.is_file():
            raise RuntimeError("independent_runtime_unavailable")
        # Admission cannot race startup recovery for this account.
        with self._user_lock(owner):
            if owner not in self.recovered:
                worker.recover()
                self.recovered.add(owner)
            return worker.enqueue(payload["attempt_id"])

    def status(self, owner: str) -> dict:
        worker = self.worker(owner)
        return {
            "running": bool(self.thread and self.thread.is_alive() and self.lease),
            "independent_runtime_executable_available": worker.runtime.is_file(),
            "feature_contract": "Alpha360-v1",
            "training_requires_signed_source_audit": True,
            "historical_replay_allowed": False,
            "automatically_enable_or_open_sealed": False,
        }

    def _user_lock(self, owner: str) -> threading.Lock:
        with self.lock:
            return self.user_locks.setdefault(owner, threading.Lock())

    def run_once(self, now: datetime) -> None:
        for user in self.users.list_users():
            if user.disabled or self.stop_event.is_set():
                continue
            try:
                self._run_user(user.username, now)
            except Exception as error:
                logging.getLogger(__name__).exception(
                    "quant.api.deep_scheduler failed: %s",
                    type(error).__name__,
                    exc_info=None,
                )

    def _run_user(self, owner: str, now: datetime) -> None:
        with user_context(owner):
            try:
                self._forward_samples(owner, now)
            except Exception as error:
                LOGGER.exception(
                    "Forward acquisition plan rejected: %s",
                    type(error).__name__,
                    exc_info=None,
                )
            worker = self.worker(owner)
            with self._user_lock(owner):
                if owner not in self.recovered:
                    worker.recover()
                    self.recovered.add(owner)
            worker.train_one()
            if not 17 <= now.astimezone(SHANGHAI).hour < 20:
                return
            day = now.astimezone(SHANGHAI).date().isoformat()
            with closing(sqlite3.connect(worker.ledger.path)) as db:
                if not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='deep_formal_models'"
                ).fetchone():
                    return
                # Never read sealed bars/outcomes, only predeclared universe.
                rows = db.execute(
                    "SELECT s.metadata_json FROM deep_formal_models m "
                    "JOIN sealed_tests s ON s.id=m.sealed_test_id "
                    "WHERE m.owner=? AND m.status='awaiting_acceptance' "
                    "AND s.status='sealed' AND s.archived_at IS NULL "
                    "AND s.result_json IS NULL "
                    "AND s.sealed_start<=? AND s.sealed_end>=? "
                    "AND date(m.registered_at,'+8 hours')<?",
                    (owner, day, day, day),
                ).fetchall()
            if not rows:
                return
            try:
                self.authority.features(owner, day)
            except FileNotFoundError:
                last = self.last_collection.get(owner, 0)
                if time.monotonic() - last < 300:
                    return
                self.last_collection[owner] = time.monotonic()
                token = os.getenv("TUSHARE_TOKEN")
                if not token:
                    return
                symbols = sorted(
                    {symbol for row in rows for symbol in json.loads(row[0])["symbols"]}
                )
                collector = FormalFeatureCollector(
                    self.cache / "formal_deep",
                    token,
                    self.authority,
                    clock=lambda: datetime.now(SHANGHAI),
                    cancelled=self.stop_event.is_set,
                )
                if not collector.collect(owner, symbols, now):
                    return
            worker.score_today()

    def _forward_samples(self, owner: str, now: datetime) -> None:
        project = Path(__file__).resolve().parents[2]
        plan_path = project / "config" / "forward-training.json"
        if not plan_path.is_file():
            return
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("owner") != owner or plan.get("enabled") is not True:
            return
        if (
            plan.get("version") != execution_code_hash()
            or plan.get("universe") != "000300.SH"
            or plan.get("feature_version") != "Alpha360-v1"
            or plan.get("horizons") != [5, 10, 20]
            or plan.get("training_approval") is not False
            or plan.get("formal_acceptance") is not False
        ):
            raise PermissionError(
                "Forward acquisition plan mismatch; no automatic version migration"
            )
        if time.monotonic() - self.last_forward_collection.get(owner, 0) < 300:
            return
        token = os.getenv("TUSHARE_TOKEN")
        if not token:
            return
        self.last_forward_collection[owner] = time.monotonic()
        try:
            collect_forward(
                self.root / "forward-training" / owner,
                self.cache / "forward-training",
                owner,
                plan["version"],
                token,
                now,
                clock=lambda: datetime.now(SHANGHAI),
                cancelled=self.stop_event.is_set,
            )
        except Exception as error:
            LOGGER.exception(
                "Forward training acquisition failed: %s",
                type(error).__name__,
                exc_info=None,
            )

    def start(self) -> None:
        if os.getenv("A_PI_DEEP_AUTO", "1").lower() in {"0", "false", "no"}:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        protect_private_path(self.root)
        path = self.root / "supervisor.lock"
        if not path.exists():
            try:
                with path.open("xb") as initial_stream:
                    protect_private_path(path)
                    initial_stream.write(b"0")
            except FileExistsError:
                pass
        # Windows byte-range locking denies reads of the leased byte itself.
        regular_path(path)
        protect_private_path(path)
        stream = path.open("r+b")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                # These symbols exist on POSIX; Windows-host mypy omits them.
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError:
            stream.close()
            LOGGER.warning("Another formal deep supervisor owns the lease")
            return
        self.lease = stream

        def loop() -> None:
            try:
                while not self.stop_event.is_set():
                    try:
                        self.run_once(datetime.now(SHANGHAI))
                    except Exception as error:
                        logging.getLogger(__name__).exception(
                            "quant.api.deep_scheduler failed: %s",
                            type(error).__name__,
                            exc_info=None,
                        )
                    if self.stop_event.wait(30):
                        break
            finally:
                stream.close()  # Release only after child termination/recovery writes.
                self.lease = None

        self.thread = threading.Thread(
            target=loop, name="a-pi-formal-deep", daemon=True
        )
        self.thread.start()
        LOGGER.info(
            "Formal deep supervisor started; approval required; "
            "never enable factors or open sealed data"
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
