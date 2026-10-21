"""Prospective, immutable daily signals and mature-label out-of-sample IC."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .analysis import rank_ic
from .decay import factor_decay_report

SHANGHAI = timezone(timedelta(hours=8))
HORIZONS = (1, 5, 10, 20)


class LiveICStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=30)) as db, db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS signals(day TEXT,version TEXT,name TEXT,values_json TEXT,prices_json TEXT,
              expected INTEGER,first_seen TEXT,source_hash TEXT,PRIMARY KEY(day,version));
            CREATE TABLE IF NOT EXISTS observations(day TEXT,version TEXT,horizon INTEGER,maturity TEXT,ic REAL,
              sample INTEGER,coverage REAL,status TEXT,observed_at TEXT,PRIMARY KEY(day,version,horizon));
            CREATE TABLE IF NOT EXISTS attempts(day TEXT PRIMARY KEY,slot TEXT,count INTEGER,status TEXT,error TEXT,updated TEXT);
            ''')

    def signal_dates(self) -> list[date]:
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            return [date.fromisoformat(row[0]) for row in db.execute('SELECT DISTINCT day FROM signals ORDER BY day')]

    def latest_symbols(self) -> list[str]:
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            row=db.execute('SELECT prices_json FROM signals ORDER BY day DESC LIMIT 1').fetchone()
        return sorted(json.loads(row[0])) if row else []

    def claim(self, now: datetime) -> bool:
        if now.tzinfo is None:raise ValueError('timezone-aware scheduler time is required')
        local = now.astimezone(SHANGHAI)
        if local.weekday() >= 5 or not 17 <= local.hour < 20 or local.minute % 15:
            return False
        day, slot = local.date().isoformat(), local.strftime('%H:%M')
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT slot,count,status FROM attempts WHERE day=?', (day,)).fetchone()
            # Cache warm-up is bounded to 25 sessions per attempt. Four slots
            # cannot reliably cover the initial 65-session window, so keep
            # retrying within today's observation window without backdating.
            limit = 12 if previous and previous[2] == 'warming_cache' else 4
            if previous and (previous[0] == slot or previous[1] >= limit or previous[2] in {'updated','market_closed'}):
                return False
            count = previous[1]+1 if previous else 1
            db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?,?,?)', (day,slot,count,'running','',local.isoformat()))
            return True

    def finish(self, now: datetime, status: str, error: str = '') -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute('UPDATE attempts SET status=?,error=?,updated=? WHERE day=?',
                (status,error[:80],now.isoformat(),now.astimezone(SHANGHAI).date().isoformat()))

    def freeze(self, day: date, version: str, name: str, values: Mapping[str,float],
               prices: Mapping[str,float], expected: int, source_hash: str, *, now: datetime) -> bool:
        if now.tzinfo is None or now.astimezone(SHANGHAI).date() != day or now.astimezone(SHANGHAI).hour < 17:
            raise ValueError('signals can only be frozen prospectively after 17:00 Shanghai')
        if expected < len(values) or expected <= 0:
            raise ValueError('invalid universe denominator')
        if any(not math.isfinite(value) for value in values.values()) or any(not math.isfinite(value) or value<=0 for value in prices.values()):
            raise ValueError('nonfinite signals or prices')
        if not set(values).issubset(prices):
            raise ValueError('missing signal-date valuation')
        payload = (day.isoformat(),version,name,json.dumps(dict(values),sort_keys=True),json.dumps(dict(prices),sort_keys=True),expected,now.isoformat(),source_hash)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            return db.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?,?,?,?,?,?)', payload).rowcount == 1

    def settle(self, sessions: Sequence[date], prices: Mapping[date,Mapping[str,float]], *, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError('timezone-aware observation time is required')
        today = now.astimezone(SHANGHAI).date()
        if list(sessions) != sorted(set(sessions)) or any(day > today for day in prices):
            raise ValueError('invalid or future label calendar')
        if today in prices and now.astimezone(SHANGHAI).hour<17:
            raise ValueError('today close labels cannot mature before 17:00 Shanghai')
        calendar = {day:index for index,day in enumerate(sessions)}
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            for signal in db.execute('SELECT day,version,values_json,prices_json,expected FROM signals').fetchall():
                day,version,value_json,price_json,expected = signal
                origin=date.fromisoformat(day)
                if origin not in calendar:
                    continue
                values,base = json.loads(value_json),json.loads(price_json)
                for horizon in HORIZONS:
                    end=calendar[origin]+horizon
                    if end >= len(sessions) or sessions[end] > today:
                        continue
                    maturity=sessions[end]
                    future=prices.get(maturity,{})
                    returns={symbol:future[symbol]/base[symbol]-1 for symbol in values
                        if symbol in future and math.isfinite(future[symbol]) and future[symbol]>0}
                    coverage=len(returns)/expected
                    ic=rank_ic(values,returns) if coverage>=.9 and len(returns)>=30 else None
                    status='available' if ic is not None else 'insufficient_coverage'
                    # Missing-data records may be repaired once real prices arrive;
                    # a successfully observed IC is never rewritten by a revision.
                    db.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(day,version,horizon) DO UPDATE SET ic=excluded.ic,sample=excluded.sample,
                        coverage=excluded.coverage,status=excluded.status,observed_at=excluded.observed_at
                        WHERE observations.status != 'available' ''',
                        (day,version,horizon,maturity.isoformat(),ic,len(returns),coverage,status,now.isoformat()))

    def report(self) -> dict[str,object]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            definitions=db.execute('SELECT version,MAX(name),MIN(day),MAX(day),COUNT(*) FROM signals GROUP BY version').fetchall()
            attempts=db.execute('SELECT day,status,error,updated,count FROM attempts ORDER BY day DESC LIMIT 7').fetchall()
            monitors=[]
            for version,name,start,end,count in definitions:
                latest=db.execute('SELECT values_json,expected,first_seen,source_hash FROM signals WHERE version=? ORDER BY day DESC LIMIT 1',(version,)).fetchone()
                factor_coverage=len(json.loads(latest[0]))/latest[1]
                for horizon in HORIZONS:
                    rows=db.execute('SELECT day,ic,maturity,sample,coverage,status,observed_at FROM observations WHERE version=? AND horizon=? ORDER BY day', (version,horizon)).fetchall()
                    valid=[{'date':row[0],'value':row[1]} for row in rows if row[5]=='available']
                    monitors.append({'version':version,'name':name,'horizon':horizon,'started_on':start,
                        'latest_factor_coverage':factor_coverage,'first_seen':latest[2],'source_hash':latest[3],
                        'signal_status':'waiting_for_mature_labels' if factor_coverage>=.9 else 'insufficient_factor_coverage',
                        'latest_signal':end,'frozen_days':count,'ic_series':valid,
                        'observations':[dict(zip(('date','value','maturity','sample','coverage','status','observed_at'),row)) for row in rows],
                        'decay':factor_decay_report(valid)})
        return {'ok':True,'mode':'prospective_out_of_sample','schedule':'17:00–19:45 Asia/Shanghai; 4 attempts, up to 12 while warming cache',
            'automatic_enabled':os.getenv('A_PI_IC_AUTO','1').lower() not in {'0','false','no'},
            'label_definition':'adjusted close-to-close returns; not executable portfolio PnL',
            'industry_neutralized':False,
            'minimum_sample':30,'minimum_coverage':.9,'monitors':monitors,
            'attempts':[dict(zip(('date','status','error','updated_at','attempts'),row)) for row in attempts],
            'status':'collecting' if definitions else 'waiting_for_first_market_session',
            'sealed_data_used':False,'research_budget_used':False,'automatic_trading':False}


def definition_version(payload: Mapping[str,object]) -> str:
    return hashlib.sha256(json.dumps(dict(payload),sort_keys=True).encode()).hexdigest()
