"""Durable, owner-scoped external-proposer memory; never executes an LLM."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class EvolutionStore:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        if read_only:
            if not path.is_file():
                raise LookupError("evolution session not found")
            return
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evolution_sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, sealed_id INTEGER NOT NULL,
                    data_version TEXT NOT NULL, budget INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS evolution_steps (
                    session_id TEXT NOT NULL, generation INTEGER NOT NULL,
                    request_json TEXT NOT NULL, status TEXT NOT NULL, history_json TEXT NOT NULL,
                    PRIMARY KEY(session_id,generation));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro" if self.read_only else self.path,
            uri=self.read_only,
            timeout=30,
        )
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner: str, sealed_id: int, data_version: str, budget: int = 100):
        if (
            not owner
            or isinstance(budget, bool)
            or not isinstance(budget, int)
            or not 1 <= budget <= 2000
        ):
            raise ValueError("evolution budget must be an integer between 1 and 2000")
        session_id = uuid.uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO evolution_sessions VALUES (?,?,?,?,?)",
                (session_id, owner, sealed_id, data_version, budget),
            )
        return self.history(owner, session_id)

    def history(self, owner: str, session_id: str):
        with self.connection() as db:
            from quant.mining.arena import ARENA_MANAGER, ARENA_SESSION

            bindings = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='arena_bindings'"
            ).fetchone()
            bound = (
                db.execute(
                    "SELECT 1 FROM arena_bindings WHERE session_id=?", (session_id,)
                ).fetchone()
                if bindings
                else None
            )
            if bound and not ARENA_MANAGER.get() and ARENA_SESSION.get() != session_id:
                raise PermissionError(
                    "Arena session requires its own participant capability"
                )
            session = db.execute(
                "SELECT * FROM evolution_sessions WHERE id=? AND owner=?",
                (session_id, owner),
            ).fetchone()
            if session is None:
                raise LookupError("evolution session not found")
            steps = db.execute(
                "SELECT * FROM evolution_steps WHERE session_id=? ORDER BY generation",
                (session_id,),
            ).fetchall()
        return {
            "session_id": session_id,
            "sealed_id": session["sealed_id"],
            "data_version": session["data_version"],
            "budget": session["budget"],
            "generations": [
                {
                    "generation": step["generation"],
                    "status": step["status"],
                    "history": json.loads(step["history_json"]),
                }
                for step in steps
            ],
        }

    def begin(self, owner: str, session_id: str, generation: int, request: dict):
        self.history(
            owner, session_id
        )  # Fail closed before touching another owner's row.
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or not 1 <= generation <= 100
        ):
            raise ValueError("generation must be an integer between 1 and 100")
        canonical = json.dumps(request, sort_keys=True, ensure_ascii=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM evolution_steps WHERE session_id=? AND generation=?",
                (session_id, generation),
            ).fetchone()
            if existing:
                if existing["request_json"] != canonical:
                    raise ValueError(
                        "generation already reserved with a different request"
                    )
                return False
            steps = db.execute(
                "SELECT generation,status,request_json FROM evolution_steps WHERE session_id=? ORDER BY generation",
                (session_id,),
            ).fetchall()
            if generation != len(steps) + 1 or any(
                step["status"] == "running" for step in steps
            ):
                raise PermissionError(
                    "finish or inspect the previous generation before continuing"
                )
            reserved = sum(
                len(json.loads(step["request_json"])["expressions"]) for step in steps
            )
            budget = db.execute(
                "SELECT budget FROM evolution_sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
            if reserved + len(request["expressions"]) > budget:
                raise PermissionError("evolution session budget exceeded")
            db.execute(
                "INSERT INTO evolution_steps VALUES (?,?,?,'running','[]')",
                (session_id, generation, canonical),
            )
        return True

    def checkpoint(
        self, session_id: str, generation: int, history: list, status: str = "running"
    ):
        with self.connection() as db:
            db.execute(
                "UPDATE evolution_steps SET history_json=?,status=? WHERE session_id=? AND generation=?",
                (
                    json.dumps(history, sort_keys=True, ensure_ascii=False),
                    status,
                    session_id,
                    generation,
                ),
            )
