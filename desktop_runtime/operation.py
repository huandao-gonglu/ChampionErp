"""单次本机操作的取消与期限；不实现 Agent 生命周期。"""
from contextlib import contextmanager
from contextvars import ContextVar
import time

_current = ContextVar('desktop_operation', default=None)


def check_cancellation():
    operation = _current.get()
    if operation and operation[0].is_set():
        raise RuntimeError('本机操作已取消；已发送操作的结果需要核对。')
    if operation and time.monotonic() >= operation[1]:
        raise TimeoutError('本机操作超时；已发送操作的结果需要核对。')


def remaining_seconds(timeout):
    check_cancellation()
    operation = _current.get()
    return max(0.01, min(timeout, operation[1] - time.monotonic())) if operation else timeout


@contextmanager
def bind_operation(cancel, timeout):
    token = _current.set((cancel, time.monotonic() + timeout))
    try:
        yield
    finally:
        _current.reset(token)


class LocalOperationError(RuntimeError):
    """固定本机动作的稳定错误码，跨管道保留原采集错误语义。"""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
