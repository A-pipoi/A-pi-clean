"""Account endpoint dispatcher. HTTP method, CSRF and optional TOTP gates belong to the caller.

GET /api/auth/sessions and /api/admin/users; POST all other supported paths.
Admin mutations retain session and role checks without a second credential prompt.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .auth import Auth, TokenClaims
from .auth_routes import session_cookie
from .auth_store import User
from .invitation_routes import invite_response


def _public_user(user: User) -> dict[str, object]:
    return {"id": user.id, "username": user.username, "role": user.role,
            "created_at": user.created_at, "disabled": bool(user.disabled),
            "must_change_password": bool(user.must_change_password)}


def handle_account_request(auth: Auth, path: str, payload: dict[str, Any], claims: TokenClaims | None,
                           ip: str, user_agent: str, method: str = 'GET', *, secure: bool | None = None) -> tuple[int, dict[str, object], dict[str, str]]:
    """Dispatch authenticated requests; use username (or user_id) for admin targets."""
    headers = {"Cache-Control": "no-store"}

    def error(status: int, code: str) -> tuple[int, dict[str, object], dict[str, str]]:
        return status, {"ok": False, "error": code}, headers

    if claims is None:
        return error(401, "unauthorized")
    actor = auth.store.get_by_id(claims.sub)
    if (actor is None or actor.disabled or actor.username != claims.username
            or actor.session_version != claims.ver or actor.role != claims.role):
        return error(401, "unauthorized")
    if actor.must_change_password:
        return error(403, 'password_change_required')
    if path == "/api/auth/sessions":
        return 200, {"ok": True, "sessions": auth.store.list_sessions(actor.id, claims.jti)}, headers
    if path in {"/api/auth/sessions/revoke", "/api/auth/revoke", '/api/auth/revoke-session'}:
        jti = payload.get("jti")
        if not isinstance(jti, str) or not 1 <= len(jti) <= 256:
            return error(422, "invalid_session")
        if not auth.store.revoke_owned_session(actor.id, jti):
            return error(404, "session_not_found")
        auth.store.audit(actor.id, "session.revoke", jti, ip, user_agent)
        if jti == claims.jti:
            headers["Set-Cookie"] = session_cookie("", 0, secure=secure)
        return 200, {"ok": True}, headers
    if path in {"/api/auth/sessions/revoke-all", "/api/auth/revoke-all"}:
        auth.store.reset_sessions(actor.username)
        auth.store.audit(actor.id, "session.revoke_all", actor.username, ip, user_agent)
        headers["Set-Cookie"] = session_cookie("", 0, secure=secure)
        return 200, {"ok": True}, headers
    if not path.startswith("/api/admin/"):
        return error(404, "not_found")
    if actor.role != "admin":
        return error(403, "forbidden")
    if path in {'/api/admin/invites', '/api/admin/invites/revoke', '/api/admin/invites/list'}:
        return invite_response(auth, path, payload, claims, ip, user_agent, method)
    if path == "/api/admin/users" and method == 'GET':
        return 200, {"ok": True, "users": [_public_user(user) for user in auth.store.list_users()]}, headers
    action = path.removeprefix("/api/admin/users/")
    if path == '/api/admin/users' and method == 'POST':
        action = 'create'
    if path == '/api/admin/users/action':
        action = str(payload.get('action') or '')
    if action not in {"create", "disable", "enable", "role", "reset-sessions"}:
        return error(404, "not_found")
    sensitive_error = auth.check_sensitive(claims, payload, ip)
    if sensitive_error:
        auth.store.audit(actor.id, "admin." + action, "", ip, user_agent, False)
        return error(429 if sensitive_error == "too_many_attempts" else 403, sensitive_error)
    target: User | None = None
    username = payload.get("username")
    if action != "create":
        if isinstance(username, str):
            target = auth.store.get_by_username(username)
        elif type(payload.get("user_id")) is int:
            target = auth.store.get_by_id(payload["user_id"])
        else:
            return error(422, "invalid_target")
        if target is None:
            return error(404, "user_not_found")
        username = target.username
    if not isinstance(username, str):
        return error(422, "invalid_target")
    try:
        if action == "create":
            new_password = payload.get("password")
            role = payload.get("role", "researcher")
            if not isinstance(username, str) or not isinstance(new_password, str) or not isinstance(role, str):
                return error(422, "invalid_user")
            target = auth.store.create_user(username, new_password, role, must_change_password=True)
        elif action in {"disable", "enable"}:
            auth.store.set_disabled(username, action == "disable")
        elif action == "role":
            role = payload.get("role")
            if not isinstance(role, str):
                return error(422, "invalid_role")
            auth.store.set_role(username, role)
        else:
            auth.store.reset_sessions(username)
    except sqlite3.IntegrityError:
        return error(409, "user_exists")
    except ValueError as exc:
        auth.store.audit(actor.id, "admin." + action, str(username), ip, user_agent, False)
        if str(exc) == "cannot remove last enabled admin":
            return error(409, "last_enabled_admin")
        if str(exc) in {'password_weak', 'password_length'}:
            return error(422, str(exc))
        return error(422, "invalid_user")
    auth.store.audit(actor.id, "admin." + action, str(username), ip, user_agent)
    target = auth.store.get_by_username(username)
    if target is not None and target.id == actor.id and target.session_version != claims.ver:
        headers["Set-Cookie"] = session_cookie("", 0, secure=secure)
    return (201 if action == "create" else 200), {"ok": True, "user": _public_user(target) if target else None}, headers
