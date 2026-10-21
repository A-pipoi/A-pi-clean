from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant.backtest.engine import PortfolioSnapshot
from quant.data.schema import Bar
from quant.strategies.rulebook import (
    DEFAULT_RECIPE,
    RecipeStore,
    RulebookStrategy,
    validate_recipe,
)


def recipe(**changes):
    result = deepcopy(DEFAULT_RECIPE)
    result.update(signals=[{'expression': 'cs_rank(close)', 'weight': 1}], eligibility='close > 1')
    result.update(changes)
    return result


def history(prices=(10, 10), symbols=('000001.SZ', '600036.SH')):
    return {symbol: [Bar(symbol, date(2026, 1, 2) + timedelta(days=i), price, price, price, price, 10000)
                     for i, price in enumerate(prices)] for symbol in symbols}


def portfolio(positions=None, entries=None):
    return PortfolioSnapshot(date(2026, 1, 4), 100000, positions or {}, 0, 100000, entries or {})


@pytest.mark.parametrize('expression', ["__import__('os')", 'close.__class__', 'market_cap', 'cs_industry_neutralize(close)', 'shift(close, 252)'])
def test_unsafe_or_unproven_expression_rejected(expression):
    with pytest.raises(ValueError):
        validate_recipe(recipe(signals=[{'expression': expression, 'weight': 1}]))


@pytest.mark.parametrize('changes', [{'top_n': True}, {'top_n': 31}, {'stop_loss': float('nan')}, {'risk_off_breadth': .8}, {'extra': 1}])
def test_invalid_controls_rejected(changes):
    with pytest.raises(ValueError):
        validate_recipe(recipe(**changes))


def test_saved_versions_immutable_and_account_scoped(tmp_path):
    first = RecipeStore(tmp_path / 'alice' / 'rules.sqlite3')
    second = RecipeStore(tmp_path / 'bob' / 'rules.sqlite3')
    source = recipe()
    saved = first.save(source)
    source['name'] = 'edited'
    assert first.get(saved['version'])['name'] != 'edited'
    assert first.save(recipe())['version'] == saved['version']
    assert len(first.list()) == 1
    with pytest.raises(ValueError):
        second.get(saved['version'])


def test_buy_lots_and_deterministic_ties():
    strategy = RulebookStrategy(recipe(top_n=1), rebalance_every=1)
    orders = strategy.orders_for_date(date(2026, 1, 4), history(), portfolio())
    assert len(orders) == 1
    assert orders[0].symbol == '000001.SZ'
    assert orders[0].side == 'BUY'
    assert orders[0].quantity % 100 == 0


def test_future_bars_rejected():
    with pytest.raises(ValueError, match='future'):
        RulebookStrategy(recipe()).orders_for_date(date(2026, 1, 3), history(), portfolio())


def test_daily_close_stop_before_rebalance():
    orders = RulebookStrategy(recipe(), rebalance_every=5).orders_for_date(
        date(2026, 1, 4), history((10, 8)), portfolio({'000001.SZ': 100}, {'000001.SZ': 10}))
    assert [(order.symbol, order.side, order.tag) for order in orders] == [('000001.SZ', 'SELL', 'rule_close_stop')]


def test_risk_off_exits_without_rebalance():
    strategy = RulebookStrategy(recipe(eligibility='close > 20'), rebalance_every=5)
    orders = strategy.orders_for_date(date(2026, 1, 4), history(), portfolio({'000001.SZ': 100}, {'000001.SZ': 10}))
    assert len(orders) == 1 and orders[0].tag == 'rule_breadth_risk_off'


def test_trailing_stop_uses_observed_peak_only():
    strategy = RulebookStrategy(recipe(take_profit=.8), rebalance_every=5)
    held = portfolio({'000001.SZ': 100}, {'000001.SZ': 10})
    assert not strategy.orders_for_date(date(2026, 1, 4), history((10, 12)), held)
    rows = history((10, 12, 10.7))
    orders = strategy.orders_for_date(date(2026, 1, 5), rows, held)
    assert len(orders) == 1 and orders[0].tag == 'rule_close_trailing_stop'


def test_missing_warmup_does_not_trade():
    assert not RulebookStrategy(DEFAULT_RECIPE, rebalance_every=1).orders_for_date(date(2026, 1, 4), history(), portfolio())
