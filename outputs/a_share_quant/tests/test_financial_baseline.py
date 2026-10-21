"""No network, production account writes, sealed evaluation or budget consumption."""

import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest

from quant.backtest.costs import CostModel
from quant.backtest.engine import PortfolioSnapshot
from quant.data.schema import Bar
from quant.data.strategy_fundamentals import observed_financial_inputs
from quant.execution.research_account import ResearchPaperAccount
from quant.strategies.baseline import CrossSectionalMomentum
from quant.strategies.cost_aware_baseline import CostAwareBaseline


def history():
    return {
        f"00000{i}.SZ": [
            Bar(
                f"00000{i}.SZ", date(2026, 9, 1) + timedelta(days=j), 10, 11, 9, 10, 1e6
            )
            for j in range(21)
        ]
        for i in range(1, 7)
    }


def portfolio(positions=None):
    return PortfolioSnapshot(date(2026, 10, 12), 1e6, positions or {}, 0, 1e6)


def provider(day, symbols):
    values = {
        symbol: {
            "roe": i / 100,
            "netprofit_yoy": i / 100,
            "tr_yoy": i / 100,
            "debt_to_assets": 1 - i / 100,
        }
        for i, symbol in enumerate(sorted(symbols), 1)
    }
    return values, {"status": "available", "covered_symbols": len(values)}


def test_financial_metrics_really_change_orders_and_are_audited():
    prices = history()
    original = CrossSectionalMomentum(rebalance_every=1)
    strategy = CostAwareBaseline(rebalance_every=1, fundamental_provider=provider)
    before = {
        order.symbol
        for order in original.orders_for_date(date(2026, 10, 12), prices, portfolio())
    }
    orders = strategy.orders_for_date(date(2026, 10, 12), prices, portfolio())
    after = {order.symbol for order in orders}
    assert "000006.SZ" not in before and "000006.SZ" in after
    assert "000001.SZ" in before and "000001.SZ" not in after
    assert all(order.quantity % 100 == 0 for order in orders)
    assert sum(order.quantity * 10 for order in orders) <= 980000
    ranked = strategy._rank_scores(prices)
    assert all(len(keys) == 6 for _, _, _, keys in ranked)
    assert strategy.factor_metadata()["fundamental_values_supplied"] is True
    assert strategy.factor_metadata()["outperformance_proven"] is False


def test_missing_financial_proof_never_falls_back_to_prices():
    strategy = CostAwareBaseline(
        rebalance_every=1,
        fundamental_provider=lambda d, s: ({}, {"status": "insufficient_coverage"}),
    )
    assert strategy.orders_for_date(date(2026, 10, 12), history(), portfolio()) == []
    assert (
        strategy.factor_metadata()["financial_input_report"]["status"]
        == "insufficient_coverage"
    )


def test_rank_buffer_deadband_and_overweight_exit():
    strategy = CostAwareBaseline()
    bar = next(iter(history().values()))[-1]
    scores = [(str(i), 10 - i, bar, ()) for i in range(1, 10)]
    selected = strategy._select(scores, portfolio({"8": 100, "9": 100}))
    assert "8" in selected and "9" not in selected and len(selected) == 5
    assert strategy._skip_small_adjustment("8", 196000, -1000, portfolio({"8": 100}))
    assert not strategy._skip_small_adjustment(
        "8", 210000, -1000, portfolio({"8": 100})
    )
    assert not strategy._skip_small_adjustment("new", 0, 1000, portfolio())


def test_real_first_seen_actual_disclosure_nulls_and_revisions(tmp_path):
    path = tmp_path / "raw.sqlite3"
    symbols = [f"00000{i}.SZ" for i in range(1, 7)]
    with sqlite3.connect(path) as db:
        for table in ("ts_disclosure_date", "ts_fina_indicator"):
            db.execute(
                f"CREATE TABLE {table}(ts_code TEXT,payload_json TEXT,source_hash TEXT,ingested_at TEXT)"
            )
        for symbol in symbols:
            disclosure = {
                "ts_code": symbol,
                "end_date": "20260630",
                "actual_date": "20260828",
            }
            row = {
                "ts_code": symbol,
                "end_date": "20260630",
                "ann_date": "20260828",
                "roe": 10,
                "netprofit_yoy": 20,
                "tr_yoy": 15,
                "debt_to_assets": 40,
            }
            db.execute(
                "INSERT INTO ts_disclosure_date VALUES(?,?,?,?)",
                (
                    symbol,
                    json.dumps(disclosure),
                    "calendar",
                    "2026-10-09T09:00:00+00:00",
                ),
            )
            db.execute(
                "INSERT INTO ts_fina_indicator VALUES(?,?,?,?)",
                (symbol, json.dumps(row), "initial", "2026-10-09T10:00:00+00:00"),
            )
            row["roe"] = 999
            db.execute(
                "INSERT INTO ts_fina_indicator VALUES(?,?,?,?)",
                (symbol, json.dumps(row), "future", "2026-10-13T01:00:00+00:00"),
            )
        db.execute(
            "UPDATE ts_fina_indicator SET payload_json=json_set(payload_json,'$.roe',NULL) WHERE ts_code=? AND source_hash='initial'",
            (symbols[-1],),
        )
    assert observed_financial_inputs(date(2026, 10, 9), symbols, path=path)[0] == {}
    values, report = observed_financial_inputs(date(2026, 10, 12), symbols, path=path)
    assert values[symbols[0]]["roe"] == 0.1
    assert symbols[-1] not in values and report["status"] == "insufficient_coverage"
    assert report["historical_backfill"] is False
    assert all(
        proof["source_hash"] == "initial" for proof in report["evidence"].values()
    )
    assert (
        observed_financial_inputs(date(2026, 10, 14), symbols, path=path)[0][
            symbols[0]
        ]["roe"]
        == 9.99
    )


@pytest.mark.parametrize(
    "day,rate",
    [
        (date(2023, 8, 27), 0.001),
        (date(2023, 8, 28), 0.0005),
        (date(2026, 10, 12), 0.0005),
    ],
)
def test_stamp_tax_uses_execution_date_and_only_sells(day, rate):
    costs = CostModel()
    assert (
        costs.calculate("SELL", 10, 1000, 1e6, trading_date=day).stamp_tax
        == 10000 * rate
    )
    assert costs.calculate("BUY", 10, 1000, 1e6, trading_date=day).stamp_tax == 0
    assert (
        CostModel(stamp_tax_rate=0)
        .calculate("SELL", 10, 1000, 1e6, trading_date=day)
        .stamp_tax
        == 0
    )


def test_unmodelled_old_tax_is_not_silently_assumed():
    with pytest.raises(ValueError, match="historical schedule"):
        CostModel().calculate("SELL", 10, 1000, 1e6, trading_date=date(2007, 1, 1))


def test_paper_sell_uses_fill_date_tax_not_signal_date(tmp_path):
    from quant.execution.store import PaperStore

    store = PaperStore(tmp_path / "paper.sqlite3")
    try:
        buy = store.create_order("000001.SZ", "BUY", 1000)
        earlier = Bar("000001.SZ", date(2023, 8, 25), 10, 11, 9, 10, 1e6)
        assert (
            store.execute_order(buy["order_id"], earlier, CostModel())["status"]
            == "FILLED"
        )
        sell = store.create_order(
            "000001.SZ", "SELL", 1000, signal_date=date(2023, 8, 25)
        )
        later = Bar("000001.SZ", date(2023, 8, 28), 10, 11, 9, 10, 1e6)
        with patch.object(
            CostModel, "calculate", autospec=True, side_effect=CostModel.calculate
        ) as calculate:
            assert (
                store.execute_order(sell["order_id"], later, CostModel())["status"]
                == "FILLED"
            )
        assert calculate.call_args.kwargs["trading_date"] == later.date
    finally:
        store.close()


def test_financial_gate_blocks_pending_orders_and_does_not_claim_cycle():
    from quant.execution.service import _execute_prepared_cycle

    store = Mock()
    store.state.return_value = {"cash": 1e6}
    strategy = CostAwareBaseline(
        fundamental_provider=lambda d, s: ({}, {"status": "insufficient_coverage"})
    )
    result = _execute_prepared_cycle(
        store,
        bars=history()["000001.SZ"],
        trading_dates=[],
        cycle_date=date(2026, 10, 12),
        preset=Mock(key="baseline_cost_aware"),
        selected_strategy=strategy,
        rebalance_every=5,
        candidate_codes={"000001"},
        factor_snapshot={},
        factor_keys=[],
        factor_snapshot_definitions=[],
        dataset_version="test",
        dataset_metadata={},
        data_quality={},
        data_freshness={},
        validation_payload={},
        metadata={},
        metadata_stale=False,
        distribution_events=[],
        universe_snapshot={},
        symbols=["000001.SZ"],
    )
    assert result["outcome"] == "blocked" and not result["paper_trading_allowed"]
    store.execute_order.assert_not_called()
    store.claim_cycle.assert_not_called()
    store.set_last_rebalance_date.assert_not_called()


def test_explicit_unused_account_upgrade_preserves_revision_and_used_account_refuses(
    tmp_path,
):
    from quant.factors.composite import FactorComposite

    account = ResearchPaperAccount(tmp_path / "paper.sqlite3")
    try:
        original = account.enable(
            strategy="baseline",
            rebalance="weekly",
            index="000300.SH",
            factors=list(FactorComposite().definitions),
            code_hash="old",
        )
        updated = account.upgrade_financial(code_hash="new")
        assert updated["strategy"] == "baseline_cost_aware"
        assert updated["previous_config_hash"] == original["config_hash"]
        assert updated["initial_cash"] == original["initial_cash"]
        with sqlite3.connect(account.config_path) as db:
            assert (
                json.loads(
                    db.execute(
                        "SELECT old_payload FROM configuration_revisions"
                    ).fetchone()[0]
                )
                == original
            )
        assert account.upgrade_financial(code_hash="new") == updated
        account.store.create_order("000001.SZ", "BUY", 100)
        with pytest.raises(ValueError, match="已有活动"):
            account.upgrade_financial(code_hash="later")
    finally:
        account.store.close()


def test_frozen_financial_resume_preserves_parameters_and_requires_review(tmp_path):
    from quant.factors.composite import FactorComposite

    account = ResearchPaperAccount(tmp_path / "paper.sqlite3")
    try:
        account.enable(
            strategy="baseline",
            rebalance="weekly",
            index="000300.SH",
            factors=list(FactorComposite().definitions),
            code_hash="old",
        )
        original = account.upgrade_financial(code_hash="reviewed")
        account.pause()
        with pytest.raises(ValueError, match="显式复核"):
            account.resume(code_hash="changed")
        assert account.configuration()["enabled"] is False
        resumed = account.resume(code_hash="reviewed")
        assert resumed == {**original, "enabled": True}
        assert account.store.has_experiment_activity() is False
        assert account.resume(code_hash="reviewed") == resumed
    finally:
        account.store.close()


def test_api_resume_never_reads_formal_strategy_or_live_factors(tmp_path):
    import threading
    from types import SimpleNamespace

    from quant.api.research_paper import research_paper_request
    from quant.api.user_scope import UserScopedStores, user_context
    from quant.factors.composite import FactorComposite

    stores = UserScopedStores(tmp_path)
    try:
        with user_context("alice"):
            account = stores.research_account("alice")
            account.enable(
                strategy="baseline",
                rebalance="weekly",
                index="000300.SH",
                factors=list(FactorComposite().definitions),
                code_hash="old",
            )
            frozen = account.upgrade_financial(code_hash="reviewed")
            account.pause()
            formal = Mock(
                side_effect=AssertionError("Formal settings must not be read")
            )
            coordinator = SimpleNamespace(
                lock=threading.RLock(),
                for_user=formal,
                for_research_user=Mock(return_value=None),
            )
            with patch(
                "quant.api.research_paper.execution_code_hash", return_value="reviewed"
            ):
                research_paper_request("enable", stores, coordinator)
            assert account.configuration() == {**frozen, "enabled": True}
            formal.assert_not_called()
    finally:
        stores.close()
