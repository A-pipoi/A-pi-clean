"""Owned acceptance child: real first page, hold lease at next page until killed."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.data.extension_scheduler import FINANCIAL_INDICATOR_FIELDS
from quant.data.extension_store import ExtensionStore, default_store_path
from quant.data.extension_sync import load_token, sync
from quant.data.extension_transport import query

store = ExtensionStore(default_store_path())
store.initialize()


def fetch(token, name, params):
    if params["offset"]:
        print("REAL_PAGE_COMMITTED", flush=True)
        while True:
            time.sleep(1)
    return query(token, name, params, fields=FINANCIAL_INDICATOR_FIELDS)


sync(
    store,
    "fina_indicator",
    {"period": "20240331"},
    load_token(),
    max_pages=2,
    fetch=fetch,
)
