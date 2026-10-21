"""Read-only diagnostics on raw revisions; no sealed feature or strategy mutation."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from contextlib import closing
from typing import Any

from quant.time_utils import parse_yyyymmdd

from .extension_store import ExtensionStore


def price_option(spot: float, strike: float, years: float, rate: float, volatility: float, call: bool) -> float:
    if min(spot, strike, years, volatility) <= 0:
        raise ValueError('positive spot, strike, time and volatility required')
    d1 = (math.log(spot / strike) + (rate + volatility ** 2 / 2) * years) / (volatility * math.sqrt(years))
    d2 = d1 - volatility * math.sqrt(years)
    def normal(value: float) -> float:
        return (1 + math.erf(value / math.sqrt(2))) / 2
    discounted = strike * math.exp(-rate * years)
    return spot * normal(d1) - discounted * normal(d2) if call else discounted * normal(-d2) - spot * normal(-d1)


def implied_volatility(premium: float, spot: float, strike: float, years: float, rate: float, call: bool) -> float | None:
    if not all(math.isfinite(v) for v in (premium, spot, strike, years, rate)) or min(premium, spot, strike, years) <= 0:
        return None
    low, high = 0.00001, 5.0
    if not price_option(spot, strike, years, rate, low, call) <= premium <= price_option(spot, strike, years, rate, high, call):
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if price_option(spot, strike, years, rate, mid, call) < premium:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def latest_rows(store: ExtensionStore, endpoint: str, *, period: str | None = None) -> list[dict[str, Any]]:
    from .extension_catalog import ENDPOINTS
    if endpoint not in ENDPOINTS:
        raise ValueError('unknown endpoint')
    with closing(store.connect()) as db:
        scope = 'a.period_date=? AND ' if period is not None else ''
        rows = db.execute(f'SELECT payload_json FROM ts_{endpoint} a WHERE {scope}NOT EXISTS (SELECT 1 FROM ts_{endpoint} b WHERE b.business_key=a.business_key AND (b.ingested_at>a.ingested_at OR (b.ingested_at=a.ingested_at AND b.rowid>a.rowid)))', (period,) if period is not None else ()).fetchall()
    return [json.loads(row[0]) for row in rows]


def diagnostics(store: ExtensionStore) -> dict[str, Any]:
    plans = {(row.get('ts_code'), row.get('end_date')): row for row in latest_rows(store, 'disclosure_date')}
    financial_differences = []
    for endpoint in ('income', 'balancesheet', 'cashflow', 'fina_indicator'):
        for row in latest_rows(store, endpoint):
            plan = plans.get((row.get('ts_code'), row.get('end_date')), {})
            actual = plan.get('actual_date')
            announcement = row.get('f_ann_date') or row.get('ann_date')
            financial_differences.append({'endpoint': endpoint, 'symbol': row.get('ts_code'), 'period': row.get('end_date'),
                'ann_date': row.get('ann_date'), 'effective_announcement': announcement, 'actual_date': actual,
                'planned_date': plan.get('pre_date'), 'status': 'missing_actual_disclosure' if not actual else 'mismatch' if actual != announcement else 'matched',
                'historical_feature_enabled': False})
    contracts = {row['ts_code']: row for row in latest_rows(store, 'opt_basic')}
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {'call_volume': 0., 'put_volume': 0., 'call_oi': 0., 'put_oi': 0., 'matched': 0., 'missing_contracts': 0.})
    for quote in latest_rows(store, 'opt_daily'):
        day = str(quote['trade_date'])
        contract = contracts.get(quote['ts_code'], {})
        side = contract.get('call_put')
        if side not in ('C', 'P'):
            totals[day]['missing_contracts'] += 1
            continue
        prefix = 'call' if side == 'C' else 'put'
        totals[day][prefix + '_volume'] += float(quote.get('vol') or 0)
        totals[day][prefix + '_oi'] += float(quote.get('oi') or 0)
        totals[day]['matched'] += 1
    pcr = [{'date': day, **values, 'volume_pcr': values['put_volume'] / values['call_volume'] if values['call_volume'] else None,
        'oi_pcr': values['put_oi'] / values['call_oi'] if values['call_oi'] else None,
        'complete_contract_mapping': values['missing_contracts'] == 0} for day, values in sorted(totals.items())]
    spots = {(row['ts_code'], row['trade_date']): float(row['close']) for row in latest_rows(store, 'fund_daily')}
    rates = {str(row['date']).replace('-', ''): float(row['1m']) / 100 for row in latest_rows(store, 'shibor') if row.get('1m') is not None}
    iv = []
    for quote in latest_rows(store, 'opt_daily'):
        contract = contracts.get(quote['ts_code'], {})
        day = str(quote['trade_date'])
        underlying = str(contract.get('opt_code', '')).removeprefix('OP')
        spot, rate = spots.get((underlying, day)), rates.get(day)
        if spot is None or rate is None or contract.get('call_put') not in ('C', 'P') or contract.get('exercise_type') != '欧式':
            continue
        try:
            expiry = str(contract['maturity_date'])
            years = (parse_yyyymmdd(expiry) - parse_yyyymmdd(day)).days / 365
            value = implied_volatility(float(quote['close']), spot, float(contract['exercise_price']), years, rate, contract['call_put'] == 'C')
        except (KeyError, TypeError, ValueError):
            continue
        iv.append({'date': day, 'symbol': quote['ts_code'], 'underlying': underlying, 'maturity_date': expiry,
            'iv': value, 'risk_free_rate': rate, 'rate_source': 'shibor_1m', 'method': 'European Black-Scholes, close, ACT/365; no dividend yield adjustment',
            'status': 'computed_diagnostic_not_risk_budget_input' if value is not None else 'price_outside_model_bounds'})
    nearest: dict[tuple[str, str], str] = {}
    for row in iv:
        key = (row['date'], row['underlying'])
        if row['iv'] is not None:
            nearest[key] = min(nearest.get(key, row['maturity_date']), row['maturity_date'])
    macro = {}
    for endpoint in ('shibor','shibor_lpr','cn_gdp','cn_cpi','cn_ppi','cn_pmi','cn_m','sf_month'):
        macro[endpoint] = latest_rows(store,endpoint)
    valuation = []
    index_history: dict[str,list[dict[str,Any]]] = defaultdict(list)
    for row in latest_rows(store,'index_dailybasic'):
        index_history[str(row.get('ts_code'))].append(row)
    for symbol, rows in index_history.items():
        for row in sorted(rows,key=lambda r:str(r.get('trade_date'))):
            previous = [r for r in rows if str(r.get('trade_date')) <= str(row.get('trade_date'))]
            out = {'symbol':symbol,'date':row.get('trade_date'),'history_rows':len(previous)}
            for metric in ('pe','pb'):
                samples = [float(r[metric]) for r in previous if isinstance(r.get(metric),(int,float)) and math.isfinite(r[metric])]
                value = row.get(metric)
                out[metric+'_percentile'] = sum(s<=float(value) for s in samples)/len(samples) if len(samples)>=60 and isinstance(value,(int,float)) else None
            valuation.append(out)
    from .extension_features import TEMPLATES
    scheduler = []
    technical: dict[str, Any] = {'status':'not_run'}
    constraint: dict[str, Any] = {'status':'not_run'}
    historical: dict[str, Any] = {}
    with closing(store.connect()) as db:
        for artifact in db.execute('SELECT report_json FROM ts_quality_artifacts WHERE phase IN (1,3) ORDER BY created_at DESC LIMIT 100'):
            saved = json.loads(artifact[0])
            if saved.get('kind') in ('disclosure_alignment_ic_diagnostic','fund_consensus_historical_layers','two_year_full_market_history_acceptance'):
                historical.setdefault(saved['kind'],saved)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='ts_schedule'").fetchone():
            scheduler = [dict(r) for r in db.execute('SELECT identity,endpoint,status,report,updated_at FROM ts_schedule ORDER BY updated_at DESC LIMIT 100')]
        for artifact in db.execute('SELECT report_json FROM ts_quality_artifacts WHERE phase=2 ORDER BY created_at DESC LIMIT 50'):
            report = json.loads(artifact[0])
            if report.get('kind')=='technical_indicator_comparison':
                technical = report
                break
        for artifact in db.execute('SELECT report_json FROM ts_quality_artifacts WHERE phase=1 ORDER BY created_at DESC LIMIT 50'):
            report = json.loads(artifact[0])
            if report.get('kind')=='fixed_strategy_execution_constraint_comparison':
                constraint = report
                break
    from .extension_coverage import reconcile_daily
    # Only ten dates are displayed. Do not deserialize millions of historical
    # bars to discover dates already indexed in the raw store.
    with closing(store.connect()) as db:
        observed_days = sorted(str(r[0]) for r in db.execute(
            'SELECT DISTINCT period_date FROM ts_daily WHERE period_date IS NOT NULL '
            'ORDER BY period_date DESC LIMIT 10'))
    coverage = [reconcile_daily(store, day) for day in observed_days[-10:]]
    return {'raw_store': store.report(), 'financial_disclosure_reconciliation': financial_differences,
        'option_pcr': pcr, 'near_month_iv': [row for row in iv if nearest.get((row['date'], row['underlying'])) == row['maturity_date']],
        'macro_series':macro, 'index_valuation':valuation, 'extension_templates':list(TEMPLATES), 'sync_schedule':scheduler,
        'daily_symbol_reconciliation':coverage,
        'technical_indicator_acceptance':technical,
        'disclosure_alignment_ic':historical.get('disclosure_alignment_ic_diagnostic',{'status':'not_run'}),
        'fund_historical_layers':historical.get('fund_consensus_historical_layers',{'status':'not_run'}),
        'full_history_acceptance':historical.get('two_year_full_market_history_acceptance',{'status':'not_run'}),
        'sealed_samples_modified': False, 'historical_industry_pit_usable': False,
        'factor_ic_acceptance': 'not_run', 'constraint_comparison_acceptance': constraint['status'],
        'constraint_comparison_report':constraint,
        'option_window_acceptance': 'single_day_sample_not_2015_onward_full_series'}
