"""Append-only provider revisions and atomic page checkpoints in one shared SQLite file."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .extension_catalog import ENDPOINTS
from .provider_endpoints import provider_endpoint


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def default_store_path() -> Path:
    root = Path(os.getenv('A_PI_DATA_ROOT', str(Path(__file__).resolve().parents[2] / 'data')))
    return Path(os.getenv('A_PI_FEATURE_DB', str(root / 'features.sqlite3')))


class ExtensionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def initialize(self) -> None:
        with closing(self.connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS ts_sync (job TEXT PRIMARY KEY, endpoint TEXT NOT NULL, params_json TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error TEXT, pages INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)')
            if 'provider_api' not in {r[1] for r in db.execute('PRAGMA table_info(ts_sync)')}:
                db.execute("ALTER TABLE ts_sync ADD COLUMN provider_api TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE ts_sync SET provider_api=endpoint WHERE provider_api=''")
            db.execute('CREATE TABLE IF NOT EXISTS ts_pages (job TEXT NOT NULL, offset INTEGER NOT NULL, digest TEXT NOT NULL, rows INTEGER NOT NULL, PRIMARY KEY(job,offset))')
            db.execute('CREATE TABLE IF NOT EXISTS ts_job_leases (job TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS ts_quality_artifacts (digest TEXT PRIMARY KEY, phase INTEGER NOT NULL, created_at TEXT NOT NULL, report_json TEXT NOT NULL)')
            for endpoint in ENDPOINTS:
                db.execute(f'CREATE TABLE IF NOT EXISTS ts_{endpoint} (business_key TEXT NOT NULL, source_hash TEXT NOT NULL, ts_code TEXT, ann_date TEXT, period_date TEXT, ingested_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(business_key,source_hash))')
                db.execute(f'CREATE INDEX IF NOT EXISTS ts_{endpoint}_lookup ON ts_{endpoint}(ts_code,ann_date)')
                db.execute(f'CREATE INDEX IF NOT EXISTS ts_{endpoint}_period ON ts_{endpoint}(period_date)')

    def begin(self, endpoint: str, params: dict[str, Any]) -> tuple[str, int, str]:
        if endpoint not in ENDPOINTS or any(key in params for key in ('token', 'offset', 'limit')):
            raise ValueError('invalid endpoint or reserved parameters')
        serialized = canonical(params)
        api_name=provider_endpoint(endpoint)
        route='' if api_name==endpoint else '|provider='+api_name
        job = hashlib.sha256((endpoint + serialized + route).encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO ts_sync(job,endpoint,params_json,status,updated_at,provider_api) VALUES(?,?,?,\'pending\',?,?)', (job, endpoint, serialized, now, api_name))
            row = db.execute('SELECT offset,status FROM ts_sync WHERE job=?', (job,)).fetchone()
            return job, int(row['offset']), str(row['status'])

    def save_page(self, job: str, endpoint: str, offset: int, rows: list[dict[str, Any]], *, complete: bool) -> None:
        spec = ENDPOINTS[endpoint]
        now = datetime.now(UTC).isoformat()
        page_digest = hashlib.sha256(canonical(rows).encode()).hexdigest()
        prepared = []
        for row in rows:
            if 'token' in row:
                raise ValueError('provider row contains reserved token field')
            payload = canonical(row)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            # Incomplete business keys fall back to content identity, never collapse distinct rows.
            key = canonical({name: row.get(name) for name in spec.keys}) if all(row.get(name) is not None for name in spec.keys) else digest
            prepared.append((key, digest, row.get('ts_code'), row.get('ann_date'), row.get('trade_date') or row.get('end_date') or row.get('date'), now, payload))
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT offset FROM ts_sync WHERE job=?', (job,)).fetchone()
            if state is None or state['offset'] != offset:
                raise ValueError('checkpoint_conflict')
            if rows and db.execute('SELECT 1 FROM ts_pages WHERE job=? AND digest=?', (job, page_digest)).fetchone():
                raise ValueError('provider_ignored_pagination')
            db.executemany(f'INSERT OR IGNORE INTO ts_{endpoint} VALUES(?,?,?,?,?,?,?)', prepared)
            db.execute('INSERT INTO ts_pages VALUES(?,?,?,?)', (job, offset, page_digest, len(rows)))
            db.execute('UPDATE ts_sync SET offset=?,status=?,error=NULL,pages=pages+1,updated_at=? WHERE job=?', (offset + len(rows), 'complete' if complete else 'partial', now, job))

    def fail(self, job: str, category: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute('UPDATE ts_sync SET status=\'failed\',error=?,updated_at=? WHERE job=? AND status<>\'complete\'', (category, datetime.now(UTC).isoformat(), job))

    def claim(self, job: str, owner: str) -> bool:
        import time
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner,expires FROM ts_job_leases WHERE job=?', (job,)).fetchone()
            if row and row['expires'] > time.time() and row['owner'] != owner:
                return False
            db.execute('INSERT OR REPLACE INTO ts_job_leases VALUES(?,?,?)', (job, owner, time.time()+180))
            return True

    def release(self, job: str, owner: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM ts_job_leases WHERE job=? AND owner=?', (job, owner))

    def refresh(self, job: str) -> None:
        """Explicit revision reconciliation; old source rows remain immutable."""
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM ts_pages WHERE job=?', (job,))
            db.execute("UPDATE ts_sync SET offset=0,pages=0,status='pending',error=NULL WHERE job=?", (job,))

    def report(self) -> dict[str, Any]:
        with closing(self.connect()) as db:
            counts = {name: db.execute(f'SELECT COUNT(*) FROM ts_{name}').fetchone()[0] for name in ENDPOINTS}
            jobs = [dict(row) for row in db.execute('SELECT job,endpoint,provider_api,offset,status,error,pages,updated_at FROM ts_sync ORDER BY updated_at DESC')]
        return {'counts': counts, 'jobs': jobs, 'storage': 'append_only_raw_revisions', 'historical_industry_pit_usable': False,
                'coverage_reconciled': False, 'reason': 'Pagination completion is not calendar/universe reconciliation; raw snapshots are not PIT features.'}

    def archive_quality(self, phase: int, report: dict[str, Any]) -> str:
        if phase not in (1,2,3):
            raise ValueError('phase must be 1..3')
        payload=canonical(report)
        digest=hashlib.sha256((str(phase)+payload).encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO ts_quality_artifacts VALUES(?,?,?,?)', (digest,phase,datetime.now(UTC).isoformat(),payload))
        return digest

    def quality_artifacts(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ts_quality_artifacts'").fetchone():
                return []
            # Keep every phase visible even when one phase produces many newer reports.
            rows: list[dict[str, Any]] = []
            for phase in (1,2,3):
                rows.extend(dict(row) for row in db.execute(
                    "SELECT digest,phase,created_at,json_extract(report_json,'$.kind') AS kind,"
                    "json_extract(report_json,'$.status') AS status FROM ts_quality_artifacts "
                    "WHERE phase=? ORDER BY created_at DESC,digest DESC LIMIT 10",(phase,)))
            return sorted(rows,key=lambda row:(row['created_at'],row['digest']),reverse=True)

    def quality_artifact(self, digest: str) -> dict[str, Any] | None:
        """Read one immutable shared report by its complete content digest."""
        if re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            return None
        with closing(self.connect()) as db:
            row = db.execute('SELECT phase,created_at,report_json FROM ts_quality_artifacts WHERE digest=?', (digest,)).fetchone()
        if row is None:
            return None
        return {'digest':digest,'phase':int(row['phase']),'created_at':row['created_at'],
                'report':json.loads(row['report_json']),'research_budget_used':False}
