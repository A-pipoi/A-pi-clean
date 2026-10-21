"""Manage second-factor enrollment inside the authenticated session."""
from __future__ import annotations

from typing import Any

from .auth import Auth, TokenClaims


def handle_second_factor(auth: Auth, path: str, payload: dict[str, Any], claims: TokenClaims, ip: str) -> tuple[int, dict[str, object], dict[str, str]]:
    if path == '/api/auth/2fa/status':
        return 200, {'ok': True, 'enabled': auth.second_factor.enabled(claims.sub)}, {}
    failure = auth.check_sensitive(claims, payload, ip)
    if failure:
        return (429 if failure == 'too_many_attempts' else 403), {'ok': False, 'error': failure}, {}
    if path == '/api/auth/2fa/setup':
        return 200, {'ok': True, **auth.second_factor.setup(claims.sub, claims.username)}, {}
    if path == '/api/auth/2fa/enable':
        if auth.second_factor.enabled(claims.sub):
            return 409, {'ok': False, 'error': 'totp_already_enabled'}, {}
        codes = auth.second_factor.enable(claims.sub, str(payload.get('otp') or ''))
        if codes is None:
            return 403, {'ok': False, 'error': 'totp_invalid'}, {}
        auth.store.audit(claims.sub, 'totp_enabled', claims.username, ip)
        return 200, {'ok': True, 'recovery_codes': codes}, {}
    if path == '/api/auth/2fa/disable':
        auth.second_factor.disable(claims.sub)
        auth.store.audit(claims.sub, 'totp_disabled', claims.username, ip)
        return 204, {}, {}
    return 404, {'ok': False, 'error': 'not_found'}, {}
