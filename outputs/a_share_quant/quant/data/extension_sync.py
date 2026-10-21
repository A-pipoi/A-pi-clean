"""Separate initialize/sync/estimate/backfill entrypoints. No strategy or sealed writes."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .extension_catalog import ENDPOINTS
from .extension_store import ExtensionStore, default_store_path
from .extension_transport import ProviderError, query
from .provider_endpoints import provider_endpoint


def sync(store: ExtensionStore, endpoint: str, params: dict[str, Any], token: str, *, max_pages: int = 100,
         fetch: Callable[..., list[dict[str, Any]]] = query, refresh: bool = False, page_size: int | None = None) -> dict[str, Any]:
    import uuid
    job, _, _ = store.begin(endpoint, params)
    owner = uuid.uuid4().hex
    if not store.claim(job, owner):
        return {'job': job, 'status': 'busy', 'requests': 0}
    try:
        if refresh:
            store.refresh(job)
        return {'logical_endpoint':endpoint,'provider_api':provider_endpoint(endpoint),
                **_sync_owned(store, endpoint, params, token, max_pages=max_pages, fetch=fetch, owner=owner, page_size=page_size)}
    finally:
        store.release(job, owner)


def _sync_owned(store: ExtensionStore, endpoint: str, params: dict[str, Any], token: str, *, max_pages: int,
                fetch: Callable[..., list[dict[str, Any]]], owner: str, page_size: int | None = None) -> dict[str, Any]:
    if not 1 <= max_pages <= 10000:
        raise ValueError('max_pages must be 1..10000')
    job, offset, status = store.begin(endpoint, params)
    if status == 'complete':
        return {'job': job, 'status': status, 'rows': offset, 'requests': 0}
    # Public ceilings, plus the fixed real 5000-row fund probe recorded in quality artifacts.
    maximum = {'daily':6000,'daily_basic':6000,'adj_factor':6000,'stk_limit':5800,'suspend_d':5000,'fund_portfolio':5000}.get(endpoint,ENDPOINTS[endpoint].page_size)
    if page_size is not None and (type(page_size) is not int or not 1<=page_size<=maximum):
        raise ValueError('page size exceeds verified endpoint ceiling')
    size = page_size or ENDPOINTS[endpoint].page_size
    for page in range(max_pages):
        if not store.claim(job, owner):
            return {'job': job, 'status': 'busy', 'requests': page}
        try:
            rows = fetch(token, endpoint, {**params, 'offset': offset, 'limit': size})
            if not store.claim(job, owner):
                return {'job': job, 'status': 'busy', 'requests': page + 1}
            complete = len(rows) < size
            store.save_page(job, endpoint, offset, rows, complete=complete)
        except ProviderError as error:
            store.fail(job, error.category)
            return {'job': job, 'status': 'failed', 'error': error.category, 'rows': offset}
        except ValueError:
            # Never persist arbitrary provider errors or arguments.
            category = 'pagination_or_schema_failure'
            store.fail(job, category)
            return {'job': job, 'status': 'failed', 'error': category, 'rows': offset}
        offset += len(rows)
        if complete:
            return {'job': job, 'status': 'complete', 'rows': offset, 'requests': page + 1}
    return {'job': job, 'status': 'partial', 'rows': offset, 'reason': 'page_budget_reached'}


def load_token() -> str:
    """Read only the existing token key; no dotenv import or credential echo."""
    token = os.getenv('TUSHARE_TOKEN', '')
    if token:
        return token
    path = Path(__file__).resolve().parents[2] / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key.strip() == 'TUSHARE_TOKEN':
                return value.strip().strip('"\'')
    return ''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('initialize', 'sync', 'estimate', 'backfill', 'report', 'diagnostics', 'archive'))
    parser.add_argument('--phase', type=int, choices=(1,2,3))
    parser.add_argument('--db', type=Path, default=default_store_path())
    parser.add_argument('--endpoint', choices=tuple(ENDPOINTS))
    parser.add_argument('--params', default='{}')
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--refresh', action='store_true', help='Reconcile source revisions; preserve old rows')
    parser.add_argument('--fields', default='', help='Explicit provider columns for a revision refresh')
    parser.add_argument('--symbols', type=int, default=300)
    parser.add_argument('--sessions', type=int, default=504)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    store = ExtensionStore(args.db)
    if args.command == 'estimate':
        if args.symbols < 1 or args.sessions < 1:
            parser.error('symbols and sessions must be positive')
        result: dict[str, Any] = {'estimated_rows_per_daily_endpoint': args.symbols * args.sessions,
            'estimated_pages_per_daily_endpoint': (args.symbols * args.sessions + 999) // 1000,
            'estimate_only': True, 'automatic_backfill_enqueued': False}
    elif args.command == 'initialize':
        store.initialize()
        result = {'initialized_tables': len(ENDPOINTS), 'db': str(args.db)}
    elif args.command == 'report':
        result = store.report()
    elif args.command in ('diagnostics','archive'):
        from .extension_analytics import diagnostics
        result = diagnostics(store)
        if args.command == 'archive':
            if not args.phase:
                parser.error('--phase is required for archive')
            result = {'artifact':store.archive_quality(args.phase,result),'phase':args.phase,'research_budget_used':False}
    else:
        if not args.endpoint:
            parser.error('--endpoint is required')
        params = json.loads(args.params)
        if not isinstance(params, dict):
            parser.error('--params must be a JSON object')
        result = sync(store, args.endpoint, params, load_token(), max_pages=args.max_pages, refresh=args.refresh,
                      fetch=lambda token,endpoint,values:query(token,endpoint,values,fields=args.fields))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.output:
        print(json.dumps({'output': str(args.output), 'saved': True}, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
