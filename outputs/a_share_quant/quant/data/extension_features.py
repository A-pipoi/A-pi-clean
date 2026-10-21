"""Prospective raw-data features. Backfills never imply historical availability."""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from .extension_derived import FORMULAS, derived_values
from .extension_store import ExtensionStore

# Public DSL field names, provider columns and deliberately transparent formulas.
SPECS = {
    'turnover_rate': ('daily_basic','turnover_rate'), 'volume_ratio': ('daily_basic','volume_ratio'),
    'ps': ('daily_basic','ps'), 'dividend_yield': ('daily_basic','dv_ttm'),
    'free_float_shares': ('daily_basic','free_share'),
    'quality_roa': ('fina_indicator','roa'), 'growth_profit': ('fina_indicator','netprofit_yoy'),
    'quality_cashflow': ('fina_indicator','ocf_to_or'), 'financial_z': ('fina_indicator','z_score'),
    'forecast_growth': ('forecast','p_change_max'), 'express_growth': ('express','yoy_net_profit'),
    'repurchase_amount': ('repurchase','amount'), 'moneyflow_intensity': ('moneyflow','net_mf_amount'),
    'margin_balance': ('margin_detail','rzye'), 'northbound_ratio': ('hk_hold','ratio'),
    'dragon_net_buy': ('top_list','net_amount'), 'institution_net_buy': ('top_inst','net_buy'),
    'block_price': ('block_trade','price'), 'holder_count': ('stk_holdernumber','holder_num'),
    'holder_trade_ratio': ('stk_holdertrade','change_ratio'), 'unlock_ratio': ('share_float','float_ratio'),
    'pledge_ratio': ('pledge_stat','pledge_ratio'),
}
TEMPLATES = tuple({'name':name,'expression':f'cs_rank({name})','category':'tushare_'+endpoint,
                   'availability':'observed_from_ingestion_only'} for name,(endpoint,_) in SPECS.items())
DERIVED_FIELDS = ('audit_standard','forecast_revision_surprise', *FORMULAS)
FIELD_NAMES = (*SPECS, *DERIVED_FIELDS)
TEMPLATES += tuple({'name':name,'expression':f'cs_rank({name})','category':'tushare_financial_derived',
                    'availability':'prior_disclosed_and_observed_only'} for name in DERIVED_FIELDS)


def values_as_of(store: ExtensionStore, cutoff: datetime) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    if cutoff.tzinfo is None:
        raise ValueError('timezone-aware cutoff required')
    from quant.time_utils import SHANGHAI_TZ
    boundary = cutoff.astimezone(UTC).isoformat()
    result: dict[str, dict[str, float]] = {name:{} for name in SPECS}
    missing: dict[str,int] = {}
    hashes: list[str] = []
    contributing: dict[tuple[str,str],str] = {}
    day = cutoff.astimezone(SHANGHAI_TZ).strftime('%Y%m%d')
    with closing(store.connect()) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ts_sync'").fetchone():
            return result, {'status':'not_initialized','historical_backfill_enabled':False}
        plans = {}
        plan_hashes = {}
        for raw,digest in db.execute('SELECT payload_json,source_hash FROM ts_disclosure_date WHERE ingested_at<=? ORDER BY ingested_at,rowid', (boundary,)):
            plan=json.loads(raw)
            if plan.get('actual_date'):
                plans[(plan.get('ts_code'),plan.get('end_date'))]=str(plan['actual_date'])
                plan_hashes[(plan.get('ts_code'),plan.get('end_date'))]=digest
        grouped: dict[str,list[tuple[str,str]]] = {}
        for name,(endpoint,column) in SPECS.items():
            grouped.setdefault(endpoint,[]).append((name,column))
            missing[name] = 0
        for endpoint,fields in grouped.items():
            rows = db.execute(f'SELECT payload_json,source_hash FROM ts_{endpoint} WHERE ingested_at<=? ORDER BY period_date,ann_date,ingested_at,rowid', (boundary,))
            for raw, source_hash in rows:
                row = json.loads(raw)
                # Also require event/disclosure date, when present, not in future.
                available = row.get('f_ann_date') or row.get('ann_date') or row.get('trade_date')
                if endpoint == 'fina_indicator':
                    actual=plans.get((row.get('ts_code'),row.get('end_date')))
                    if not actual:
                        for name,_ in fields:
                            missing[name] += 1
                        continue
                    available=max(str(available or ''),actual)
                if available and str(available).replace('-','') > day:
                    continue
                for name,column in fields:
                    symbol, value = row.get('ts_code'), row.get(column)
                    if not symbol or not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
                        missing[name] += 1
                        continue
                    result[name][str(symbol)] = float(value)
                    contributing[(name,str(symbol))] = source_hash
                    if endpoint == 'fina_indicator':
                        contributing[(name,str(symbol)+' disclosure')] = plan_hashes[(row.get('ts_code'),row.get('end_date'))]
    # Versioned provenance covers exactly the observations supplying values,
    # not millions of superseded bars. Stored old versions remain immutable.
    hashes.extend(contributing.values())
    from .extension_financial import financial_status
    symbols=sorted({symbol for values in result.values() for symbol in values})
    financial=financial_status(store,cutoff,symbols)
    result['audit_standard']={row['symbol']:float(row['eligible']) for row in financial['audit_eligibility'] if row['source_hash']}
    result['forecast_revision_surprise']={}
    for row in financial['forecast_revisions']:
        result['forecast_revision_surprise'][row['symbol']]=float(row['revision_surprise'])
        hashes.extend((row['source_hash'],row['baseline_source_hash']))
    hashes.extend(row['source_hash'] for row in financial['audit_eligibility'] if row['source_hash'])
    derived,derived_proof=derived_values(store,cutoff)
    result.update(derived)
    for entries in derived_proof['evidence'].values():
        for entry in entries.values():
            hashes.extend(entry['source_hashes'])
    return result, {'availability_basis':'max(source_announcement,first_observed_ingestion)',
                     'provenance_scope':'latest_contributing_field_observations_v2',
                     'financial_eligibility':financial,
                     'derived_features':derived_proof,
                     'cutoff':cutoff.isoformat(),'missing_numeric_rows':missing,'historical_backfill_enabled':False,
                     'source_hash':hashlib.sha256(''.join(sorted(hashes)).encode()).hexdigest()}
