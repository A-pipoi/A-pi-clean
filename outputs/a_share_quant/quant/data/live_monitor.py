"""Shared public market cache and bounded Tushare batch reads for daily monitoring."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from quant.api.resource_limits import ResourceLimitExceeded
from quant.time_utils import parse_yyyymmdd

from ..factors.live_ic import SHANGHAI
from .adapters import _tushare_http_query
from .industry_evidence import preserve_response
from .observed_industry import ObservedIndustryStore
from .schema import Bar


@dataclass
class MonitorMarket:
    sessions: list[date]
    symbols: list[str]
    bars: list[Bar]
    prices: dict[date,dict[str,float]]
    source_hash: str


class LiveMonitorProvider:
    def __init__(self, root: Path, token: str, clock: Callable[[],datetime] | None = None, cancelled: Callable[[],bool] | None = None) -> None:
        self.root,self.token=root,token
        self.clock=clock or (lambda:datetime.now(SHANGHAI))
        self.cancelled=cancelled or (lambda:False)
        root.mkdir(parents=True,exist_ok=True)
        self.cache=root/'daily.sqlite3'
        with closing(sqlite3.connect(self.cache,timeout=30)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS market(day TEXT PRIMARY KEY,payload TEXT,first_seen TEXT)')

    def query(self, name: str, params: dict[str,Any], fields: str) -> list[dict[str,Any]]:
        if self.cancelled():raise RuntimeError('monitor_cancelled')
        frame=_tushare_http_query(self.token,name,params,fields=fields)
        return [] if frame is None or frame.empty else frame.to_dict(orient='records')

    def fetch(self, day: date, now: datetime, signal_dates: list[date] | None = None) -> MonitorMarket | None:
        start=min([day-timedelta(days=100),*(signal_dates or [])])
        calendar=self.query('trade_cal',{'exchange':'SSE','start_date':start.strftime('%Y%m%d'),'end_date':day.strftime('%Y%m%d')},'cal_date,is_open')
        sessions=sorted({parse_yyyymmdd(str(row['cal_date'])) for row in calendar if int(row['is_open'])==1})
        if not calendar:
            raise RuntimeError('calendar_unavailable')
        if day not in sessions:
            return None
        # Monthly snapshots only within a bounded recent range; never query
        # three years in one request and silently accept a truncated universe.
        members=self.query('index_weight',{'index_code':'000300.SH','start_date':(day-timedelta(days=62)).strftime('%Y%m%d'),'end_date':day.strftime('%Y%m%d')},'con_code,trade_date,weight')
        eligible=[row for row in members if str(row['trade_date'])<=day.strftime('%Y%m%d')]
        if not eligible or len(members)>=6000:
            raise RuntimeError('membership_unavailable')
        latest=max(str(row['trade_date']) for row in eligible)
        symbols=sorted({str(row['con_code']) for row in eligible if str(row['trade_date'])==latest})
        if len(symbols)<280 or len(symbols)>350:
            raise RuntimeError('membership_coverage_failed')
        # Evidence is saved before the potentially multi-attempt price warm-up.
        # Availability is response-completion time, NOT job start / effective date.
        evidence=ObservedIndustryStore(self.root/'industry.sqlite3')
        try:
            raw_weights={str(row['con_code']):float(row['weight'])/100 for row in eligible if str(row['trade_date'])==latest}
            evidence.record_weights(parse_yyyymmdd(latest).isoformat(),raw_weights,self.clock(),hashlib.sha256(json.dumps(members,sort_keys=True).encode()).hexdigest())
        except (ValueError,KeyError,TypeError):
            # Industry evidence remains independently collectable without weights.
            pass
        try:
            basics=self.query('stock_basic',{'exchange':'','list_status':'L'},'ts_code,industry')
            industry={str(row['ts_code']):str(row['industry']).strip() for row in basics if isinstance(row.get('industry'),str) and str(row['industry']).strip()}
            observed=self.clock()
            industry_digest, _ = preserve_response(self.root, basics, observed)
            evidence.record_universe([str(row['ts_code']) for row in basics],observed)
            evidence.record(industry,observed,industry_digest)
            evidence.capture_status(observed,'complete' if set(symbols).issubset(industry) else 'incomplete')
        except ResourceLimitExceeded:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("quant.data.live_monitor failed: %s", type(error).__name__, exc_info=None)
            evidence.capture_status(self.clock(),'unavailable',type(error).__name__)
        # Include exited names when settling old snapshots: full-market daily
        # prices, not today's index membership, determine the mature labels.
        payloads={}
        requests=0
        needed=set(sessions[-65:])
        by_session={session:index for index,session in enumerate(sessions)}
        for signal_day in signal_dates or []:
            if signal_day in by_session:
                for horizon in (1,5,10,20):
                    position=by_session[signal_day]+horizon
                    if position<len(sessions):needed.add(sessions[position])
        with closing(sqlite3.connect(self.cache,timeout=30)) as db:
            for observed_day in sorted(needed):
                if self.cancelled():raise RuntimeError('monitor_cancelled')
                key=observed_day.isoformat()
                cached=db.execute('SELECT payload FROM market WHERE day=?',(key,)).fetchone()
                if cached:
                    payloads[observed_day]=json.loads(cached[0]);continue
                if requests>=50:
                    raise RuntimeError('warming_market_cache')
                query_day=observed_day.strftime('%Y%m%d')
                raw=self.query('daily',{'trade_date':query_day},'ts_code,trade_date,open,high,low,close,vol')
                adjustments=self.query('adj_factor',{'trade_date':query_day},'ts_code,trade_date,adj_factor')
                requests+=2
                if len(raw)<3000 or len(raw)>=6000 or len(adjustments)<len(raw)*.9:
                    raise RuntimeError('daily_or_adjustment_coverage_failed')
                factors={str(row['ts_code']):float(row['adj_factor']) for row in adjustments if str(row['trade_date'])==query_day}
                records=[]
                for row in raw:
                    symbol=str(row['ts_code'])
                    if str(row['trade_date'])!=query_day or symbol not in factors:
                        continue
                    factor=factors[symbol]
                    numbers=[float(row[name])*factor for name in ('open','high','low','close')]+[float(row['vol'])*100]
                    if factor<=0 or any(not math.isfinite(value) or value<=0 for value in numbers):
                        continue
                    records.append([symbol,*numbers])
                if len(records)<3000:
                    raise RuntimeError('validated_price_coverage_failed')
                payload=json.dumps(records,separators=(',',':'))
                with db:
                    db.execute('INSERT OR IGNORE INTO market VALUES(?,?,?)',(key,payload,self.clock().isoformat()))
                payloads[observed_day]=records
        current={record[0] for record in payloads.get(day,[])}
        if len(current.intersection(symbols))/len(symbols)<.9:
            raise RuntimeError('latest_market_not_fresh')
        bars: list[Bar]=[]
        prices: dict[date,dict[str,float]]={}
        wanted=set(symbols)
        for session,records in payloads.items():
            prices[session]={record[0]:record[4] for record in records}
            bars.extend(Bar(record[0],session,*record[1:]) for record in records if record[0] in wanted and session in sessions[-65:])
        digest=hashlib.sha256(json.dumps([symbols,[(session.isoformat(),payloads[session]) for session in sorted(payloads)]],sort_keys=True).encode()).hexdigest()
        return MonitorMarket(sessions,symbols,sorted(bars,key=lambda bar:(bar.date,bar.symbol)),prices,digest)
