"""Stdlib MACD/RSI comparison on the provider's static qfq snapshot prices."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .extension_analytics import latest_rows
from .extension_store import ExtensionStore, default_store_path
from .extension_sync import load_token, sync


def indicator_series(prices: list[float]) -> list[dict[str, float]]:
    if not prices or any(not math.isfinite(p) or p <= 0 for p in prices):
        raise ValueError('finite positive prices required')
    fast = slow = previous = prices[0]
    signal = 0.0
    gain = {n:0.0 for n in (6,12,24)}
    movement = {n:0.0 for n in (6,12,24)}
    output = []
    for price in prices:
        fast += 2/13*(price-fast)
        slow += 2/27*(price-slow)
        dif = fast-slow
        signal += 2/10*(dif-signal)
        row = {'macd_dif':dif, 'macd_dea':signal, 'macd':2*(dif-signal)}
        change = price-previous
        for n, previous_gain in gain.items():
            gain[n] = previous_gain + (max(change,0)-previous_gain)/n
            movement[n] += (abs(change)-movement[n])/n
            row[f'rsi_{n}'] = 100*gain[n]/movement[n] if movement[n] else 50.0
        output.append(row)
        previous = price
    return output


def compare(rows: list[dict[str, Any]], *, evaluation_start: str, warmup: int = 250) -> dict[str, Any]:
    symbols = sorted({str(r['ts_code']) for r in rows if r.get('ts_code')})
    comparisons: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for symbol in symbols:
        history = sorted([r for r in rows if r.get('ts_code') == symbol], key=lambda r:str(r['trade_date']))
        valid = [r for r in history if isinstance(r.get('close_qfq'),(int,float)) and r['close_qfq']>0]
        if len(valid) != len(history) or not valid:
            missing.append({'symbol':symbol,'reason':'missing_qfq_snapshot_price'})
            continue
        series = indicator_series([float(r['close_qfq']) for r in valid])
        for i, (raw, calculated) in enumerate(zip(valid, series)):
            if str(raw['trade_date']) < evaluation_start:
                continue
            if i < warmup:
                missing.append({'symbol':symbol,'reason':'insufficient_indicator_warmup'})
                continue
            for field, value in calculated.items():
                reference = raw.get(field)
                if not isinstance(reference,(int,float)) or not math.isfinite(reference):
                    missing.append({'symbol':symbol,'reason':'missing_reference_'+field})
                    continue
                # Explicit engineering tolerances, not a profitability criterion.
                # Static qfq prices and published indicators are rounded independently.
                tolerance = 0.005 if field.startswith('macd') else 0.1
                difference = abs(value-float(reference))
                comparisons.append({'symbol':symbol,'date':raw['trade_date'],'field':field,
                    'calculated':value,'reference':reference,'absolute_difference':difference,
                    'absolute_tolerance':tolerance,'exceeds_tolerance':difference>tolerance})
    failures = sum(r['exceeds_tolerance'] for r in comparisons)
    ratio = failures/len(comparisons) if comparisons else None
    compared_rows = {(r['symbol'],r['date']) for r in comparisons}
    outlier_rows = {(r['symbol'],r['date']) for r in comparisons if r['exceeds_tolerance']}
    row_ratio = len(outlier_rows)/len(compared_rows) if compared_rows else None
    return {'status':'passed' if row_ratio is not None and row_ratio<0.005 and not missing else 'not_passed',
            'compared_values':len(comparisons),'outlier_values':failures,'outlier_ratio':ratio,
            'compared_rows':len(compared_rows),'outlier_rows':len(outlier_rows),'outlier_row_ratio':row_ratio,
            'required_outlier_ratio_exclusive':0.005,'missing':missing,
            'symbols':symbols,'warmup_sessions':warmup,'evaluation_start':evaluation_start,
            'price_basis':'stk_factor.close_qfq static snapshots, not dynamic pro_bar qfq',
            'initialization':'EMA first close; Wilder positive/absolute changes, zero initial state; 250-session warmup',
            'comparisons':comparisons,'source_document':'https://tushare.pro/document/2?doc_id=296',
            'historical_pit_created':False,'sealed_samples_modified':False,'research_budget_used':False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', default='000001.SZ,600036.SH')
    parser.add_argument('--start', default='20220101')
    parser.add_argument('--end', required=True)
    parser.add_argument('--evaluation-start', default='20240101')
    parser.add_argument('--db', type=Path, default=default_store_path())
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    symbols = args.symbols.split(',')
    if len(symbols)>10 or not symbols or not args.start <= args.evaluation_start <= args.end:
        parser.error('at most 10 symbols and ordered dates required')
    store = ExtensionStore(args.db); store.initialize()
    jobs = [sync(store,'stk_factor',{'ts_code':symbol,'start_date':args.start,'end_date':args.end},load_token(),max_pages=10) for symbol in symbols]
    rows = [r for r in latest_rows(store,'stk_factor') if r.get('ts_code') in symbols and args.start<=str(r.get('trade_date',''))<=args.end]
    result = compare(rows, evaluation_start=args.evaluation_start)
    result['ingestion_jobs'] = jobs
    absent_symbols = sorted(set(symbols)-set(result['symbols']))
    result['absent_requested_symbols'] = absent_symbols
    if absent_symbols:
        result['status'] = 'not_passed'
    if any(job['status']!='complete' for job in jobs):
        result['status'] = 'not_passed'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    compact = {key:value for key,value in result.items() if key!='comparisons'}
    store.archive_quality(2, {'kind':'technical_indicator_comparison', **compact})
    print(json.dumps({k:result[k] for k in ('status','compared_values','outlier_values','outlier_ratio')}))


if __name__ == '__main__':
    main()
