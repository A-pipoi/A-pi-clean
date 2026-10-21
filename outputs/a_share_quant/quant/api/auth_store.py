"""Short-connection user store; credentials and revocation survive restarts."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .credential_policy import validate_password
from .private_files import protect_private_path
from .sqlite_support import connect_sqlite

ITERATIONS = 600_000


def _protect_database_files(path: Path) -> None:
    for filename in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')):
        if filename.exists():
            try:
                protect_private_path(filename)
            except FileNotFoundError:
                # SQLite can unlink sidecars when the last connection closes.
                # Recreated files inherit the already-private directory ACL.
                if filename == path:
                    raise


def validate_username(username: str) -> str:
    if not isinstance(username, str) or not re.fullmatch(r"[a-z0-9_-]{3,32}", username):
        raise ValueError("username must match [a-z0-9_-]{3,32}")
    if username in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise ValueError("username is a reserved Windows device name")
    return username


def password_hash(password: str, *, salt: bytes | None = None) -> str:
    validate_password(password)
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2$sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        kind, algorithm, iterations, salt, expected = encoded.split("$")
        if kind != "pbkdf2" or algorithm != "sha256" or int(iterations) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str
    role: str
    created_at: str
    disabled: int
    session_version: int
    must_change_password: int = 0


class AuthStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        protect_private_path(self.path.parent)
        _protect_database_files(self.path)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','researcher')),
                created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
                session_version INTEGER NOT NULL DEFAULT 0)""")
            connection.execute("CREATE TABLE IF NOT EXISTS revoked_sessions(jti TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS sessions(
                jti TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
                ip TEXT NOT NULL, user_agent TEXT NOT NULL, session_version INTEGER NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id)")
            session_columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "remembered" not in session_columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN remembered INTEGER NOT NULL DEFAULT 0 CHECK(remembered IN (0,1))")
            connection.execute("""CREATE TABLE IF NOT EXISTS security_audit(
                id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, actor_id INTEGER,
                action TEXT NOT NULL, target TEXT NOT NULL, ip TEXT NOT NULL,
                user_agent TEXT NOT NULL, success INTEGER NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS invites(
                code_hash TEXT PRIMARY KEY, created_by INTEGER NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL,
                used_at REAL, used_by INTEGER)""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            if "session_version" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
            if "must_change_password" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        _protect_database_files(self.path)

    def _connect(self) -> sqlite3.Connection:
        connection = connect_sqlite(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_user(self, username: str, password: str, role: str | None = None, *, must_change_password: bool = False) -> User:
        validate_username(username)
        hashed = password_hash(password)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            selected_role = role or ("admin" if not connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() else "researcher")
            if selected_role not in {"admin", "researcher"}:
                raise ValueError("invalid role")
            connection.execute("INSERT INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,?,?,?)", (username, hashed, selected_role, datetime.now(UTC).isoformat(), int(must_change_password)))
        user = self.get_by_username(username)
        assert user is not None
        return user

    def get_by_username(self, username: str) -> User | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return User(**dict(row)) if row else None

    def create_invite(self, actor_id: int) -> tuple[str, float]:
        code = secrets.token_hex(8)
        now = time.time()
        expires = now + 7 * 86400
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            actor = connection.execute('SELECT role,disabled FROM users WHERE id=?', (actor_id,)).fetchone()
            if not actor or actor['role'] != 'admin' or actor['disabled']:
                raise PermissionError('forbidden')
            count = connection.execute('SELECT COUNT(*) FROM invites WHERE created_by=? AND used_at IS NULL AND expires_at>?', (actor_id, now)).fetchone()[0]
            if count >= 20:
                raise ValueError('invite_limit')
            connection.execute('INSERT INTO invites(code_hash,created_by,created_at,expires_at) VALUES(?,?,?,?)', (hashlib.sha256(code.encode()).hexdigest(), actor_id, now, expires))
        return code, expires

    def list_invites(self) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute('SELECT substr(code_hash,1,8) AS code_hash,created_by,created_at,expires_at,used_at,used_by FROM invites ORDER BY created_at DESC')]

    def revoke_invite(self, code_hash: str) -> bool:
        if not re.fullmatch(r'(?:[0-9a-f]{8}|[0-9a-f]{64})', code_hash):
            raise ValueError('invalid_request')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            matches = connection.execute('SELECT code_hash FROM invites WHERE substr(code_hash,1,?)=?', (len(code_hash), code_hash)).fetchall()
            if len(matches) > 1:
                raise ValueError('invite_ambiguous')
            if not matches:
                return False
            return connection.execute('DELETE FROM invites WHERE code_hash=? AND used_at IS NULL', (matches[0]['code_hash'],)).rowcount == 1

    def register_user(self, username: str, password: str, code_hash: str) -> User:
        validate_username(username)
        hashed = password_hash(password)
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            now = time.time()
            invite = connection.execute('SELECT code_hash FROM invites WHERE code_hash=? AND used_at IS NULL AND expires_at>?', (code_hash, now)).fetchone()
            if not invite:
                raise ValueError('invite_invalid')
            cursor = connection.execute("INSERT INTO users(username,password_hash,role,created_at,must_change_password) VALUES(?,?,'researcher',?,0)", (username, hashed, datetime.now(UTC).isoformat()))
            connection.execute('UPDATE invites SET used_at=?,used_by=? WHERE code_hash=?', (now, cursor.lastrowid, code_hash))
        user = self.get_by_username(username)
        assert user is not None
        return user

    def get_by_id(self, user_id: int) -> User | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return User(**dict(row)) if row else None

    def list_users(self) -> list[User]:
        with closing(self._connect()) as connection:
            return [User(**dict(row)) for row in connection.execute("SELECT * FROM users ORDER BY id")]

    def set_password(self, username: str, password: str, *, must_change_password: bool = False) -> None:
        self._update(username, "password_hash=?, must_change_password=?, session_version=session_version+1", (password_hash(password), int(must_change_password)))

    def set_disabled(self, username: str, disabled: bool) -> None:
        if type(disabled) is not bool:
            raise ValueError("disabled must be boolean")
        self._change_access(username, disabled=disabled)

    def set_role(self, username: str, role: str) -> None:
        if role not in {"admin", "researcher"}:
            raise ValueError("invalid role")
        self._change_access(username, role=role)

    def _change_access(self, username: str, *, disabled: bool | None = None, role: str | None = None) -> None:
        validate_username(username)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if user is None:
                raise ValueError("user not found")
            next_role = role if role is not None else user["role"]
            next_disabled = int(disabled) if disabled is not None else user["disabled"]
            if (user["role"] == "admin" and not user["disabled"] and (next_role != "admin" or next_disabled)
                    and connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0] <= 1):
                raise ValueError("cannot remove last enabled admin")
            connection.execute("UPDATE users SET role=?, disabled=?, session_version=session_version+1 WHERE id=?",
                               (next_role, next_disabled, user["id"]))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))

    def register_session(self, jti: str, user_id: int, expires_at: int, ip: str, user_agent: str, *, remembered: bool = False) -> None:
        """Register once; duplicate JTIs never resurrect or transfer a session."""
        now = int(time.time())
        if not isinstance(jti, str) or not 1 <= len(jti) <= 256 or type(expires_at) is not int or type(remembered) is not bool:
            raise ValueError("invalid session")
        # Legacy callers issue expired tokens to test verification; they never
        # become valid registry entries or consume an active-session slot.
        if expires_at <= now:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None or user["disabled"] or connection.execute("SELECT 1 FROM revoked_sessions WHERE jti=?", (jti,)).fetchone():
                raise ValueError("invalid session owner or revoked session")
            connection.execute("INSERT INTO sessions(jti,user_id,expires_at,created_at,last_seen,ip,user_agent,session_version,remembered) VALUES(?,?,?,?,?,?,?,?,?)",
                               (jti, user_id, expires_at, now, now, str(ip)[:256], str(user_agent)[:1024], user["session_version"], int(remembered)))
            excess = connection.execute("""SELECT jti,expires_at FROM sessions WHERE user_id=?
                ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET 12""", (user_id,)).fetchall()
            for old in excess:
                connection.execute("INSERT OR REPLACE INTO revoked_sessions VALUES(?,?)", (old["jti"], old["expires_at"]))
                connection.execute("DELETE FROM sessions WHERE jti=?", (old["jti"],))

    def check_session(self, jti: str, user_id: int, now: float, idle_seconds: int = 1800, touch: bool = False, *, remembered: bool | None = None) -> bool:
        """Fail closed for unregistered sessions; expiry/idle boundaries are exclusive."""
        if idle_seconds <= 0:
            return False
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE" if touch else "BEGIN")
            row = connection.execute("""SELECT s.last_seen,s.remembered FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.jti=? AND s.user_id=? AND s.expires_at>? AND (s.remembered=1 OR s.last_seen>?)
                AND u.disabled=0 AND s.session_version=u.session_version
                AND NOT EXISTS(SELECT 1 FROM revoked_sessions r WHERE r.jti=s.jti)""",
                                     (jti, user_id, now, now - idle_seconds)).fetchone()
            if row is None or (remembered is not None and bool(row['remembered']) != remembered):
                return False
            if touch:
                connection.execute("UPDATE sessions SET last_seen=MAX(last_seen,?) WHERE jti=?", (int(now), jti))
            return True

    def list_sessions(self, user_id: int, current_jti: str) -> list[dict[str, object]]:
        now = int(time.time())
        with closing(self._connect()) as connection:
            rows = connection.execute("""SELECT s.jti,s.expires_at,s.created_at,s.last_seen,s.ip,s.user_agent
                FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.user_id=? AND s.expires_at>?
                AND (s.remembered=1 OR s.last_seen>?) AND u.disabled=0 AND s.session_version=u.session_version
                AND NOT EXISTS(SELECT 1 FROM revoked_sessions r WHERE r.jti=s.jti)
                ORDER BY s.last_seen DESC,s.jti""", (user_id, now, now - 1800))
            result = []
            for row in rows:
                item = dict(row, current=row["jti"] == current_jti)
                for field in ("created_at", "last_seen", "expires_at"):
                    item[field] = datetime.fromtimestamp(item[field], UTC).isoformat()
                result.append(item)
            return result

    def revoke_owned_session(self, user_id: int, jti: str) -> bool:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT expires_at FROM sessions WHERE user_id=? AND jti=?", (user_id, jti)).fetchone()
            if row is None:
                return False
            connection.execute("INSERT OR REPLACE INTO revoked_sessions VALUES(?,?)", (jti, row["expires_at"]))
            connection.execute("DELETE FROM sessions WHERE jti=?", (jti,))
            return True

    def purge_security_records(self) -> None:
        """Remove expired registry/revocation entries; retain audit history."""
        now = int(time.time())
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE expires_at<=? OR (remembered=0 AND last_seen<=?)", (now, now - 1800))
            connection.execute("DELETE FROM revoked_sessions WHERE expires_at<=?", (now,))

    def audit(self, actor_id: int | None, action: str, target: str = "", ip: str = "", user_agent: str = "", success: bool = True) -> None:
        """Only supply action/target identifiers, never credentials or payloads."""
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO security_audit(created_at,actor_id,action,target,ip,user_agent,success) VALUES(?,?,?,?,?,?,?)",
                               (int(time.time()), actor_id, action[:128], target[:256], ip[:256], user_agent[:1024], int(success)))

    def reset_sessions(self, username: str) -> None:
        self._update(username, "session_version=session_version+1", ())

    def revoke_session(self, jti: str, expires_at: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM revoked_sessions WHERE expires_at <= strftime('%s','now')")
            connection.execute("INSERT OR REPLACE INTO revoked_sessions VALUES(?,?)", (jti, expires_at))
            connection.execute("DELETE FROM sessions WHERE jti=?", (jti,))

    def session_revoked(self, jti: str) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute("SELECT 1 FROM revoked_sessions WHERE jti=?", (jti,)).fetchone() is not None

    def _update(self, username: str, sql: str, values: tuple[object, ...]) -> None:
        validate_username(username)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(f"UPDATE users SET {sql} WHERE username=?", (*values, username)).rowcount != 1:
                raise ValueError("user not found")
            connection.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (username,))
