"""Malformed upstream field values must not escape as raw TypeError."""
import io
import json
from unittest.mock import patch

import pytest

from quant.data.extension_transport import ProviderError, query


@pytest.mark.parametrize("fields", [[[]], [{}], [None], [7], ["code", "code"]])
def test_malformed_fields_raise_redacted_provider_error(fields):
    response = io.BytesIO(json.dumps({"code": 0, "data": {"fields": fields, "items": []}}).encode())
    with patch("quant.data.extension_transport._open_provider", return_value=response), patch(
        "quant.data.extension_transport.limited_tushare_call", side_effect=lambda fn, **kw: fn()
    ), pytest.raises(ProviderError, match="^malformed_response$"):
        query("synthetic-fixture-only", "daily", {}, attempts=1)
