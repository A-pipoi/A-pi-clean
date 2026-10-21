import json
from contextlib import closing

from quant.data.extension_store import ExtensionStore, default_store_path

store = ExtensionStore(default_store_path())
with closing(store.connect()) as db:
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT endpoint,params_json,offset,status,pages,error,updated_at FROM ts_sync WHERE endpoint='fund_portfolio' AND params_json LIKE '%period%' ORDER BY updated_at DESC LIMIT 15"
        )
    ]
    print(json.dumps({"fund_jobs": rows}, ensure_ascii=False))
