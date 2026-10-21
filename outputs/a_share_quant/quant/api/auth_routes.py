"""HTTP authentication endpoints. No business data is returned here."""
from __future__ import annotations

import ipaddress
import logging
import os
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from typing import Any

from .auth import Auth, TokenClaims
from .registration_routes import register_response


def client_ip(peer: str, forwarded: str | None) -> str:
    try:
        trusted = {value.strip() for value in os.getenv('A_PI_TRUSTED_PROXIES', '').split(',') if value.strip()}
        if peer in trusted and forwarded:
            candidate = forwarded.split(",")[-1].strip()
            return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    return peer


def session_cookie(token: str, ttl: int = 43_200, *, secure: bool | None = None) -> str:
    cookie = SimpleCookie()
    cookie["a_pi_session"] = token
    morsel = cookie["a_pi_session"]
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    morsel["path"] = "/"
    morsel["max-age"] = str(ttl)
    if secure is True or (secure is None and os.getenv("A_PI_SECURE_COOKIES") == "1"):
        morsel["secure"] = True
    return morsel.OutputString()


def auth_response(auth: Auth, path: str, payload: dict[str, Any], claims: TokenClaims | None, ip: str, logger: logging.Logger, user_agent: str = '', *, secure: bool | None = None) -> tuple[int, dict[str, object], dict[str, str]]:
    if path == '/api/auth/register':
        return register_response(auth, payload, ip, user_agent)
    if path == "/api/auth/login":
        if not auth.store.list_users():
            return 503, {"ok": False, "error": "auth_not_configured"}, {}
        username = str(payload.get("username") or "")[:128]
        password = payload.get("password")
        reservation, delay = auth.limits.reserve(username, ip)
        if not reservation:
            logger.warning("auth locked username=%r ip=%s", username, ip)
            auth.store.audit(None, 'login.rate_limited', username, ip, user_agent, success=False)
            return 429, {"ok": False, "error": "too_many_attempts"}, {"Retry-After": str(delay)}
        success = False
        challenge = False
        try:
            user = auth.verify_credentials(username, password if isinstance(password, str) else "")
            if not user:
                logger.warning("auth login failed username=%r ip=%s", username, ip)
                return 401, {"ok": False, "error": "invalid_credentials"}, {}
            if auth.second_factor.enabled(user.id):
                otp = str(payload.get('otp') or '')
                if not otp:
                    challenge = True
                    return 401, {'ok': False, 'error': 'totp_required'}, {}
                if not auth.second_factor.verify(user.id, otp):
                    return 401, {'ok': False, 'error': 'totp_invalid'}, {}
            ttl = 604_800 if payload.get("remember") is True else 43_200
            token = auth.issue_token(user, ttl, ip=ip, user_agent=user_agent, remembered=payload.get('remember') is True)
            success = True
        finally:
            auth.limits.finish(reservation, successful=success or challenge)
            auth.store.audit(None, 'login', username, ip, user_agent, success=success)
        logger.info("auth login successful username=%s ip=%s", user.username, ip)
        return 200, {"ok": True, "username": user.username, "role": user.role, 'must_change_password': bool(user.must_change_password)}, {"Set-Cookie": session_cookie(token, ttl, secure=secure)}
    if claims is None:
        return 401, {"ok": False, "error": "unauthorized"}, {}
    if path == "/api/auth/session":
        user = auth.store.get_by_id(claims.sub)
        return 200, {"ok": True, "authenticated": True, "username": claims.username, "role": claims.role, 'must_change_password': bool(user and user.must_change_password), "expires_at": datetime.fromtimestamp(claims.exp, UTC).isoformat()}, {}
    if path == "/api/auth/logout":
        auth.revoke(claims.jti, claims.exp)
        return 204, {}, {"Set-Cookie": session_cookie("", 0, secure=secure)}
    if path == "/api/auth/change-password":
        failure = auth.check_sensitive(claims, {**payload, 'current_password': payload.get('old_password')}, ip)
        if failure:
            auth.store.audit(claims.sub, 'password.change', claims.username, ip, user_agent, success=False)
            return (429 if failure == 'too_many_attempts' else 403), {'ok': False, 'error': failure}, {}
        try:
            new_password = payload.get("new_password")
            if not isinstance(new_password, str):
                return 422, {'ok': False, 'error': 'password_invalid'}, {}
            if new_password == payload.get('old_password'):
                return 422, {'ok': False, 'error': 'password_unchanged'}, {}
            auth.store.set_password(claims.username, new_password)
        except ValueError as error:
            return 422, {"ok": False, "error": str(error)}, {}
        auth.store.audit(claims.sub, 'password.change', claims.username, ip, user_agent)
        logger.info("auth password changed username=%s ip=%s", claims.username, ip)
        return 204, {}, {"Set-Cookie": session_cookie("", 0, secure=secure)}
    return 404, {"ok": False, "error": "not_found"}, {}
