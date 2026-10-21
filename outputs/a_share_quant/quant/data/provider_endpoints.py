"""Verified 5000-point bulk aliases; storage names and PIT semantics stay stable."""
from __future__ import annotations

VIP_ENDPOINTS = {
    'fina_indicator': 'fina_indicator_vip',
    'income': 'income_vip',
    'balancesheet': 'balancesheet_vip',
    'cashflow': 'cashflow_vip',
    'forecast': 'forecast_vip',
    'express': 'express_vip',
}


def provider_endpoint(logical_endpoint: str) -> str:
    # Permission failures must be reported, never silently downgraded/retried.
    return VIP_ENDPOINTS.get(logical_endpoint, logical_endpoint)
