"""Estimated, bounded historical daily ingestion; does not create historical PIT proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from quant.time_utils import parse_yyyymmdd

from .extension_analytics import latest_rows
from .extension_catalog import ENDPOINTS
from .extension_coverage import reconcile_daily
from .extension_store import ExtensionStore, canonical, default_store_path
from .extension_sync import load_token, sync

DAILY_ENDPOINTS = ('daily', 'daily_basic', 'adj_factor', 'stk_limit')


def estimate(store: ExtensionStore, start: str, end: str) -> dict[str, Any]:
    start_day, end_day = parse_yyyymmdd(start), parse_yyyymmdd(end)
    if start > end:
        raise ValueError('start must not exceed end')
    calendar = {str(r['cal_date']): r for r in latest_rows(store, 'trade_cal')
                if r.get('exchange') == 'SSE' and start <= str(r.get('cal_date', '')) <= end}
    expected_calendar_days = (end_day - start_day).days + 1
    if len(calendar) != expected_calendar_days:
        raise ValueError('complete exchange calendar required before estimation')
    sessions = sorted(day for day, row in calendar.items() if row.get('is_open') == 1)
    listings = latest_rows(store, 'stock_basic')
    if not listings:
        raise ValueError('listing metadata required before estimation')
    counts = {day: len({r['ts_code'] for r in listings if r.get('ts_code') and r.get('list_date')
                       and str(r['list_date']) <= day
                       and (not r.get('delist_date') or str(r['delist_date']) > day)}) for day in sessions}
    jobs: list[dict[str, Any]] = [{'endpoint': endpoint, 'params': {'trade_date': day},
             'estimated_rows': counts[day],
             'estimated_requests': counts[day]//ENDPOINTS[endpoint].page_size+1}
            for day in sessions for endpoint in DAILY_ENDPOINTS]
    payload = {'start':start, 'end':end, 'sessions':sessions, 'jobs':jobs,
               'estimated_rows':sum(r['estimated_rows'] for r in jobs),
               'estimated_requests':sum(r['estimated_requests'] for r in jobs),
               'historical_pit_created':False, 'estimate_basis':'historical listing intervals; endpoint population may differ',
               'research_budget_used':False, 'sealed_samples_modified':False}
    return {**payload, 'plan_hash':hashlib.sha256(canonical(payload).encode()).hexdigest()}


def execute(store: ExtensionStore, plan: dict[str, Any], *, max_jobs: int = 16,
            max_seconds: int = 300) -> dict[str, Any]:
    if not 1 <= max_jobs <= 10000 or not 1 <= max_seconds <= 86400:
        raise ValueError('invalid execution bounds')
    token = load_token()
    if not token:
        raise ValueError('token unavailable')
    started = time.monotonic()
    reports: list[dict[str, Any]] = []
    for job in plan['jobs']:
        _, _, status = store.begin(job['endpoint'], job['params'])
        if status == 'complete':
            continue
        if len(reports) >= max_jobs or time.monotonic()-started >= max_seconds:
            break
        result = sync(store, job['endpoint'], job['params'], token, max_pages=20)
        reports.append({'endpoint':job['endpoint'], 'params':job['params'], **result})
        if result['status'] == 'failed':
            break
    remaining = sum(store.begin(j['endpoint'], j['params'])[2] != 'complete' for j in plan['jobs'])
    touched = plan['sessions']
    return {'plan_hash':plan['plan_hash'], 'estimated_requests':plan['estimated_requests'],
            'elapsed_seconds':time.monotonic()-started, 'executed_jobs':reports,
            'remaining_jobs':remaining, 'status':'ingested' if remaining == 0 else 'partial',
            'coverage':[reconcile_daily(store, day) for day in touched],
            'next_step':'rerun the identical date range to resume persisted checkpoints',
            'historical_pit_created':False, 'sealed_samples_modified':False, 'research_budget_used':False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('estimate','run'))
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--db', type=Path, default=default_store_path())
    parser.add_argument('--max-jobs', type=int, default=16)
    parser.add_argument('--max-seconds', type=int, default=300)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    store = ExtensionStore(args.db)
    store.initialize()
    plan = estimate(store, args.start, args.end)
    result = plan if args.command == 'estimate' else execute(store, plan, max_jobs=args.max_jobs, max_seconds=args.max_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output':str(args.output), 'status':result.get('status','estimated'),
                      'estimated_requests':plan['estimated_requests'], 'remaining_jobs':result.get('remaining_jobs')}))


if __name__ == '__main__':
    main()
