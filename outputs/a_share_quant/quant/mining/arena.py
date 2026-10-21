"""Durable fair seasons. Participant capability is not an owner login."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from quant.mining.evolution import EvolutionStore

ARENA_SESSION: ContextVar[str | None] = ContextVar("arena_session", default=None)
ARENA_MANAGER: ContextVar[bool] = ContextVar("arena_manager", default=False)


@contextmanager
def arena_access(session_id: str | None = None):
    participant = ARENA_SESSION.set(session_id)
    manager = ARENA_MANAGER.set(session_id is None)
    try:
        yield
    finally:
        ARENA_SESSION.reset(participant)
        ARENA_MANAGER.reset(manager)


def canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def issue_capability(
    secret: bytes,
    *,
    owner: str,
    arena_id: str,
    session_id: str,
    account_version: int,
    expires: int,
) -> str:
    payload = (
        base64.urlsafe_b64encode(
            canonical(
                {
                    "owner": owner,
                    "arena_id": arena_id,
                    "session_id": session_id,
                    "version": account_version,
                    "expires": expires,
                    "purpose": "arena-participant-v1",
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    return (
        payload
        + "."
        + hmac.new(secret, ("arena:" + payload).encode(), hashlib.sha256).hexdigest()
    )


def verify_capability(secret: bytes, token: str) -> dict:
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError()
        payload, signature = token.split(".")
        expected = hmac.new(
            secret, ("arena:" + payload).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        if (
            set(claims)
            != {"owner", "arena_id", "session_id", "version", "expires", "purpose"}
            or claims["purpose"] != "arena-participant-v1"
            or type(claims["expires"]) is not int
            or claims["expires"] <= time.time()
            or type(claims["version"]) is not int
        ):
            raise ValueError()
        return claims
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise PermissionError("Invalid participant capability") from error


class ArenaStore:
    def __init__(self, ledger, *, read_only: bool = False):
        self.ledger = ledger
        self.path: Path = ledger.path
        self.read_only = read_only
        if not read_only:
            with self.connection() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS arena_seasons(
                      arena_id TEXT PRIMARY KEY,owner TEXT NOT NULL,
                      sealed_id INTEGER NOT NULL,status TEXT NOT NULL,
                      brief TEXT NOT NULL,manifest TEXT,result TEXT,
                      created_at TEXT NOT NULL,archived_at TEXT);
                    CREATE TABLE IF NOT EXISTS arena_participants(
                      arena_id TEXT NOT NULL,session_id TEXT PRIMARY KEY,
                      proposer TEXT NOT NULL,
                      last_step REAL NOT NULL DEFAULT 0,
                      UNIQUE(arena_id,proposer));
                """)

    @contextmanager
    def connection(self):
        with closing(
            sqlite3.connect(
                self.path.as_uri() + "?mode=ro" if self.read_only else self.path,
                uri=self.read_only,
                timeout=30,
            )
        ) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def create(
        self,
        owner: str,
        *,
        sealed: dict,
        training_start: str,
        training_end: str,
        proposers: list[str],
        guide: str,
        registry: list[dict],
        budget: int = 100,
        top_n: int = 5,
        horizon: int = 5,
        interval_seconds: int = 2,
        portfolio_policy: dict | None = None,
    ) -> dict:
        if (
            not owner
            or not isinstance(proposers, list)
            or not 2 <= len(proposers) <= 8
            or any(
                not isinstance(x, str) or not x.strip() or len(x) > 100
                for x in proposers
            )
            or len(set(proposers)) != len(proposers)
            or type(budget) is not int
            or not 1 <= budget <= 100
            or type(top_n) is not int
            or not 1 <= top_n <= 10
            or type(horizon) is not int
            or horizon not in (5, 10, 20)
            or type(interval_seconds) is not int
            or not 0 <= interval_seconds <= 3600
            or training_start > training_end
            or training_end >= str(sealed["sealed_start"])
        ):
            raise ValueError("Invalid uniform arena rules")
        identity = secrets.token_hex(16)
        brief = {
            "training_start": training_start,
            "training_end": training_end,
            "sealed_start": sealed["sealed_start"],
            "sealed_end": sealed["sealed_end"],
            "data_version": sealed["data_version"],
            "label_horizons": [horizon],
            "evaluation_budget": budget,
            "top_n": top_n,
            "ranking": "training_icir_descending",
            "interval_seconds": interval_seconds,
            "dsl_guide": guide,
            "guide_hash": hashlib.sha256(guide.encode()).hexdigest(),
            "registry_summary": registry,
            "portfolio_policy": portfolio_policy or {},
            "one_expression_per_step": True,
            "global_N_scope": "same_user_same_sealed_window",
            "sealed_dates_used": False,
        }
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cycle = db.execute(
                "SELECT status,archived_at FROM sealed_tests WHERE id=?",
                (sealed["id"],),
            ).fetchone()
            if not cycle or cycle["status"] != "sealed" or cycle["archived_at"]:
                raise PermissionError("Current unopened cycle required")
            if db.execute(
                "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
                (sealed["id"],),
            ).fetchone():
                raise PermissionError("Sealed already opened")
            if db.execute(
                "SELECT 1 FROM arena_seasons WHERE sealed_id=?", (sealed["id"],)
            ).fetchone():
                raise PermissionError(
                    "One arena per sealed window; "
                    "archive the research cycle for a new season"
                )
            db.execute(
                "INSERT INTO arena_seasons VALUES(?,?,?,'running',?,NULL,NULL,?,NULL)",
                (
                    identity,
                    owner,
                    sealed["id"],
                    canonical(brief),
                    datetime.now(UTC).isoformat(),
                ),
            )
            store = EvolutionStore(self.path.with_name("factor_evolution.sqlite3"))
            with arena_access():
                for proposer in proposers:
                    session = store.create(
                        owner, int(sealed["id"]), str(sealed["data_version"]), budget
                    )
                    sid = session["session_id"]
                    with store.connection() as evo:
                        evo.execute(
                            "CREATE TABLE IF NOT EXISTS arena_bindings("
                            "session_id TEXT PRIMARY KEY,arena_id TEXT NOT NULL)"
                        )
                        evo.execute(
                            "INSERT INTO arena_bindings VALUES(?,?)", (sid, identity)
                        )
                    db.execute(
                        "INSERT INTO arena_participants(arena_id,session_id,proposer) "
                        "VALUES(?,?,?)",
                        (identity, sid, proposer),
                    )
        return self.report(owner, identity)

    def season(self, owner: str, arena_id: str) -> dict:
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='arena_seasons'"
            ).fetchone():
                raise LookupError("Arena not found")
            row = db.execute(
                "SELECT * FROM arena_seasons WHERE arena_id=? AND owner=?",
                (arena_id, owner),
            ).fetchone()
            if not row:
                raise LookupError("Arena not found")
            result = dict(row)
        for key in ("brief", "manifest", "result"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def list(self, owner: str) -> dict:
        with self.connection() as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='arena_seasons'"
            ).fetchone()
            rows = (
                db.execute(
                    "SELECT arena_id,status,created_at,archived_at FROM arena_seasons "
                    "WHERE owner=? ORDER BY created_at DESC LIMIT 100",
                    (owner,),
                ).fetchall()
                if exists
                else []
            )
        return {"seasons": [dict(row) for row in rows]}

    def participant(self, owner: str, arena_id: str, session_id: str) -> dict:
        season = self.season(owner, arena_id)
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM arena_participants WHERE arena_id=? AND session_id=?",
                (arena_id, session_id),
            ).fetchone()
        if not row:
            raise PermissionError("Cross-session arena access forbidden")
        return {**dict(row), "season": season}

    def history(self, owner: str, arena_id: str, session_id: str) -> dict:
        self.participant(owner, arena_id, session_id)
        with arena_access(session_id):
            return EvolutionStore(
                self.path.with_name("factor_evolution.sqlite3"), read_only=True
            ).history(owner, session_id)

    def step(
        self, owner: str, arena_id: str, session_id: str, payload: dict, *, evaluate
    ) -> dict:
        if set(payload) != {"generation", "expression"}:
            raise ValueError(
                "Only generation and one expression allowed; "
                "rules and proposer are fixed"
            )
        party = self.participant(owner, arena_id, session_id)
        season = party["season"]
        if season["status"] != "running":
            raise PermissionError("Arena participant is locked")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute(
                "SELECT status FROM arena_seasons WHERE arena_id=?", (arena_id,)
            ).fetchone()[0]
            previous = db.execute(
                "SELECT last_step FROM arena_participants WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            if (
                state != "running"
                or time.time() - previous < season["brief"]["interval_seconds"]
            ):
                raise PermissionError("Uniform participant rate limit or season lock")
            db.execute(
                "UPDATE arena_participants SET last_step=? WHERE session_id=?",
                (time.time(), session_id),
            )
        with arena_access(session_id):
            return evaluate(
                {
                    "mode": "evolution_step",
                    "session_id": session_id,
                    "generation": payload["generation"],
                    "expressions": [payload["expression"]],
                    "proposer_tag": party["proposer"],
                    "label_horizons": season["brief"]["label_horizons"],
                }
            )

    def report(self, owner: str, arena_id: str) -> dict:
        from quant.api.mining_evolution import training_feedback

        season = self.season(owner, arena_id)
        with self.connection() as db:
            parties = db.execute(
                "SELECT session_id,proposer FROM arena_participants "
                "WHERE arena_id=? ORDER BY proposer",
                (arena_id,),
            ).fetchall()
        rows = []
        for party in parties:
            history = self.history(owner, arena_id, party["session_id"])
            feedback = training_feedback(
                history, top_n=20, horizon=season["brief"]["label_horizons"][0]
            )
            candidates = feedback["top_candidates"]
            all_feedback = [
                entry.get("feedback", {})
                for generation in history["generations"]
                for entry in generation["history"]
                if entry.get("status") != "failed"
            ]
            ics = [
                x.get("train_rank_ic", {}).get(
                    str(season["brief"]["label_horizons"][0])
                )
                for x in all_feedback
            ]
            used = len(history["generations"])
            counted = sum(
                int(run["n_tested"])
                for generation in history["generations"]
                for entry in generation["history"]
                if (
                    run := self.ledger.factor_mining_run(str(entry.get("run_id") or ""))
                )
            )
            correlations = [
                x["max_corr_with_registry"]
                for x in all_feedback
                if type(x.get("max_corr_with_registry")) in (int, float)
            ]
            rows.append(
                {
                    "session_id": party["session_id"],
                    "proposer_tag": party["proposer"],
                    "evaluations_used": used,
                    "n_tested": counted,
                    "budget": history["budget"],
                    "locked": used >= history["budget"]
                    or season["status"] != "running",
                    "best_icir": candidates[0]["score"] if candidates else None,
                    "top10_mean_icir": mean([x["score"] for x in candidates[:10]])
                    if candidates
                    else None,
                    "effective_hypothesis_rate": sum(
                        type(x) in (int, float) and math.isfinite(x) and abs(x) > 0.02
                        for x in ics
                    )
                    / used
                    if used
                    else None,
                    "registry_correlations": correlations,
                    "training_candidates": candidates,
                    "sealed_pass_rate": None,
                    "sealed_mean_ic": None,
                }
            )
        result = season["result"]
        if result:
            candidates = result.get("result", result).get("candidates", [])
            selected = (season["manifest"] or {}).get("selected", {})
            for row in rows:
                keys = {x["expression"] for x in selected.get(row["session_id"], [])}
                matches = [x for x in candidates if x.get("expression") in keys]
                row["sealed_pass_rate"] = (
                    sum(x.get("status") == "accepted" for x in matches) / len(keys)
                    if keys
                    else None
                )
                ics = [
                    x.get("evaluation", {})
                    .get("horizons", {})
                    .get(str(season["brief"]["label_horizons"][0]), {})
                    .get("mean_ic")
                    for x in matches
                ]
                values = [
                    x for x in ics if type(x) in (int, float) and math.isfinite(x)
                ]
                row["sealed_mean_ic"] = mean(values) if values else None
        # Training ranking is explicitly provisional. Final rank by sealed pass
        # rate then sealed IC, not by training feedback.
        rows.sort(
            key=lambda x: (
                (
                    -(x["sealed_pass_rate"] or 0),
                    -(x["sealed_mean_ic"] or 0),
                    x["proposer_tag"],
                )
                if result
                else (
                    -(x["best_icir"] if x["best_icir"] is not None else -math.inf),
                    x["proposer_tag"],
                )
            )
        )
        return {
            "arena_id": arena_id,
            "status": season["status"],
            "brief": season["brief"],
            "participants": rows,
            "manifest": season["manifest"],
            "result": result,
            "global_n_tested": self.ledger.factor_mining_trial_total(
                int(season["sealed_id"])
            ),
            "ranking_basis": "sealed_results" if result else "training_provisional",
            "final_winner": rows[0]["proposer_tag"]
            if result and rows and (rows[0]["sealed_pass_rate"] or 0) > 0
            else None,
        }

    def close(self, owner: str, arena_id: str) -> dict:
        report = self.report(owner, arena_id)
        season = self.season(owner, arena_id)
        if season["status"] != "running":
            return report
        selected = {}
        for row in report["participants"]:
            history = self.history(owner, arena_id, row["session_id"])
            if not history["generations"] or any(
                x["status"] != "completed" for x in history["generations"]
            ):
                raise PermissionError(
                    "Every participant must finish its counted evaluations before close"
                )
            selected[row["session_id"]] = row["training_candidates"][
                : season["brief"]["top_n"]
            ]
            if not selected[row["session_id"]]:
                raise PermissionError(
                    "Every participant needs eligible training candidates"
                )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            runs = db.execute(
                "SELECT run_id,n_tested,status,result_json FROM factor_mining_runs "
                "WHERE sealed_test_id=? ORDER BY run_id",
                (season["sealed_id"],),
            ).fetchall()
            if any(
                x["status"]
                not in {"awaiting_acceptance", "evaluation_failed", "multi_seed_report"}
                for x in runs
            ):
                raise PermissionError("Cycle contains unfinished research")
            manifest = {
                "selected": selected,
                "cycle": [
                    {
                        "run_id": x["run_id"],
                        "n_tested": x["n_tested"],
                        "result_hash": hashlib.sha256(
                            x["result_json"].encode()
                        ).hexdigest(),
                    }
                    for x in runs
                ],
                "n_tested": sum(x["n_tested"] for x in runs),
                "acceptance_scope": (
                    "arena_top_n_each_plus_nonarena_candidates_all_cycle_N"
                ),
                "confirmation_required": True,
            }
            manifest["digest"] = hashlib.sha256(
                canonical(manifest).encode()
            ).hexdigest()
            cursor = db.execute(
                "UPDATE arena_seasons SET status='closed',manifest=? "
                "WHERE arena_id=? AND status='running'",
                (canonical(manifest), arena_id),
            )
            if not cursor.rowcount:
                raise PermissionError("Concurrent arena close")
        return self.report(owner, arena_id)

    def accept(self, owner: str, arena_id: str, *, confirm: bool, accept) -> dict:
        if confirm is not True:
            raise ValueError("Explicit confirm_open_sealed=true required")
        season = self.season(owner, arena_id)
        if season["status"] != "closed":
            raise PermissionError(
                "Only the closed, reviewed uniform batch may be accepted once"
            )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                db.execute(
                    "UPDATE arena_seasons SET status='accepting' "
                    "WHERE arena_id=? AND owner=? AND status='closed'",
                    (arena_id, owner),
                ).rowcount
                != 1
            ):
                raise PermissionError("Acceptance already requested")
        first = next(iter(season["manifest"]["selected"].values()))[0]["run_id"]
        try:
            result = accept(first)
        except Exception:
            with self.connection() as db:
                db.execute(
                    "UPDATE arena_seasons SET status='acceptance_failed' "
                    "WHERE arena_id=?",
                    (arena_id,),
                )
            raise
        with self.connection() as db:
            db.execute(
                "UPDATE arena_seasons SET status='completed',result=? WHERE arena_id=?",
                (canonical(result), arena_id),
            )
        return self.report(owner, arena_id)

    def add_correlations(self, owner: str, arena_id: str, correlations: dict) -> None:
        self.season(owner, arena_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,manifest FROM arena_seasons WHERE arena_id=?",
                (arena_id,),
            ).fetchone()
            if row[0] != "closed":
                raise PermissionError(
                    "Only a closed training batch can receive correlations"
                )
            manifest = json.loads(row[1])
            manifest.pop("digest")
            manifest["peer_correlations"] = correlations
            manifest["digest"] = hashlib.sha256(
                canonical(manifest).encode()
            ).hexdigest()
            db.execute(
                "UPDATE arena_seasons SET manifest=? WHERE arena_id=?",
                (canonical(manifest), arena_id),
            )

    def archive(self, owner: str, arena_id: str) -> dict:
        season = self.season(owner, arena_id)
        if season["status"] not in {
            "completed",
            "acceptance_failed",
            "closed",
            "archived",
        }:
            raise PermissionError(
                "Close season before archive; "
                "archive does not refund N or reopen sealed"
            )
        with self.connection() as db:
            db.execute(
                "UPDATE arena_seasons SET status='archived',"
                "archived_at=COALESCE(archived_at,?) WHERE arena_id=?",
                (datetime.now(UTC).isoformat(), arena_id),
            )
        return self.report(owner, arena_id)


def arena_claim_guard(db, sealed_id: int) -> None:
    """Called inside the existing ledger's atomic sealed claim."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='arena_seasons'"
    ).fetchone():
        return
    season = db.execute(
        "SELECT status,manifest FROM arena_seasons WHERE sealed_id=?", (sealed_id,)
    ).fetchone()
    if not season:
        return
    if season[0] != "accepting":
        raise PermissionError(
            "Arena participants cannot open sealed; "
            "organizer must close and confirm the batch"
        )
    manifest = json.loads(season[1])
    document = {k: v for k, v in manifest.items() if k != "digest"}
    if hashlib.sha256(canonical(document).encode()).hexdigest() != manifest["digest"]:
        raise PermissionError("Arena batch digest mismatch")
    runs = db.execute(
        "SELECT run_id,n_tested,result_json FROM factor_mining_runs "
        "WHERE sealed_test_id=? ORDER BY run_id",
        (sealed_id,),
    ).fetchall()
    current = [
        {
            "run_id": row[0],
            "n_tested": row[1],
            "result_hash": hashlib.sha256(row[2].encode()).hexdigest(),
        }
        for row in runs
    ]
    if current != manifest["cycle"]:
        raise PermissionError(
            "Research population changed after arena close; "
            "no sealed opportunity consumed"
        )


def arena_candidate_filter(
    path: Path, sealed_id: int, candidates: list[dict]
) -> list[dict]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='arena_seasons'"
        ).fetchone():
            return candidates
        row = db.execute(
            "SELECT manifest,brief FROM arena_seasons WHERE sealed_id=?", (sealed_id,)
        ).fetchone()
        if not row or not row[0]:
            return candidates
        manifest = json.loads(row[0])
        horizon = json.loads(row[1])["label_horizons"][0]
        sessions = db.execute(
            "SELECT session_id FROM arena_participants WHERE arena_id="
            "(SELECT arena_id FROM arena_seasons WHERE sealed_id=?)",
            (sealed_id,),
        ).fetchall()
    evo = EvolutionStore(path.with_name("factor_evolution.sqlite3"), read_only=True)
    with evo.connection() as db:
        owner = db.execute(
            "SELECT owner FROM evolution_sessions WHERE id=?", (sessions[0][0],)
        ).fetchone()[0]
    arena_runs: set[str] = set()
    with arena_access():
        for sid in sessions:
            for generation in evo.history(owner, sid[0])["generations"]:
                arena_runs.update(x.get("run_id") for x in generation["history"])
    selected_expressions = {
        x["expression"] for rows in manifest["selected"].values() for x in rows
    }
    return [
        dict(x, label_horizon=horizon)
        if x.get("expression") in selected_expressions
        else x
        for x in candidates
        if x.get("expression") in selected_expressions
        or any(run not in arena_runs for run in x.get("source_run_ids", []))
    ]
