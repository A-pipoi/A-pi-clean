"""Allowlisted, daily-only shared data endpoints; not a permission assertion."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    name: str
    phase: int
    keys: tuple[str, ...]
    financial: bool = False
    page_size: int = 1000
    historical_availability: bool = False


def _endpoint(name: str, phase: int, keys: str, financial: bool = False) -> Endpoint:
    return Endpoint(name, phase, tuple(keys.split(',')), financial, 100 if name == 'fina_indicator' else 1000)


ENDPOINTS = {item.name: item for item in (
    _endpoint('trade_cal', 1, 'exchange,cal_date'),
    _endpoint('daily', 1, 'ts_code,trade_date'),
    _endpoint('daily_basic', 1, 'ts_code,trade_date'),
    _endpoint('adj_factor', 1, 'ts_code,trade_date'),
    _endpoint('stk_limit', 1, 'ts_code,trade_date'),
    _endpoint('suspend_d', 1, 'ts_code,trade_date,suspend_type,suspend_timing'),
    _endpoint('stock_basic', 1, 'ts_code'),
    _endpoint('bse_mapping', 1, 'o_code,n_code'),
    _endpoint('namechange', 1, 'ts_code,start_date,name'),
    _endpoint('new_share', 1, 'ts_code,ipo_date'),
    _endpoint('disclosure_date', 1, 'ts_code,end_date,ann_date', True),
    *(_endpoint(name, 2, 'ts_code,end_date,ann_date', True) for name in
      ('income', 'balancesheet', 'cashflow', 'fina_indicator', 'fina_audit', 'forecast', 'express', 'dividend', 'repurchase')),
    *(_endpoint(name, 2, 'ts_code,trade_date') for name in
      ('moneyflow', 'margin_detail', 'hk_hold', 'top_list', 'top_inst', 'stk_factor')),
    _endpoint('block_trade', 2, 'ts_code,trade_date,buyer,seller,price,vol,amount'),
    _endpoint('margin', 2, 'exchange_id,trade_date'),
    _endpoint('stk_holdernumber', 2, 'ts_code,end_date,ann_date', True),
    _endpoint('stk_holdertrade', 2, 'ts_code,ann_date,holder_name', True),
    _endpoint('share_float', 2, 'ts_code,ann_date,float_date', True),
    _endpoint('pledge_stat', 2, 'ts_code,end_date'),
    _endpoint('pledge_detail', 2, 'ts_code,ann_date,holder_name', True),
    _endpoint('fund_portfolio', 3, 'ts_code,end_date,ann_date,symbol', True),
    *(_endpoint(name, 3, 'ts_code,trade_date') for name in ('fund_daily', 'fund_adj', 'opt_daily', 'index_daily', 'index_dailybasic')),
    _endpoint('fund_nav', 3, 'ts_code,nav_date,ann_date', True),
    _endpoint('opt_basic', 3, 'ts_code'),
    _endpoint('index_classify', 3, 'index_code'),
    _endpoint('index_member_all', 3, 'ts_code,l3_code,in_date'),
    _endpoint('index_weight', 3, 'index_code,con_code,trade_date'),
    _endpoint('shibor', 3, 'date'),
    _endpoint('shibor_lpr', 3, 'date'),
    *(_endpoint(name, 3, 'month') for name in ('cn_cpi', 'cn_ppi', 'cn_pmi', 'cn_m', 'sf_month')),
    _endpoint('cn_gdp', 3, 'quarter'),
)}

# Raw snapshots are not PIT proof. In particular industry effective dates,
# macro period labels and static adjusted indicators never imply availability.
SCHEDULE = {
    '08:10': ('hk_hold',), '09:10': ('margin', 'margin_detail', 'stk_limit', 'suspend_d'),
    '15:40': ('daily', 'daily_basic', 'index_daily', 'index_dailybasic', 'adj_factor', 'suspend_d'),
    '17:20': ('opt_daily', 'fund_daily', 'fund_adj', 'fund_nav'),
    '19:10': ('moneyflow', 'stk_holdertrade', 'new_share'),
    '20:10': ('top_list', 'top_inst'),
    '21:10': ('block_trade', 'pledge_stat', 'pledge_detail'),
}
