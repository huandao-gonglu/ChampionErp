"""独立账号库；业务数据继续由各用户空间内的领域 Store 持有。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4

from erp_web.schemas.accounts import AccountError, AccountSession, SESSION_SECONDS


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise RuntimeError("账号数据库版本不受支持，请使用匹配的程序版本。")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','user')),
                    status TEXT NOT NULL CHECK(status IN ('enabled','disabled')),
                    workspace TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS initial_admin ON users(role) WHERE role='admin';
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf_token TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                PRAGMA user_version=1;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def admin(self):
        with self._connection() as db:
            row = db.execute("SELECT * FROM users WHERE role='admin'").fetchone()
            return dict(row) if row else None

    def users(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM users ORDER BY created_at,id")]

    def by_email(self, email):
        with self._connection() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return dict(row) if row else None

    def create_user(self, email, name, password_hash, *, admin=False):
        identity = uuid4().hex
        now = time.time()
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if admin:
                    existing = db.execute("SELECT * FROM users WHERE role='admin'").fetchone()
                    if existing:
                        return dict(existing)
                elif not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                    raise AccountError("请先通过部署命令初始化管理员。", status_code=503, error_code="ACCOUNT_NOT_INITIALIZED")
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)", (
                    identity, email, name, password_hash, "admin" if admin else "user",
                    "enabled", "." if admin else f"users/{identity}", now, now,
                ))
                return dict(db.execute("SELECT * FROM users WHERE id=?", (identity,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise AccountError("该邮箱已注册。", status_code=409, error_code="EMAIL_EXISTS") from exc

    def create_session(self, user_id, password_hash):
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE id=? AND status='enabled' AND password_hash=?",
                              (user_id, password_hash)).fetchone()
            if not user:
                raise AccountError("邮箱或密码错误，或账号已停用。", status_code=401, error_code="INVALID_CREDENTIALS")
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (digest, user_id, csrf, now, now + SESSION_SECONDS))
        return token, AccountSession(dict(user), digest, csrf, now + SESSION_SECONDS)

    def session(self, token):
        if not token or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connection() as db:
            row = db.execute("SELECT u.*,s.csrf_token,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires_at>? AND u.status='enabled'", (digest, time.time())).fetchone()
            if not row:
                return None
            user = dict(row)
            return AccountSession(user, digest, user.pop("csrf_token"), user.pop("expires_at"))

    def revoke_session(self, token_hash):
        with self._connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def session_valid(self, token_hash):
        with self._connection() as db:
            return db.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id "
                              "WHERE s.token_hash=? AND s.expires_at>? AND u.status='enabled'",
                              (token_hash, time.time())).fetchone() is not None

    def update_password(self, user_id, password_hash, *, expected_hash=None, ordinary_only=False):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or (ordinary_only and row["role"] != "user"):
                raise AccountError("只能管理普通用户。", status_code=403, error_code="FORBIDDEN")
            if expected_hash is not None and row["password_hash"] != expected_hash:
                raise AccountError("密码已变更，请重新登录。", status_code=401, error_code="SESSION_EXPIRED")
            db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, time.time(), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def rehash_password(self, user_id, old_hash, new_hash):
        with self._connection() as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=? AND password_hash=?", (new_hash, user_id, old_hash))

    def set_status(self, user_id, status):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE users SET status=?,updated_at=? WHERE id=? AND role='user'", (status, time.time(), user_id))
            if not result.rowcount:
                raise AccountError("只能管理普通用户。", status_code=403, error_code="FORBIDDEN")
            if status == "disabled":
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
