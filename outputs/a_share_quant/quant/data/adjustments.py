from __future__ import annotations

from .schema import CorporateAction


def total_return(prev_close: float, current_close: float, action: CorporateAction | None = None) -> float:
    """Return including a cash dividend and share/rights issue on the ex-date.

    This is the local accounting primitive used by the backtest. A production
    adapter should also persist the vendor's adjustment factors and raw actions.
    """
    if prev_close <= 0:
        raise ValueError("prev_close must be positive")
    if action is None:
        return current_close / prev_close - 1.0
    shares = 1.0 + action.stock_dividend_ratio + action.rights_issue_ratio
    cash = action.cash_dividend
    rights_cash = action.rights_issue_ratio * (action.rights_issue_price or 0.0)
    adjusted_previous = prev_close - cash - rights_cash
    if adjusted_previous <= 0:
        raise ValueError("corporate action makes adjusted previous price non-positive")
    return current_close * shares / adjusted_previous - 1.0
