"""Observed audit eligibility and prior-disclosed forecast revision baseline."""
from __future__ import annotations

import json
import math
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from quant.time_utils import SHANGHAI_TZ

from .extension_store import ExtensionStore

STANDARD_AUDITS = frozenset(('标准无保留意见','标准无保留审计意见','无保留意见'))


def observed_rows(store: ExtensionStore, endpoint: str, cutoff: datetime) -> list[dict[str, Any]]:
    if endpoint not in {'fina_audit','forecast','express'} or cutoff.tzinfo is None:
        raise ValueError('allowlisted endpoint and aware cutoff required')
    boundary=cutoff.astimezone(UTC).isoformat()
    local_day=cutoff.astimezone(SHANGHAI_TZ).strftime('%Y%m%d')
    rows=[]
    with closing(store.connect()) as db:
        for raw, digest, observed in db.execute(f'SELECT payload_json,source_hash,ingested_at FROM ts_{endpoint} WHERE ingested_at<=? ORDER BY ingested_at,rowid',(boundary,)):
            row=json.loads(raw)
            announced=str(row.get('ann_date') or '').replace('-','')
            if len(announced)!=8 or announced>local_day:
                continue
            rows.append({**row,'source_hash':digest,'first_observed_revision_at':observed})
    return rows


def financial_status(store: ExtensionStore, cutoff: datetime, symbols: list[str]) -> dict[str, Any]:
    audits: dict[str, dict[str, Any]]={}
    for row in sorted(observed_rows(store,'fina_audit',cutoff),key=lambda r:(str(r.get('ann_date')),str(r.get('end_date')),r['first_observed_revision_at'])):
        audits[str(row.get('ts_code'))]=row
    checks=[]
    for symbol in symbols:
        audit_row=audits.get(symbol)
        result=str(audit_row.get('audit_result') or '').strip() if audit_row else ''
        checks.append({'symbol':symbol,'eligible':bool(result and result in STANDARD_AUDITS),
                       'reason':'standard_audit' if result in STANDARD_AUDITS else 'missing_audit_evidence' if not result else 'nonstandard_audit',
                       'audit_result':result,'source_hash':audit_row['source_hash'] if audit_row else None})
    # A baseline is only an earlier publicly dated forecast for the SAME period,
    # never the final earnings report or a later revised version of the same date.
    groups: dict[tuple[str,str], dict[str, dict[str, Any]]]={}
    for row in observed_rows(store,'forecast',cutoff):
        key=(str(row.get('ts_code')),str(row.get('end_date')))
        groups.setdefault(key,{})[str(row['ann_date'])]=row
    revisions=[]
    for (symbol, period), announcements in sorted(groups.items()):
        if symbol not in symbols:
            continue
        previous=None
        for announced,row in sorted(announcements.items()):
            lo,hi=row.get('net_profit_min'),row.get('net_profit_max')
            if not isinstance(lo,(float,int)) or not isinstance(hi,(float,int)) or any(isinstance(v,bool) or not math.isfinite(v) for v in (lo,hi)):
                continue
            midpoint=(float(lo)+float(hi))/2
            if previous is not None and previous['midpoint']!=0:
                revisions.append({'symbol':symbol,'period':period,'ann_date':announced,
                                  'revision_surprise':(midpoint-previous['midpoint'])/abs(previous['midpoint']),
                                  'baseline_ann_date':previous['ann_date'],
                                  'baseline_source_hash':previous['source_hash'],'source_hash':row['source_hash'],
                                  'basis':'prior public same-period forecast midpoint; not analyst consensus',
                                  'available_at':max(str(row['first_observed_revision_at']),str(previous['first_observed_revision_at']))})
            previous={'midpoint':midpoint,'ann_date':announced,'source_hash':row['source_hash'],
                      'first_observed_revision_at':row['first_observed_revision_at']}
    return {'cutoff':cutoff.isoformat(),'audit_eligibility':checks,'forecast_revisions':revisions,
            'historical_backfill_enabled':False,'production_exclusions_automatically_changed':False}
