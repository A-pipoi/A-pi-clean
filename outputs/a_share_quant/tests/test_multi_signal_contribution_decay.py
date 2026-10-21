import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from quant.backtest.engine import EventDrivenBacktester, Order, PortfolioSnapshot
from quant.data.schema import Bar
from quant.factors.decay import factor_decay_report
from quant.reports.contribution import build_security_contribution
from quant.strategies.multi_signal import MultiSignalStrategy, OverlayRules
from quant.strategies.registry import build_strategy


def bars(closes, symbol='000001.SZ'):
    return [Bar(symbol, date(2024, 1, 1)+timedelta(days=i), c, c, c, c, 1_000_000)
        for i, c in enumerate(closes)]

def test_research_recipe_registered_and_future_bars_rejected():
    strategy = build_strategy('multi_signal', rebalance_every=5)
    assert isinstance(strategy, MultiSignalStrategy)
    with pytest.raises(ValueError, match='future'):
        strategy.orders_for_date(date(2024,1,1), {'000001.SZ':bars([10])},
            PortfolioSnapshot(date(2024,1,1), 10000, {}, 0, 10000))
    with pytest.raises(ValueError):
        OverlayRules(stop_loss=math.nan)

def test_stop_is_next_open_order_not_stop_price_and_timing_exit():
    day = date(2024,2,1)
    strategy = MultiSignalStrategy(rebalance_every=5)
    history = {'000001.SZ':bars([8.0+i*.05 for i in range(20)])}
    snapshot = PortfolioSnapshot(day, 10000, {'000001.SZ':100}, 895, 10895,
        {'000001.SZ':10.0})
    orders = strategy.orders_for_date(day, history, snapshot)
    assert orders == [Order('000001.SZ','SELL',100,tag='close_stop_loss')]
    declining = {'000001.SZ':bars([10-i*.05 for i in range(20)])}
    assert strategy.orders_for_date(day, declining, snapshot)[0].tag == 'breadth_risk_off'
    assert strategy.orders_for_date(day, {}, snapshot) == []

def test_security_contribution_reconciles_cash_fills_and_costs():
    class Trades:
        def orders_for_date(self, day, history, portfolio):
            if day == date(2024,1,1): return [Order('000001.SZ','BUY',100)]
            if day == date(2024,1,3): return [Order('000001.SZ','SELL',100)]
            return []
    data = bars([10,11,12,12])
    result = EventDrivenBacktester(initial_cash=10000).run(data, Trades())
    report = build_security_contribution(result, data)
    assert report['status'] == 'available'
    assert abs(report['reconciliation_error']) < 1e-8
    assert report['net_pnl'] == pytest.approx(result.final_equity-10000)
    assert all(abs(row['reconciliation_error']) < 1e-8 for row in report['daily'])

def test_decay_uses_nonoverlapping_reference_and_requires_sustained_confirmation():
    def rows(values):
        return [{'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),'value':v}
            for i,v in enumerate(values)]
    assert factor_decay_report(rows([.1]*79))['status'] == 'insufficient_data'
    stable = factor_decay_report(rows([.1]*100))
    assert stable['status'] == 'normal' and not stable['alerts']
    assert factor_decay_report(rows([-.1]*100))['status'] == 'unproven_baseline'
    decayed = factor_decay_report(rows([.1]*60+[-.1]*22))
    assert decayed['status'] == 'alert'
    assert decayed['series'][-1]['reference_ic'] > 0
    assert decayed['alerts'] == factor_decay_report(rows([.1]*60+[-.1]*22))['alerts']
    with pytest.raises(ValueError,match='duplicate'):
        factor_decay_report(rows([.1])+rows([.2]))


def test_final_close_is_not_fabricated_as_delisting_cash():
    class Buy:
        def orders_for_date(self, day, history, portfolio):
            return [Order('000001.SZ','BUY',100)] if day == date(2024,1,1) else []
    data = bars([10,11,12,12])
    data[2] = replace(data[2], is_delisted=True)
    with pytest.raises(ValueError, match='settlement evidence required'):
        EventDrivenBacktester(initial_cash=10000).run(data, Buy())
