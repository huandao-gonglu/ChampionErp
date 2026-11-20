"""把当前对话标识传给业务内部运行，用于诊断关联。"""

from contextlib import contextmanager
from contextvars import ContextVar

_conversation: ContextVar[str] = ContextVar("ai_conversation", default="")


def current_ai_conversation() -> str:
    return _conversation.get()


@contextmanager
def bind_ai_conversation(conversation_id: str):
    token = _conversation.set(conversation_id)
    try:
        yield
    finally:
        _conversation.reset(token)
