"""Synthetic pagination worker for the real process-kill durability test."""
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from quant.data.extension_store import ExtensionStore
from quant.data.extension_sync import sync

store=ExtensionStore(sys.argv[1]); store.initialize()
def fetch(token,endpoint,params):
    if params['offset']==0:
        return [{'ts_code':str(i),'trade_date':'20260930'} for i in range(1000)]
    print('PAGE_COMMITTED',flush=True)
    time.sleep(60)
    return []
sync(store,'daily_basic',{'trade_date':'20260930'},'',fetch=fetch)
