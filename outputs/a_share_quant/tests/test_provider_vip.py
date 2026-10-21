import hashlib
import json
import sqlite3
from unittest.mock import patch

import pytest

from quant.data.extension_store import ExtensionStore, canonical
from quant.data.extension_sync import sync
from quant.data.extension_transport import ProviderError, query
from quant.data.provider_endpoints import VIP_ENDPOINTS, provider_endpoint


@pytest.mark.parametrize("logical,physical", VIP_ENDPOINTS.items())
def test_transport_uses_verified_vip_names_without_changing_storage(
    tmp_path, logical, physical
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            return b'{"code":0,"data":{"fields":["ts_code","ann_date","end_date"],"items":[["a","20240801","20240630"]]}}'

    store = ExtensionStore(tmp_path / "raw.sqlite3")
    store.initialize()
    with (
        patch(
            "quant.data.extension_transport._open_provider", return_value=Response()
        ) as request,
        patch(
            "quant.data.extension_transport.limited_tushare_call",
            side_effect=lambda f, **kw: f(),
        ),
    ):
        result = sync(store, logical, {"period": "20240630"}, "secret")
    assert json.loads(request.call_args.args[0].data)["api_name"] == physical
    if logical == "fina_indicator":
        projection = json.loads(request.call_args.args[0].data)["fields"].split(",")
        assert {
            "roe",
            "netprofit_yoy",
            "tr_yoy",
            "debt_to_assets",
            "ocf_to_or",
        }.issubset(projection)
    assert result["provider_api"] == physical
    assert result["logical_endpoint"] == logical
    assert store.report()["counts"][logical] == 1
    assert store.report()["jobs"][0]["provider_api"] == physical
    with sqlite3.connect(store.path) as db:
        assert "secret" not in str(db.execute("SELECT * FROM ts_sync").fetchall())


def test_old_completed_job_never_masks_new_route_and_old_rows_are_kept(tmp_path):
    store = ExtensionStore(tmp_path / "raw.sqlite3")
    store.initialize()
    params = {"period": "20240630"}
    old_job = hashlib.sha256(
        ("fina_indicator" + canonical(params)).encode()
    ).hexdigest()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO ts_sync(job,endpoint,params_json,status,updated_at,provider_api) VALUES(?,?,?,'complete','2024','fina_indicator')",
            (old_job, "fina_indicator", canonical(params)),
        )
    called = []
    result = sync(
        store,
        "fina_indicator",
        params,
        "",
        fetch=lambda *args: called.append(args) or [],
    )
    assert called and result["job"] != old_job
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT status FROM ts_sync WHERE job=?", (old_job,)).fetchone()[
                0
            ]
            == "complete"
        )
    assert provider_endpoint("fina_audit") == "fina_audit"
    assert provider_endpoint("fund_portfolio") == "fund_portfolio"


def test_vip_denial_is_not_silently_downgraded():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            return b'{"code":-1,"msg":"permission denied"}'

    with (
        patch(
            "quant.data.extension_transport._open_provider", return_value=Response()
        ) as request,
        patch(
            "quant.data.extension_transport.limited_tushare_call",
            side_effect=lambda f, **kw: f(),
        ),
        pytest.raises(ProviderError, match="permission_denied"),
    ):
        query("secret", "fina_indicator", {"period": "20240630"})
    assert request.call_count == 1
