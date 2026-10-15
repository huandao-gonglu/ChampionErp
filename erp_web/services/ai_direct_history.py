"""Direct Model 原生消息的显式收集边界，不执行模型或编码事件。"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pydantic_ai.messages import ModelMessage

HistorySink = Callable[[Sequence[ModelMessage]], None]
_sink: ContextVar[HistorySink | None] = ContextVar("direct_history_sink", default=None)


def current_history_sink() -> HistorySink | None:
    return _sink.get()


@contextmanager
def capture_direct_history(sink: HistorySink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
