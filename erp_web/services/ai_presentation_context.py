"""Presentation 运行上下文与 observer 协议（dependency-light）。

通用 Pydantic Agent 可观测层（docs/aiworkpage-universal-pydantic-observability-
refactor-plan.md §6）：

- ``AiPresentationContext``：一次 Agent 运行在业务请求调用范围中的位置
  （root/child）与 observer 的不可变描述。
- ``bind_presentation_context()`` / ``current_presentation_context()``：
  contextvar 绑定与读取。HTTP 公共边界在 claim presentation 后建立 root
  scope；``AiAgentFactory`` 在 Agent 运行开始时把带实际 run_id 的上下文
  重新绑定，运行内部再次进入 factory 时派生 child 上下文。
- ``AiRunObserver`` / ``NullAiRunObserver``：生命周期/子运行状态观察协议。
  没有 presentation scope 时 observer 为 no-op，业务执行语义不得因是否有
  浏览器观察而改变。
- ``AiNativeEventPublisher``：native event 流的发布协议；factory 只依赖该
  窄接口，具体实现（官方 Vercel 转换 + registry chunk 发布）在
  presentation service 中完成。

本模块不导入 HTTP/SSE、Vercel Adapter、presentation registry、消息存储或
任何业务 runtime unit。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

RunOrigin = Literal["global.chat", "business.ui", "global.task"]

__all__ = [
    "AiNativeEventPublisher",
    "AiPresentationContext",
    "AiRunObserver",
    "NullAiRunObserver",
    "RunOrigin",
    "bind_presentation_context",
    "current_presentation_context",
    "root_presentation_context",
]


@runtime_checkable
class AiRunObserver(Protocol):
    """观察运行生命周期/状态用于展示；不保存消息正文。"""

    def claim_root_run(self, *, run_id: str) -> str:
        """原子领取 presentation 的唯一 root run 槽位。

        一次前台交互最多一个根流。返回实际 root 的 ``run_id``：领取成功返回
        自己的 ``run_id``；槽位已被更早的顺序 Agent 领取时返回已领取的 root
        ``run_id``；presentation 不存在/已过期返回空字符串（放弃展示关联）。
        """

    def run_started(
        self,
        *,
        run_id: str,
        parent_run_id: str,
        use_case_id: str,
        label: str,
    ) -> None:
        """根或子运行开始。"""

    def running(self, *, run_id: str) -> None:
        """进入运行中（已产生模型/工具活动）。"""

    def tool_activity(self, *, run_id: str, tool_name: str) -> None:
        """观察到一次工具调用活动（安全展示标签）。"""

    def finalizing(self, *, run_id: str) -> None:
        """Agent 已产生类型化 output，进入业务收尾阶段。"""

    def completed(self, *, run_id: str) -> None:
        """业务校验收尾成功。"""

    def failed(self, *, run_id: str, code: str, message: str) -> None:
        """运行失败；``code``/``message`` 必须已脱敏。"""

    def cancelled(self, *, run_id: str) -> None:
        """运行被显式取消。"""

    def child_status(
        self,
        *,
        child_run_id: str,
        status: str,
        label: str,
    ) -> None:
        """子运行紧凑状态更新；不建立独立 assistant stream。"""


@runtime_checkable
class AiNativeEventPublisher(Protocol):
    """把 Agent native event 流转换为展示 chunk 的窄接口。

    factory 执行内核只依赖该协议；官方 Vercel 转换、编码与 registry 发布
    由 presentation service 的具体实现完成。null 实现只透传事件。
    """

    def observe_native_events(
        self,
        events: AsyncIterator[Any],
    ) -> AsyncIterator[Any]:
        """包装 native event 流：调用方消费返回值时驱动 Agent 运行。

        实现必须原样透传事件（调用方可能依赖结果事件），发布失败不得改写
        Agent 执行语义（按安全策略降级展示）。
        """


class NullAiRunObserver:
    """无 presentation scope 时的 no-op observer；业务语义不受影响。"""

    def claim_root_run(self, *, run_id: str) -> str:
        """无展示订阅者：root 语义保留（不发布任何 chunk），直接返回自身。"""

        return str(run_id or "")

    def run_started(
        self,
        *,
        run_id: str,
        parent_run_id: str,
        use_case_id: str,
        label: str,
    ) -> None:
        return None

    def running(self, *, run_id: str) -> None:
        return None

    def tool_activity(self, *, run_id: str, tool_name: str) -> None:
        return None

    def finalizing(self, *, run_id: str) -> None:
        return None

    def completed(self, *, run_id: str) -> None:
        return None

    def failed(self, *, run_id: str, code: str, message: str) -> None:
        return None

    def cancelled(self, *, run_id: str) -> None:
        return None

    def child_status(
        self,
        *,
        child_run_id: str,
        status: str,
        label: str,
    ) -> None:
        return None

    def observe_native_events(
        self,
        events: AsyncIterator[Any],
    ) -> AsyncIterator[Any]:
        """无展示订阅者：只消费 native events，不编码、不保存 UIMessage。"""

        return events


@dataclass(frozen=True)
class AiPresentationContext:
    """一次 Agent 运行在 presentation 调用范围中的不可变位置描述。

    HTTP 公共边界 claim presentation 后建立 root scope（``run_id`` 为空占位）；
    该 scope 中第一次进入 ``AiAgentFactory`` 的 Agent 是 presentation root
    Agent（``derive_agent_run``），运行内部再次进入 factory 时派生 child
    （``derive_child_run``），继承 presentation 与 observer。
    """

    presentation_id: str
    root_run_id: str
    conversation_id: str
    run_id: str
    parent_run_id: str
    origin: RunOrigin
    observer: AiRunObserver

    @property
    def is_root_scope(self) -> bool:
        """True 表示尚未派生任何 Agent run（HTTP 边界建立的 root scope）。"""

        return not self.run_id and not self.parent_run_id

    @property
    def is_root_run(self) -> bool:
        return bool(self.run_id) and not self.parent_run_id

    @property
    def is_child_run(self) -> bool:
        return bool(self.parent_run_id)

    def derive_agent_run(self, run_id: str) -> "AiPresentationContext":
        """从 root scope 派生 presentation root Agent 上下文。"""

        if self.run_id or self.parent_run_id:
            raise ValueError("只有未派生运行的 root scope 才能派生 root Agent")
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("root Agent 必须携带非空 run_id")
        return replace(self, run_id=normalized)

    def derive_child_run(self, run_id: str) -> "AiPresentationContext":
        """派生子运行上下文：继承 presentation 与 observer，本 run 成为 parent。"""

        if not self.run_id:
            raise ValueError("没有 root Agent 的 presentation scope 不能派生子运行")
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("子运行必须携带非空 run_id")
        return AiPresentationContext(
            presentation_id=self.presentation_id,
            root_run_id=self.root_run_id,
            conversation_id=self.conversation_id,
            run_id=normalized,
            parent_run_id=self.run_id,
            origin=self.origin,
            observer=self.observer,
        )

    def derive_child_of_claimed_root(
        self,
        *,
        run_id: str,
        parent_run_id: str,
    ) -> "AiPresentationContext":
        """root 槽位已被更早的顺序 Agent 领取时，从 root scope 派生 child。

        第一个 root run 结束后 contextvar 恢复为 root scope（``run_id`` 为空
        占位），同一请求内后续 Agent 不能再成为第二个 root；parent 由 registry
        已领取的 root ``run_id`` 指定，presentation 与 observer 继承。
        """

        if self.run_id or self.parent_run_id:
            raise ValueError("只有 root scope 才能派生 claimed-root child")
        normalized = str(run_id or "").strip()
        parent = str(parent_run_id or "").strip()
        if not normalized:
            raise ValueError("子运行必须携带非空 run_id")
        if not parent:
            raise ValueError("claimed-root child 必须携带已领取的 parent_run_id")
        return AiPresentationContext(
            presentation_id=self.presentation_id,
            root_run_id=self.root_run_id,
            conversation_id=self.conversation_id,
            run_id=normalized,
            parent_run_id=parent,
            origin=self.origin,
            observer=self.observer,
        )


def root_presentation_context(
    *,
    presentation_id: str,
    root_run_id: str,
    conversation_id: str,
    origin: RunOrigin,
    observer: AiRunObserver | None = None,
) -> AiPresentationContext:
    """构造 HTTP 公共边界的 root scope（``run_id`` 为空占位）。"""

    normalized_presentation = str(presentation_id or "").strip()
    normalized_root_run = str(root_run_id or "").strip()
    normalized_conversation = str(conversation_id or "").strip()
    if not normalized_presentation or not normalized_root_run:
        raise ValueError("presentation scope 必须携带非空 presentation/root_run ID")
    return AiPresentationContext(
        presentation_id=normalized_presentation,
        root_run_id=normalized_root_run,
        conversation_id=normalized_conversation,
        run_id="",
        parent_run_id="",
        origin=origin,
        observer=observer or NullAiRunObserver(),
    )


_current_context: ContextVar[AiPresentationContext | None] = ContextVar(
    "ai_presentation_context",
    default=None,
)


@contextmanager
def bind_presentation_context(
    context: AiPresentationContext,
) -> Iterator[AiPresentationContext]:
    """在调用范围内绑定 presentation 上下文；退出时恢复原值。"""

    token = _current_context.set(context)
    try:
        yield context
    finally:
        _current_context.reset(token)


def current_presentation_context() -> AiPresentationContext | None:
    """读取当前线程/协程的 presentation 上下文；无 scope 返回 None。"""

    return _current_context.get()
