"""执行器到 Electron 主进程的私有管道；每个请求只关联本次本机调用。"""
import base64
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
import time
from uuid import uuid4

from .operation import check_cancellation, remaining_seconds

_active = ContextVar('embedded_browser', default=None)


class BrowserPipe:
    def __init__(self, emit):
        self.emit = emit
        self.pending = {}
        self.lock = Lock()

    def deliver(self, reply):
        with self.lock:
            future = self.pending.get(reply['browser_reply'])
            if future is not None and not future.done():
                future.set_result(reply)

    def request(self, call_id, action, payload, timeout):
        key = uuid4().hex
        future = Future()
        deadline = time.monotonic() + remaining_seconds(timeout)
        with self.lock:
            self.pending[key] = future
        try:
            self.emit({'id': call_id, 'browser_request': key, 'action': action, 'payload': payload})
            while True:
                check_cancellation()
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError('ERP 内置网页操作超时。')
                try:
                    reply = future.result(timeout=min(left, 0.25))
                    break
                except FutureTimeout:
                    continue
            if reply.get('error'):
                raise RuntimeError(reply['error'])
            return reply.get('value')
        finally:
            with self.lock:
                self.pending.pop(key, None)


@contextmanager
def bind_browser(pipe, call_id):
    token = _active.set((pipe, call_id))
    try:
        yield
    finally:
        _active.reset(token)


def request(action, timeout=30, **payload):
    context = _active.get()
    if context is None:
        raise RuntimeError('此操作需要 ERP 桌面内置浏览器。')
    pipe, call_id = context
    return pipe.request(call_id, action, payload, timeout)


def is_navigation_error(error):
    message = str(error).lower()
    return any(text in message for text in (
        'execution context was destroyed', 'cannot find context with specified id',
        'inspected target navigated',
    ))


def is_browser_bound() -> bool:
    return _active.get() is not None


class EmbeddedPage:
    def __init__(self, page_id):
        self.id = page_id
        self.reusable = True
        self.account_email = ''
        self.account_name = ''

    def call(self, method, params=None, timeout=20, *, session_id='', on_event=None):
        response = request('command', timeout=timeout, page=self.id, method=method,
                           params=params or {}, session_id=session_id, collect_events=on_event is not None)
        if on_event:
            for event in response['events']:
                on_event(event)
        return response['result']

    def fetch_asset(self, url, _referer, limit):
        try:
            result = request('asset', page=self.id, url=url, limit=limit)
        except RuntimeError as error:
            check_cancellation()
            raise OSError(str(error)) from error
        return base64.b64decode(result['data'], validate=True), result['mime']

    def show(self, reason=''):
        request('show', page=self.id, reason=reason)

    def hide(self):
        request('hide', page=self.id)
