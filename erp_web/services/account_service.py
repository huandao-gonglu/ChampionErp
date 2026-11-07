"""账号业务、密码库和有界时间窗口限流。"""
from collections import OrderedDict, deque
from threading import RLock
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from erp_web.schemas.accounts import (
    AccountError, normalize_email, validate_password, validate_display_name, public_user,
)


class AccountService:
    def __init__(self, store):
        self.store = store
        self.hasher = PasswordHasher()
        self._dummy_hash = self.hasher.hash("仅用于未知邮箱的恒定密码校验")
        self._attempts = OrderedDict()
        self._lock = RLock()

    def limit(self, action, email, ip):
        now = time.monotonic()
        with self._lock:
            for key, maximum in ((f"{action}:ip:{ip}", 40), (f"{action}:email:{str(email).strip().casefold()[:254]}", 10)):
                items = self._attempts.setdefault(key, deque())
                self._attempts.move_to_end(key)
                while items and items[0] <= now - 600:
                    items.popleft()
                if len(items) >= maximum:
                    raise AccountError("尝试次数过多，请稍后再试。", status_code=429, error_code="ACCOUNT_RATE_LIMITED")
                items.append(now)
            while len(self._attempts) > 10000:
                self._attempts.popitem(last=False)

    def create(self, body, *, admin=False, registration_digest=None):
        email = normalize_email(body["email"])
        name = validate_display_name(body["display_name"])
        password = validate_password(body["password"])
        return public_user(self.store.create_user(email, name, self.hasher.hash(password), admin=admin, registration_digest=registration_digest))

    def login(self, body):
        email = normalize_email(body["email"])
        password = body["password"]
        # 登录也限制输入大小；密码按原文校验，允许空格及密码管理器粘贴。
        if not 1 <= len(password) <= 128:
            raise AccountError("邮箱或密码错误，或账号已停用。", status_code=401, error_code="INVALID_CREDENTIALS")
        user = self.store.by_email(email)
        try:
            self.hasher.verify(user["password_hash"] if user else self._dummy_hash, password)
        except (VerificationError, InvalidHashError):
            user = None
        if not user or user["status"] != "enabled":
            raise AccountError("邮箱或密码错误，或账号已停用。", status_code=401, error_code="INVALID_CREDENTIALS")
        if self.hasher.check_needs_rehash(user["password_hash"]):
            updated = self.hasher.hash(password)
            self.store.rehash_password(user["id"], user["password_hash"], updated)
            user["password_hash"] = updated
        return self.store.create_session(user["id"], user["password_hash"])

    def change_password(self, session, body):
        validate_password(body["password"])
        if not 1 <= len(body["current_password"]) <= 128:
            raise AccountError("原密码不正确。", error_code="INVALID_PASSWORD")
        try:
            self.hasher.verify(session.user["password_hash"], body["current_password"])
        except (VerificationError, InvalidHashError) as exc:
            raise AccountError("原密码不正确。", error_code="INVALID_PASSWORD") from exc
        self.store.update_password(session.user["id"], self.hasher.hash(body["password"]), expected_hash=session.user["password_hash"])

    def reset_password(self, body):
        self.store.update_password(body["user_id"], self.hasher.hash(validate_password(body["password"])), ordinary_only=True)
