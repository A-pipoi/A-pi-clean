import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from quant.backtest import CostModel
from quant.backtest.engine import Order
from quant.execution import service
from quant.execution.service import run_strategy_cycle
from quant.execution.store import PaperStore
from quant.strategies.baseline import CrossSectionalMomentum
from tests.helpers import bar

DAY = date(2026, 9, 24)


@contextmanager
def cycle_inputs(*, passed=True, orders=()):
    bars = [
        replace(bar(symbol, offset, close=11 if offset == 2 else 10, volume=100000),
                date=date(2026, 9, 22 + offset))
        for symbol in ("000001.SZ", "000002.SZ") for offset in range(3)
    ]
    metadata = {"data_source": "tushare_live", "latest_trade_date": DAY.isoformat(),
                "limits_available": True}
    adapter = SimpleNamespace(daily_bars=lambda **kwargs: bars,
                              snapshot=lambda: SimpleNamespace(version="fixture", metadata=metadata))
    validation = SimpleNamespace(passed=passed, data_days=3, folds=0, embargo=0,
                                 oos_observations=0, annualized_return=0, sharpe=0,
                                 max_drawdown=0, note="fixture", checks=[])
    with ExitStack() as stack:
        for target, value in (
            ("_china_today", DAY), ("is_a_share_trading_day", True),
            ("TushareDailyAdapter", adapter), ("validate_strategy", validation),
            ("get_tushare_stock_metadata", ({}, False)), ("check_order_limits", []),
            ("resolve_strategy_config", (SimpleNamespace(key="baseline", label="Baseline"), 1, 1)),
        ):
            stack.enter_context(patch("quant.execution.service." + target, return_value=value))
        stack.enter_context(patch("quant.execution.corporate_actions.observed_events", return_value=[]))
        stack.enter_context(patch.object(CrossSectionalMomentum, "orders_for_date", return_value=list(orders)))
        yield bars


@pytest.fixture
def account(tmp_path):
    store = PaperStore(tmp_path / "paper.sqlite3")
    yield store
    store.close()


def test_claim_is_running_not_completed_or_counted(account):
    assert account.claim_cycle(DAY, strategy="baseline")
    assert not account.has_cycle(DAY)
    assert account.cycle_summary()["cycles"] == 0
    assert account.recent_cycles()[0]["status"] == "running"
    assert account.recent_cycles()[0]["completed_at"] is None
    assert account.last_rebalance_date("baseline", 5) is None
    account.record_cycle(DAY, strategy="baseline", generated_orders=0, executed_orders=0)
    assert account.has_cycle(DAY)
    assert account.cycle_summary()["cycles"] == 1


def test_all_nested_writes_roll_back(account):
    before = account.state()
    with pytest.raises(RuntimeError), account.transaction():
        account.claim_cycle(DAY, strategy="baseline")
        order = account.create_order("000001.SZ", "BUY", 100, idempotency_key="persist-once")
        account.execute_order(order["order_id"], bar("000001.SZ", 0), CostModel())
        account.record_quotes([bar("000001.SZ", 0)])
        account.record_entitlements(DAY)
        account.record_equity(DAY, {"000001.SZ": 11})
        account.set_last_rebalance_date("baseline", 5, DAY)
        account.record_cycle(DAY, strategy="baseline", generated_orders=1, executed_orders=1)
        raise RuntimeError("late failure")
    assert account.state() == before
    assert account.recent_cycles() == []
    assert account.last_rebalance_date("baseline", 5) is None
    for table in ("paper_fills", "paper_quotes", "paper_entitlement_days"):
        assert account._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_caught_nested_error_uses_savepoint(account):
    with account.transaction():
        account.create_order("000001.SZ", "BUY", 100, idempotency_key="keep")
        with pytest.raises(ValueError), account.transaction():
            account.create_order("000002.SZ", "BUY", 100, idempotency_key="discard")
            raise ValueError("inner")
        account.record_quotes([bar("000001.SZ", 0)])
    assert account.order_by_idempotency_key("keep")
    assert account.order_by_idempotency_key("discard") is None


@pytest.mark.parametrize("forced_kill", [False, True])
def test_process_exit_rolls_back_uncommitted_work(account, forced_kill):
    account.close()
    code = """import os, sys
from datetime import date
from quant.execution.store import PaperStore
from tests.helpers import bar
s = PaperStore(sys.argv[1])
with s.transaction():
    s.claim_cycle(date(2026, 9, 24), strategy='baseline')
    o = s.create_order('000001.SZ', 'BUY', 100)
    s.execute_order(o['order_id'], bar('000001.SZ', 0))
    s.record_quotes([bar('000001.SZ', 0)])
    s.record_equity(date(2026, 9, 24), {'000001.SZ': 10})
    s.set_last_rebalance_date('baseline', 5, date(2026, 9, 24))
    os._exit(37)
"""
    if forced_kill:
        code = code.replace("os._exit(37)", "print('READY', flush=True); __import__('time').sleep(30)")
        process = subprocess.Popen([sys.executable, "-c", code, str(account.path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert process.stdout.readline().strip() == b"READY"
            process.kill()
            process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    else:
        result = subprocess.run([sys.executable, "-c", code, str(account.path)],
                                capture_output=True, timeout=30, check=False)
        assert result.returncode == 37, result.stderr
    # Rebind the fixture connection for normal finalization.
    account._connection = sqlite3.connect(account.path, check_same_thread=False)
    account._connection.row_factory = sqlite3.Row
    assert account.state()["orders"] == []
    assert account.state()["cash"] == account.initial_cash
    assert account.state()["equity_curve"] == []
    assert account.recent_cycles() == []
    assert account.last_rebalance_date("baseline", 5) is None


@pytest.mark.parametrize("same_connection", [True, False])
def test_concurrent_cycles_are_serialized(account, same_connection):
    second = account if same_connection else PaperStore(account.path)
    try:
        with cycle_inputs(orders=[Order("000001.SZ", "BUY", 100)]), ThreadPoolExecutor(max_workers=2) as pool:
            prepared = threading.Barrier(2, timeout=5)
            validation = service.validate_strategy

            def concurrent_validation(*args, **kwargs):
                assert not account._connection.in_transaction
                prepared.wait()
                return validation(*args, **kwargs)

            def checked_has_cycle(day):
                assert account._connection.in_transaction
                return original_has_cycle(day)

            original_has_cycle = account.has_cycle
            with (patch.object(service, "validate_strategy", side_effect=concurrent_validation),
                  patch.object(account, "has_cycle", side_effect=checked_has_cycle)):
                results = list(pool.map(
                    lambda store: run_strategy_cycle(store, symbols=["000001.SZ"]), [account, second]
                ))
        assert sum(not result["skipped"] for result in results) == 1
        assert len(account.state()["orders"]) == 1
        assert account.cycle_summary()["cycles"] == 1
        duplicate = next(result for result in results if result["skipped"])
        assert duplicate["outcome"] == "completed"
        assert duplicate["idempotency"]["claimed"] is False
    finally:
        if second is not account:
            second.close()


@pytest.mark.parametrize("sealed", [True, False])
def test_gate_blocks_old_and_new_orders_but_updates_valuation(account, sealed):
    bought = account.create_order("000001.SZ", "BUY", 100)
    account.execute_order(bought["order_id"], bar("000001.SZ", 0))
    old = account.create_order("000002.SZ", "BUY", 100,
                               signal_date=date(2026, 9, 23), source="strategy:baseline")
    before_cash = account.state()["cash"]
    with cycle_inputs(passed=sealed, orders=[Order("000001.SZ", "BUY", 100)]):
        result = run_strategy_cycle(account, symbols=["000001.SZ"],
                                    sealed_test={"passed": False} if sealed else None)
    assert result["outcome"] == "governance_blocked"
    if sealed:
        assert result["data_freshness"]["source"] == "sealed_test_blocked"
    assert result["maintenance_stages"]["pending_orders"]["status"] == "blocked"
    assert result["maintenance_stages"]["rebalance"]["status"] == "blocked"
    assert result["maintenance_stages"]["equity"]["status"] == "completed"
    assert account.order(old["order_id"])["status"] == "NEW"
    assert len(account.state()["orders"]) == 2
    assert account.state()["cash"] == before_cash
    assert account.state()["positions"][0]["last_price"] == 11
    assert account.state()["equity_curve"][-1]["quote_date"] == DAY.isoformat()
    assert not account.has_cycle(DAY)


def test_failed_cycle_retry_preserves_existing_idempotency(account):
    account.claim_cycle(DAY, strategy="baseline", factor_keys=["original"])
    existing = account.create_order("000001.SZ", "BUY", 100, signal_date=DAY,
                                   idempotency_key=f"cycle:{DAY}:baseline:000001.SZ:BUY")
    with cycle_inputs(orders=[Order("000001.SZ", "BUY", 100)]):
        with patch.object(account, "record_equity", side_effect=RuntimeError("late")), pytest.raises(RuntimeError):
            run_strategy_cycle(account, symbols=["000001.SZ"])
        assert account.recent_cycles()[0]["status"] == "running"
        result = run_strategy_cycle(account, symbols=["000001.SZ"])
    assert not result["skipped"]
    assert account.has_cycle(DAY)
    assert account.recent_cycles()[0]["factor_keys"] == ["original"]
    assert [o["order_id"] for o in account.state()["orders"]] == [existing["order_id"]]


def test_pending_fill_uses_its_own_bar(account):
    pending = account.create_order("000001.SZ", "BUY", 100,
                                  signal_date=date(2026, 9, 23), source="strategy:baseline")
    with cycle_inputs(), patch("quant.execution.service.check_order_limits", return_value=[]) as risk:
        result = run_strategy_cycle(account, symbols=["000001.SZ"])
    assert account.order(pending["order_id"])["status"] == "FILLED"
    assert risk.call_args.args[4] == "000001.SZ"
    assert result["outcome"] == "traded"
    assert result["no_trade_reason"] is None


def test_cycle_failure_rolls_back_pending_fill_and_schedule(account):
    pending = account.create_order("000001.SZ", "BUY", 100,
                                  signal_date=date(2026, 9, 23), source="strategy:baseline")
    before = account.state()
    with (cycle_inputs(orders=[Order("000002.SZ", "BUY", 100)]),
          patch.object(account, "record_cycle", side_effect=RuntimeError("completion")),
          pytest.raises(RuntimeError)):
        run_strategy_cycle(account, symbols=["000001.SZ", "000002.SZ"])
    assert account.state() == before
    assert account.order(pending["order_id"])["status"] == "NEW"
    assert not account.has_cycle(DAY)
    assert account.last_rebalance_date("baseline", 5) is None


def test_blocked_day_can_resume_after_gate_passes(account):
    with cycle_inputs():
        first = run_strategy_cycle(account, symbols=["000001.SZ"], sealed_test={"passed": False})
        second = run_strategy_cycle(account, symbols=["000001.SZ"], sealed_test={"passed": True})
    assert first["outcome"] == "governance_blocked"
    assert not second["skipped"]
    assert account.has_cycle(DAY)
    assert {"maintenance_stages", "outcome", "no_trade_reason", "next_rebalance"} <= second.keys()


def test_legacy_claim_migration_requires_completion_evidence(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE paper_cycles (cycle_date TEXT PRIMARY KEY, strategy TEXT NOT NULL,"
                   "generated_orders INTEGER NOT NULL, executed_orders INTEGER NOT NULL,"
                   "completed_at TEXT NOT NULL)")
        db.execute("CREATE TABLE paper_equity (quote_date TEXT PRIMARY KEY, cash REAL NOT NULL,"
                   "market_value REAL NOT NULL, equity REAL NOT NULL)")
        db.executemany("INSERT INTO paper_cycles VALUES (?,'baseline',0,0,'legacy-time')",
                       [("2026-09-23",), ("2026-09-24",)])
        db.execute("INSERT INTO paper_equity VALUES ('2026-09-23',1000000,0,1000000)")
    store = PaperStore(path)
    try:
        cycles = {row["cycle_date"]: row for row in store.recent_cycles()}
        assert cycles["2026-09-23"]["status"] == "completed"
        assert cycles["2026-09-24"]["status"] == "running"
        assert cycles["2026-09-24"]["completed_at"] is None
        assert store.cycle_summary()["cycles"] == 1
    finally:
        store.close()


def test_legacy_order_is_reused_and_keeps_its_identity(account):
    existing = account.create_order("000001.SZ", "BUY", 100,
                                    source="strategy:baseline", signal_date=DAY)
    account.reject_order(existing["order_id"], "original rejection")
    account.claim_cycle(DAY, strategy="baseline")
    with cycle_inputs(orders=[Order("000001.SZ", "BUY", 100)]):
        run_strategy_cycle(account, symbols=["000001.SZ"])
    assert len(account.state()["orders"]) == 1
    assert account.order_by_idempotency_key(f"cycle:{DAY}:baseline:000001.SZ:BUY")["order_id"] == existing["order_id"]


def test_maintenance_failure_is_atomic(account):
    before = account.state()
    with (cycle_inputs(), patch.object(account, "record_equity", side_effect=RuntimeError("valuation")),
          pytest.raises(RuntimeError)):
        run_strategy_cycle(account, symbols=["000001.SZ"], sealed_test={"passed": False})
    assert account.state() == before
    assert account._connection.execute("SELECT COUNT(*) FROM paper_quotes").fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["daily_bars", "validate_strategy", "get_tushare_stock_metadata"])
def test_preparation_does_not_lock_ui_or_manual_account_writes(account, stage):
    entered, release = threading.Event(), threading.Event()
    other = PaperStore(account.path)
    try:
        with cycle_inputs(), ThreadPoolExecutor(max_workers=3) as pool:
            owner = service.TushareDailyAdapter.return_value if stage == "daily_bars" else service
            original = getattr(owner, stage)

            def waiting(*args, **kwargs):
                assert not account._connection.in_transaction
                entered.set()
                assert release.wait(10), "preparation was not released"
                return original(*args, **kwargs)

            with patch.object(owner, stage, side_effect=waiting):
                running = pool.submit(run_strategy_cycle, account, symbols=["000001.SZ"])
                try:
                    assert entered.wait(5)
                    # Verify both the connection RLock and SQLite writer lock are free.
                    assert pool.submit(account.state).result(timeout=2)["cash"] == account.initial_cash
                    manual = pool.submit(other.create_order, "000002.SZ", "BUY", 100).result(timeout=2)
                    assert manual["status"] == "NEW"
                    assert not account._connection.in_transaction
                finally:
                    release.set()
                assert not running.result(timeout=10)["skipped"]
    finally:
        other.close()


@pytest.mark.parametrize("forced_kill", [False, True])
def test_service_exit_rolls_back_execution_helper(account, forced_kill):
    account.create_order("000001.SZ", "BUY", 100, signal_date=date(2026, 9, 23),
                         source="strategy:baseline")
    before = account.state()
    account.close()
    code = """import os, sys, time
from quant.execution.store import PaperStore
from quant.execution.service import run_strategy_cycle
from quant.backtest.engine import Order
from tests.test_paper_cycle_recovery import cycle_inputs
s = PaperStore(sys.argv[1])
def interrupt(*args, **kwargs):
    assert s._connection.in_transaction
    print('READY', flush=True)
    if sys.argv[2] == 'kill':
        time.sleep(30)
    else:
        os._exit(37)
s.record_cycle = interrupt
with cycle_inputs(orders=[Order('000002.SZ', 'BUY', 100)]):
    run_strategy_cycle(s, symbols=['000001.SZ', '000002.SZ'])
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(account.path), "kill" if forced_kill else "exit"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout.readline().strip() == b"READY"
        if forced_kill:
            process.kill()
        process.communicate(timeout=10)
        if not forced_kill:
            assert process.returncode == 37
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        account._connection = sqlite3.connect(account.path, check_same_thread=False)
        account._connection.row_factory = sqlite3.Row
    assert account.state() == before
    assert account.recent_cycles() == []
    assert account.last_rebalance_date("baseline", 5) is None
    for table in ("paper_fills", "paper_quotes", "paper_entitlement_days", "paper_strategy_state"):
        assert account._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize(("simulated", "rejected", "due", "pending", "outcome", "reason"), [
    ([{"status": "PARTIALLY_FILLED"}], [], False, 1, "traded", None),
    ([{"status": "REJECTED"}, {"status": "REJECTED"}], [{}, {}], True, 0, "risk_rejected", "共 2 笔"),
    ([], [], False, 2, "not_rebalance_day", "尚未到调仓日"),
    ([], [], True, 0, "no_trade", "调仓检查未生成差额委托（目标持仓不变或无有效信号）"),
    ([], [], True, 3, "orders_pending", "当前 3 笔活动策略委托"),
])
def test_result_explanations(account, simulated, rejected, due, pending, outcome, reason):
    @service._atomic_cycle
    def result_only(store, **kwargs):
        assert not store._connection.in_transaction
        return {"simulated_orders": simulated, "risk_rejections": rejected,
                "pending_orders": pending, "skipped": False,
                "rebalance_schedule": {"due": due, "interval_trading_days": 5,
                                       "trading_days_since_previous": 2}}

    result = result_only(account)
    assert result["outcome"] == outcome
    if reason is None:
        assert result["no_trade_reason"] is None
    else:
        assert reason in result["no_trade_reason"]
    if outcome == "risk_rejected":
        assert "violations" in result["no_trade_reason"]


def test_pending_count_includes_previous_new_and_partial_orders(account):
    older = account.create_order("000002.SZ", "BUY", 100, signal_date=DAY,
                                 source="strategy:baseline")
    partial = account.create_order("000003.SZ", "BUY", 100, signal_date=DAY,
                                   source="strategy:baseline")
    with account.transaction():
        account._connection.execute(
            "UPDATE paper_orders SET status='PARTIALLY_FILLED',filled_quantity=50 WHERE order_id=?",
            (partial["order_id"],),
        )
    account.set_last_rebalance_date("baseline", 5, date(2026, 9, 23))
    with cycle_inputs():
        result = run_strategy_cycle(account, symbols=["000001.SZ"])
    assert result["pending_orders"] == 2
    assert result["outcome"] == "not_rebalance_day"
    assert account.order(older["order_id"])["status"] == "NEW"
