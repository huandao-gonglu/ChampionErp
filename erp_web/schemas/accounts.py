"""个人账号的公开形状与统一输入校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from erp_web.schemas.requests import RequestValidationError

PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
SESSION_SECONDS = 7 * 24 * 3600
SESSION_COOKIE = "champion_session"


class AccountError(RequestValidationError):
    pass


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise AccountError("请输入有效的登录邮箱。")
    return email


def validate_password(value: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        raise AccountError("密码长度须为 15 至 128 个字符。")
    return value


def validate_display_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise AccountError("显示名称须为 1 至 80 个字符。")
    return name


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: user[key] for key in (
        "id", "email", "display_name", "role", "status", "created_at", "updated_at",
    )}


@dataclass(frozen=True, repr=False)
class AccountSession:
    user: dict[str, Any]
    token_hash: str
    csrf_token: str
    expires_at: float
