"""Administrator invitation operations; never list plaintext codes."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .auth import Auth, TokenClaims


def invite_response(auth: Auth, path: str, payload: dict[str, Any], claims: TokenClaims, ip: str, user_agent: str, method: str) -> tuple[int, dict[str, object], dict[str, str]]:
    headers = {'Cache-Control': 'no-store'}
    listing = (path == '/api/admin/invites' and method == 'GET') or (path == '/api/admin/invites/list' and method == 'POST')
    creating = path == '/api/admin/invites' and method == 'POST'
    revoking = path == '/api/admin/invites/revoke' and method == 'POST'
    if not (listing or creating or revoking):
        return 405, {'ok': False, 'error': 'method_not_allowed'}, headers
    action = 'admin.invite_list' if listing else 'admin.invite_create' if creating else 'admin.invite_revoke'
    failure = auth.check_sensitive(claims, payload, ip)
    if failure:
        auth.store.audit(claims.sub, action, '', ip, user_agent, False)
        return (429 if failure == 'too_many_attempts' else 403), {'ok': False, 'error': failure}, headers
    if listing:
        return 200, {'ok': True, 'invites': auth.store.list_invites()}, headers
    try:
        if creating:
            code, expires = auth.store.create_invite(claims.sub)
            auth.store.audit(claims.sub, action, '', ip, user_agent)
            return 201, {'ok': True, 'code': code, 'expires_at': datetime.fromtimestamp(expires, UTC).isoformat()}, headers
        code_hash = payload.get('code_hash')
        if not isinstance(code_hash, str):
            raise TypeError('invalid_request')
        revoked = auth.store.revoke_invite(code_hash)
        auth.store.audit(claims.sub, action, code_hash[:8], ip, user_agent, revoked)
        return (200 if revoked else 404), {'ok': revoked, **({} if revoked else {'error': 'invite_not_found'})}, headers
    except PermissionError:
        return 403, {'ok': False, 'error': 'forbidden'}, headers
    except (TypeError, ValueError) as error:
        auth.store.audit(claims.sub, action, '', ip, user_agent, False)
        return (409 if str(error) in {'invite_limit', 'invite_ambiguous'} else 422), {'ok': False, 'error': str(error)}, headers
