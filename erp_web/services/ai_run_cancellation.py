"""传递 Pydantic 原生取消令牌；同步工具和嵌套 Agent 共享停止范围。"""

from contextlib import contextmanager
from contextvars import ContextVar

from pydantic_ai import CancellationToken, RunCancelled

_token: ContextVar[CancellationToken | None] = ContextVar("ai_cancellation_token", default=None)


def current_cancellation_token() -> CancellationToken | None:
    return _token.get()


def check_cancellation() -> None:
    token = _token.get()
    if token is not None and token.cancelled:
        raise RunCancelled("用户已停止当前操作。")


@contextmanager
def bind_cancellation_token(token: CancellationToken):
    binding = _token.set(token)
    try:
        yield
    finally:
        _token.reset(binding)
