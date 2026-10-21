from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..backtest.costs import CostModel
from ..data.schema import Bar
from ..data.trading_lots import buy_quantity_error
from .paper import PaperOrderStatus

CHINA_TZ = timezone(timedelta(hours=8))


class PaperStore:
    """SQLite-backed paper account and order ledger.

    This store is deliberately isolated from broker integrations. It only
    simulates fills and never sends an order to a real market.
    """

    def __init__(self, path: str | Path, initial_cash: float = 1_000_000.0) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_cash = initial_cash
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._transaction_depth = 0
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        self._ensure_account()
        from .corporate_actions import initialize
        with self.transaction():
            initialize(self._connection)

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    filled_quantity INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    limit_price REAL,
                    average_price REAL,
                    reject_reason TEXT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    idempotency_key TEXT,
                    signal_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                    trade_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_lots (
                    lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    acquired_on TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS paper_orders_created_order
                    ON paper_orders(created_at DESC, order_id DESC);
                CREATE TABLE IF NOT EXISTS paper_quotes (
                    symbol TEXT PRIMARY KEY,
                    quote_date TEXT NOT NULL,
                    price REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_equity (
                    quote_date TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    equity REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_cycles (
                    cycle_date TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    generated_orders INTEGER NOT NULL,
                    executed_orders INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'strategy',
                    factor_pipeline_version TEXT,
                    factor_keys_json TEXT,
                    factor_definitions_json TEXT,
                    status TEXT NOT NULL DEFAULT 'completed',
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_strategy_state (
                    strategy TEXT NOT NULL,
                    rebalance_every INTEGER NOT NULL,
                    last_rebalance_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(strategy, rebalance_every)
                );
                """
            )
            # Schema upgrades must also survive an interrupted startup atomically.
            self._connection.execute("BEGIN IMMEDIATE")
            cycle_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(paper_cycles)").fetchall()
            }
            if "source" not in cycle_columns:
                self._connection.execute(
                    "ALTER TABLE paper_cycles ADD COLUMN source TEXT NOT NULL DEFAULT 'strategy'"
                )
            for column, definition in (
                ("factor_pipeline_version", "TEXT"),
                ("factor_keys_json", "TEXT"),
                ("factor_definitions_json", "TEXT"),
            ):
                if column not in cycle_columns:
                    self._connection.execute(f"ALTER TABLE paper_cycles ADD COLUMN {column} {definition}")
            if "status" not in cycle_columns:
                # Legacy claims and zero-order completions were indistinguishable.
                # Only retain completed status where persisted work proves completion.
                self._connection.execute("ALTER TABLE paper_cycles RENAME TO paper_cycles_legacy")
                self._connection.execute(
                    "CREATE TABLE paper_cycles (cycle_date TEXT PRIMARY KEY,strategy TEXT NOT NULL,"
                    "generated_orders INTEGER NOT NULL,executed_orders INTEGER NOT NULL,"
                    "source TEXT NOT NULL DEFAULT 'strategy',factor_pipeline_version TEXT,"
                    "factor_keys_json TEXT,factor_definitions_json TEXT,status TEXT NOT NULL,"
                    "completed_at TEXT)"
                )
                self._connection.execute(
                    "INSERT INTO paper_cycles SELECT cycle_date,strategy,generated_orders,executed_orders,"
                    "source,factor_pipeline_version,factor_keys_json,factor_definitions_json,"
                    "CASE WHEN EXISTS (SELECT 1 FROM paper_equity e WHERE e.quote_date=cycle_date) "
                    "THEN 'completed' ELSE 'running' END,"
                    "CASE WHEN EXISTS (SELECT 1 FROM paper_equity e WHERE e.quote_date=cycle_date) "
                    "THEN completed_at ELSE NULL END FROM paper_cycles_legacy"
                )
                self._connection.execute("DROP TABLE paper_cycles_legacy")
            order_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(paper_orders)").fetchall()
            }
            if "idempotency_key" not in order_columns:
                self._connection.execute("ALTER TABLE paper_orders ADD COLUMN idempotency_key TEXT")
            if "signal_date" not in order_columns:
                self._connection.execute("ALTER TABLE paper_orders ADD COLUMN signal_date TEXT")
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_idempotency_key "
                "ON paper_orders(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )

    @contextmanager
    def transaction(self):
        """Serialize a complete unit of work; nested writes use rollback-safe savepoints.

        BEGIN IMMEDIATE also serializes independent connections/processes. Never use
        sqlite connection context managers inside this scope: they commit the parent.
        """
        with self._lock:
            depth = self._transaction_depth
            savepoint = f"paper_nested_{depth}"
            self._connection.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
            self._transaction_depth += 1
            try:
                yield self
                if depth == 0:
                    self._connection.commit()
                else:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                if depth == 0:
                    self._connection.rollback()
                else:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            finally:
                self._transaction_depth -= 1

    def _ensure_account(self) -> None:
        now = _now()
        with self.transaction():
            self._connection.execute(
                "INSERT OR IGNORE INTO paper_account(id, initial_cash, cash, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
                (self.initial_cash, self.initial_cash, now, now),
            )

    def close(self) -> None:
        """Close the SQLite connection for tests and controlled shutdowns."""
        with self._lock:
            self._connection.close()

    def reset(self, initial_cash: float | None = None) -> None:
        cash = self.initial_cash if initial_cash is None else float(initial_cash)
        if cash <= 0:
            raise ValueError("initial_cash must be positive")
        now = _now()
        with self.transaction():
            for table in (
                "paper_fills",
                "paper_orders",
                "paper_lots",
                "paper_quotes",
                "paper_equity",
                "paper_cycles",
                "paper_strategy_state",
                "paper_dividends",
                "paper_entitlement_days",
            ):
                self._connection.execute(f"DELETE FROM {table}")
            self._connection.execute(
                "UPDATE paper_account SET initial_cash=?, cash=?, updated_at=? WHERE id=1",
                (cash, cash, now),
            )

    def create_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        *,
        limit_price: float | None = None,
        source: str = "manual",
        idempotency_key: str | None = None,
        order_id: str | None = None,
        signal_date: date | None = None,
    ) -> dict[str, Any]:
        order_id = order_id or _new_order_id()
        side = side.upper().strip()
        now = _now()
        status = PaperOrderStatus.NEW.value
        reason = None
        if side not in {"BUY", "SELL"} or quantity <= 0:
            status = PaperOrderStatus.REJECTED.value
            reason = "invalid order"
        elif side == "BUY" and (lot_error := buy_quantity_error(symbol, quantity)):
            status = PaperOrderStatus.REJECTED.value
            reason = lot_error
        if limit_price is not None and limit_price <= 0:
            status = PaperOrderStatus.REJECTED.value
            reason = "limit_price must be positive"
        normalized_idempotency_key = str(idempotency_key or "").strip() or None
        with self.transaction():
            if normalized_idempotency_key:
                existing = self._connection.execute(
                    "SELECT * FROM paper_orders WHERE idempotency_key=?",
                    (normalized_idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return dict(existing)
                if signal_date is not None and source.startswith("strategy:"):
                    legacy = self._connection.execute(
                        "SELECT * FROM paper_orders WHERE signal_date=? AND source=? "
                        "AND symbol=? AND side=? AND idempotency_key IS NULL "
                        "ORDER BY created_at,order_id LIMIT 1",
                        (signal_date.isoformat(), source, symbol, side),
                    ).fetchone()
                    if legacy is not None:
                        self._connection.execute(
                            "UPDATE paper_orders SET idempotency_key=? WHERE order_id=?",
                            (normalized_idempotency_key, legacy["order_id"]),
                        )
                        return self.order(legacy["order_id"]) or {}
            self._connection.execute(
                "INSERT INTO paper_orders(order_id,symbol,side,quantity,status,limit_price,source,idempotency_key,signal_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, symbol, side, int(quantity), status, limit_price, source, normalized_idempotency_key, signal_date.isoformat() if signal_date else None, now, now),
            )
            if reason:
                self._connection.execute(
                    "UPDATE paper_orders SET reject_reason=? WHERE order_id=?", (reason, order_id)
                )
        return self.order(order_id) or {}

    def order(self, order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM paper_orders WHERE order_id=?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    def order_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM paper_orders WHERE idempotency_key=?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def pending_strategy_orders(self, before_date: date) -> list[dict[str, Any]]:
        """Return close-generated orders that are eligible for a later session's open."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM paper_orders WHERE status=? AND signal_date IS NOT NULL "
                "AND signal_date<? ORDER BY created_at,order_id",
                (PaperOrderStatus.NEW.value, before_date.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_order_symbols(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT symbol FROM paper_orders WHERE status IN (?,?) AND signal_date IS NOT NULL",
                (PaperOrderStatus.NEW.value, PaperOrderStatus.PARTIALLY_FILLED.value),
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def active_strategy_order_count(self) -> int:
        """Count all outstanding close-generated orders, including partial fills."""
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE status IN (?,?) AND signal_date IS NOT NULL",
                (PaperOrderStatus.NEW.value, PaperOrderStatus.PARTIALLY_FILLED.value),
            ).fetchone()[0])

    def reject_order(self, order_id: str, reason: str) -> dict[str, Any]:
        """Persist a deterministic rejection without attempting a fill."""
        if not str(reason).strip():
            raise ValueError("rejection reason cannot be empty")
        now = _now()
        with self.transaction():
            row = self._connection.execute(
                "SELECT status FROM paper_orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown paper order: {order_id}")
            if row["status"] in {PaperOrderStatus.FILLED.value, PaperOrderStatus.PARTIALLY_FILLED.value}:
                raise ValueError("cannot reject a filled paper order")
            self._connection.execute(
                "UPDATE paper_orders SET status=?, reject_reason=?, updated_at=? WHERE order_id=?",
                (PaperOrderStatus.REJECTED.value, str(reason)[:500], now, order_id),
            )
        return self.order(order_id) or {}

    def execute_order(self, order_id: str, bar: Bar, cost_model: CostModel | None = None) -> dict[str, Any]:
        model = cost_model or CostModel(max_participation=0.10)
        with self.transaction():
            row = self._connection.execute(
                "SELECT * FROM paper_orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown paper order: {order_id}")
            if row["status"] not in {PaperOrderStatus.NEW.value, PaperOrderStatus.PARTIALLY_FILLED.value}:
                return dict(row)
            result = self._try_fill(dict(row), bar, model)
            return result

    def _try_fill(self, order: dict[str, Any], bar: Bar, model: CostModel) -> dict[str, Any]:
        order_id = order["order_id"]
        now = _now()
        def reject(reason: str) -> dict[str, Any]:
            self._connection.execute(
                "UPDATE paper_orders SET status=?, reject_reason=?, updated_at=? WHERE order_id=?",
                (PaperOrderStatus.REJECTED.value, reason, now, order_id),
            )
            return self.order(order_id) or {}

        if order["side"] == "BUY" and (lot_error := buy_quantity_error(order["symbol"], int(order["quantity"]))):
            return reject(lot_error)
        if bar.paused or bar.volume <= 0:
            return reject("paused_or_no_volume")
        reference = order["limit_price"] or bar.open
        if reference <= 0:
            return reject("invalid_price")
        max_quantity = int(bar.volume * model.max_participation)
        remaining = int(order["quantity"] - order["filled_quantity"])
        if remaining > max_quantity:
            return reject("volume_limit")
        if order["side"] == "SELL":
            available = self._available_quantity(order["symbol"], bar.date)
            if available < remaining:
                return reject("t_plus_one_or_no_position")
        executed_price, _ = model.execution_price(order["side"], reference, remaining, bar.volume)
        if bar.amount is not None and remaining * executed_price > bar.amount * model.max_participation + 1e-9:
            return reject('amount_limit')
        if bar.limit_up is not None and order["side"] == "BUY" and executed_price >= bar.limit_up - 1e-10:
            return reject("limit_up_buy")
        if bar.limit_down is not None and order["side"] == "SELL" and executed_price <= bar.limit_down + 1e-10:
            return reject("limit_down_sell")
        costs = model.calculate(order["side"], reference, remaining, bar.volume, trading_date=bar.date)
        account = self._connection.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        cash = float(account["cash"])
        if order["side"] == "BUY":
            required = costs.notional + costs.total
            if required > cash + 1e-9:
                return reject("insufficient_cash")
            self._connection.execute("UPDATE paper_account SET cash=?, updated_at=? WHERE id=1", (cash - required, now))
            self._connection.execute(
                "INSERT INTO paper_lots(symbol,acquired_on,quantity,price) VALUES (?,?,?,?)",
                (order["symbol"], bar.date.isoformat(), remaining, executed_price),
            )
        else:
            self._remove_lots(order["symbol"], bar.date, remaining)
            self._connection.execute(
                "UPDATE paper_account SET cash=?, updated_at=? WHERE id=1",
                (cash + costs.notional - costs.total, now),
            )
        filled = int(order["filled_quantity"] + remaining)
        self._connection.execute(
            "UPDATE paper_orders SET filled_quantity=?, status=?, average_price=?, updated_at=? WHERE order_id=?",
            (filled, PaperOrderStatus.FILLED.value, executed_price, now, order_id),
        )
        self._connection.execute(
            "INSERT INTO paper_fills(order_id,trade_date,quantity,price,total_cost,created_at) VALUES (?,?,?,?,?,?)",
            (order_id, bar.date.isoformat(), remaining, executed_price, costs.total, now),
        )
        return self.order(order_id) or {}

    def _available_quantity(self, symbol: str, as_of: date) -> int:
        rows = self._connection.execute(
            "SELECT quantity FROM paper_lots WHERE symbol=? AND acquired_on<?",
            (symbol, as_of.isoformat()),
        ).fetchall()
        return sum(int(row["quantity"]) for row in rows)

    def _remove_lots(self, symbol: str, as_of: date, quantity: int) -> None:
        remaining = quantity
        rows = self._connection.execute(
            "SELECT lot_id,quantity FROM paper_lots WHERE symbol=? AND acquired_on<? ORDER BY acquired_on,lot_id",
            (symbol, as_of.isoformat()),
        ).fetchall()
        for row in rows:
            if remaining <= 0:
                break
            used = min(remaining, int(row["quantity"]))
            left = int(row["quantity"]) - used
            if left:
                self._connection.execute("UPDATE paper_lots SET quantity=? WHERE lot_id=?", (left, row["lot_id"]))
            else:
                self._connection.execute("DELETE FROM paper_lots WHERE lot_id=?", (row["lot_id"],))
            remaining -= used
        if remaining:
            raise ValueError("not enough T+1-eligible lots")

    def record_quotes(self, bars: list[Bar]) -> None:
        with self.transaction():
            for bar in bars:
                self._connection.execute(
                    "INSERT INTO paper_quotes(symbol,quote_date,price) VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET quote_date=excluded.quote_date,price=excluded.price",
                    (bar.symbol, bar.date.isoformat(), bar.close),
                )

    def record_entitlements(self, day: date) -> None:
        from .corporate_actions import snapshot
        with self.transaction():
            snapshot(self._connection, day)

    def settle_distributions(self, day: date, events: list[dict[str, Any]]) -> dict[str, Any]:
        from .corporate_actions import settle
        with self.transaction():
            return settle(self._connection, day, events)

    def record_equity(self, quote_date: date, prices: dict[str, float]) -> dict[str, float]:
        with self.transaction():
            state = self.state(prices)
            self._connection.execute(
                "INSERT INTO paper_equity(quote_date,cash,market_value,equity) VALUES (?,?,?,?) ON CONFLICT(quote_date) DO UPDATE SET cash=excluded.cash,market_value=excluded.market_value,equity=excluded.equity",
                (quote_date.isoformat(), state["cash"], state["market_value"], state["equity"]),
            )
        return state

    def has_cycle(self, cycle_date: date) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM paper_cycles WHERE cycle_date=? AND status='completed'", (cycle_date.isoformat(),)
            ).fetchone()
        return row is not None

    def last_rebalance_date(self, strategy: str, rebalance_every: int) -> date | None:
        """Return the persisted rebalance anchor, bootstrapping legacy accounts safely."""

        if rebalance_every <= 0:
            raise ValueError("rebalance_every must be positive")
        with self.transaction():
            row = self._connection.execute(
                "SELECT last_rebalance_date FROM paper_strategy_state WHERE strategy=? AND rebalance_every=?",
                (str(strategy), int(rebalance_every)),
            ).fetchone()
            if row is None:
                # Existing accounts predate the persistent cadence marker. Use the
                # latest completed daily cycle as a one-time anchor, so a restart or
                # upgrade does not cause an immediate catch-up rebalance.
                row = self._connection.execute(
                    "SELECT MAX(cycle_date) AS last_rebalance_date FROM paper_cycles WHERE status='completed'"
                ).fetchone()
                value = row["last_rebalance_date"] if row is not None else None
                if value:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO paper_strategy_state "
                        "(strategy,rebalance_every,last_rebalance_date,updated_at) VALUES (?,?,?,?)",
                        (str(strategy), int(rebalance_every), str(value), _now()),
                    )
            else:
                value = row["last_rebalance_date"]
        return date.fromisoformat(str(value)) if value else None

    def set_last_rebalance_date(self, strategy: str, rebalance_every: int, cycle_date: date) -> None:
        """Persist one completed rebalance check, including checks with no orders."""

        if rebalance_every <= 0:
            raise ValueError("rebalance_every must be positive")
        with self.transaction():
            self._connection.execute(
                "INSERT INTO paper_strategy_state(strategy,rebalance_every,last_rebalance_date,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(strategy,rebalance_every) DO UPDATE SET "
                "last_rebalance_date=excluded.last_rebalance_date,updated_at=excluded.updated_at",
                (str(strategy), int(rebalance_every), cycle_date.isoformat(), _now()),
            )

    def claim_cycle(
        self,
        cycle_date: date,
        *,
        strategy: str,
        source: str = "strategy",
        factor_pipeline_version: str | None = None,
        factor_keys: list[str] | tuple[str, ...] | None = None,
        factor_definitions: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
        resume_running: bool = False,
    ) -> bool:
        """Atomically reserve one trading date before creating any orders.

        The unique cycle date is the idempotency key shared by scheduled runs,
        manual triggers and retries.  A zero-order placeholder is completed by
        ``record_cycle`` only after the strategy loop finishes.
        """

        if resume_running and self._transaction_depth == 0:
            raise RuntimeError("resume_running requires an enclosing transaction")
        with self.transaction():
            if resume_running:
                existing = self._connection.execute(
                    "SELECT status,strategy FROM paper_cycles WHERE cycle_date=?",
                    (cycle_date.isoformat(),),
                ).fetchone()
                if existing is not None:
                    return existing["status"] == "running" and existing["strategy"] == strategy
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO paper_cycles(cycle_date,strategy,generated_orders,executed_orders,source,factor_pipeline_version,factor_keys_json,factor_definitions_json,status,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    cycle_date.isoformat(),
                    str(strategy),
                    0,
                    0,
                    str(source),
                    factor_pipeline_version,
                    json.dumps(list(factor_keys or []), ensure_ascii=False, sort_keys=True),
                    json.dumps(list(factor_definitions or []), ensure_ascii=False, sort_keys=True),
                    "running",
                    None,
                ),
            )
        return cursor.rowcount == 1

    def recent_cycles(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._connection.execute(
                "SELECT cycle_date,strategy,generated_orders,executed_orders,source,factor_pipeline_version,factor_keys_json,factor_definitions_json,status,completed_at "
                "FROM paper_cycles ORDER BY cycle_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for field in ("factor_keys_json", "factor_definitions_json"):
                raw = item.pop(field, None)
                try:
                    item[field.removesuffix("_json")] = json.loads(raw) if raw else []
                except (TypeError, json.JSONDecodeError):
                    item[field.removesuffix("_json")] = []
            result.append(item)
        return result

    def has_experiment_activity(self) -> bool:
        """Include incomplete cycles and manual orders, not just filled trades."""
        with self._lock:
            return any(self._connection.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0]
                       for table in ("paper_cycles", "paper_orders", "paper_equity", "paper_lots", "paper_dividends"))

    def cycle_summary(self) -> dict[str, Any]:
        """Return persisted paper-cycle coverage for SOP auditing."""

        with self._lock:
            cycle = self._connection.execute(
                "SELECT COUNT(*) AS count, MIN(cycle_date) AS first_date, MAX(cycle_date) AS last_date FROM paper_cycles WHERE status='completed'"
            ).fetchone()
            equity = self._connection.execute(
                "SELECT COUNT(*) AS count, MIN(quote_date) AS first_date, MAX(quote_date) AS last_date FROM paper_equity"
            ).fetchone()
            orders = self._connection.execute(
                "SELECT COUNT(*) AS count, SUM(CASE WHEN status='FILLED' THEN 1 ELSE 0 END) AS filled, SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected FROM paper_orders"
            ).fetchone()
        return {
            "cycles": int(cycle["count"] or 0),
            "first_cycle_date": cycle["first_date"],
            "last_cycle_date": cycle["last_date"],
            "equity_observations": int(equity["count"] or 0),
            "first_equity_date": equity["first_date"],
            "last_equity_date": equity["last_date"],
            "orders": int(orders["count"] or 0),
            "filled_orders": int(orders["filled"] or 0),
            "rejected_orders": int(orders["rejected"] or 0),
        }

    def record_cycle(
        self,
        cycle_date: date,
        *,
        strategy: str,
        generated_orders: int,
        executed_orders: int,
        source: str = "strategy",
        factor_pipeline_version: str | None = None,
        factor_keys: list[str] | tuple[str, ...] | None = None,
        factor_definitions: list[dict[str, object]] | tuple[dict[str, object], ...] | None = None,
    ) -> None:
        with self.transaction():
            updated = self._connection.execute(
                "UPDATE paper_cycles SET strategy=?,generated_orders=?,executed_orders=?,source=?,status='completed',completed_at=? WHERE cycle_date=?",
                (
                    strategy,
                    int(generated_orders),
                    int(executed_orders),
                    str(source),
                    _now(),
                    cycle_date.isoformat(),
                ),
            )
            if updated.rowcount == 0:
                self._connection.execute(
                    "INSERT INTO paper_cycles(cycle_date,strategy,generated_orders,executed_orders,source,factor_pipeline_version,factor_keys_json,factor_definitions_json,status,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        cycle_date.isoformat(),
                        strategy,
                        int(generated_orders),
                        int(executed_orders),
                        str(source),
                        factor_pipeline_version,
                        json.dumps(list(factor_keys or []), ensure_ascii=False, sort_keys=True),
                        json.dumps(list(factor_definitions or []), ensure_ascii=False, sort_keys=True),
                        "completed",
                        _now(),
                    ),
                )

    def orders_page(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValueError("Invalid order pagination")
        with self._lock:
            total = int(self._connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0])
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = self._connection.execute(
                "SELECT * FROM paper_orders ORDER BY created_at DESC,order_id DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        return {"orders": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total, "pages": pages}

    def state(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        with self._lock:
            account = self._connection.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            quote_rows = self._connection.execute("SELECT * FROM paper_quotes").fetchall()
            order_rows = self._connection.execute(
                "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            equity_rows = self._connection.execute(
                "SELECT * FROM paper_equity ORDER BY quote_date DESC LIMIT 90"
            ).fetchall()
            lots = self._connection.execute(
                "SELECT symbol,SUM(quantity) AS quantity,SUM(quantity*price) / SUM(quantity) AS average_price FROM paper_lots GROUP BY symbol"
            ).fetchall()
            distribution_rows = self._connection.execute(
                'SELECT event_id,symbol,record_date,quantity,cash_paid,shares_paid,cash_amount,share_amount,status FROM paper_dividends ORDER BY record_date DESC LIMIT 50'
            ).fetchall()
            distribution_cash = self._connection.execute('SELECT COALESCE(SUM(cash_amount),0) FROM paper_dividends').fetchone()[0]
        quote_map = {row["symbol"]: float(row["price"]) for row in quote_rows}
        quote_dates = {row["symbol"]: row["quote_date"] for row in quote_rows}
        if prices:
            quote_map.update({symbol: float(price) for symbol, price in prices.items()})
        positions = []
        market_value = 0.0
        for row in lots:
            price = quote_map.get(row["symbol"], float(row["average_price"]))
            value = int(row["quantity"]) * price
            market_value += value
            positions.append(
                {
                    "symbol": row["symbol"],
                    "quantity": int(row["quantity"]),
                    "average_price": float(row["average_price"]),
                    "last_price": price,
                    "quote_date": None if prices and row["symbol"] in prices else quote_dates.get(row["symbol"]),
                    "market_value": value,
                }
            )
        cash = float(account["cash"])
        equity = cash + market_value
        initial_cash = float(account["initial_cash"])
        return {
            "initial_cash": initial_cash,
            "cash": cash,
            "market_value": market_value,
            "equity": equity,
            "return_pct": equity / initial_cash - 1.0 if initial_cash else None,
            "positions": positions,
            "orders": [dict(row) for row in order_rows],
            "equity_curve": [dict(row) for row in reversed(equity_rows)],
            "corporate_distributions": {'net_cash_received':float(distribution_cash),
                                        'entitlements':[dict(row) for row in distribution_rows],
                                        'basis':'provider net cash; raw price and bonus listing date; no historical entitlement reconstruction'},
        }

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_order_id() -> str:
    return f"AP-{datetime.now(CHINA_TZ).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
