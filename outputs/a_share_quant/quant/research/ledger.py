from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..numeric import as_int as _as_int


class ResearchBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ExperimentRecord:
    strategy_version: str
    data_version: str
    parameters: dict[str, object]
    status: str
    notes: str = ""
    git_commit: str = "uncommitted"
    kind: str = ""


class ResearchLedger:
    def __init__(self, path: str | Path, budget: int = 20) -> None:
        self.path = Path(path)
        self.budget = budget
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    git_commit TEXT NOT NULL DEFAULT 'uncommitted',
                    kind TEXT NOT NULL DEFAULT 'research',
                    archived_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(experiments)"
                ).fetchall()
            }
            if "git_commit" not in columns:
                connection.execute(
                    "ALTER TABLE experiments ADD COLUMN git_commit TEXT NOT NULL DEFAULT 'uncommitted'"
                )
            if "kind" not in columns:
                connection.execute(
                    "ALTER TABLE experiments ADD COLUMN kind TEXT NOT NULL DEFAULT 'research'"
                )
            if "archived_at" not in columns:
                connection.execute(
                    "ALTER TABLE experiments ADD COLUMN archived_at TEXT"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_version TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    archived_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sealed_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_version TEXT NOT NULL,
                    factor_pipeline_version TEXT NOT NULL,
                    data_version TEXT NOT NULL,
                    sealed_start TEXT NOT NULL,
                    sealed_end TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    bars_json TEXT NOT NULL,
                    pit_data_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'sealed',
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    evaluated_at TEXT,
                    archived_at TEXT
                )
                """
            )
            sealed_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(sealed_tests)"
                ).fetchall()
            }
            connection.execute("""CREATE TABLE IF NOT EXISTS research_paper_exposure (
                trade_date TEXT PRIMARY KEY, config_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
                data_version TEXT NOT NULL)""")
            if "pit_data_json" not in sealed_columns:
                connection.execute(
                    "ALTER TABLE sealed_tests ADD COLUMN pit_data_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS backtest_default_display (
                id INTEGER PRIMARY KEY CHECK(id=1), display_json TEXT NOT NULL
            )""")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_mining_runs (
                    run_id TEXT PRIMARY KEY,
                    sealed_test_id INTEGER NOT NULL,
                    data_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    n_tested INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_mining_acceptances (
                    sealed_test_id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    claimed_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS factor_mining_acceptance_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sealed_test_id INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def count(self, strategy_version: str, *, kind: str | None = None) -> int:
        with self._connection() as connection:
            query = "SELECT COUNT(*) FROM experiments WHERE strategy_version = ? AND archived_at IS NULL"
            params: list[object] = [strategy_version]
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            row = connection.execute(query, tuple(params)).fetchone()
        return int(row[0])

    def kind_counts(self, strategy_version: str) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT kind, COUNT(*) FROM experiments WHERE strategy_version=? AND archived_at IS NULL GROUP BY kind",
                (strategy_version,),
            ).fetchall()
        return {str(kind or "research"): int(count) for kind, count in rows}

    def archived_count(self, strategy_version: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM experiments WHERE strategy_version=? AND archived_at IS NOT NULL",
                (strategy_version,),
            ).fetchone()
        return int(row[0])

    def can_record(self, strategy_version: str) -> bool:
        return self.count(strategy_version) < self.budget

    def status(self, strategy_version: str) -> dict[str, object]:
        used = self.count(strategy_version)
        return {
            "strategy": strategy_version,
            "used": used,
            "budget": self.budget,
            "remaining": max(0, self.budget - used),
            "kind_counts": self.kind_counts(strategy_version),
            "archived_count": self.archived_count(strategy_version),
            "can_record": used < self.budget,
        }

    def latest(
        self, strategy_version: str, *, kind: str | None = None
    ) -> dict[str, object] | None:
        """Return the latest auditable experiment, optionally filtered by kind."""

        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, strategy_version, data_version, parameters_json, status,
                       notes, git_commit, kind, created_at
                FROM experiments
                WHERE strategy_version = ? AND archived_at IS NULL
                ORDER BY id DESC
                """,
                (strategy_version,),
            ).fetchall()
        for row in rows:
            try:
                parameters = json.loads(row["parameters_json"])
            except (TypeError, json.JSONDecodeError):
                parameters = {}
            row_kind = str(row["kind"] or parameters.get("kind") or "research")
            if kind is not None and row_kind != kind:
                continue
            return {
                "id": int(row["id"]),
                "strategy_version": row["strategy_version"],
                "data_version": row["data_version"],
                "parameters": parameters,
                "status": row["status"],
                "notes": row["notes"],
                "git_commit": row["git_commit"],
                "kind": row_kind,
                "created_at": row["created_at"],
            }
        return None

    def experiments(
        self, strategy_version: str, *, limit: int = 50
    ) -> list[dict[str, object]]:
        """Show every budget debit, including failed and unfinished runs."""
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT id, data_version, parameters_json, status, notes, kind, created_at
                   FROM experiments WHERE strategy_version=? AND archived_at IS NULL
                   ORDER BY id DESC LIMIT ?""",
                (strategy_version, min(max(1, limit), 200)),
            ).fetchall()
            completed_jobs = connection.execute(
                "SELECT job_id, result_json FROM backtest_jobs WHERE status='completed' AND result_json IS NOT NULL ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        result_urls: dict[int, str] = {}
        for job in completed_jobs:
            try:
                payload = json.loads(job["result_json"])
                experiment_id = (payload.get("research") or {}).get("experiment_id")
                if experiment_id is not None:
                    result_urls[int(experiment_id)] = f"/api/backtest/{job['job_id']}"
            except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                continue
        result = []
        for row in rows:
            try:
                parameters = json.loads(row["parameters_json"])
            except (TypeError, json.JSONDecodeError):
                parameters = {}
            result.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "reason": row["notes"],
                    "created_at": row["created_at"],
                    "data_version": row["data_version"],
                    "parameters": parameters,
                    "result_url": parameters.get("result_url")
                    or result_urls.get(int(row["id"])),
                }
            )
        return result

    def record(self, experiment: ExperimentRecord) -> int:
        kind = (
            str(
                experiment.kind or experiment.parameters.get("kind") or "research"
            ).strip()
            or "research"
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = int(
                connection.execute(
                    "SELECT COUNT(*) FROM experiments WHERE strategy_version=? AND archived_at IS NULL",
                    (experiment.strategy_version,),
                ).fetchone()[0]
            )
            if current >= self.budget:
                raise ResearchBudgetExceeded(
                    f"strategy {experiment.strategy_version!r} has reached the {self.budget}-run research budget"
                )
            cursor = connection.execute(
                """
                INSERT INTO experiments
                (strategy_version, data_version, parameters_json, status, notes, git_commit, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.strategy_version,
                    experiment.data_version,
                    json.dumps(
                        experiment.parameters, ensure_ascii=False, sort_keys=True
                    ),
                    experiment.status,
                    experiment.notes,
                    experiment.git_commit or "uncommitted",
                    kind,
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Research insert did not return a row ID")
            return int(cursor.lastrowid)

    def archive(self, strategy_version: str, *, reason: str) -> dict[str, object]:
        clean_reason = str(reason).strip()
        if not clean_reason:
            raise ValueError("archive reason is required")
        archived_at = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE experiments SET archived_at=? WHERE strategy_version=? AND archived_at IS NULL",
                (archived_at, strategy_version),
            )
            sealed_cursor = connection.execute(
                "UPDATE sealed_tests SET archived_at=? WHERE strategy_version=? AND archived_at IS NULL",
                (archived_at, strategy_version),
            )
            connection.execute(
                "INSERT INTO ledger_events(strategy_version,event_type,reason,archived_count,created_at) VALUES (?,?,?,?,?)",
                (
                    strategy_version,
                    "archive",
                    clean_reason,
                    int(cursor.rowcount),
                    archived_at,
                ),
            )
        return {
            "strategy": strategy_version,
            "archived_count": int(cursor.rowcount),
            "sealed_archived_count": int(sealed_cursor.rowcount),
            "archived_at": archived_at,
            "reason": clean_reason,
            "remaining": self.count(strategy_version),
        }

    def get_sealed_test(
        self,
        strategy_version: str,
        factor_pipeline_version: str,
        *,
        include_bars: bool = True,
    ) -> dict[str, object] | None:
        """Return the active immutable sealed snapshot for a strategy pipeline."""

        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT id, strategy_version, factor_pipeline_version, data_version,
                       sealed_start, sealed_end, dataset_hash, bars_json, pit_data_json, metadata_json,
                       parameters_json, status, result_json, created_at, evaluated_at
                FROM sealed_tests
                WHERE strategy_version=? AND factor_pipeline_version=? AND archived_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (strategy_version, factor_pipeline_version),
            ).fetchone()
        if row is None:
            return None
        return self._sealed_row(dict(row), include_bars=include_bars)

    def latest_sealed_test_for_universe(
        self,
        strategy_version: str,
        *,
        index_code: str,
        protocol: str | None = None,
        include_bars: bool = False,
    ) -> dict[str, object] | None:
        """Find the newest active seal whose recorded PIT universe is an index."""

        wanted_index = str(index_code).strip().upper()
        payload_columns = ", bars_json, pit_data_json" if include_bars else ""
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT id, strategy_version, factor_pipeline_version, data_version,
                       sealed_start, sealed_end, dataset_hash, metadata_json, parameters_json,
                       status, result_json, created_at, evaluated_at{payload_columns}
                FROM sealed_tests
                WHERE strategy_version=? AND archived_at IS NULL
                ORDER BY id DESC
                """,
                (strategy_version,),
            ).fetchall()
        for row in rows:
            item = self._sealed_row(dict(row), include_bars=include_bars)
            metadata = item.get("metadata")
            universe = (
                metadata.get("research_universe")
                if isinstance(metadata, dict)
                else None
            )
            parameters = item.get("parameters")
            if (
                isinstance(universe, dict)
                and bool(universe.get("pit_membership"))
                and str(universe.get("index_code") or "").strip().upper()
                == wanted_index
                and (
                    protocol is None
                    or (
                        isinstance(parameters, dict)
                        and parameters.get("sealed_acceptance_protocol") == protocol
                    )
                )
            ):
                return item
        return None

    def has_active_sealed_result(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM sealed_tests WHERE archived_at IS NULL AND status='passed' LIMIT 1"
            ).fetchone()
        return row is not None

    def earliest_evaluated_seal_start(self) -> str | None:
        """Read-only cutoff; archived evaluations remain exposed forever."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT MIN(sealed_start) FROM sealed_tests WHERE result_json IS NOT NULL"
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    def record_paper_exposure(
        self, trade_date: str, config_hash: str, data_version: str
    ) -> None:
        """Mark observed research signals BEFORE computing/committing orders."""
        from datetime import date

        date.fromisoformat(trade_date)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO research_paper_exposure VALUES(?,?,?,?)",
                (trade_date, config_hash, datetime.now(UTC).isoformat(), data_version),
            )

    def assert_unexposed_interval(self, start: str, end: str) -> None:
        with self._connection() as connection:
            evaluated = connection.execute(
                "SELECT 1 FROM sealed_tests WHERE result_json IS NOT NULL AND sealed_start<=? AND sealed_end>=? LIMIT 1",
                (end, start),
            ).fetchone()
            observed = connection.execute(
                "SELECT trade_date FROM research_paper_exposure WHERE trade_date BETWEEN ? AND ? LIMIT 1",
                (start, end),
            ).fetchone()
        if evaluated or observed:
            raise ResearchBudgetExceeded(
                "密封区间包含已评估或研究纸面已观察数据，不能恢复样本未知性"
            )

    def create_sealed_test(
        self,
        *,
        strategy_version: str,
        factor_pipeline_version: str,
        data_version: str,
        sealed_start: str,
        sealed_end: str,
        dataset_hash: str,
        bars: list[dict[str, object]],
        pit_data: dict[str, object] | None = None,
        metadata: dict[str, object],
        parameters: dict[str, object],
    ) -> dict[str, object]:
        """Create the first immutable snapshot, or return the existing one."""

        with self._connection() as connection:
            # Serialize the select-then-insert so two simultaneous browser
            # requests cannot create two sealed snapshots for one scope.
            connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                """
                SELECT id, strategy_version, factor_pipeline_version, data_version,
                       sealed_start, sealed_end, dataset_hash, bars_json, pit_data_json, metadata_json,
                       parameters_json, status, result_json, created_at, evaluated_at
                FROM sealed_tests
                WHERE strategy_version=? AND factor_pipeline_version=? AND archived_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (strategy_version, factor_pipeline_version),
            ).fetchone()
            if existing is not None:
                return self._sealed_row(dict(existing), include_bars=True)
            exposed = connection.execute(
                """SELECT id FROM sealed_tests
                   WHERE result_json IS NOT NULL
                   AND sealed_start<=? AND sealed_end>=? LIMIT 1""",
                (sealed_end, sealed_start),
            ).fetchone()
            if exposed is not None:
                raise ResearchBudgetExceeded(
                    "密封区间与已评估的历史区间重叠；归档或换政策版本不能恢复样本未知性，请使用未评估的新区间"
                )
            observed = connection.execute(
                "SELECT 1 FROM research_paper_exposure WHERE trade_date BETWEEN ? AND ? LIMIT 1",
                (sealed_start, sealed_end),
            ).fetchone()
            if observed:
                raise ResearchBudgetExceeded(
                    "密封区间包含研究纸面已观察数据；不能作为独立密封样本"
                )
            created_at = datetime.now(UTC).isoformat()
            cursor = connection.execute(
                """
                INSERT INTO sealed_tests
                (strategy_version, factor_pipeline_version, data_version, sealed_start, sealed_end,
                 dataset_hash, bars_json, pit_data_json, metadata_json, parameters_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sealed', ?)
                """,
                (
                    strategy_version,
                    factor_pipeline_version,
                    data_version,
                    sealed_start,
                    sealed_end,
                    dataset_hash,
                    json.dumps(bars, ensure_ascii=False, sort_keys=True),
                    json.dumps(pit_data or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            row = connection.execute(
                """
                SELECT id, strategy_version, factor_pipeline_version, data_version,
                       sealed_start, sealed_end, dataset_hash, bars_json, pit_data_json, metadata_json,
                       parameters_json, status, result_json, created_at, evaluated_at
                FROM sealed_tests WHERE id=?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return self._sealed_row(dict(row), include_bars=True)

    def record_sealed_result(
        self,
        sealed_id: int,
        *,
        status: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        """Write the result once; subsequent calls return the original result."""

        evaluated_at = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM sealed_tests WHERE id=? AND archived_at IS NULL",
                (int(sealed_id),),
            ).fetchone()
            if row is None:
                raise ValueError("sealed test snapshot does not exist")
            if row["result_json"] is None:
                connection.execute(
                    "UPDATE sealed_tests SET status=?, result_json=?, evaluated_at=? WHERE id=? AND result_json IS NULL",
                    (
                        str(status),
                        json.dumps(result, ensure_ascii=False, sort_keys=True),
                        evaluated_at,
                        int(sealed_id),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM sealed_tests WHERE id=?", (int(sealed_id),)
                ).fetchone()
        return self._sealed_row(dict(row), include_bars=False)

    def save_factor_mining_run(
        self,
        run_id: str,
        *,
        sealed_test_id: int,
        data_version: str,
        n_tested: int,
        result: dict[str, object],
        status: str = "awaiting_acceptance",
    ) -> dict[str, object]:
        """Persist a reproducible pre-sealed GP run without spending strategy backtest budget."""

        created_at = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._enforce_formal_mining_budget(
                connection, int(sealed_test_id), int(n_tested)
            )
            connection.execute(
                """INSERT INTO factor_mining_runs
                   (run_id,sealed_test_id,data_version,status,n_tested,result_json,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    str(run_id),
                    int(sealed_test_id),
                    str(data_version),
                    str(status),
                    int(n_tested),
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO ledger_events(strategy_version,event_type,reason,archived_count,created_at) VALUES(?,?,?,?,?)",
                (
                    "factor-mining",
                    "gp-run",
                    f"run_id={run_id}; N_tested={int(n_tested)}; sealed_test_id={int(sealed_test_id)}",
                    0,
                    created_at,
                ),
            )
        return self.factor_mining_run(run_id) or {}

    def update_factor_mining_run(
        self,
        run_id: str,
        result: dict[str, object],
        *,
        status: str = "awaiting_acceptance",
    ) -> dict[str, object]:
        """Finalize an already counted run; never increment or erase its N_tested."""
        with self._connection() as connection:
            connection.execute(
                "UPDATE factor_mining_runs SET result_json=?,status=? WHERE run_id=?",
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    status,
                    str(run_id),
                ),
            )
        return self.factor_mining_run(run_id) or {}

    def record_factor_mining_attempts(self, run_id: str, n_tested: int) -> None:
        if n_tested < 0:
            raise ValueError("N_tested must be nonnegative")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT sealed_test_id,n_tested,status "
                "FROM factor_mining_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
            if row is not None and row[2] == "evaluation_in_progress":
                self._enforce_formal_mining_budget(
                    connection, row[0], max(0, int(n_tested) - row[1])
                )
            connection.execute(
                "UPDATE factor_mining_runs SET n_tested=MAX(n_tested,?) WHERE run_id=? AND status='evaluation_in_progress'",
                (int(n_tested), str(run_id)),
            )

    @staticmethod
    def _enforce_formal_mining_budget(
        connection: sqlite3.Connection, sealed_id: int, increment: int
    ) -> None:
        # Legacy cycles retain their existing policy. Once a future deep cycle
        # declares a cap, expression/GP attempts share it rather than bypass it.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='arena_seasons'").fetchone():
            arena = connection.execute("SELECT status FROM arena_seasons WHERE sealed_id=?", (sealed_id,)).fetchone()
            if arena and arena[0] != "running":
                raise PermissionError("Closed arena freezes its whole-cycle trial population")
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='deep_formal_cycle_limits'"
        ).fetchone():
            return
        limit = connection.execute(
            "SELECT trial_limit FROM deep_formal_cycle_limits WHERE sealed_test_id=?",
            (sealed_id,),
        ).fetchone()
        if limit is None:
            return
        if increment < 0:
            raise ValueError("Formal trial count cannot be negative")
        total = connection.execute(
            "SELECT COALESCE(SUM(n_tested),0) FROM factor_mining_runs "
            "WHERE sealed_test_id=?",
            (sealed_id,),
        ).fetchone()[0]
        if total + increment > limit[0]:
            raise ResearchBudgetExceeded("Whole-cycle trial budget exhausted")
        if connection.execute(
            "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
            (sealed_id,),
        ).fetchone():
            raise PermissionError("Formal acceptance manifest is already frozen")

    def factor_mining_run(self, run_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM factor_mining_runs WHERE run_id=?", (str(run_id),)
            ).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            result = {}
        return {
            "run_id": str(row["run_id"]),
            "sealed_test_id": int(row["sealed_test_id"]),
            "data_version": str(row["data_version"]),
            "status": str(row["status"]),
            "n_tested": int(row["n_tested"]),
            "result": result,
            "created_at": str(row["created_at"]),
        }

    def factor_mining_runs(self, *, limit: int = 20) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM factor_mining_runs ORDER BY created_at DESC LIMIT ?",
                (min(max(int(limit), 1), 100),),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.factor_mining_run(str(row[0]))) is not None
        ]

    def factor_mining_runs_for_sealed_test(
        self, sealed_test_id: int
    ) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id FROM factor_mining_runs WHERE sealed_test_id=? ORDER BY created_at,run_id",
                (int(sealed_test_id),),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.factor_mining_run(str(row[0]))) is not None
        ]

    def factor_mining_trial_total(self, sealed_test_id: int | None = None) -> int:
        with self._connection() as connection:
            if sealed_test_id is None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(n_tested),0) FROM factor_mining_runs"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COALESCE(SUM(n_tested),0) FROM factor_mining_runs WHERE sealed_test_id=?",
                    (int(sealed_test_id),),
                ).fetchone()
        return int(row[0])

    def claim_factor_mining_acceptance(self, run_id: str) -> dict[str, object]:
        """Atomically grant the only factor-batch evaluation for a sealed snapshot."""

        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row
            run = connection.execute(
                "SELECT * FROM factor_mining_runs WHERE run_id=?", (str(run_id),)
            ).fetchone()
            if run is None:
                return {"allowed": False, "code": "factor_mining_run_not_found"}
            sealed_id = int(run["sealed_test_id"])
            from quant.mining.arena import arena_claim_guard
            arena_claim_guard(connection, sealed_id)
            if connection.execute(
                "SELECT 1 FROM factor_mining_runs WHERE sealed_test_id=? AND status='evaluation_in_progress'",
                (sealed_id,),
            ).fetchone():
                return {
                    "allowed": False,
                    "code": "factor_mining_still_running",
                    "detail": "研究结果仍在写入；请等待完成再打开密封样本",
                }
            existing = connection.execute(
                "SELECT run_id,status,result_json FROM factor_mining_acceptances WHERE sealed_test_id=?",
                (sealed_id,),
            ).fetchone()
            if existing is not None:
                detail = f"密封快照已由批次 {existing['run_id']} 打开，禁止再次评估；本次尝试已审计。"
                connection.execute(
                    "INSERT INTO factor_mining_acceptance_attempts(sealed_test_id,run_id,status,detail,created_at) VALUES(?,?,?,?,?)",
                    (sealed_id, str(run_id), "blocked_repeat", detail, now),
                )
                connection.execute(
                    "INSERT INTO ledger_events(strategy_version,event_type,reason,archived_count,created_at) VALUES(?,?,?,?,?)",
                    ("factor-mining", "sealed_batch_replay_blocked", detail, 0, now),
                )
                try:
                    result = (
                        json.loads(existing["result_json"])
                        if existing["result_json"]
                        else None
                    )
                except (TypeError, json.JSONDecodeError):
                    result = None
                return {
                    "allowed": False,
                    "code": "sealed_factor_batch_already_opened",
                    "detail": detail,
                    "existing_run_id": existing["run_id"],
                    "existing_status": existing["status"],
                    "result": result,
                }
            # Recheck while holding the same write lock as the unique claim:
            # new model registrations/receipts cannot race this manifest.
            from .deep_formal import deep_candidates_ready

            frozen_runs = [
                self._mining_run_row(item)
                for item in connection.execute(
                    "SELECT * FROM factor_mining_runs WHERE sealed_test_id=?",
                    (sealed_id,),
                ).fetchall()
            ]
            deep_candidates_ready(self, frozen_runs, connection=connection)
            connection.execute(
                "INSERT INTO factor_mining_acceptances(sealed_test_id,run_id,status,claimed_at) VALUES(?,?,'claimed',?)",
                (sealed_id, str(run_id), now),
            )
            connection.execute(
                "INSERT INTO factor_mining_acceptance_attempts(sealed_test_id,run_id,status,detail,created_at) VALUES(?,?,?,?,?)",
                (
                    sealed_id,
                    str(run_id),
                    "claimed",
                    "首次且唯一的 sealed 因子批次验收已占用；即使计算中断也不可重试。",
                    now,
                ),
            )
            connection.execute(
                "UPDATE factor_mining_runs SET status='acceptance_claimed' WHERE sealed_test_id=? AND status='awaiting_acceptance'",
                (sealed_id,),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='deep_formal_models'"
            ).fetchone():
                from .deep_formal import deep_cycle_digest

                connection.execute(
                    "CREATE TABLE IF NOT EXISTS deep_formal_acceptance_manifests "
                    "(sealed_test_id INTEGER PRIMARY KEY,digest TEXT NOT NULL,"
                    "claimed_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO deep_formal_acceptance_manifests VALUES(?,?,?)",
                    (sealed_id, deep_cycle_digest(connection, sealed_id), now),
                )
                connection.execute(
                    "UPDATE deep_formal_models SET status='acceptance_claimed' "
                    "WHERE sealed_test_id=? AND status='awaiting_acceptance' AND enabled=0",
                    (sealed_id,),
                )
            return {"allowed": True, "run": self._mining_run_row(run)}

    def finish_factor_mining_acceptance(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT sealed_test_id FROM factor_mining_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise ValueError("因子挖掘批次不存在")
            claim = connection.execute(
                "SELECT status FROM factor_mining_acceptances "
                "WHERE sealed_test_id=? AND run_id=?",
                (int(row[0]), str(run_id)),
            ).fetchone()
            if claim is None or claim[0] != "claimed":
                raise PermissionError("Only the claimed acceptance may be completed once")
            connection.execute(
                "UPDATE factor_mining_acceptances SET status=?,result_json=?,completed_at=? WHERE sealed_test_id=? AND run_id=?",
                (str(status), encoded, now, int(row[0]), str(run_id)),
            )
            connection.execute(
                "UPDATE factor_mining_runs SET status=? WHERE sealed_test_id=?",
                (f"acceptance_{status}", int(row[0])),
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='deep_formal_models'"
            ).fetchone():
                candidates = result.get("candidates", [])
                if status == "failed":
                    connection.execute(
                        "UPDATE deep_formal_models SET status='acceptance_failed' "
                        "WHERE sealed_test_id=? AND status='acceptance_claimed' AND enabled=0",
                        (int(row[0]),),
                    )
                for candidate in candidates if isinstance(candidates, list) else []:
                    if isinstance(candidate, dict) and candidate.get("model_id"):
                        connection.execute(
                            "UPDATE deep_formal_models SET status=? WHERE model_id=? "
                            "AND sealed_test_id=? AND enabled=0",
                            (
                                str(candidate.get("status") or "rejected"),
                                str(candidate["model_id"]),
                                int(row[0]),
                            ),
                        )
            connection.execute(
                "INSERT INTO ledger_events(strategy_version,event_type,reason,archived_count,created_at) VALUES(?,?,?,?,?)",
                (
                    "factor-mining",
                    "sealed-factor-acceptance",
                    f"run_id={run_id}; status={status}; sealed_test_id={int(row[0])}",
                    0,
                    now,
                ),
            )
        return {
            "run_id": str(run_id),
            "sealed_test_id": int(row[0]),
            "status": str(status),
            "result": result,
            "completed_at": now,
        }

    def factor_mining_acceptance(self, sealed_test_id: int) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_id,status,result_json,claimed_at,completed_at FROM factor_mining_acceptances WHERE sealed_test_id=?",
                (int(sealed_test_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(row[2]) if row[2] else None
        except (TypeError, json.JSONDecodeError):
            result = None
        return {
            "run_id": row[0],
            "status": row[1],
            "result": result,
            "claimed_at": row[3],
            "completed_at": row[4],
        }

    @staticmethod
    def _mining_run_row(row: sqlite3.Row) -> dict[str, object]:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            result = {}
        return {
            "run_id": str(row["run_id"]),
            "sealed_test_id": int(row["sealed_test_id"]),
            "data_version": str(row["data_version"]),
            "status": str(row["status"]),
            "n_tested": int(row["n_tested"]),
            "result": result,
            "created_at": str(row["created_at"]),
        }

    def create_backtest_job(self, job_id: str, payload: dict[str, object]) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO backtest_jobs(job_id,status,progress,message,payload_json,created_at)
                VALUES (?, 'queued', 0, '等待 Python 回测任务启动', ?, ?)
                """,
                (
                    job_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def update_backtest_job(self, job_id: str, **changes: object) -> None:
        allowed = {
            "status",
            "progress",
            "message",
            "result",
            "error",
            "started_at",
            "completed_at",
        }
        assignments: list[str] = []
        values: list[object] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            column = "result_json" if key == "result" else key
            assignments.append(f"{column}=?")
            values.append(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if key == "result"
                else value
            )
        if not assignments:
            return
        values.append(job_id)
        guard = (
            " AND status NOT IN ('cancelling','cancelled')"
            if changes.get("status") in {"running", "completed", "failed"} or "status" not in changes
            else ""
        )
        with self._connection() as connection:
            connection.execute(
                f"UPDATE backtest_jobs SET {', '.join(assignments)} WHERE job_id=?{guard}",
                tuple(values),
            )

    def get_backtest_job(self, job_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM backtest_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ("payload_json", "result_json"):
            try:
                item[key] = json.loads(item[key]) if item.get(key) else None
            except (TypeError, json.JSONDecodeError):
                item[key] = None
        return {
            "job_id": item["job_id"],
            "status": item["status"],
            "progress": int(item["progress"]),
            "message": item["message"],
            "payload": item["payload_json"],
            "result": item["result_json"],
            "error": item["error"],
            "created_at": item["created_at"],
            "started_at": item["started_at"],
            "completed_at": item["completed_at"],
        }

    def list_backtest_jobs(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT job_id,status,progress,message,created_at FROM backtest_jobs ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_default_backtest_display(self) -> dict[str, object] | None:
        """Account-local saved display; never a strategy promotion or evaluation."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT display_json FROM backtest_default_display WHERE id=1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def set_default_backtest_display(self, job_id: str) -> dict[str, object]:
        from .backtest_display import completed_display

        # Read and save the successful artifact in one transaction. The client
        # provides only a job id, never metrics, labels or strategy parameters.
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM backtest_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            display = completed_display(dict(row) if row else None)
            connection.execute(
                "INSERT INTO backtest_default_display VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET display_json=excluded.display_json",
                (json.dumps(display, ensure_ascii=False, allow_nan=False),),
            )
        return display

    def request_backtest_cancel(self, job_id: str) -> bool:
        """Atomic scoped cancellation; completed artifacts are never discarded."""
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE backtest_jobs SET status='cancelling',message='已请求取消，等待安全检查点；已记账研究不退预算' WHERE job_id=? AND status IN ('queued','running','cancelling')",
                (job_id,),
            )
        return bool(cursor.rowcount)

    def recover_backtest_jobs(self) -> int:
        """Mark non-terminal jobs as interrupted after a service restart."""

        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE backtest_jobs
                   SET status='failed', progress=0,
                       message='Python 服务重启，任务未继续执行',
                       error='backtest_interrupted_by_service_restart',
                       completed_at=?
                 WHERE status IN ('queued', 'running', 'cancelling')
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    @staticmethod
    def _sealed_row(row: dict[str, object], *, include_bars: bool) -> dict[str, object]:
        def decode(value: object, fallback: object) -> object:
            try:
                return json.loads(str(value)) if value is not None else fallback
            except (TypeError, json.JSONDecodeError):
                return fallback

        payload: dict[str, object] = {
            "id": _as_int(row["id"]),
            "strategy_version": row["strategy_version"],
            "factor_pipeline_version": row["factor_pipeline_version"],
            "data_version": row["data_version"],
            "sealed_start": row["sealed_start"],
            "sealed_end": row["sealed_end"],
            "dataset_hash": row["dataset_hash"],
            "metadata": decode(row.get("metadata_json"), {}),
            "parameters": decode(row.get("parameters_json"), {}),
            "status": row["status"],
            "result": decode(row.get("result_json"), None),
            "created_at": row["created_at"],
            "evaluated_at": row.get("evaluated_at"),
        }
        if include_bars:
            payload["bars"] = decode(row.get("bars_json"), [])
            payload["pit_data"] = decode(row.get("pit_data_json"), {})
        return payload

    def update_result(
        self, experiment_id: int, *, status: str, notes: str | None = None
    ) -> None:
        with self._connection() as connection:
            if notes is None:
                connection.execute(
                    "UPDATE experiments SET status=? WHERE id=?",
                    (status, experiment_id),
                )
            else:
                connection.execute(
                    "UPDATE experiments SET status=?, notes=? WHERE id=?",
                    (status, notes, experiment_id),
                )

    def update_experiment_parameters(
        self, experiment_id: int, patch: dict[str, object]
    ) -> dict[str, object]:
        """Merge derived reports into an existing experiment's audit record."""

        if not isinstance(patch, dict):
            raise TypeError("experiment parameter patch must be an object")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT parameters_json FROM experiments WHERE id=? AND archived_at IS NULL",
                (int(experiment_id),),
            ).fetchone()
            if row is None:
                raise ValueError("experiment does not exist or is archived")
            try:
                current = json.loads(row[0]) if row[0] else {}
            except (TypeError, json.JSONDecodeError):
                current = {}
            if not isinstance(current, dict):
                current = {}
            current.update(patch)
            encoded = json.dumps(current, ensure_ascii=False, sort_keys=True)
            connection.execute(
                "UPDATE experiments SET parameters_json=? WHERE id=?",
                (encoded, int(experiment_id)),
            )
        return current
