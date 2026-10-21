import unittest
from datetime import date

from quant.backtest import (
    CostModel,
    EventDrivenBacktester,
    Order,
    OrderRejectReason,
    PortfolioSnapshot,
)
from tests.helpers import bar


class OneShotStrategy:
    def __init__(self, orders_by_day: dict[date, list[Order]]) -> None:
        self.orders_by_day = orders_by_day

    def orders_for_date(self, trading_date, history, portfolio: PortfolioSnapshot):
        return list(self.orders_by_day.get(trading_date, []))


def no_costs() -> CostModel:
    return CostModel(commission_rate=0, min_commission=0, stamp_tax_rate=0, transfer_fee_rate=0, fixed_slippage_bps=0, impact_bps=0)


class BacktestRuleTests(unittest.TestCase):
    def test_limit_up_buy_is_rejected(self) -> None:
        bars = [bar("A", 0, open_=10, limit_up=10)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 10)]})
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(result.rejected[0].reason, OrderRejectReason.LIMIT_UP_BUY)

    def test_paused_trading_is_rejected(self) -> None:
        bars = [bar("A", 0, paused=True)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 10)]})
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(result.rejected[0].reason, OrderRejectReason.PAUSED)

    def test_t_plus_one_sell_is_rejected(self) -> None:
        bars = [bar("A", 0), bar("A", 1)]
        strategy = OneShotStrategy(
            {
                bars[0].date: [Order("A", "BUY", 10)],
                bars[1].date: [Order("A", "SELL", 10)],
            }
        )
        # bar 1 is the next calendar date, so the lot from day 0 is sellable.
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(len(result.fills), 2)

    def test_same_day_buy_then_sell_cannot_be_t0(self) -> None:
        bars = [bar("A", 0)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 10), Order("A", "SELL", 10)]})
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(result.rejected[0].reason, OrderRejectReason.NO_POSITION)
        self.assertEqual(len(result.fills), 1)

    def test_volume_limit_is_rejected(self) -> None:
        bars = [bar("A", 0, volume=10)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 2)]})
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(result.rejected[0].reason, OrderRejectReason.VOLUME_LIMIT)

    def test_limit_down_sell_is_rejected(self) -> None:
        bars = [bar("A", 0), bar("A", 1, open_=9, close=9, limit_down=9)]
        strategy = OneShotStrategy({bars[0].date: [Order("A", "BUY", 10)], bars[1].date: [Order("A", "SELL", 10)]})
        result = EventDrivenBacktester(100_000, no_costs()).run(bars, strategy)
        self.assertEqual(result.rejected[-1].reason, OrderRejectReason.LIMIT_DOWN_SELL)
