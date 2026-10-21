"""Read-only permission/evidence probe. Effective history does not prove PIT."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded

from .adapters import _tushare_http_query


def probe(root: Path, token: str) -> dict[str, Any]:
    if not token:
        raise ValueError('token_unavailable')
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    results=[]
    # Bounded requests establish actual access, not whole-market coverage.
    for symbol, is_new in ((symbol, view) for symbol in ('000001.SZ','600036.SH','600519.SH') for view in ('N','Y')):
        started=time.monotonic()
        try:
            frame=_tushare_http_query(token,'index_member_all',{'ts_code':symbol,'is_new':is_new},fields='ts_code,l1_code,l1_name,in_date,out_date,is_new')
            rows=[] if frame is None or frame.empty else frame.to_dict(orient='records')
            completed=datetime.now(UTC).isoformat()
            payload=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str)
            digest=hashlib.sha256(payload.encode()).hexdigest()
            # Raw provider rows are retained, never converted to retrospective
            # availability. This completion clock is evidence only FROM now on.
            evidence_path = root / f'{symbol}-{is_new}-{run_id}.json'
            with evidence_path.open('x', encoding='utf-8') as stream:
                json.dump({'observed_at':completed,'source_hash':digest,'rows':rows}, stream, ensure_ascii=False, default=str)
            results.append({'symbol':symbol,'is_new':is_new,'accessible':True,'rows':len(rows),'possibly_truncated':len(rows)>=2000,
                            'evidence_file':evidence_path.name,'earliest_effective_date':min((str(row.get('in_date','')) for row in rows),default=None),
                            'has_historical_availability_evidence':False,'source_hash':digest,'elapsed_seconds':round(time.monotonic()-started,3)})
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("quant.data.industry_history_probe failed: %s", type(error).__name__, exc_info=None)
            message=str(error).lower()
            category='permission_denied' if any(word in message for word in ('权限','积分','permission')) else 'provider_request_failed'
            results.append({'symbol':symbol,'is_new':is_new,'accessible':False,'error':category,'error_type':type(error).__name__,'elapsed_seconds':round(time.monotonic()-started,3)})
    report={'checked_at':datetime.now(UTC).isoformat(),'endpoint':'index_member_all','scope':'three-symbol permission probe, not full coverage',
            'results':results,'strict_historical_pit_usable':False,'historical_backfill':False,'reason':'Provider documents effective dates but no historical disclosure/availability timestamp.'}
    report['run_id'] = run_id
    report_json = json.dumps(report,ensure_ascii=False,indent=2)
    with (root / f'report-{run_id}.json').open('x', encoding='utf-8') as stream:
        stream.write(report_json)
    (root/'report.json').write_text(report_json,encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('data_cache/industry_history_probe'))
    args=parser.parse_args()
    try:
        print(json.dumps(probe(args.root,os.getenv('TUSHARE_TOKEN','')),ensure_ascii=False))
    except Exception as error:
        logging.getLogger(__name__).exception("quant.data.industry_history_probe failed: %s", type(error).__name__, exc_info=None)
        print(json.dumps({'ok':False,'error_type':type(error).__name__}))
        raise SystemExit(1)
