// One-time mechanical wiring patch, emitted for apply_patch rather than writing source.
import fs from 'node:fs';
import path from 'node:path';
import { patchChanges } from './patch-diff.mjs';
const file=path.resolve('../a_share_quant/quant/api_server.py');
const old=fs.readFileSync(file,'utf8');
let next=old;
function replace(a,b) { if(!next.includes(a)) throw new Error(a); next=next.replaceAll(a,b); }
replace('from typing import Any, Sequence','from typing import Any, Sequence, cast\nfrom .api.auth import Auth\nfrom .api.auth_store import AuthStore\nfrom .api.user_scope import CURRENT_USERNAME, ScopedStoreProxy, UserScopedStores, user_context\nfrom .api.user_scheduler import UserPaperScheduler');
replace('PAPER_STORE = PaperStore(PAPER_DB_PATH)\nFACTOR_STORE = FactorRegistryStore(FACTOR_DB_PATH)\nRESEARCH_LEDGER = ResearchLedger(RESEARCH_DB_PATH, budget=20)', 'AUTH_DATA_ROOT = Path(os.getenv("A_PI_DATA_ROOT", str(Path(__file__).resolve().parents[1] / "data")))\nAUTH_STORE = AuthStore(AUTH_DATA_ROOT / "auth" / "users.sqlite3")\nAUTH = Auth(AUTH_STORE, AUTH_DATA_ROOT / "auth" / "auth_secret.key")\nUSER_STORES = UserScopedStores(AUTH_DATA_ROOT)\nPAPER_STORE = cast(PaperStore, ScopedStoreProxy(USER_STORES, "paper_store"))\nFACTOR_STORE = cast(FactorRegistryStore, ScopedStoreProxy(USER_STORES, "factor_store"))\nRESEARCH_LEDGER = cast(ResearchLedger, ScopedStoreProxy(USER_STORES, "ledger"))');
replace('PAPER_SCHEDULER: PaperScheduler | None = None','PAPER_SCHEDULER: UserPaperScheduler | None = None\n\ndef _current_paper_scheduler() -> PaperScheduler | None:\n    username = CURRENT_USERNAME.get()\n    return PAPER_SCHEDULER.for_user(username) if PAPER_SCHEDULER and username else None');
replace('scheduler=lambda: PAPER_SCHEDULER','scheduler=_current_paper_scheduler');
replace('PAPER_SCHEDULER=PAPER_SCHEDULER,','PAPER_SCHEDULER=_current_paper_scheduler(),');
replace('return RUNTIME_CACHES.workspace.response(_build_workspace_snapshot)','return USER_STORES.current().workspace_cache.response(_build_workspace_snapshot)');
replace('        FACTOR_DB_PATH=FACTOR_DB_PATH,','        FACTOR_DB_PATH=USER_STORES.current().factor_store.path,');
replace('        PAPER_DB_PATH=PAPER_DB_PATH,','        PAPER_DB_PATH=USER_STORES.current().paper_store.path,');
replace('        RESEARCH_DB_PATH=RESEARCH_DB_PATH,','        RESEARCH_DB_PATH=USER_STORES.current().ledger.path,');
replace('        BACKUP_DIR=BACKUP_DIR,','        BACKUP_DIR=USER_STORES.current().paper_store.path.parent / "backups",');
replace('    return BACKTEST_JOB_MANAGER.submit(payload)','    username = CURRENT_USERNAME.get()\n    if username is None:\n        raise RuntimeError("authenticated owner required")\n    with BACKTEST_JOBS_LOCK:\n        return BACKTEST_JOB_MANAGER.submit({**payload, "_auth_owner": username})');
replace('class ApiHandler(HttpApiHandler):','class ApiHandler(HttpApiHandler):\n    def _auth(self) -> Auth:\n        return AUTH\n');
replace('        RESEARCH_LEDGER.recover_backtest_jobs()','        for user in AUTH_STORE.list_users():\n            if not user.disabled:\n                USER_STORES.get(user.username).ledger.recover_backtest_jobs()');
const start=next.indexOf('        PAPER_SCHEDULER = PaperScheduler(');
const end=next.indexOf('        PAPER_SCHEDULER.start()', start);
if(start<0||end<0)throw new Error('scheduler assembly not found');
const factory=next.slice(start,end).replace('PAPER_SCHEDULER = PaperScheduler(', 'return PaperScheduler(').split('\n').map(line=>line?'    '+line:line).join('\n');
next=next.slice(0,start)+'        def scheduler_for_user(username: str) -> PaperScheduler:\n'+factory+'        PAPER_SCHEDULER = UserPaperScheduler(AUTH_STORE, USER_STORES, scheduler_for_user)\n'+next.slice(end);
replace('        RUNTIME_CACHES.close()','        RUNTIME_CACHES.close()\n        USER_STORES.close()');
console.log(JSON.stringify('*** Begin Patch\n*** Update File: '+file.replaceAll('\\','/')+'\n'+patchChanges(old,next)+'*** End Patch'));
