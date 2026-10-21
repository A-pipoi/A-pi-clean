"""Standard-library signed sessions, persistent account checks and rate limits."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .auth_store import AuthStore, User, password_hash, password_matches
from .login_limits import LoginLimits
from .private_files import protect_private_path
from .two_factor import TwoFactor


@dataclass(frozen=True)
class TokenClaims:
    sub: int
    username: str
    role: str
    exp: int
    iat: int
    jti: str
    ver: int
    remembered: bool = False


class Auth:
    def __init__(self, store: AuthStore, secret_path: Path, *, secret: str | None = None) -> None:
        self.store = store
        supplied = secret if secret is not None else os.getenv("A_PI_AUTH_SECRET")
        if supplied is not None:
            if len(supplied.encode()) < 32:
                raise ValueError("A_PI_AUTH_SECRET must be at least 32 bytes")
            self.secret = supplied.encode()
        else:
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            protect_private_path(secret_path.parent)
            if not secret_path.exists():
                with tempfile.NamedTemporaryFile(dir=secret_path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(secrets.token_hex(32).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                    protect_private_path(temporary)
                try:
                    try:
                        os.link(temporary, secret_path)
                    except FileExistsError:
                        pass
                finally:
                    temporary.unlink()
            protect_private_path(secret_path)
            self.secret = secret_path.read_text().strip().encode()
            if len(self.secret) < 32:
                raise ValueError("invalid persisted authentication secret")
        self.lock = threading.Lock()
        self.limits = LoginLimits(store.path)
        self.second_factor = TwoFactor(store.path)
        self.dummy = password_hash("dummy-password-for-timing", salt=b"a-pi-fixed-dummy-salt-v1")
        self.maintenance_stop = threading.Event()
        self.maintenance_thread: threading.Thread | None = None

    def start_maintenance(self) -> None:
        def cleanup() -> None:
            while not self.maintenance_stop.wait(300):
                try:
                    self.store.purge_security_records()
                    self.limits.cleanup()
                    self.second_factor.cleanup()
                except Exception:
                    logging.getLogger('a_pi.auth').exception('Security record cleanup failed')
        with self.lock:
            if self.maintenance_thread is None:
                self.maintenance_thread = threading.Thread(target=cleanup, name='auth-cleanup', daemon=True)
                self.maintenance_thread.start()

    def stop_maintenance(self) -> None:
        self.maintenance_stop.set()
        if self.maintenance_thread:
            self.maintenance_thread.join(timeout=31)

    def verify_credentials(self, username: str, password: str) -> User | None:
        user = self.store.get_by_username(username)
        valid = password_matches(password if len(password) <= 1024 else "invalid-overlong-password", user.password_hash if user else self.dummy)
        return user if user and valid and not user.disabled else None

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    def issue_token(self, user: User, ttl: int = 43_200, *, ip: str = 'local', user_agent: str = '', remembered: bool = False) -> str:
        self.store.purge_security_records()
        now = int(time.time())
        jti = secrets.token_hex(24)
        self.store.register_session(jti, user.id, now + ttl, ip, user_agent[:240], remembered=remembered)
        payload = self._encode(json.dumps({'sub': user.id, 'username': user.username, 'role': user.role, 'exp': now + ttl, 'iat': now, 'jti': jti, 'ver': user.session_version, 'remembered': remembered}, separators=(",", ":")).encode())
        signature = self._encode(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        return payload + "." + signature

    def verify_token(self, token: str) -> TokenClaims | None:
        try:
            if len(token) > 4096:
                return None
            payload, signature = token.split(".")
            expected = self._encode(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            claims = TokenClaims(**raw)
            if not all(type(value) is int for value in (claims.sub, claims.exp, claims.iat, claims.ver)) or not isinstance(claims.jti, str) or type(claims.remembered) is not bool:
                return None
            if claims.exp <= time.time() or claims.iat > time.time() + 30:
                return None
            if self.store.session_revoked(claims.jti):
                return None
            user = self.store.get_by_id(claims.sub)
            if not user or user.disabled or user.username != claims.username or user.role != claims.role or user.session_version != claims.ver:
                return None
            if not self.store.check_session(claims.jti, claims.sub, time.time(), idle_seconds=1800, touch=False, remembered=claims.remembered):
                return None
            return claims
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None

    def revoke(self, jti: str, expires_at: int | None = None) -> None:
        self.store.revoke_session(jti, expires_at or int(time.time()) + 604_800)

    def revoke_all_for_user(self, sub: int) -> None:
        user = self.store.get_by_id(sub)
        if user:
            self.store.reset_sessions(user.username)

    def locked_seconds(self, username: str, ip: str) -> int:
        return self.limits.locked_seconds(username, ip)

    def failed(self, username: str, ip: str) -> None:
        self.limits.failed(username, ip)

    def check_sensitive(self, claims: TokenClaims, payload: dict[str, object], ip: str) -> str | None:
        """Keep sensitive actions inside the authenticated session without step-up prompts.

        Callers still enforce session validity, role/ownership, CSRF, input validation,
        audit logging, and operation-specific proofs such as a TOTP when enrolling it.
        """
        return None
