"""Anonymous invitation registration, sharing existing credential and IP limits."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from .auth import Auth


def register_response(auth: Auth, payload: dict[str, Any], ip: str, user_agent: str) -> tuple[int, dict[str, object], dict[str, str]]:
    username = str(payload.get('username') or '')[:128]
    reservation, delay = auth.limits.reserve(f'reg:{ip}', ip)
    if not reservation:
        return 429, {'ok': False, 'error': 'too_many_attempts'}, {'Retry-After': str(delay)}
    success = False
    try:
        code = payload.get('invite_code')
        if not isinstance(code, str) or not re.fullmatch('[0-9a-fA-F]{16}', code.strip()):
            return 403, {'ok': False, 'error': 'invite_invalid'}, {}
        password = payload.get('password')
        if not isinstance(password, str) or not isinstance(payload.get('username'), str):
            return 422, {'ok': False, 'error': 'invalid_request'}, {}
        auth.store.register_user(username, password, hashlib.sha256(code.strip().lower().encode()).hexdigest())
        success = True
        return 201, {'ok': True, 'username': username}, {}
    except sqlite3.IntegrityError:
        return 409, {'ok': False, 'error': 'user_exists'}, {}
    except ValueError as error:
        reason = str(error)
        if reason == 'invite_invalid':
            return 403, {'ok': False, 'error': reason}, {}
        return 422, {'ok': False, 'error': reason if reason in {'password_length', 'password_weak'} else 'invalid_request'}, {}
    finally:
        try:
            auth.store.audit(None, 'register', username, ip, user_agent, success)
        finally:
            auth.limits.finish(reservation, successful=success)
