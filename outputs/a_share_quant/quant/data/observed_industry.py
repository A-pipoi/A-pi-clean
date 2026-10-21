"""Forward-only industry availability evidence; never historical stock_basic backfill."""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class ObservedIndustryStore:
    def __init__(self, path: Path) -> None:
        self.path=path
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path,timeout=30)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS industries(day TEXT,symbol TEXT,industry TEXT,available_at REAL,available_iso TEXT,source_hash TEXT,PRIMARY KEY(day,symbol))')
            db.execute('CREATE TABLE IF NOT EXISTS captures(day TEXT PRIMARY KEY,status TEXT,error TEXT,updated TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS weights(day TEXT PRIMARY KEY,effective TEXT,payload TEXT,available_at REAL,available_iso TEXT,source_hash TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS universes(day TEXT PRIMARY KEY,payload TEXT)')

    def record(self, values: Mapping[str,str], now: datetime, source_hash: str) -> None:
        if now.tzinfo is None or not source_hash:
            raise ValueError('availability evidence is required')
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.executemany('INSERT OR IGNORE INTO industries VALUES(?,?,?,?,?,?)',
                [(now.astimezone(timezone(timedelta(hours=8))).date().isoformat(),symbol,industry,now.timestamp(),now.isoformat(),source_hash)
                    for symbol,industry in values.items() if industry.strip()])

    def capture_status(self, now: datetime, status: str, error: str = '') -> None:
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?,?)',
                (now.astimezone(timezone(timedelta(hours=8))).date().isoformat(),status,error[:80],now.isoformat()))

    def latest_symbols(self) -> list[str]:
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            row=db.execute('SELECT payload FROM universes ORDER BY day DESC LIMIT 1').fetchone()
            if row:return sorted(json.loads(row[0]))
            return [row[0] for row in db.execute('SELECT symbol FROM industries WHERE day=(SELECT MAX(day) FROM industries) ORDER BY symbol')]

    def record_universe(self, symbols: Sequence[str], now: datetime) -> None:
        if now.tzinfo is None:raise ValueError('timezone-aware universe observation is required')
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.execute('INSERT OR IGNORE INTO universes VALUES(?,?)',
                (now.astimezone(timezone(timedelta(hours=8))).date().isoformat(),json.dumps(sorted(set(symbols)))))

    def records(self, symbols: Sequence[str], end: date) -> list[dict[str,object]]:
        """Compatible strict-PIT rows; never infer earlier availability."""
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            rows=db.execute('SELECT day,symbol,industry,available_iso,source_hash FROM industries WHERE day<=? ORDER BY available_at',(end.isoformat(),)).fetchall()
        wanted=set(symbols)
        return [{'symbol':symbol,'industry':industry,'in_date':day,'first_seen_at':available,
            'source_version':digest,'availability_basis':'actual_first_seen'}
            for day,symbol,industry,available,digest in rows if symbol in wanted]

    def record_weights(self, effective: str, weights: Mapping[str,float], now: datetime, source_hash: str) -> None:
        if now.tzinfo is None or not source_hash or not weights or any(not math.isfinite(v) or v<=0 for v in weights.values()):
            raise ValueError('valid first-seen benchmark evidence is required')
        if not .98<=sum(weights.values())<=1.02:
            raise ValueError('benchmark weights must cover the complete index')
        day=now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
        if effective>day:raise ValueError('future membership cannot be observed')
        with closing(sqlite3.connect(self.path,timeout=30)) as db,db:
            db.execute('INSERT OR IGNORE INTO weights VALUES(?,?,?,?,?,?)',
                (day,effective,json.dumps(dict(weights),sort_keys=True),now.timestamp(),now.isoformat(),source_hash))

    def at(self, cutoff: datetime, symbols: Sequence[str]) -> dict[str,object]:
        if cutoff.tzinfo is None:
            raise ValueError('timezone-aware PIT cutoff is required')
        with closing(sqlite3.connect(self.path,timeout=30)) as db:
            rows=db.execute('SELECT symbol,industry,available_iso,source_hash FROM industries WHERE available_at<=? ORDER BY available_at', (cutoff.timestamp(),)).fetchall()
            latest=db.execute('SELECT status,error,updated FROM captures ORDER BY day DESC LIMIT 1').fetchone()
            weight=db.execute('SELECT effective,payload,available_iso,source_hash FROM weights WHERE available_at<=? ORDER BY available_at DESC LIMIT 1',(cutoff.timestamp(),)).fetchone()
        wanted=set(symbols)
        selected={row[0]:{'industry':row[1],'available_at':row[2],'source_hash':row[3],
            'availability_basis':'actual_first_seen'} for row in rows if row[0] in wanted}
        return {'source':'tushare:stock_basic_forward_observations','rows':selected,
            'coverage':len(selected)/max(1,len(wanted)), 'missing_symbols':sorted(wanted-set(selected)),
            'benchmark':{'effective_date':weight[0],'weights':json.loads(weight[1]),'available_at':weight[2],'source_hash':weight[3]} if weight else None,
            'last_capture':dict(zip(('status','error','updated_at'),latest)) if latest else None,
            'historical_backfill':False,'status':'verified' if wanted and len(selected)==len(wanted) else 'incomplete'}
