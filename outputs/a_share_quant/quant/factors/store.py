from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FACTOR_TYPES = {"momentum", "reversal", "volatility", "fundamental"}
FUNDAMENTAL_METRICS = {"roe", "netprofit_yoy", "tr_yoy", "debt_to_assets"}
DEFAULT_FACTOR_DEFINITIONS = (
    ("20日动量", "momentum", 20, "momentum"),
    ("20日波动率", "volatility", 20, "realized_volatility"),
    ("最新净资产收益率", "fundamental", 0, "roe"),
    ("最新净利润同比", "fundamental", 0, "netprofit_yoy"),
    ("最新营业收入同比", "fundamental", 0, "tr_yoy"),
    ("最新资产负债率", "fundamental", 0, "debt_to_assets"),
)


@dataclass(frozen=True)
class FactorDefinition:
    key: str
    name: str
    factor_type: str
    window: int
    enabled: bool
    created_at: str
    metric: str = ""
    expression: str = ""
    category: str = ""
    status: str = "active"

    def as_dict(self) -> dict[str, object]:
        payload = {
            "key": self.key,
            "name": self.name,
            "factor_type": self.factor_type,
            "window": self.window,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "metric": self.metric,
        }
        if self.expression:
            payload.update({"expression": self.expression, "category": self.category, "status": self.status})
        return payload


class FactorRegistryStore:
    """Persistent, auditable factor definitions for the local research desk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def definitions(self, *, enabled_only: bool = False) -> list[FactorDefinition]:
        query = "SELECT key, name, factor_type, window, enabled, created_at, metric FROM factors"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at, key"
        with self._connection() as connection:
            rows = connection.execute(query).fetchall()
        definitions = [
            FactorDefinition(
                key=str(row[0]),
                name=str(row[1]),
                factor_type=str(row[2]),
                window=int(row[3]),
                enabled=bool(row[4]),
                created_at=str(row[5]),
                metric=str(row[6] or ""),
            )
            for row in rows
        ]
        expression_query = "SELECT factor_key,name,expression,category,created_at,data_version,code_version,status FROM expression_factors WHERE enabled=1 AND status='accepted' ORDER BY created_at,factor_key"
        with self._connection() as connection:
            expression_rows = connection.execute(expression_query).fetchall()
        definitions.extend(
            FactorDefinition(
                key=str(row[0]), name=str(row[1]), factor_type="expression", window=0,
                enabled=True,
                created_at=str(row[4]), metric=str(row[3] or "expression"), expression=str(row[2]),
                category=str(row[3] or "mined"), status=str(row[7]),
            )
            for row in expression_rows
        )
        return definitions

    def save_mined_candidates(
        self,
        candidates: list[dict[str, object]],
        *,
        data_version: str,
        code_version: str,
        sealed_test_id: int,
        run_id: str,
    ) -> list[dict[str, object]]:
        now = datetime.now(UTC).isoformat()
        saved: list[dict[str, object]] = []
        with self._connection() as connection:
            for candidate in candidates:
                expression = str(candidate.get("expression") or "").strip()
                if not expression:
                    continue
                identity = f"{expression}\0{data_version}\0{code_version}".encode()
                digest = hashlib.sha256(identity).hexdigest()
                key = f"expr_{digest[:20]}"
                metadata = {
                    "training": candidate,
                    "n_tested": candidate.get("n_tested"),
                    "sealed_test_id": int(sealed_test_id),
                    "mining_run_id": str(run_id),
                }
                connection.execute(
                    """INSERT OR IGNORE INTO expression_factors
                       (factor_key,name,expression,expression_hash,category,author,created_at,status,enabled,
                        data_version,code_version,sealed_test_id,mining_run_id,metadata_json)
                       VALUES(?,?,?,?,?,?,?,'candidate',0,?,?,?,?,?)""",
                    (
                        key, f"GP {expression[:42]}", expression, digest, "mined", "A-pi GP", now,
                        str(data_version), str(code_version), int(sealed_test_id), str(run_id),
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
                row = connection.execute(
                    "SELECT factor_key,name,expression,category,created_at,status,enabled,data_version,code_version,metadata_json FROM expression_factors WHERE expression_hash=? AND data_version=? AND code_version=?",
                    (digest, str(data_version), str(code_version)),
                ).fetchone()
                if row:
                    saved.append({
                        "key": str(row[0]), "name": str(row[1]), "expression": str(row[2]),
                        "category": str(row[3]), "created_at": str(row[4]), "status": str(row[5]),
                        "enabled": bool(row[6]), "data_version": str(row[7]), "code_version": str(row[8]),
                    })
        return saved

    def update_mined_candidate(self, key: str, *, status: str, metadata: dict[str, object]) -> bool:
        if status not in {"accepted", "overfitting-suspect", "duplicate-discovery", "rejected"}:
            raise ValueError("invalid mined factor status")
        with self._connection() as connection:
            row = connection.execute("SELECT metadata_json FROM expression_factors WHERE factor_key=?", (str(key),)).fetchone()
            if row is None:
                return False
            try:
                current = json.loads(row[0]) if row[0] else {}
            except (TypeError, json.JSONDecodeError):
                current = {}
            if not isinstance(current, dict):
                current = {}
            current["acceptance"] = metadata
            cursor = connection.execute(
                "UPDATE expression_factors SET status=?,metadata_json=? WHERE factor_key=?",
                (status, json.dumps(current, ensure_ascii=False, sort_keys=True), str(key)),
            )
            return cursor.rowcount == 1

    def promote_expression(self, key: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE expression_factors SET enabled=1 WHERE factor_key=? AND status='accepted'",
                (str(key),),
            )
            return cursor.rowcount == 1

    def disable_expression(self, key: str) -> None:
        with self._connection() as connection:
            connection.execute("UPDATE expression_factors SET enabled=0 WHERE factor_key=?", (str(key),))

    def expression_library(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT factor_key,name,expression,category,author,created_at,status,enabled,data_version,code_version,sealed_test_id,mining_run_id,metadata_json FROM expression_factors ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            try:
                metadata = json.loads(row[12]) if row[12] else {}
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            result.append({
                "key": row[0], "name": row[1], "expression": row[2], "category": row[3],
                "author": row[4], "created_at": row[5], "status": row[6], "enabled": bool(row[7]),
                "data_version": row[8], "code_version": row[9], "sealed_test_id": row[10],
                "mining_run_id": row[11], "metadata": metadata,
            })
        return result

    def custom_count(self, *, enabled_only: bool = False) -> int:
        """Count user-registered factors for multiple-testing accounting."""

        query = "SELECT COUNT(*) FROM factors WHERE key NOT LIKE 'builtin_%'"
        if enabled_only:
            query += " AND enabled = 1"
        with self._connection() as connection:
            row = connection.execute(query).fetchone()
            expression_query = "SELECT COUNT(*) FROM expression_factors WHERE status='accepted'"
            if enabled_only:
                expression_query += " AND enabled=1"
            expression_row = connection.execute(expression_query).fetchone()
        return int(row[0]) + int(expression_row[0])

    def register(self, name: str, factor_type: str, window: int, metric: str | None = None) -> FactorDefinition:
        clean_name = str(name).strip()
        clean_type = str(factor_type).strip().lower()
        if not 2 <= len(clean_name) <= 60:
            raise ValueError("因子名称长度必须在 2 到 60 个字符之间")
        if clean_type not in FACTOR_TYPES:
            raise ValueError("factor_type must be momentum, reversal, volatility, or fundamental")
        clean_metric = str(metric or "").strip().lower()
        parsed_window = int(window)
        if clean_type == "fundamental":
            if clean_metric not in FUNDAMENTAL_METRICS:
                raise ValueError("基本面因子必须选择 roe, netprofit_yoy, tr_yoy, or debt_to_assets")
            parsed_window = 0
        else:
            if not 3 <= parsed_window <= 120:
                raise ValueError("因子窗口必须在 3 到 120 个交易日之间")
            clean_metric = clean_type if clean_type != "volatility" else "realized_volatility"
        now = datetime.now(UTC).isoformat()
        key = f"custom_{_slug(clean_name)}_{clean_type}_{parsed_window or clean_metric}_{now.replace(':', '').replace('.', '')[-12:]}"
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO factors (key, name, factor_type, window, enabled, created_at, metric) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (key, clean_name, clean_type, parsed_window, now, clean_metric),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("该因子已经注册") from error
        return next(item for item in self.definitions() if item.key == key)

    def unregister(self, key: str) -> bool:
        """Remove a custom factor during an atomic registration rollback."""

        clean_key = str(key).strip()
        if not clean_key or clean_key.startswith("builtin_"):
            return False
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM factors WHERE key=?", (clean_key,))
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS factors (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    factor_type TEXT NOT NULL,
                    window INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    metric TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(factors)").fetchall()}
            if "metric" not in columns:
                connection.execute("ALTER TABLE factors ADD COLUMN metric TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS expression_factors (
                    factor_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    expression_hash TEXT NOT NULL,
                    category TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    data_version TEXT NOT NULL,
                    code_version TEXT NOT NULL,
                    sealed_test_id INTEGER NOT NULL,
                    mining_run_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(expression_hash,data_version,code_version)
                )"""
            )
            created_at = datetime.now(UTC).isoformat()
            existing = {str(row[0]) for row in connection.execute("SELECT key FROM factors").fetchall()}
            missing_defaults = [
                (f"builtin_{factor_type}_{window or metric}", name, factor_type, window, created_at, metric)
                for name, factor_type, window, metric in DEFAULT_FACTOR_DEFINITIONS
                if f"builtin_{factor_type}_{window or metric}" not in existing
            ]
            if missing_defaults:
                connection.executemany(
                    "INSERT INTO factors (key, name, factor_type, window, enabled, created_at, metric) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    missing_defaults,
                )


def calculate_factor(definition: FactorDefinition, history: list[Any]) -> float | None:
    """Calculate one registered factor from bars strictly available to the caller."""
    if definition.factor_type == "fundamental":
        return None
    if definition.factor_type == "expression":
        raise ValueError("表达式因子必须通过横截面表达式引擎计算")
    window = definition.window
    if len(history) < window + 1:
        return None
    closes = [
        float(item.signal_close if item.signal_close is not None else item.close)
        for item in history
        if float(item.signal_close if item.signal_close is not None else item.close) > 0
    ]
    if len(closes) < window + 1:
        return None
    if definition.factor_type in {"momentum", "reversal"}:
        value = closes[-1] / closes[-window - 1] - 1.0
        return -value if definition.factor_type == "reversal" else value
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(len(closes) - window, len(closes))]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return variance**0.5 * (252**0.5)


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_").lower()
    return result[:30] or "factor"
