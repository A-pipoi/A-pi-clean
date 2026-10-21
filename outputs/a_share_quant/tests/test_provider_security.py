"""Credential destination and response guards. No real provider requests."""

import io
from unittest.mock import patch
from urllib.request import Request

import pytest

from quant.data import extension_transport as transport


@pytest.mark.parametrize(
    "url",
    [
        "http://api.tushare.pro",
        "file:///fixture",
        "https:///missing",
        "https://name:synthetic-secret@example.invalid",
        "https://@example.invalid",
        "https://example.invalid/?token=synthetic-secret",
        "https://example.invalid/#secret",
        "https://example.invalid:bad",
        "https://example.invalid:0",
        "https://example.invalid:65536",
        "https://example.invalid\n/path",
        " https://example.invalid",
        "https://bad\\host.invalid",
    ],
)
def test_invalid_destination_rejected_before_budget_or_network(monkeypatch, url):
    monkeypatch.setenv("A_PI_TUSHARE_API_URL", url)
    with (
        patch.object(transport, "limited_tushare_call") as budget,
        patch.object(transport, "_open_provider") as network,
        pytest.raises(transport.ProviderError, match="^insecure_provider_url$"),
    ):
        transport.query("fixture-only", "daily", {})
    budget.assert_not_called()
    network.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.tushare.pro",
        "https://approved.example.invalid/v1",
        "https://127.0.0.1:9443/api",
        "https://[::1]:9443/api",
    ],
)
def test_explicit_https_custom_routes_remain_supported(monkeypatch, url):
    monkeypatch.setenv("A_PI_TUSHARE_API_URL", url)
    assert transport._provider_url() == url


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "target",
    ["https://other.invalid", "http://api.tushare.pro", "https://api.tushare.pro/new"],
)
def test_all_redirects_rejected_without_replaying_token(code, target):
    request = Request("https://api.tushare.pro", data=b"synthetic-fixture-only")
    with pytest.raises(transport.ProviderError, match="^provider_redirect_rejected$"):
        transport._NoProviderRedirect().redirect_request(
            request, None, code, "", {}, target
        )


def test_fresh_opener_is_wired_with_redirect_guard():
    request = Request("https://api.tushare.pro", data=b"synthetic-fixture-only")
    with patch.object(transport, "build_opener") as factory:
        response = transport._open_provider(request, timeout=30)
    assert isinstance(factory.call_args.args[0], transport._NoProviderRedirect)
    factory.return_value.open.assert_called_once_with(request, timeout=30)
    assert response is factory.return_value.open.return_value


@pytest.mark.parametrize(
    "body,category",
    [
        (b"not-json synthetic-secret", "malformed_response"),
        (b"\xff", "malformed_response"),
        (b"[]", "malformed_response"),
        (b"x" * 33, "response_too_large"),
    ],
)
def test_invalid_body_redacted_and_not_retried(monkeypatch, body, category):
    monkeypatch.delenv("A_PI_TUSHARE_API_URL", raising=False)
    with (
        patch.object(transport, "MAX_RESPONSE_BYTES", 32),
        patch.object(
            transport, "_open_provider", return_value=io.BytesIO(body)
        ) as network,
        patch.object(
            transport, "limited_tushare_call", side_effect=lambda fn, **kw: fn()
        ),
        patch.object(transport.time, "sleep") as delay,
        pytest.raises(transport.ProviderError, match="^" + category + "$"),
    ):
        transport.query("synthetic-secret", "daily", {})
    network.assert_called_once()
    delay.assert_not_called()
