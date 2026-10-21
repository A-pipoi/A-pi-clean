from __future__ import annotations

from datetime import date

import pytest

from quant.backtest import CostModel, EventDrivenBacktester, Order, PortfolioSnapshot
from tests.helpers import bar


class Strategy:
    def __init__(self) -> None:
        self.sent = False

    def orders_for_date(self, trading_date, history, portfolio: PortfolioSnapshot):
        if self.sent:
            return []
        self.sent = True
        return [Order("A.SH", "BUY", 100)]


def event() -> dict[str, object]:
    return {
        "ts_code": "A.SH",
        "div_proc": "实施",
        "ann_date": "20240102",
        "imp_ann_date": "20240102",
        "record_date": "20240103",
        "ex_date": "20240104",
        "pay_date": "20240106",
        "div_listdate": "20240105",
        "end_date": "20231231",
        "cash_div": 0.5,
        "stk_div": 0.2,
    }


def test_entitlements_are_separate_until_payment_and_listing_dates() -> None:
    bars = [
        bar("A.SH", 0, close=10, open_=10),
        bar("A.SH", 1, close=10, open_=10),
        bar("A.SH", 2, close=7.9166666667, open_=7.9166666667),
        bar("A.SH", 3, close=7.9166666667, open_=7.9166666667),
        bar("A.SH", 4, close=7.9166666667, open_=7.9166666667),
    ]
    source_event = event()
    provider = lambda day: [source_event] if day >= date(2024, 1, 4) else []
    engine = EventDrivenBacktester(
        initial_cash=1000,
        cost_model=CostModel(
            commission_rate=0, min_commission=0, stamp_tax_rate=0,
            transfer_fee_rate=0, fixed_slippage_bps=0, impact_bps=0,
        ),
    )

    result = engine.run(bars, Strategy(), corporate_actions=True, action_provider=provider)

    assert result.equity_curve[2].positions["A.SH"] == 100
    assert result.equity_curve[2].receivables == pytest.approx(208.33333333)
    assert result.equity_curve[3].positions["A.SH"] == 120
    assert result.equity_curve[3].receivables == pytest.approx(50)
    assert result.equity_curve[4].cash == pytest.approx(50)
    assert result.equity_curve[4].receivables == 0
    assert result.corporate_action_report["price_basis"].startswith("raw quotes")


def test_default_path_does_not_call_action_provider() -> None:
    bars = [bar("A.SH", 0)]
    result = EventDrivenBacktester().run(
        bars, Strategy(), action_provider=lambda _day: (_ for _ in ()).throw(AssertionError())
    )
    assert result.corporate_action_report == {}


def test_fractional_share_entitlement_fails_closed() -> None:
    bars = [bar("A.SH", 0), bar("A.SH", 1), bar("A.SH", 2)]
    action = event() | {"stk_div": 0.003}
    with pytest.raises(ValueError, match="fractional_share"):
        EventDrivenBacktester().run(
            bars, Strategy(), corporate_actions=True,
            action_provider=lambda day: [action] if day >= date(2024, 1, 4) else [],
        )
