"""管理网页到桌面的短期导航交接与页面恢复确认。"""
from dataclasses import dataclass
import json
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit

from erp_web.schemas.requests import RequestValidationError
from erp_web.schemas.api import DesktopHandoffCreated, DesktopHandoffResolved, DesktopHandoffStatus


@dataclass
class _Handoff:
    session_hash: str
    target: str
    state: dict
    expires: float
    connection: str = ''
    opened: bool = False


class DesktopHandoffs:
    TTL = 600
    MAX_PENDING = 20
    MAX_STATE_BYTES = 32 * 1024 * 1024

    def __init__(self, *, session_validator=None):
        self._lock = Lock()
        self._requests: dict[str, _Handoff] = {}
        self._session_validator = session_validator

    def create(self, session_hash, target, state) -> DesktopHandoffCreated:
        parsed = urlsplit(target)
        if (parsed.scheme or parsed.netloc or parsed.path not in {'/', '/aiWork'}
                or '\\' in target or any(ord(char) < 32 for char in target) or len(target) > 8192):
            raise ValueError('桌面目标页面无效。')
        if len(json.dumps(state, ensure_ascii=False).encode()) > self.MAX_STATE_BYTES:
            raise ValueError('待交接内容过大，请减少图片后重试。')
        with self._lock:
            now = time.monotonic()
            self._requests = {key: row for key, row in self._requests.items() if row.expires > now}
            if sum(not row.opened for row in self._requests.values()) >= self.MAX_PENDING:
                raise ValueError('待打开的页面过多，请稍后重试。')
            key = secrets.token_urlsafe(32)
            self._requests[key] = _Handoff(session_hash, target, state, now + self.TTL)
        return {'ok': True, 'request_id': key, 'expires_in': self.TTL}

    def _get(self, key):
        row = self._requests.get(key)
        if (row is None or row.expires <= time.monotonic()
                or (self._session_validator is not None and not self._session_validator(row.session_hash))):
            self._requests.pop(key, None)
            raise RequestValidationError('打开请求已过期或账号不匹配，请确认与网页登录同一账号后重试。', status_code=410)
        return row

    def status(self, key, session_hash) -> DesktopHandoffStatus:
        with self._lock:
            row = self._get(key)
            if row.session_hash != session_hash:
                raise RequestValidationError('打开请求不属于当前网页会话。', status_code=403)
            return {'ok': True, 'opened': row.opened}

    def resolve(self, key, connection) -> DesktopHandoffResolved:
        with self._lock:
            row = self._get(key)
            if row.connection and row.connection != connection:
                raise RequestValidationError('此请求已由另一个桌面窗口接收，请回网页重试。', status_code=409)
            row.connection = connection
            return {'ok': True, 'target': row.target, 'state': row.state, 'opened': row.opened}

    def acknowledge(self, key, connection):
        with self._lock:
            row = self._get(key)
            if row.connection != connection:
                raise RequestValidationError('桌面接收信息不匹配，请重新打开。', status_code=403)
            row.opened = True
            row.state = {}
            return {'ok': True}
