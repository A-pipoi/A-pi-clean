from __future__ import annotations

import hashlib
import inspect
import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

from ..api.resource_limits import ResourceLimitExceeded
from ..factors.store import FactorDefinition
from ..notifications import NotificationOutbox
from .maintenance import MaintenanceJournal, in_window
from .service import is_a_share_trading_day, run_strategy_cycle
from .store import PaperStore

LOGGER = logging.getLogger("a_pi.paper_scheduler")
CHINA_TZ = timezone(timedelta(hours=8))
PAPER_RUN_TIME = datetime_time(hour=16, minute=0)


class PaperScheduler:
    """Small idempotent scheduler for the paper account only.

    It never talks to a broker. The cycle itself is guarded by the persisted
    trading-date marker, so service restarts cannot duplicate a paper cycle.
    """

    def __init__(
        self,
        store: PaperStore,
        *,
        symbols: list[str],
        strategy: str = "baseline",
        rebalance: str = "weekly",
        cache_dir: str | None = None,
        interval_seconds: int = 900,
        factor_definitions_provider: Callable[[], Sequence[FactorDefinition]] | None = None,
        sealed_test_provider: Callable[..., dict[str, Any]] | None = None,
        universe_provider: Callable[[date], dict[str, Any]] | None = None,
        universe_index: str | None = None,
        after_cycle: Callable[[dict[str, Any]], None] | None = None,
        execution_mode: str = "formal",
        research_observer: Callable[[str, str], None] | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("symbols cannot be empty")
        if interval_seconds < 60:
            raise ValueError("interval_seconds must be at least 60 seconds")
        if rebalance not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance must be daily, weekly, or monthly")
        self.store = store
        self.symbols = list(dict.fromkeys(symbols))
        self.strategy = strategy
        self.rebalance = rebalance
        self.cache_dir = cache_dir
        self.interval_seconds = interval_seconds
        self.factor_definitions_provider = factor_definitions_provider
        self.sealed_test_provider = sealed_test_provider
        self.universe_provider = universe_provider
        self.universe_index = universe_index
        self._last_universe: dict[str, Any] | None = None
        self._last_symbols = list(self.symbols)
        self.after_cycle = after_cycle
        if execution_mode not in {"formal", "research"}:
            raise ValueError("invalid paper execution mode")
        self.execution_mode = execution_mode
        self.research_observer = research_observer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._universe_resolve_lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._execution_in_progress = False
        self.managed = False
        self.journal = MaintenanceJournal(self.store.path)
        self._last_run_at: str | None = None
        self._last_cycle_date: str | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        try:
            self._last_cycle_date = self.store.cycle_summary().get("last_cycle_date")
        except Exception:
            LOGGER.debug("could not restore paper cycle status", exc_info=True)
        persisted = self.journal.status().get("latest")
        if persisted:
            self._last_result = persisted.get("result") or None
            self._last_run_at = persisted.get("finished_at") or persisted.get("started_at")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="a-pi-paper-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def remember_universe_snapshot(self, universe: dict[str, Any], symbols: Sequence[str]) -> None:
        """Keep the most recently resolved PIT universe visible to status APIs."""

        normalized_symbols = list(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
        if not normalized_symbols:
            raise ValueError("resolved PIT universe cannot be empty")
        snapshot = {key: value for key, value in universe.items() if key != "symbols"}
        if self.universe_index and str(snapshot.get("index_code") or "").strip().upper() != self.universe_index:
            raise ValueError("resolved PIT universe does not match the configured paper index")
        if snapshot.get("mode") != "pit_index_members":
            raise ValueError("paper status only accepts a PIT index-members snapshot")
        with self._lock:
            self._last_universe = snapshot
            self._last_symbols = normalized_symbols

    def resolve_universe_for_status(self, as_of: date) -> dict[str, Any] | None:
        """Resolve PIT membership for status display without running a paper cycle."""

        if self.universe_provider is None or not self.universe_index:
            return None
        with self._universe_resolve_lock:
            with self._lock:
                cached_as_of = str((self._last_universe or {}).get("as_of") or "")
                if self._last_universe and self._last_symbols and cached_as_of == as_of.isoformat():
                    return {**self._last_universe, "symbols": list(self._last_symbols)}
            universe = self.universe_provider(as_of)
            symbols = [str(item).strip() for item in universe.get("symbols", []) if str(item).strip()]
            self.remember_universe_snapshot(universe, symbols)
            return {**(self._last_universe or {}), "symbols": list(self._last_symbols)}

    def run_once(self) -> dict[str, Any]:
        """The shared scheduled/manual entry: bounded window, durable retries."""
        now = _china_now()
        if _china_today().weekday() >= 5 or now.time() < PAPER_RUN_TIME:
            return self._perform_cycle()
        if not in_window(now):
            self.check_missed_window(now)
            return {"skipped": True, "cycle_date": None, "generated_orders": 0,
                    "simulated_orders": [], "paper_trading_allowed": False,
                    "outcome": "outside_window", "skip_reason": "已超出 16:00–16:10:59 执行窗口，不自动补跑"}
        if not self._execution_lock.acquire(blocking=False):
            return {"skipped": True, "cycle_date": None, "generated_orders": 0,
                    "simulated_orders": [], "paper_trading_allowed": False,
                    "outcome": "already_running", "skip_reason": "当前账户正在执行，未重复启动"}
        owner = None
        renewal_stop = threading.Event()
        renewal_thread = None
        day = now.date().isoformat()
        try:
            config = json.dumps({"strategy": self.strategy, "rebalance": self.rebalance,
                                 "universe_index": self.universe_index, "symbols": self.symbols}, sort_keys=True)
            owner = self.journal.claim(now, config)
            if owner is None:
                latest = self.journal.status().get("latest") or {}
                if latest.get("reason_code") == "config_changed":
                    self._notify(day, "blocked")
                return {"skipped": True, "cycle_date": day, "generated_orders": 0,
                        "simulated_orders": [], "paper_trading_allowed": False,
                        "outcome": "maintenance_not_due", "skip_reason": "今日维护已结束、正在执行或等待有限重试"}
            self._execution_in_progress = True
            def renew() -> None:
                while not renewal_stop.wait(30):
                    try:
                        self.journal.renew(day, owner, _china_now())
                    except Exception:
                        LOGGER.exception("maintenance lease renewal failed")
            renewal_thread = threading.Thread(target=renew, name="paper-maintenance-lease", daemon=True)
            renewal_thread.start()
            result = self._perform_cycle()
            source = (result.get("data_freshness") or {}).get("source")
            if not result.get("skipped") or result.get("outcome") == "completed" or "已运行过" in str(result.get("skip_reason", "")):
                status, reason = "completed", str(result.get("outcome") or "daily_cycle_completed")
            elif result.get("idempotency"):
                status, reason = "blocked", "unfinished_cycle_conflict"
            elif source == "market_closed":
                status, reason = "market_closed", "exchange_closed"
            elif source != "sealed_test_blocked" and result.get("strategy_validation") is None and result.get("data_freshness") is not None:
                status, reason = "waiting_data", "data_not_ready"
            else:
                status, reason = "blocked", "governance_gate"
            summary = dict(self._last_result or {key: value for key, value in result.items() if key != "state"})
            summary["execution_source_sha256"] = _EXECUTION_SOURCE_DIGEST
            summary["sealed_check_mode"] = "existing_acceptance_only"
            self._last_result = summary
            self.journal.finish(day, owner, _china_now(), status, reason, summary)
            if status == "blocked":
                self._notify(day, "blocked")
            fills = result.get("simulated_orders") or []
            if any(item.get("status") in {"FILLED", "PARTIALLY_FILLED"} for item in fills):
                self._notify(day, "new_fills")
            return result
        except Exception as error:
            if owner:
                quota = isinstance(error, ResourceLimitExceeded)
                failure = {"outcome": "failed", "cycle_date": day, "skipped": True,
                           "no_trade_reason": ("Tushare 本机请求预算耗尽；未返回部分股票池冒充完整行情，未生成委托"
                                               if quota else "本次维护失败，账户写入由事务保护；请查看运行日志"),
                           "paper_trading_allowed": False,
                           "data_freshness": {"fresh": False, "source": "maintenance_failed"},
                           "generated_orders": 0, "simulated_orders": 0, "risk_rejections": 0}
                self._last_result = failure
                if isinstance(error, ResourceLimitExceeded):
                    failure["retry_after_seconds"] = error.retry_after
                self.journal.finish(day, owner, _china_now(), "blocked" if quota else "failed",
                                    error.reason if isinstance(error, ResourceLimitExceeded) else type(error).__name__, failure)
                self._notify(day, "blocked" if quota else "failed")
            raise
        finally:
            renewal_stop.set()
            if renewal_thread:
                renewal_thread.join(timeout=2)
            self._execution_in_progress = False
            self._execution_lock.release()

    def run_manual_catchup(self, *, expected_day: str, reason: str) -> dict[str, Any]:
        """Explicit recovery only: existing gates, real time, no retroactive fills."""
        now = _china_now()
        if expected_day != now.date().isoformat():
            raise ValueError("人工补跑日期必须是当前北京时间日期，不接受历史补跑")
        if not self._execution_lock.acquire(blocking=False):
            return {"skipped": True, "outcome": "already_running", "paper_trading_allowed": False}
        owner = None
        try:
            config = json.dumps({"strategy": self.strategy, "rebalance": self.rebalance,
                                 "universe_index": self.universe_index, "symbols": self.symbols}, sort_keys=True)
            owner = self.journal.claim_manual(now, config, reason)
            if owner is None:
                return {"skipped": True, "outcome": "manual_catchup_not_due", "paper_trading_allowed": False,
                        "skip_reason": "仅允许当日零次尝试的漏跑账户一次人工补跑"}
            self._execution_in_progress = True
            result = self._perform_cycle(defer_pending_fills=True)
            result["manual_catchup"] = {"started_at": now.isoformat(), "reason": reason,
                                        "retroactive_fills": False, "missed_window_preserved": True}
            summary = {key: value for key, value in result.items() if key != "state"}
            summary["execution_source_sha256"] = _EXECUTION_SOURCE_DIGEST
            status = "completed" if result.get("outcome") == "completed" or not result.get("skipped") else "blocked"
            self.journal.finish_manual(expected_day, owner, _china_now(), status, summary)
            self._last_result = summary
            return result
        except Exception as error:
            if owner:
                self.journal.finish_manual(expected_day, owner, _china_now(), "failed", {"error_type": type(error).__name__})
            raise
        finally:
            self._execution_in_progress = False
            self._execution_lock.release()

    def _notify(self, day: str, event: str) -> None:
        messages = {
            "blocked": ("A-pi 纸面维护被门禁阻止", "请查看纸面交易页的门禁原因；未自动放宽校验或修改策略。"),
            "failed": ("A-pi 纸面维护失败", "请检查本地服务日志和运行记录；窗口内将有限重试，窗口外不补跑。"),
            "new_fills": ("A-pi 出现新纸面成交", "请在纸面交易页核对模拟成交与持仓；没有向券商发送真实订单。"),
            "missed_window": ("A-pi 今日维护窗口未完成", "16:00–16:10:59 内未完成有效启动或检查。请查看服务及数据状态；没有窗口外补跑。"),
        }
        title, body = messages[event]
        try:
            NotificationOutbox(self.store.path.with_name("notifications.sqlite3")).enqueue(f"paper:{day}:{event}", title, body)
        except Exception:
            LOGGER.exception("paper notification persistence failed")

    def check_missed_window(self, now: datetime | None = None) -> None:
        if self.journal.expire_window(now or _china_now()):
            self._notify((now or _china_now()).date().isoformat(), "missed_window")

    def _perform_cycle(self, *, defer_pending_fills: bool = False) -> dict[str, Any]:
        today = _china_today()
        if today.weekday() >= 5:
            result: dict[str, Any] = {
                "cycle_date": None,
                "skipped": True,
                "skip_reason": "周末休眠：A股非交易日，不请求 Tushare",
                "generated_orders": 0,
                "simulated_orders": [],
                "risk_rejections": [],
                "strategy_validation": None,
                "data_quality": {"limits_available": None, "warnings": ["周末非交易日"]},
                "data_freshness": {"fresh": False, "source": "market_closed", "warnings": ["周末非交易日"]},
                "paper_trading_allowed": False,
            }
            with self._lock:
                self._last_run_at = datetime.now(UTC).isoformat()
                if self._last_result is None:
                    self._last_result = {
                        "cycle_date": None,
                        "skipped": True,
                        "skip_reason": result["skip_reason"],
                        "generated_orders": 0,
                        "simulated_orders": 0,
                        "risk_rejections": 0,
                        "strategy_validation": None,
                        "data_quality": result["data_quality"],
                        "data_freshness": result.get("data_freshness"),
                        "pre_trade_risk": result.get("pre_trade_risk"),
                        "paper_universe": result.get("paper_universe"),
                        "paper_trading_allowed": result.get("paper_trading_allowed", False),
                    }
                self._last_error = None
            return result
        now = _china_now()
        if now.time() < PAPER_RUN_TIME:
            result = {
                "cycle_date": None,
                "skipped": True,
                "skip_reason": "未到 16:00：纸面交易仅在 A 股收盘后执行，不请求 Tushare",
                "generated_orders": 0,
                "simulated_orders": [],
                "risk_rejections": [],
                "strategy_validation": None,
                "data_quality": {"limits_available": None, "warnings": ["未到 16:00 执行时间"]},
                "data_freshness": {"fresh": False, "source": "before_schedule", "warnings": ["未到 16:00 执行时间"]},
                "paper_trading_allowed": False,
            }
            with self._lock:
                self._last_run_at = datetime.now(UTC).isoformat()
                if self._last_result is None:
                    self._last_result = {
                        "cycle_date": None,
                        "skipped": True,
                        "skip_reason": result["skip_reason"],
                        "generated_orders": 0,
                        "simulated_orders": 0,
                        "risk_rejections": 0,
                        "strategy_validation": None,
                        "data_quality": result["data_quality"],
                        "data_freshness": result["data_freshness"],
                        "paper_trading_allowed": False,
                    }
                self._last_error = None
            return result
        try:
            sealed_test: dict[str, Any] | None = None
            if not is_a_share_trading_day(today):
                result = {
                    "cycle_date": None,
                    "skipped": True,
                    "skip_reason": f"{today.isoformat()} 为 A 股休市日，未请求日线或生成纸面委托",
                    "generated_orders": 0,
                    "simulated_orders": [],
                    "risk_rejections": [],
                    "strategy_validation": None,
                    "data_quality": {"limits_available": None, "warnings": ["Tushare trade_cal 标记休市"]},
                    "data_freshness": {"fresh": False, "source": "market_closed", "warnings": ["A 股休市日"]},
                    "paper_trading_allowed": False,
                }
            else:
                provided_universe = self.universe_provider(today) if self.universe_provider is not None else {
                    "mode": "explicit_symbols",
                    "as_of": today.isoformat(),
                    "count": len(self.symbols),
                    "symbols": list(self.symbols),
                }
                if not isinstance(provided_universe, dict):
                    raise RuntimeError("PIT 股票池响应格式无效")
                universe: dict[str, object] = provided_universe
                raw_symbols = universe.get("symbols")
                if not isinstance(raw_symbols, (list, tuple)):
                    raise RuntimeError("PIT 股票池未提供成分列表")
                symbols = [str(item).strip() for item in raw_symbols if str(item).strip()]
                if not symbols:
                    raise RuntimeError("PIT 股票池为空，已停止纸面循环")
                if universe.get("mode") == "pit_index_members":
                    self.remember_universe_snapshot(universe, symbols)
                else:
                    self._last_symbols = list(symbols)
                    self._last_universe = {key: value for key, value in universe.items() if key != "symbols"}
                open_orders = self.store.active_order_symbols()
                held_symbols = [str(item["symbol"]) for item in self.store.state().get("positions", [])]
                additional_symbols = list(dict.fromkeys([*held_symbols, *open_orders]))
                sealed_test = _provide_sealed_test(
                    self.sealed_test_provider,
                    symbols,
                    self._last_universe,
                )
                mode_options = ({"execution_mode": "research", "research_observer": self.research_observer}
                                if self.execution_mode == "research" else {})
                result = run_strategy_cycle(
                    self.store,
                    symbols=symbols,
                    additional_symbols=additional_symbols,
                    universe_snapshot=self._last_universe,
                    cache_dir=self.cache_dir,
                    strategy=self.strategy,
                    rebalance_every={"daily": 1, "weekly": 5, "monthly": 21}[self.rebalance],
                    factor_definitions=(
                        list(self.factor_definitions_provider())
                        if self.factor_definitions_provider is not None
                        else None
                    ),
                    sealed_test=sealed_test,
                    **({"defer_pending_fills": True} if defer_pending_fills else {}),
                    **mode_options,
                )
            if self.after_cycle is not None:
                try:
                    self.after_cycle(result)
                except Exception:
                    LOGGER.exception("post-cycle operations failed")
            with self._lock:
                self._last_run_at = datetime.now(UTC).isoformat()
                cycle_date = result.get("cycle_date")
                if isinstance(cycle_date, str) and not result.get("skipped"):
                    self._last_cycle_date = cycle_date
                self._last_result = {
                    "cycle_date": result.get("cycle_date"),
                    "skipped": result.get("skipped", False),
                    "skip_reason": result.get("skip_reason"),
                    "generated_orders": result.get("generated_orders", 0),
                    "simulated_orders": _collection_length(result.get("simulated_orders")),
                    "risk_rejections": _collection_length(result.get("risk_rejections")),
                    "strategy_validation": result.get("strategy_validation"),
                    "rebalance_schedule": result.get("rebalance_schedule"),
                    "next_rebalance": result.get("next_rebalance"),
                    "sealed_test": result.get("sealed_test") or sealed_test,
                    "data_quality": result.get("data_quality"),
                    "data_freshness": result.get("data_freshness"),
                    "paper_trading_allowed": result.get("paper_trading_allowed", False),
                    "outcome": result.get("outcome"),
                    "no_trade_reason": result.get("no_trade_reason") or result.get("skip_reason"),
                    "maintenance_stages": result.get("maintenance_stages"),
                    "pending_orders": result.get("pending_orders", 0),
                }
                self._last_error = None
            return result
        except Exception as error:  # scheduler must stay alive after provider failures
            with self._lock:
                self._last_run_at = datetime.now(UTC).isoformat()
                self._last_error = str(error)
            LOGGER.exception("paper cycle failed")
            raise

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "enabled": True,
                "running": thread_alive and not self._stop.is_set(),
                "scheduler_running": (thread_alive or self.managed) and not self._stop.is_set(),
                "execution_in_progress": self._execution_in_progress,
                "authority": "backend",
                "execution_mode": self.execution_mode,
                "account_label": "未验收研究纸面账户" if self.execution_mode == "research" else "正式纸面账户",
                "execution_source_sha256": _EXECUTION_SOURCE_DIGEST,
                "scheduled_window": "16:00:00–16:10:59",
                "maintenance": self.journal.status(),
                "interval_seconds": self.interval_seconds,
                "strategy": self.strategy,
                "rebalance": self.rebalance,
                "symbols": list(self._last_symbols),
                "symbol_count": int(self._last_universe.get("count", len(self.symbols))) if self._last_universe else len(self.symbols),
                "universe_index": self.universe_index,
                "universe": self._last_universe,
                "last_run_at": self._last_run_at,
                "last_cycle_date": self._last_cycle_date,
                "last_result": self._last_result,
                "last_error": self._last_error,
                "calendar": "Tushare SSE trade_cal（执行窗口核验）",
                "scheduled_time": "16:00",
                "timezone": "Asia/Shanghai",
                "next_run_at": _next_scheduled_at(self).isoformat(),
                "weekend_sleep": _china_today().weekday() >= 5,
            }

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait_seconds = max(0.0, (_next_scheduled_at(self) - _china_now()).total_seconds())
            if self._stop.wait(wait_seconds):
                return
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("Scheduled paper-trading cycle failed")
            self._stop.wait(15)


def _next_scheduled_at(scheduler: PaperScheduler) -> datetime:
    """Return the next local 16:00 run without touching market data."""

    now = _china_now()
    today = now.date()
    if today.weekday() < 5:
        scheduled_today = datetime.combine(today, PAPER_RUN_TIME, tzinfo=CHINA_TZ)
        if now <= scheduled_today:
            return scheduled_today
        if in_window(now):
            latest = scheduler.journal.status().get("latest")
            if not latest or latest.get("run_date") != today.isoformat():
                return now
            if latest.get("status") in {"waiting_data", "failed", "running"} and latest.get("attempts", 0) < 5:
                retry_at = latest.get("next_attempt_at") or latest.get("lease_until")
                if retry_at:
                    retry = datetime.fromisoformat(retry_at)
                    if in_window(retry):
                        return max(now, retry)

    next_date = today + timedelta(days=1)
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    return datetime.combine(next_date, PAPER_RUN_TIME, tzinfo=CHINA_TZ)


def _provide_sealed_test(
    provider: Callable[..., dict[str, Any]] | None,
    symbols: list[str],
    universe: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if provider is None:
        return None
    parameters: Mapping[str, inspect.Parameter] = {}
    try:
        parameters = inspect.signature(provider).parameters
    except (TypeError, ValueError):
        pass
    if len(parameters) >= 2:
        return provider(symbols, universe or {})
    return provider(symbols) if parameters else provider()


def _china_now() -> datetime:
    return datetime.now(CHINA_TZ)


def _collection_length(value: object) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _china_today() -> date:
    return _china_now().date()


def _execution_source_digest() -> str:
    """Pin actual execution sources, including dirty/uncommitted code."""
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    paths = sorted({*root.joinpath("execution").glob("*.py"),
                    *root.joinpath("strategies").glob("*.py"),
                    *root.joinpath("portfolio").glob("*.py"),
                    root / "api" / "paper_governance.py"})
    for path in paths:
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


_EXECUTION_SOURCE_DIGEST = _execution_source_digest()
