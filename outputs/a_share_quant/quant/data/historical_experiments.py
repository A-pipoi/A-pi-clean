"""Retrospective supplier diagnostics, never historical first-seen proof."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from contextlib import closing
from typing import Any

from .extension_analytics import latest_rows
from .extension_store import ExtensionStore


def rank_ic(values: list[float], returns: list[float]) -> float | None:
    if len(values) < 20 or len(values) != len(returns) or not all(math.isfinite(v) for v in (*values,*returns)):
        return None
    def ranks(items: list[float]) -> list[float]:
        out = [0.] * len(items)
        order = sorted(range(len(items)), key=lambda i: items[i])
        i = 0
        while i < len(order):
            j = i + 1
            while j < len(order) and items[order[j]] == items[order[i]]:
                j += 1
            for k in order[i:j]:
                out[k] = (i + j - 1) / 2
            i = j
        return out
    x, y = ranks(values), ranks(returns)
    mx, my = sum(x)/len(x), sum(y)/len(y)
    numerator = sum((a-mx)*(b-my) for a,b in zip(x,y))
    denominator = math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
    return numerator / denominator if denominator else None


def adjusted_prices(store: ExtensionStore, day: str) -> dict[str, float]:
    adjustments = {r['ts_code']: r.get('adj_factor') for r in latest_rows(store,'adj_factor',period=day)}
    out = {}
    for row in latest_rows(store,'daily',period=day):
        close, adjustment = row.get('close'), adjustments.get(row.get('ts_code'))
        if isinstance(close,(int,float)) and isinstance(adjustment,(int,float)) and close>0 and adjustment>0 and math.isfinite(close*adjustment):
            out[str(row['ts_code'])] = close*adjustment
    return out


def etf_allocation_diagnostic(store: ExtensionStore, symbols: tuple[str, ...],
                              sessions: list[str], *, cost_bps: float = 5.) -> dict[str, Any]:
    """Monthly equal-weight adjusted-close research diagnostic, not executable PnL."""
    if not symbols or len(symbols) != len(set(symbols)) or not sessions or sessions != sorted(set(sessions)):
        raise ValueError('fixed, unique ETF symbols and sorted sessions required')
    if not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError('invalid modeled cost')
    session_set = set(sessions)
    quotes: dict[str, dict[str, float]] = defaultdict(dict)
    factors: dict[str, dict[str, float]] = defaultdict(dict)
    with closing(store.connect()) as db:
        for endpoint, field, destination in (('fund_daily','close',quotes),('fund_adj','adj_factor',factors)):
            for symbol in symbols:
                rows = db.execute(f'SELECT payload_json FROM ts_{endpoint} a WHERE a.ts_code=? '
                    'AND a.period_date BETWEEN ? AND ? AND NOT EXISTS ('
                    f'SELECT 1 FROM ts_{endpoint} b WHERE b.business_key=a.business_key '
                    'AND (b.ingested_at>a.ingested_at OR (b.ingested_at=a.ingested_at AND b.rowid>a.rowid)))',
                    (symbol,sessions[0],sessions[-1]))
                for (payload,) in rows:
                    row=json.loads(payload)
                    day,value=row.get('trade_date'),row.get(field)
                    if day in session_set and isinstance(value,(int,float)) and math.isfinite(value) and value>0:
                        destination[str(day)][symbol]=float(value)
    missing = [{'date': day, 'symbol': symbol, 'missing':
                'fund_daily' if symbol not in quotes[day] else 'fund_adj'}
               for day in sessions for symbol in symbols
               if symbol not in quotes[day] or symbol not in factors[day]]
    base: dict[str, Any] = {'kind':'etf_allocation_historical_diagnostic', 'symbols':list(symbols),
        'start':sessions[0], 'end':sessions[-1], 'expected_sessions':len(sessions),
        'missing_observations':missing, 'complete_price_coverage':not missing,
        'method':'monthly equal weight; daily adjusted-close returns; rebalance at month first observed close',
        'modeled_turnover_cost_bps':cost_bps, 'execution_price_verified':False,
        'tradability_verified':False, 'retrospective_only':True,
        'historical_first_seen_created':False, 'sealed_approval_usable':False}
    if missing or len(sessions) < 2:
        return {**base, 'status':'insufficient_real_history', 'curve':[], 'rebalance_dates':[]}
    prices = {day:{s:quotes[day][s]*factors[day][s] for s in symbols} for day in sessions}
    weight = {s:1/len(symbols) for s in symbols}
    equity, curve, rebalances = 1., [], []
    for i, day in enumerate(sessions):
        if i:
            previous = sessions[i-1]
            gross = sum(weight[s]*prices[day][s]/prices[previous][s] for s in symbols)
            equity *= gross
            weight = {s:weight[s]*prices[day][s]/prices[previous][s]/gross for s in symbols}
            if day[:6] != previous[:6]:
                target = 1/len(symbols)
                turnover = sum(abs(weight[s]-target) for s in symbols)
                equity *= 1-turnover*cost_bps/10000
                weight = {s:target for s in symbols}
                rebalances.append({'date':day,'one_way_weight_turnover':turnover/2})
        curve.append({'date':day,'modeled_index':equity})
    return {**base,'status':'computed_research_only','curve':curve,'rebalance_dates':rebalances,
            'modeled_total_return':equity-1,'not_executable_reason':
            'close-based rebalancing uses same-day close; no order-book, open, halt or dividend cash settlement proof'}


def disclosure_ic(store: ExtensionStore, pairs: list[tuple[str,str]]) -> dict[str, Any]:
    rows = latest_rows(store,'fina_indicator')
    calendar = {(r.get('ts_code'),r.get('end_date')):r.get('actual_date') for r in latest_rows(store,'disclosure_date')}
    series = []
    for day, next_day in pairs:
        current, following = adjusted_prices(store,day), adjusted_prices(store,next_day)
        aligned: dict[str,dict[str,Any]] = {'ann_date':{},'end_date':{}}
        for row in rows:
            value = row.get('netprofit_yoy')
            if not isinstance(value,(int,float)) or not math.isfinite(value):
                continue
            symbol, period = str(row.get('ts_code')), str(row.get('end_date') or '')
            announcement = str(row.get('f_ann_date') or row.get('ann_date') or '')
            actual = str(calendar.get((symbol,period)) or '')
            visible = max(announcement,actual)
            for basis, boundary in [('ann_date',visible),('end_date',period)]:
                if len(boundary)!=8 or boundary>day:
                    continue
                old = aligned[basis].get(symbol)
                if old is None or (period,announcement)>(str(old.get('end_date')),str(old.get('ann_date'))):
                    aligned[basis][symbol] = row
        eligible = set(current)&set(following)
        common = sorted(eligible & set(aligned['ann_date']) & set(aligned['end_date']))
        outcome = [following[s]/current[s]-1 for s in common]
        record = {'date':day,'label_date':next_day,'common_symbols':len(common),
                  'actual_disclosure_verified_symbols':sum(bool(calendar.get((s,aligned['ann_date'][s].get('end_date')))) for s in common)}
        for basis, aligned_values in aligned.items():
            record[basis+'_ic'] = rank_ic([float(aligned_values[s]['netprofit_yoy']) for s in common],outcome)
            record[basis+'_coverage'] = len(eligible & set(aligned_values))
        record['end_date_only_unannounced_symbols'] = len((eligible&set(aligned['end_date']))-set(aligned['ann_date']))
        series.append(record)
    return {'kind':'disclosure_alignment_ic_diagnostic','factor':'netprofit_yoy','series':series,
            'cohort':'same symbols for both IC curves; unannounced coverage separately disclosed',
            'retrospective_only':True,'historical_first_seen_created':False,
            'disclosure_rule':'max(financial announcement, actual disclosure when independently supplied); calendar coverage disclosed per point',
            'production_feature_registered':False,'sealed_approval_usable':False}


def fund_layers(store: ExtensionStore, windows: list[tuple[str,str,str]]) -> dict[str, Any]:
    periods = []
    for period, signal, label in windows:
        # Deduplicate fund/share-class holdings using the source's fund identity.
        holdings: dict[str,set[str]] = defaultdict(set)
        rows = latest_rows(store,'fund_portfolio',period=period)
        excluded_non_a_share_rows = 0
        for row in rows:
            if str(row.get('ann_date') or '99999999') > signal or not row.get('symbol') or not row.get('ts_code'):
                continue
            if not isinstance(row.get('amount'),(int,float)) or row['amount']<=0:
                continue
            if not str(row['symbol']).endswith(('.SH','.SZ','.BJ')):
                excluded_non_a_share_rows += 1
                continue
            holdings[str(row['symbol'])].add(str(row['ts_code']))
        before, after = adjusted_prices(store,signal), adjusted_prices(store,label)
        eligible = sorted(set(holdings)&set(before)&set(after),key=lambda s:(len(holdings[s]),s))
        missing = sorted(set(holdings)-set(eligible))
        groups = []
        # Whole tied consensus counts share a layer; never manufacture a spread with arbitrary tie splitting.
        unique = sorted({len(holdings[s]) for s in eligible})
        for i in range(5):
            counts = set(unique[len(unique)*i//5:len(unique)*(i+1)//5])
            symbols = [s for s in eligible if len(holdings[s]) in counts]
            returns = [after[s]/before[s]-1 for s in symbols]
            groups.append({'layer':i+1,'symbols':len(symbols),'gross_buy_hold_return':sum(returns)/len(returns) if returns else None})
        periods.append({'period':period,'signal_date':signal,'label_date':label,'source_rows':len(rows),
                        'observed_funds':len(set().union(*holdings.values())) if holdings else 0,
                        'excluded_non_a_share_rows':excluded_non_a_share_rows,
                        'priced_symbols':len(eligible),'missing_price_symbols':missing,'layers':groups,
                        'complete_priced_population':bool(holdings) and not missing})
    return {'kind':'fund_consensus_historical_layers','periods':periods,'retrospective_only':True,
            'layer_rule':'quintiles of distinct consensus counts; whole ties retained in one layer',
            'share_class_deduplication':'provider fund code; economic share-class equivalence unavailable',
            'costs_included':False,'tradability_verified':False,'sealed_approval_usable':False,
            'historical_first_seen_created':False,'all_periods_priced':bool(periods) and all(p['complete_priced_population'] for p in periods)}
