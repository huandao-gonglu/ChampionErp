"""当前桌面连接的进程内调用关联；不持久化任务、不重放已发送操作。"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import secrets
from threading import Condition, Event, Thread
import time
from typing import Any
from uuid import uuid4

from erp_shared.desktop import validate_action
from .capability_errors import BusinessCapabilityError

_connection = ContextVar('desktop_connection', default='')


class DesktopUnavailable(BusinessCapabilityError):
    def __init__(self, message='本机连接不可用，请在桌面端连接后重试。', *, outcome_unknown=False):
        super().__init__('DESKTOP_RESULT_UNKNOWN' if outcome_unknown else 'DESKTOP_UNAVAILABLE', message,
                         details={'outcome_unknown': True} if outcome_unknown else None)
        self.outcome_unknown = outcome_unknown


def current_desktop_connection():
    return _connection.get()


def server_worker_context():
    """常驻服务继承业务空间，但不能继承碰巧启动它的请求所绑定的设备。"""
    context = copy_context()
    context.run(_connection.set, '')
    return context


def desktop_unavailable_reason(connection_id=None):
    """只核对本次操作的连接，不探测浏览器或挑选同账号的其他设备。"""
    key = current_desktop_connection() if connection_id is None else connection_id
    if not key:
        return '当前操作需要本机执行器，请从桌面端发起。'
    from erp_web.context import get_context
    if not get_context().desktop_connections.is_connected(key):
        return '发起操作的桌面连接已断开，请在原桌面端重新登录。'
    return ''


@contextmanager
def bind_desktop_connection(connection_id):
    token = _connection.set(connection_id or '')
    try:
        yield
    finally:
        _connection.reset(token)


@dataclass
class _Call:
    message: dict[str, Any]
    sent: bool = False
    done: Event = field(default_factory=Event)
    reply: dict[str, Any] | None = None
    assets: dict[str, dict] = field(default_factory=dict)


@dataclass
class _Connection:
    session_hash: str = ''
    touched: float = field(default_factory=time.monotonic)
    queue: deque = field(default_factory=deque)
    calls: dict[str, _Call] = field(default_factory=dict)
    polling: bool = False


class DesktopConnections:
    LEASE_SECONDS = 45

    def __init__(self, *, session_validator=None):
        self.session_validator = session_validator
        self.condition = Condition()
        self.connections: dict[str, _Connection] = {}
        self.stopped = Event()
        self.worker = Thread(target=self._expire, name='desktop-connections', daemon=True)
        self.worker.start()

    def connect(self, *, session_hash=''):
        with self.condition:
            if self.stopped.is_set():
                raise DesktopUnavailable()
            if self.session_validator is not None and not self.session_validator(session_hash):
                raise DesktopUnavailable('登录会话已失效，请重新登录。')
            key = secrets.token_urlsafe(32)
            self.connections[key] = _Connection(session_hash=session_hash)
            return {'ok': True, 'connection_id': key}

    def _get(self, key):
        connection = self.connections.get(key)
        if connection is None:
            raise DesktopUnavailable()
        if (time.monotonic() - connection.touched > self.LEASE_SECONDS
                or (self.session_validator is not None and not self.session_validator(connection.session_hash))):
            self.disconnect(key)
            raise DesktopUnavailable()
        return connection

    def validate_connection(self, key, *, session_hash=''):
        with self.condition:
            connection = self._get(key)
            if self.session_validator is not None and connection.session_hash != session_hash:
                raise DesktopUnavailable('桌面连接不属于当前登录会话。')
        return key

    def is_connected(self, key):
        with self.condition:
            try:
                self._get(key)
            except DesktopUnavailable:
                return False
            return True

    def revoke_sessions(self, token_hash=None):
        with self.condition:
            for key, connection in list(self.connections.items()):
                if token_hash is None or connection.session_hash == token_hash:
                    self.disconnect(key)

    def disconnect(self, key):
        with self.condition:
            connection = self.connections.pop(key, None)
            if connection:
                for call in connection.calls.values():
                    if call.done.is_set():
                        continue
                    call.reply = {'ok': False, 'error': '桌面连接已断开；已发送的操作不会自动重发。',
                                  'error_code': 'DESKTOP_RESULT_UNKNOWN' if call.sent else 'DESKTOP_UNAVAILABLE',
                                  'outcome_unknown': call.sent}
                    call.done.set()
            self.condition.notify_all()

    def poll(self, key, *, wait_seconds=15):
        with self.condition:
            connection = self._get(key)
            if connection.polling:
                raise ValueError('同一桌面连接不能并行等待调用。')
            connection.polling = True
            connection.touched = time.monotonic()
            deadline = time.monotonic() + min(15, max(0, wait_seconds))
            try:
                while not connection.queue and key in self.connections and time.monotonic() < deadline:
                    self.condition.wait(deadline - time.monotonic())
                self._get(key)
                connection.touched = time.monotonic()
                messages = []
                while connection.queue:
                    message = connection.queue.popleft()
                    call = connection.calls.get(message['id'])
                    if message.get('cancel'):
                        messages.append(message)
                    elif call and not call.done.is_set():
                        call.sent = True
                        messages.append(message)
                return {'ok': True, 'calls': messages}
            finally:
                connection.polling = False

    def complete(self, key, reply):
        with self.condition:
            connection = self._get(key)
            call = connection.calls.get(reply['id'])
            if call is None or not call.sent or call.done.is_set():
                # 取消、过期和重复回执不改变任何领域结果。
                return {'ok': True, 'accepted': False}
            call.reply = reply
            call.done.set()
            return {'ok': True, 'accepted': True}

    def accept_asset(self, key, call_id, asset):
        from erp_web.context import get_context
        from .desktop_asset_service import save_collected_asset
        with self.condition:
            call = self._get(key).calls.get(call_id)
            if call is None or not call.sent or call.done.is_set() or call.message['operation'] != 'collect.read':
                raise DesktopUnavailable('本机采集调用已结束，不再接受素材。')
        saved = save_collected_asset(get_context().paths.app_dir, asset)
        with self.condition:
            if call.done.is_set():
                raise DesktopUnavailable('本机调用已结束，不再接受素材。')
            call.assets[asset['url']] = saved
        return {'ok': True}

    def call(self, operation, params, *, timeout=180):
        from .external_request_context import request_context
        from .ai_run_cancellation import check_cancellation
        key = current_desktop_connection()
        if not key:
            raise DesktopUnavailable(desktop_unavailable_reason(key))
        params = validate_action(operation, params)
        ctx = request_context('desktop://local', timeout=timeout, source='desktop')
        deadlines = [time.time() + timeout]
        if ctx.deadline_at is not None:
            deadlines.append(ctx.deadline_at)
        deadline = min(deadlines)
        call = _Call({'id': uuid4().hex, 'operation': operation, 'params': params,
                      'timeout': max(0.01, deadline - time.time())})
        def check():
            check_cancellation()
            if ctx.cancellation_check:
                ctx.cancellation_check()
            if ctx.cancel is not None and ctx.cancel.is_set():
                raise DesktopUnavailable('本机操作已取消；已发送的操作需要核对。', outcome_unknown=call.sent)
            if time.time() >= deadline:
                raise DesktopUnavailable('等待本机操作超时；已发送的操作需要核对。', outcome_unknown=call.sent)
        check()
        with self.condition:
            connection = self._get(key)
            connection.calls[call.message['id']] = call
            connection.queue.append(call.message)
            self.condition.notify_all()
        try:
            while not call.done.wait(0.1):
                check()
            reply = call.reply or {}
            if not reply.get('ok'):
                error = DesktopUnavailable(reply.get('error') or '本机操作失败。', outcome_unknown=bool(reply.get('outcome_unknown')))
                if not error.outcome_unknown:
                    error.code = reply.get('error_code') or error.code
                raise error
            result = reply.get('result')
            if not isinstance(result, dict):
                raise DesktopUnavailable('本机执行器返回了无效结果。', outcome_unknown=call.sent)
            return {**result, 'uploaded_assets': dict(call.assets)} if operation == 'collect.read' else result
        finally:
            with self.condition:
                connection.calls.pop(call.message['id'], None)
                if not call.done.is_set() and call.sent and key in self.connections:
                    connection.queue.append({'id': call.message['id'], 'cancel': True})
                    self.condition.notify_all()
                # 未投递的取消项及时移除，不积累传输队列。
                if not call.sent:
                    connection.queue = deque(m for m in connection.queue if m['id'] != call.message['id'])

    def _expire(self):
        while not self.stopped.wait(2):
            with self.condition:
                for key, connection in list(self.connections.items()):
                    if (time.monotonic() - connection.touched > self.LEASE_SECONDS
                            or (self.session_validator is not None and not self.session_validator(connection.session_hash))):
                        self.disconnect(key)

    def close(self):
        self.stopped.set()
        with self.condition:
            for key in list(self.connections):
                self.disconnect(key)
        self.worker.join(timeout=3)


def call_desktop(operation, params=None, *, timeout=180):
    from erp_web.context import get_context
    return get_context().desktop_connections.call(operation, params or {}, timeout=timeout)


def desktop_request(func):
    """只接收 Electron 代理注入的当前连接，不从模型或业务请求体选择设备。"""
    from functools import wraps
    @wraps(func)
    def wrapped(handler, *args, **kwargs):
        headers = getattr(handler, 'headers', None)
        key = str(headers.get('X-Champion-Desktop', '') or '') if headers else ''
        from urllib.parse import urlsplit
        path = urlsplit(handler.path).path
        # Electron 主进程使用原生 HTTP。普通网页不能注册执行器或领取本机调用。
        if path.startswith('/api/desktop/') and headers and any(
            headers.get(name) for name in ('Origin', 'Sec-Fetch-Mode', 'Sec-Fetch-Site')
        ):
            handler.send_json({'ok': False, 'error': '桌面连接协议仅供本机执行器使用。',
                               'error_code': 'DESKTOP_PROTOCOL_FORBIDDEN'}, 403)
            return
        if path.startswith(('/api/account/', '/api/admin/')):
            key = ''
        if not getattr(handler, 'desktop_scope_allowed', True):
            key = ''
        if key:
            from erp_web.context import get_context
            from erp_web.http_request import validate_request_metadata
            from erp_web.schemas.requests import RequestValidationError
            try:
                validate_request_metadata(handler)
                from .account_request import current_session
                session = current_session()
                get_context().desktop_connections.validate_connection(key, session_hash=session.token_hash if session else '')
            except RequestValidationError as exc:
                handler.send_json({'ok': False, 'error': str(exc), 'error_code': exc.error_code}, exc.status_code)
                return
            except DesktopUnavailable as exc:
                handler.send_json({'ok': False, 'error': str(exc), 'error_code': exc.code}, 409)
                return
        with bind_desktop_connection(key):
            return func(handler, *args, **kwargs)
    return wrapped
