"""Real one-price-board execution probe, not a strategy search or sealed test."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from quant.backtest.engine import EventDrivenBacktester, Order, OrderRejectReason

from .extension_analytics import latest_rows
from .extension_store import ExtensionStore, default_store_path
from .schema import Bar


class SingleOrderProbe:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def orders_for_date(self, *args: object) -> list[Order]:
        return [Order(self.symbol, 'BUY', 100, tag='execution_acceptance_not_strategy_signal')]


def audit(store: ExtensionStore) -> dict[str, object]:
    limits = {(r.get('ts_code'),r.get('trade_date')):r for r in latest_rows(store,'stk_limit')}
    candidates = []
    for row in latest_rows(store,'daily'):
        limit = limits.get((row.get('ts_code'),row.get('trade_date')), {})
        up = limit.get('up_limit')
        prices = [row.get(k) for k in ('open','high','low','close')]
        if up is not None and all(isinstance(p,(int,float)) and abs(p-float(up))<0.00001 for p in prices):
            candidates.append((str(row['trade_date']), str(row['ts_code']), row, limit))
    if not candidates:
        return {'status':'not_run','reason':'no_observed_one_price_limit_up_board', 'strategy_comparison_completed':False}
    day, symbol, row, limit = min(candidates, key=lambda r:(r[0],r[1]))
    trading_day = date(int(day[:4]),int(day[4:6]),int(day[6:8]))
    bar = Bar(symbol,trading_day,open=float(row['open']),high=float(row['high']),low=float(row['low']),close=float(row['close']),
              volume=float(row['vol'])*100,limit_up=float(limit['up_limit']),limit_down=float(limit['down_limit']),
              amount=float(row['amount'])*1000)
    enforced = EventDrivenBacktester().run([bar],SingleOrderProbe(symbol))
    # Diagnostics only: explicitly relaxed prices/turnover, never production settings.
    relaxed = EventDrivenBacktester().run([replace(bar,limit_up=None,limit_down=None,amount=None)],SingleOrderProbe(symbol))
    reasons = [r.reason.value for r in enforced.rejected]
    return {'status':'passed' if OrderRejectReason.LIMIT_UP_BUY.value in reasons and len(relaxed.fills)==1 else 'not_passed',
            'symbol':symbol,'date':day,'quantity':100,'price':bar.open,
            'constraints_on':{'fills':len(enforced.fills),'reject_reasons':reasons,'final_equity':enforced.final_equity},
            'price_amount_constraints_off':{'fills':len(relaxed.fills),'final_equity':relaxed.final_equity},
            'scope':'execution validation with identical forced order, not a factor strategy performance experiment',
            'strategy_comparison_completed':False,'production_settings_changed':False,
            'research_budget_used':False,'sealed_samples_modified':False}


def main() -> None:
    store = ExtensionStore(default_store_path())
    result = audit(store)
    digest = store.archive_quality(1, {'kind':'one_price_board_execution_probe', **result})
    path = Path(__file__).resolve().parents[2]/'data_cache/data_extension/execution_probe_20261008.json'
    path.write_text(json.dumps({**result,'artifact_digest':digest},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
