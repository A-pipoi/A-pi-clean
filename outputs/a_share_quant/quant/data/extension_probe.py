"""Bounded real permission samples, NOT full historical coverage acceptance."""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .extension_catalog import ENDPOINTS
from .extension_store import ExtensionStore, default_store_path
from .extension_sync import load_token, sync


def probe_params(endpoint: str, day: str) -> dict[str, Any]:
    if endpoint == 'trade_cal':
        return {'exchange': 'SSE', 'start_date': day, 'end_date': day}
    if endpoint == 'stock_basic':
        return {'list_status': 'D', 'fields': 'ts_code,name,list_date,delist_date,list_status,industry'}
    if endpoint in ('index_classify',):
        return {'src': 'SW2021', 'level': 'L1'}
    if endpoint == 'index_member_all':
        return {'ts_code': '000001.SZ', 'is_new': 'Y'}
    if endpoint == 'opt_basic':
        return {'exchange': 'SSE', 'list_date': '20250901'}
    if endpoint == 'opt_daily':
        return {'exchange': 'SSE', 'trade_date': day}
    if endpoint in ('fund_daily', 'fund_adj', 'fund_nav'):
        return {'ts_code': '510300.SH', 'start_date': day, 'end_date': day}
    if endpoint == 'fund_portfolio':
        return {'ts_code': '510300.SH', 'period': '20260630'}
    if endpoint in ('index_daily', 'index_dailybasic', 'index_weight'):
        return {'ts_code': '000300.SH', 'index_code': '000300.SH', 'start_date': day, 'end_date': day}
    if endpoint == 'cn_gdp':
        return {'q': '202501'}
    if endpoint in ('cn_cpi', 'cn_ppi', 'cn_pmi', 'cn_m', 'sf_month'):
        return {'m': '202501'}
    if endpoint in ('shibor', 'shibor_lpr'):
        return {'start_date': day, 'end_date': day}
    if endpoint in ('margin', 'top_inst', 'top_list', 'block_trade', 'new_share'):
        return {'start_date': day, 'end_date': day, 'trade_date': day}
    if ENDPOINTS[endpoint].financial or endpoint in ('namechange', 'pledge_stat'):
        return {'ts_code': '000001.SZ', 'start_date': '20250101', 'end_date': day}
    return {'ts_code': '000001.SZ', 'trade_date': day}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=default_store_path())
    parser.add_argument('--day', default='20260930')
    parser.add_argument('--output', type=Path, default=Path('data_cache/data_extension/permission_report.json'))
    args = parser.parse_args()
    store = ExtensionStore(args.db)
    store.initialize()
    token = load_token()
    if not token:
        raise SystemExit('token_unavailable')
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for endpoint in ENDPOINTS:
        started = time.monotonic()
        result = sync(store, endpoint, probe_params(endpoint, args.day), token, max_pages=2)
        result.update(endpoint=endpoint, elapsed_seconds=round(time.monotonic() - started, 3), scope='bounded permission sample')
        results.append(result)
        report = {'checked_at': datetime.now(UTC).isoformat(), 'sample_day': args.day,
            'results': results, 'all_endpoints_checked': len(results) == len(ENDPOINTS),
            'strict_pit_acceptance_passed': False, 'full_coverage_acceptance_passed': False,
            'store': store.report()}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
