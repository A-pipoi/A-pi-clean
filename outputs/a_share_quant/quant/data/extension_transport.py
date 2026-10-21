"""Standard-library Tushare HTTP transport. Never logs a token or response error text."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from quant.api.resource_limits import limited_tushare_call

from .provider_endpoints import provider_endpoint

LOG = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class ProviderError(RuntimeError):
    def __init__(self, category: str, code: object = None) -> None:
        self.category = category
        # Provider messages can echo request data; do not retain them.
        super().__init__(category)


class _NoProviderRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A POST body contains the token. Never replay it at a redirect target.
        raise ProviderError("provider_redirect_rejected")


def _provider_url() -> str:
    url = os.getenv("A_PI_TUSHARE_API_URL", "https://api.tushare.pro")
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and parts.hostname
            and re.fullmatch(r"[a-zA-Z0-9.:-]+", parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and not any(
                char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url
            )
            and (parts.port is None or 1 <= parts.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        # Do not expose an invalid configured URL: it can itself contain secrets.
        raise ProviderError("insecure_provider_url")
    return url


def _open_provider(request: Request, *, timeout: int):
    # Fresh opener avoids a process-global opener with a different redirect policy.
    # HTTPS certificate verification remains enabled, including custom HTTPS hosts.
    return build_opener(_NoProviderRedirect()).open(request, timeout=timeout)


def query(
    token: str,
    endpoint: str,
    params: dict[str, Any],
    *,
    fields: str = "",
    attempts: int = 3,
) -> list[dict[str, Any]]:
    if not token:
        raise ProviderError("token_unavailable")
    url = _provider_url()
    if not 1 <= attempts <= 5:
        raise ValueError("attempts must be 1..5")
    api_name = provider_endpoint(endpoint)
    if endpoint == "fina_indicator" and not fields:
        # Explicitly request fields omitted by the provider's default projection.
        # Keep the rest of the financial panel: templates need more than ROE.
        fields = "ts_code,ann_date,end_date,roe,netprofit_yoy,tr_yoy,debt_to_assets,eps,bps,roa,grossprofit_margin,netprofit_margin,ocfps,ocf_to_or,update_flag"
    body = json.dumps(
        {"api_name": api_name, "token": token, "params": params, "fields": fields}
    ).encode()
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            outbound_request = Request(
                url,
                body,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "A-pi/data-extension",
                },
            )

            def receive(request: Request = outbound_request) -> dict[str, Any]:
                with _open_provider(request, timeout=30) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ProviderError("response_too_large")
                try:
                    decoded = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise ProviderError("malformed_response") from None
                if not isinstance(decoded, dict):
                    raise ProviderError("malformed_response")
                return decoded

            payload = limited_tushare_call(receive, _rate_endpoint=api_name)
            if payload.get("code") != 0:
                message = str(payload.get("msg", "")).lower()
                if any(
                    word in message for word in ("权限", "积分", "permission", "token")
                ):
                    raise ProviderError("permission_denied")
                if any(
                    word in message
                    for word in ("频次", "每分钟", "rate limit", "too many")
                ):
                    raise ProviderError("rate_limited")
                raise ProviderError("provider_rejected")
            data = payload.get("data")
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("fields"), list)
                or not isinstance(data.get("items"), list)
            ):
                raise ProviderError("malformed_response")
            columns = data["fields"]
            if not all(isinstance(col, str) for col in columns) or len(
                set(columns)
            ) != len(columns):
                raise ProviderError("malformed_response")
            rows = []
            for row in data["items"]:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise ProviderError("malformed_response")
                rows.append(dict(zip(columns, row)))
            LOG.info(
                "tushare endpoint=%s logical=%s rows=%d elapsed=%.3f",
                api_name,
                endpoint,
                len(rows),
                time.monotonic() - started,
            )
            return rows
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise ProviderError("http_rejected") from None
            category = "rate_limited" if error.code == 429 else "network_unavailable"
        except (OSError, URLError, HTTPException):
            category = "network_unavailable"
        except ProviderError as error:
            if error.category != "rate_limited":
                raise
            category = error.category
        if attempt + 1 < attempts:
            time.sleep(min(8, 2**attempt))
    raise ProviderError(category)
