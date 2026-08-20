"""Pydantic Agent 的集中装配与同步/流式运行入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, Sequence, TypeVar
from uuid import uuid4

from pydantic_ai import (
    Agent,
    AgentRunResult,
    RunContext,
    UsageLimits,
    capture_run_messages,
)
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.settings import ModelSettings

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.stores.pydantic_message_store import (
    PydanticMessageStore,
    PydanticMessageStoreError,
)

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_instrumentation import AiAgentInstrumentation, AiAgentTrace
from .ai_model_factory import (
    AiModelFactoryError,
    PydanticModelBinding,
    create_pydantic_model_binding_for_use_case,
)
from .ai_model_errors import model_http_error_payload, safe_model_error_text
from .ai_presentation_context import (
    AiPresentationContext,
    bind_presentation_context,
    current_presentation_context,
)
from .ai_tool_bridge import AiToolBridgeError, build_pydantic_toolset
from .ai_tool_registry import AiToolSet
from .ai_tool_runtime import AiToolRuntime


_logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT")
OutputValidator = Callable[[RunContext[AiAgentDependencies], OutputT], OutputT]
ModelBindingFactory = Callable[..., PydanticModelBinding]


@dataclass(frozen=True)
class AiAgentExecutionProfile(Generic[OutputT]):
    """项目稳定的 Agent execution profile，不透传任意 Agent 参数。"""

    use_case_id: str
    output_type: type[OutputT]
    toolset_id: str
    budget_profile: str
    permissions: frozenset[str]
    timeout_seconds: float
    max_model_requests: int
    max_tool_calls: int
    max_tool_output_bytes: int
    retries: int = 2
    result_version: str = "v1"
    allow_write: bool = False

    def __post_init__(self) -> None:
        if not self.use_case_id or not self.toolset_id or not self.budget_profile:
            raise ValueError("Agent execution profile 的标识不能为空")
        if self.timeout_seconds <= 0:
            raise ValueError("Agent execution profile timeout_seconds 必须大于 0")
        if self.max_model_requests < 1 or self.max_tool_calls < 1:
            raise ValueError("Agent execution profile 的调用上限必须大于 0")
        if self.max_tool_output_bytes < 1:
            raise ValueError("Agent execution profile 的输出上限必须大于 0")
        if self.retries < 0:
            raise ValueError("Agent execution profile retries 不能小于 0")
        object.__setattr__(self, "permissions", frozenset(self.permissions))


class AiAgentExecutionError(RuntimeError):
    """Agent 边界的安全错误；不保留 Provider/Pydantic 原始异常链。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        conversation_id: str = "",
        task_run_id: str = "",
        run_id: str = "",
        trace_id: str = "",
    ) -> None:
        self.code = str(code or "AI_AGENT_RUN_FAILED")
        self.retryable = bool(retryable)
        self.conversation_id = str(conversation_id or "")
        self.task_run_id = str(task_run_id or "")
        self.run_id = str(run_id or "")
        self.trace_id = str(trace_id or "")
        super().__init__(message)


@dataclass
class AiAgentRunOutcome(Generic[OutputT]):
    """从 Pydantic 边界返回的项目类型；终态由业务终检决定。"""

    output: OutputT
    conversation_id: str
    task_run_id: str
    attempt_id: str
    run_id: str
    trace_id: str
    usage: dict[str, int]
    messages: list[ModelMessage] = field(repr=False)
    _observer: Any = field(default=None, repr=False)
    _presentation_run_id: str = field(default="", repr=False)
    _terminal: bool = field(default=False, init=False, repr=False)

    def _notify_observer_completed(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.completed(
                run_id=self._presentation_run_id or self.run_id,
            )
        except Exception:
            # 展示观察是尽力而为；不得改写业务终检语义。
            _logger.warning(
                "AI 展示 observer completed 通知失败：%s",
                self._presentation_run_id or self.run_id,
                exc_info=True,
            )

    def _notify_observer_failed(self, error: AiAgentExecutionError) -> None:
        if self._observer is None:
            return
        try:
            self._observer.failed(
                run_id=self._presentation_run_id or self.run_id,
                code=error.code,
                message=str(error),
            )
        except Exception:
            _logger.warning(
                "AI 展示 observer failed 通知失败：%s",
                self._presentation_run_id or self.run_id,
                exc_info=True,
            )

    def complete(self) -> None:
        if self._terminal:
            return
        self._notify_observer_completed()
        self._terminal = True

    def fail(self, error: AiAgentExecutionError) -> None:
        if self._terminal:
            return
        self._notify_observer_failed(error)
        self._terminal = True


def _safe_usage(value: Any) -> dict[str, int]:
    try:
        payload = asdict(value)
    except (TypeError, ValueError):
        return {}
    return {
        key: int(item)
        for key, item in payload.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _safe_agent_error(
    exc: Exception,
    *,
    validator: Any,
    model_messages: list[ModelMessage] | None = None,
    conversation_id: str,
    task_run_id: str,
    run_id: str = "",
    trace_id: str = "",
) -> AiAgentExecutionError:
    correlation = {
        "conversation_id": conversation_id,
        "task_run_id": task_run_id,
        "run_id": run_id,
        "trace_id": trace_id,
    }
    if isinstance(exc, ModelHTTPError):
        provider_error = model_http_error_payload(exc)
        status_code = int(provider_error["status_code"])
        if status_code == 402:
            return AiAgentExecutionError(
                "AI_PROVIDER_PAYMENT_REQUIRED",
                "AI Provider 拒绝请求（HTTP 402）：余额不足或计费配置不可用。",
                retryable=False,
                **correlation,
            )
        message = f"HTTP {provider_error['status_code']}: {provider_error['message']}"
        if provider_error["request_id"]:
            message += f" (request_id={provider_error['request_id']})"
        return AiAgentExecutionError(
            str(provider_error["code"]),
            message,
            retryable=(status_code in {408, 425, 429} or status_code >= 500),
            **correlation,
        )
    if isinstance(exc, ModelAPIError):
        return AiAgentExecutionError(
            exc.__class__.__name__,
            safe_model_error_text(exc.message) or exc.__class__.__name__,
            retryable=True,
            **correlation,
        )
    if isinstance(exc, UnexpectedModelBehavior):
        empty_response = next(
            (
                message
                for message in reversed(model_messages or [])
                if isinstance(message, ModelResponse) and not message.parts
            ),
            None,
        )
        if empty_response is not None:
            details = empty_response.provider_details or {}
            usage = _safe_usage(empty_response.usage)
            return AiAgentExecutionError(
                "AI_PROVIDER_RESPONSE_INVALID",
                "Provider 返回无法使用的空响应："
                f"provider={empty_response.provider_name or 'unknown'}; "
                f"background={str(bool(details.get('background'))).lower()}; "
                f"response_id={empty_response.provider_response_id or 'null'}; "
                f"finish_reason={empty_response.finish_reason or 'null'}; "
                f"state={empty_response.state}; parts=0; "
                f"input_tokens={usage.get('input_tokens', 0)}; "
                f"output_tokens={usage.get('output_tokens', 0)}。",
                **correlation,
            )
    validation_code = str(getattr(validator, "error_code", "") or "")
    if validation_code:
        return AiAgentExecutionError(
            validation_code,
            "模型输出未满足当前业务约束。",
            **correlation,
        )
    if isinstance(exc, AiModelFactoryError):
        return AiAgentExecutionError(
            exc.code,
            "当前 AI 模型配置无效。",
            **correlation,
        )
    if isinstance(exc, PydanticMessageStoreError):
        return AiAgentExecutionError(
            exc.code,
            str(exc),
            **correlation,
        )
    if isinstance(exc, AiToolBridgeError):
        return AiAgentExecutionError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
            **correlation,
        )
    if isinstance(exc, (TimeoutError,)):
        return AiAgentExecutionError(
            "TASK_DEADLINE_EXCEEDED",
            "AI Agent 总 deadline 已耗尽。",
            retryable=True,
            **correlation,
        )
    if isinstance(exc, UsageLimitExceeded):
        return AiAgentExecutionError(
            "AI_AGENT_USAGE_LIMIT_EXCEEDED",
            "AI Agent 已达到当前 execution profile 的资源上限。",
            **correlation,
        )
    if isinstance(exc, (UnexpectedModelBehavior, AgentRunError)):
        return AiAgentExecutionError(
            exc.__class__.__name__,
            safe_model_error_text(str(exc)) or exc.__class__.__name__,
            **correlation,
        )
    return AiAgentExecutionError(
        "AI_AGENT_RUN_FAILED",
        "AI Agent 运行失败。",
        retryable=True,
        **correlation,
    )


class AiAgentStreamSession(Generic[OutputT]):
    """一次流式 run 的 opaque owner。

    协议层只能通过 ``events()`` 拿到已脱敏边界的 native event iterator 和相关
    ID；raw Agent、deps、usage limits 不外泄，``agent.run_stream_events()``
    也只发生在这里。
    """

    def __init__(
        self,
        *,
        factory: "AiAgentFactory",
        profile: AiAgentExecutionProfile[OutputT],
        agent: Agent[AiAgentDependencies, OutputT],
        dependencies: AiAgentDependencies,
        execution_context: AiExecutionContext,
        conversation_id: str,
        message_history: list[ModelMessage],
        captured_messages: list[ModelMessage],
        technical_trace: AiAgentTrace,
        output_validator: OutputValidator[OutputT] | None = None,
        presentation_context: AiPresentationContext | None = None,
    ) -> None:
        self._factory = factory
        self._profile = profile
        self._agent = agent
        self._dependencies = dependencies
        self._execution_context = execution_context
        self._conversation_id = conversation_id
        self._message_history = list(message_history)
        self._captured_messages = captured_messages
        self._technical_trace = technical_trace
        self._output_validator = output_validator
        self._presentation = presentation_context
        self._run_id = execution_context.attempt_id
        self._started = False
        self._history_persisted = False
        self._completed = False
        self._failure_error: AiAgentExecutionError | None = None
        self._running_notified = False
        self._finalizing_notified = False
        self._result: AgentRunResult[Any] | None = None
        self._events: AsyncIterator[
            AgentStreamEvent | AgentRunResultEvent[Any]
        ] | None = None
        self._published: AsyncIterator[Any] | None = None

    @property
    def started(self) -> bool:
        """native event 流是否已经启动（用于区分装配期/流中期失败）。"""

        return self._started

    @property
    def presentation_context(self) -> AiPresentationContext | None:
        return self._presentation

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def task_run_id(self) -> str:
        return self._execution_context.task_run_id

    @property
    def attempt_id(self) -> str:
        return self._execution_context.attempt_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def trace_id(self) -> str:
        return self._technical_trace.trace_id

    @property
    def history_persisted(self) -> bool:
        return self._history_persisted

    @property
    def completed(self) -> bool:
        """run 正常完成并且结果消息已经持久化。"""

        return self._completed

    @property
    def failure_error(self) -> AiAgentExecutionError | None:
        """流被协议适配器消费后仍可读取的稳定失败终态。"""

        return self._failure_error

    def events(
        self,
        new_messages: Sequence[ModelMessage],
    ) -> AsyncIterator[AgentStreamEvent | AgentRunResultEvent[Any]]:
        """启动本轮流式运行；同一 session 只允许调用一次。

        存在 presentation root run 时，返回值是 observer 包装后的事件流：
        消费它同时驱动官方转换/发布；事件本身原样透传。没有 presentation
        scope 时返回原始 native event 流，语义不变。
        """

        if self._started:
            raise AiAgentExecutionError(
                "AI_AGENT_STREAM_ALREADY_STARTED",
                "当前流式 session 已经启动，不能再次注入消息。",
                conversation_id=self._conversation_id,
                task_run_id=self.task_run_id,
            )
        self._started = True
        iterator = self._stream(list(new_messages))
        self._events = iterator
        presentation = self._presentation
        if presentation is None:
            return iterator
        observer = presentation.observer
        if presentation.is_root_run:
            # observer 装配期故障只降级展示，不改写业务执行语义。
            try:
                observer.run_started(
                    run_id=presentation.run_id,
                    parent_run_id=presentation.parent_run_id,
                    use_case_id=self._profile.use_case_id,
                    label=self._profile.use_case_id,
                )
            except Exception:
                _logger.warning(
                    "AI 展示 observer run_started 通知失败，降级展示：%s",
                    presentation.run_id,
                    exc_info=True,
                )
            try:
                published = observer.observe_native_events(iterator)
            except Exception:
                _logger.warning(
                    "AI 展示 observer 事件包装失败，降级为无展示运行：%s",
                    presentation.run_id,
                    exc_info=True,
                )
                published = None
            if published is not None:
                self._published = published
                return published
            return iterator
        if presentation.is_child_run:
            # 首期：子运行只通过父 observer 的紧凑状态展示，不建立第二条
            # assistant stream，也不编码子 native stream。
            try:
                observer.child_status(
                    child_run_id=presentation.run_id,
                    status="running",
                    label=self._profile.use_case_id,
                )
            except Exception:
                _logger.warning(
                    "AI 展示 observer child_status 通知失败，降级展示：%s",
                    presentation.run_id,
                    exc_info=True,
                )
        return iterator

    async def aclose_events(self) -> None:
        """关闭未消费完的 native event iterator，幂等。"""

        published = self._published
        if published is not None:
            self._published = None
            try:
                await published.aclose()
            except Exception:
                pass
        iterator = self._events
        if iterator is not None:
            self._events = None
            try:
                await iterator.aclose()
            except Exception:
                pass

    async def _stream(
        self,
        new_messages: list[ModelMessage],
    ) -> AsyncIterator[AgentStreamEvent | AgentRunResultEvent[Any]]:
        try:
            async with self._agent.run_stream_events(
                None,
                message_history=[*self._message_history, *new_messages],
                conversation_id=self._conversation_id,
                run_id=self._execution_context.attempt_id,
                deps=self._dependencies,
                usage_limits=UsageLimits(
                    request_limit=self._profile.max_model_requests,
                    tool_calls_limit=self._profile.max_tool_calls,
                ),
            ) as native_events:
                async for event in native_events:
                    self._notify_running_once()
                    if isinstance(event, AgentRunResultEvent):
                        self._complete_with_result(event.result)
                    yield event
        except AiAgentExecutionError as exc:
            self._failure_error = exc
            self._notify_presentation_failed(exc.code, str(exc))
            raise
        except Exception as exc:
            if self._captured_messages and not self._history_persisted:
                try:
                    self._factory.message_store.save(
                        self._conversation_id,
                        list(self._captured_messages),
                    )
                    self._history_persisted = True
                except Exception as persistence_exc:
                    exc = persistence_exc
            error = _safe_agent_error(
                exc,
                validator=self._output_validator,
                model_messages=self._captured_messages,
                conversation_id=self._conversation_id,
                task_run_id=self.task_run_id,
                run_id=self._run_id,
                trace_id=self.trace_id,
            )
            self._failure_error = error
            self._notify_presentation_failed(error.code, str(error))
            raise error from None

    def _notify_running_once(self) -> None:
        """第一个 native event 到达时通知展示层进入 running；幂等。"""

        if self._running_notified:
            return
        self._running_notified = True
        presentation = self._presentation
        if presentation is None or not presentation.is_root_run:
            return
        try:
            presentation.observer.running(run_id=presentation.run_id)
        except Exception:
            _logger.warning(
                "AI 展示 observer running 通知失败：%s",
                presentation.run_id,
                exc_info=True,
            )

    def _notify_finalizing_once(self) -> None:
        """类型化 output 产生后通知展示层进入 finalizing；幂等。"""

        if self._finalizing_notified:
            return
        self._finalizing_notified = True
        presentation = self._presentation
        if presentation is None or not presentation.is_root_run:
            return
        try:
            presentation.observer.finalizing(run_id=presentation.run_id)
        except Exception:
            _logger.warning(
                "AI 展示 observer finalizing 通知失败：%s",
                presentation.run_id,
                exc_info=True,
            )

    def _notify_presentation_failed(self, code: str, message: str) -> None:
        """流中期失败时通知展示层；不改变向上抛出的错误语义。"""

        presentation = self._presentation
        if presentation is None:
            return
        try:
            if presentation.is_root_run:
                presentation.observer.failed(
                    run_id=presentation.run_id,
                    code=code,
                    message=message,
                )
            elif presentation.is_child_run:
                presentation.observer.child_status(
                    child_run_id=presentation.run_id,
                    status="failed",
                    label=self._profile.use_case_id,
                )
        except Exception:
            _logger.warning(
                "AI 展示 observer failed 通知失败：%s",
                presentation.run_id,
                exc_info=True,
            )

    def _complete_with_result(self, result: AgentRunResult[Any]) -> None:
        """成功完成：用官方结果原子替换 conversation 历史。"""

        self._run_id = str(result.run_id or "") or self._run_id
        self._technical_trace.set_agent_run_id(self._run_id)
        self._execution_context.bounded_timeout_seconds()
        if not self._history_persisted:
            messages = list(result.all_messages())
            if messages:
                self._factory.message_store.save(
                    self._conversation_id,
                    messages,
                )
                self._history_persisted = True
        self._result = result
        self._completed = True
        self._notify_finalizing_once()

    @property
    def finalizing(self) -> bool:
        """Agent 已产生类型化 output、历史已持久化，但业务收尾尚未完成。"""

        return self._completed and self._result is not None

    def require_outcome(self) -> AiAgentRunOutcome[OutputT]:
        """成功 run 后返回窄的类型化完成结果。

        只暴露 ``AiAgentRunOutcome``，不外泄 raw Agent、deps 或原始 iterator；
        focused business service 用它取得 output 并执行领域收尾（``complete()`` /
        ``fail()``）。审批和长任务恢复统一由 ``GlobalTaskController`` 负责，
        Agent session 不产生第二套 deferred pending state。
        """

        if self._result is None or not self._completed:
            raise AiAgentExecutionError(
                "AI_AGENT_STREAM_RESULT_UNAVAILABLE",
                "流式运行尚未产生类型化完成结果。",
                conversation_id=self._conversation_id,
                task_run_id=self.task_run_id,
                run_id=self._run_id,
                trace_id=self.trace_id,
            )
        result = self._result
        presentation = self._presentation
        observer: Any = None
        presentation_run_id = ""
        if presentation is not None:
            if presentation.is_root_run:
                # 业务终检（complete/fail）负责发布展示终态；observer 由
                # outcome 携带，业务 service 不感知 presentation 类型。
                observer = presentation.observer
                presentation_run_id = presentation.run_id
            elif presentation.is_child_run:
                try:
                    presentation.observer.child_status(
                        child_run_id=presentation.run_id,
                        status="completed",
                        label=self._profile.use_case_id,
                    )
                except Exception:
                    _logger.warning(
                        "AI 展示 observer child_status 通知失败：%s",
                        presentation.run_id,
                        exc_info=True,
                    )
        return AiAgentRunOutcome(
            output=result.output,
            conversation_id=self._conversation_id,
            task_run_id=self.task_run_id,
            attempt_id=self._execution_context.attempt_id,
            run_id=self._run_id,
            trace_id=self.trace_id,
            usage=_safe_usage(result.usage),
            messages=list(result.all_messages()),
            _observer=observer,
            _presentation_run_id=presentation_run_id,
        )


class AiAgentFactory:
    """唯一 Pydantic Agent 装配 owner。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any] | None,
        message_store: PydanticMessageStore,
        model_binding_factory: ModelBindingFactory = (
            create_pydantic_model_binding_for_use_case
        ),
        instrumentation: AiAgentInstrumentation | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.message_store = message_store
        self.model_binding_factory = model_binding_factory
        self.instrumentation = instrumentation or AiAgentInstrumentation(
            self.app_dir / "data" / "logs" / "ai_traces" / "agent_spans.jsonl"
        )

    @staticmethod
    def _bounded_model_settings(
        base: ModelSettings,
    ) -> Callable[[RunContext[AiAgentDependencies]], ModelSettings]:
        base_settings = dict(base)

        def settings(ctx: RunContext[AiAgentDependencies]) -> ModelSettings:
            remaining = ctx.deps.execution_context.bounded_timeout_seconds()
            configured = base_settings.get("timeout")
            if isinstance(configured, (int, float)) and configured > 0:
                remaining = min(remaining, float(configured))
            return ModelSettings(**{**base_settings, "timeout": remaining})

        return settings

    def _build_agent(
        self,
        *,
        profile: AiAgentExecutionProfile[OutputT],
        binding: PydanticModelBinding,
        instructions: str,
        toolset: AiToolSet,
        output_validator: OutputValidator[OutputT] | None,
        model_override: Model | None,
    ) -> Agent[AiAgentDependencies, OutputT]:
        """集中装配唯一 Agent 定义；审批写工具必须交给 Global Task。"""

        approval_tools = sorted(
            definition.name
            for definition in toolset.definitions
            if definition.approval_required
        )
        if approval_tools:
            raise AiAgentExecutionError(
                "TOOL_APPROVAL_REQUIRED",
                "需审批工具只能通过 GlobalTaskController 执行："
                + "、".join(approval_tools),
            )

        agent: Agent[AiAgentDependencies, OutputT] = Agent(
            model_override or binding.model,
            output_type=profile.output_type,
            instructions=instructions,
            deps_type=AiAgentDependencies,
            model_settings=self._bounded_model_settings(binding.model_settings),
            retries=profile.retries,
            toolsets=[build_pydantic_toolset(toolset)],
            name=profile.use_case_id.replace(".", "_"),
        )
        agent.instrument = self.instrumentation.settings
        if output_validator is not None:
            agent.output_validator(output_validator)
        return agent

    def run_sync(
        self,
        *,
        profile: AiAgentExecutionProfile[OutputT],
        instructions: str,
        user_prompt: str,
        toolset: AiToolSet,
        use_case_state: Any = None,
        output_validator: OutputValidator[OutputT] | None = None,
        actor_id: str = "local-user",
        tenant_id: str = "local",
        business_scope: Mapping[str, str] | None = None,
        idempotency_context: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        model_override: Model | None = None,
    ) -> AiAgentRunOutcome[OutputT]:
        """同步 Agent 入口；内部通过统一流式内核消费 native events。

        与流式入口 ``open_stream_run`` 共用同一装配与消费路径：相同的
        profile/binding/instructions/ToolSet/限额/超时/消息持久化/安全错误
        映射。存在 presentation scope 时，root Agent 的官方 chunk 由 observer
        在消费事件的同时实时发布；没有 scope 时只消费事件，业务执行语义
        不因是否有浏览器观察而改变。
        """

        if toolset.toolset_id != profile.toolset_id:
            raise AiAgentExecutionError(
                "TOOLSET_BINDING_MISMATCH",
                "Agent execution profile 与 ToolSet 不一致。",
            )
        effective_timeout = min(
            profile.timeout_seconds,
            float(timeout_seconds or profile.timeout_seconds),
        )
        execution_context = AiExecutionContext.create(
            timeout_seconds=effective_timeout,
            budget_profile=profile.budget_profile,
            actor_id=actor_id,
            tenant_id=tenant_id,
            permissions=profile.permissions,
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            allow_write=profile.allow_write,
        )
        run_id = execution_context.attempt_id
        trace_id = ""
        captured_messages: list[ModelMessage] = []
        session: AiAgentStreamSession[OutputT] | None = None
        presentation = self._derive_presentation_context(run_id)
        if presentation is not None and presentation.is_root_run:
            # presentation root run 的规范历史必须落在前端预留的 conversation
            # 上：任务结束后 AiWork 按 presentation conversation_id 读取历史，
            # 实时流与持久化历史必须同一 ID。
            conversation_id = presentation.conversation_id
        else:
            conversation_id = f"conversation_{uuid4().hex}"
        try:
            binding = self.model_binding_factory(
                self.app_dir,
                self.app_config,
                profile.use_case_id,
                timeout_seconds=effective_timeout,
                default_timeout_seconds=profile.timeout_seconds,
            )
            runtime = AiToolRuntime(
                toolset=toolset,
                execution_context=execution_context,
                max_tool_calls=profile.max_tool_calls,
                max_output_bytes=profile.max_tool_output_bytes,
            )
            dependencies = AiAgentDependencies(
                use_case_id=profile.use_case_id,
                execution_context=execution_context,
                tool_runtime=runtime,
                use_case_state=use_case_state,
                invocation_id=execution_context.attempt_id,
            )
            agent = self._build_agent(
                profile=profile,
                binding=binding,
                instructions=instructions,
                toolset=toolset,
                output_validator=output_validator,
                model_override=model_override,
            )
            entity_ids = {
                key: value
                for key, value in dict(business_scope or {}).items()
                if str(key).endswith("_id")
            }
            new_messages: list[ModelMessage] = [
                ModelRequest(parts=[UserPromptPart(user_prompt)])
            ]
            with capture_run_messages() as captured_messages:
                with self.instrumentation.start_run_span(
                    use_case_id=profile.use_case_id,
                    conversation_id=conversation_id,
                    invocation_id=execution_context.attempt_id,
                    business_entity_ids=entity_ids,
                ) as technical_trace:
                    trace_id = technical_trace.trace_id
                    technical_trace.set_agent_run_id(run_id)
                    session = AiAgentStreamSession(
                        factory=self,
                        profile=profile,
                        agent=agent,
                        dependencies=dependencies,
                        execution_context=execution_context,
                        conversation_id=conversation_id,
                        message_history=[],
                        captured_messages=captured_messages,
                        technical_trace=technical_trace,
                        output_validator=output_validator,
                        presentation_context=presentation,
                    )
                    if presentation is not None:
                        # contextvar 在整个运行调用范围内绑定：运行内部再次
                        # 进入 factory 时派生 child，工具执行也可见该上下文。
                        with bind_presentation_context(presentation):
                            outcome = asyncio.run(
                                self._consume_stream(session, new_messages)
                            )
                    else:
                        outcome = asyncio.run(
                            self._consume_stream(session, new_messages)
                        )
                    run_id = outcome.run_id
                    execution_context.bounded_timeout_seconds()
                    technical_trace.set_agent_run_id(run_id)
            return outcome
        except AiAgentExecutionError as error:
            self._notify_pre_stream_failure(
                presentation,
                session,
                error,
                use_case_id=profile.use_case_id,
            )
            raise
        except Exception as exc:
            if captured_messages and not (
                session is not None and session.history_persisted
            ):
                try:
                    self.message_store.save(conversation_id, captured_messages)
                except Exception as persistence_exc:
                    exc = persistence_exc
            error = _safe_agent_error(
                exc,
                validator=output_validator,
                model_messages=captured_messages,
                conversation_id=conversation_id,
                task_run_id=execution_context.task_run_id,
                run_id=run_id,
                trace_id=trace_id,
            )
            self._notify_pre_stream_failure(
                presentation,
                session,
                error,
                use_case_id=profile.use_case_id,
            )
            raise error from None

    @staticmethod
    def _derive_presentation_context(run_id: str) -> AiPresentationContext | None:
        """从当前 presentation scope 派生本次 Agent 运行上下文。

        一次前台交互最多一个根流：HTTP 边界 root scope 中第一个进入 factory
        的 Agent 经 observer 原子领取 registry 的唯一 root 槽位成为
        presentation root Agent；root 结束后 contextvar 恢复为 root scope，
        同一请求内后续顺序 Agent 领取失败，一律派生为 child（紧凑状态展示，
        不建立第二条流）。运行内部再次进入 factory 时按当前上下文派生
        child。没有 scope 返回 None：null observer，不创建 registry 状态或
        SSE；presentation 已过期/被清理时同样返回 None，业务执行语义不变。
        """

        current = current_presentation_context()
        if current is None:
            return None
        if current.is_root_scope:
            try:
                claimed_run_id = current.observer.claim_root_run(run_id=run_id)
            except Exception:
                _logger.warning(
                    "AI 展示 root 领取失败，降级为无展示运行：%s",
                    run_id,
                    exc_info=True,
                )
                return None
            if not claimed_run_id:
                return None
            if claimed_run_id == run_id:
                return current.derive_agent_run(run_id)
            return current.derive_child_of_claimed_root(
                run_id=run_id,
                parent_run_id=claimed_run_id,
            )
        return current.derive_child_run(run_id)

    @staticmethod
    async def _consume_stream(
        session: "AiAgentStreamSession[OutputT]",
        new_messages: Sequence[ModelMessage],
    ) -> AiAgentRunOutcome[OutputT]:
        """统一流式内核：消费 native events 直到产生类型化结果。

        有 presentation observer 时 ``session.events()`` 返回包装流，消费它
        同时驱动官方转换/发布（事件原样透传）；没有 observer 时直接消费
        native events。消费结束后才读取 ``require_outcome()``。
        """

        events = session.events(new_messages)
        async for _event in events:
            pass
        return session.require_outcome()

    @staticmethod
    def _notify_pre_stream_failure(
        presentation: AiPresentationContext | None,
        session: "AiAgentStreamSession[Any] | None",
        error: AiAgentExecutionError,
        *,
        use_case_id: str,
    ) -> None:
        """native event 流启动前失败时通知展示层并补发官方 error chunk。

        流中期失败已由官方 transform 转换为 error/finish chunk、且 session
        已通知 observer；这里只在流尚未启动时补发，不重复发布。
        """

        if presentation is None:
            return
        if session is not None and session.started:
            return
        observer = presentation.observer
        try:
            if presentation.is_root_run:
                observer.failed(
                    run_id=presentation.run_id,
                    code=error.code,
                    message=str(error),
                )
                publish = getattr(observer, "publish_error_chunks", None)
                if publish is not None:
                    try:
                        asyncio.run(publish(error))
                    except Exception:
                        _logger.warning(
                            "AI 展示错误 chunk 补发失败：%s",
                            presentation.run_id,
                            exc_info=True,
                        )
            elif presentation.is_child_run:
                observer.child_status(
                    child_run_id=presentation.run_id,
                    status="failed",
                    label=use_case_id,
                )
        except Exception:
            _logger.warning(
                "AI 展示 observer 流前失败通知失败：%s",
                presentation.run_id,
                exc_info=True,
            )

    @asynccontextmanager
    async def open_stream_run(
        self,
        *,
        profile: AiAgentExecutionProfile[OutputT],
        instructions: str,
        toolset: AiToolSet,
        conversation_id: str,
        message_history: Sequence[ModelMessage] | None = None,
        use_case_state: Any = None,
        output_validator: OutputValidator[OutputT] | None = None,
        actor_id: str = "local-user",
        tenant_id: str = "local",
        business_scope: Mapping[str, str] | None = None,
        idempotency_context: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        model_override: Model | None = None,
    ) -> AsyncIterator[AiAgentStreamSession[OutputT]]:
        """协议无关的流式运行入口，装配语义与 ``run_sync`` 完全一致。

        一次用户发送不是向正在运行的 Agent 注入消息，而是用服务端可信历史
        加本轮用户输入启动一次新 run；run 完成后用 ``result.all_messages()``
        原子替换该 conversation 的完整历史。
        """

        if toolset.toolset_id != profile.toolset_id:
            raise AiAgentExecutionError(
                "TOOLSET_BINDING_MISMATCH",
                "Agent execution profile 与 ToolSet 不一致。",
            )
        normalized_conversation_id = str(conversation_id or "").strip()
        if not normalized_conversation_id:
            raise AiAgentExecutionError(
                "AI_CHAT_CONVERSATION_ID_INVALID",
                "流式运行必须携带 conversation ID。",
            )
        effective_timeout = min(
            profile.timeout_seconds,
            float(timeout_seconds or profile.timeout_seconds),
        )
        execution_context = AiExecutionContext.create(
            timeout_seconds=effective_timeout,
            budget_profile=profile.budget_profile,
            actor_id=actor_id,
            tenant_id=tenant_id,
            permissions=profile.permissions,
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            allow_write=profile.allow_write,
        )
        run_id = execution_context.attempt_id
        trace_id = ""
        captured_messages: list[ModelMessage] = []
        session: AiAgentStreamSession[OutputT] | None = None
        presentation = self._derive_presentation_context(run_id)
        if presentation is not None and presentation.is_root_run:
            # presentation root run 的规范历史必须落在前端预留的 conversation
            # 上：调用方传入的随机/本地 conversation ID 只用于无 scope 场景；
            # 实时流与持久化历史必须同一 ID，任务结束后才能按 presentation
            # conversation 读取历史。
            normalized_conversation_id = presentation.conversation_id
        try:
            binding = self.model_binding_factory(
                self.app_dir,
                self.app_config,
                profile.use_case_id,
                timeout_seconds=effective_timeout,
                default_timeout_seconds=profile.timeout_seconds,
            )
            runtime = AiToolRuntime(
                toolset=toolset,
                execution_context=execution_context,
                max_tool_calls=profile.max_tool_calls,
                max_output_bytes=profile.max_tool_output_bytes,
            )
            dependencies = AiAgentDependencies(
                use_case_id=profile.use_case_id,
                execution_context=execution_context,
                tool_runtime=runtime,
                use_case_state=use_case_state,
                invocation_id=execution_context.attempt_id,
            )
            agent = self._build_agent(
                profile=profile,
                binding=binding,
                instructions=instructions,
                toolset=toolset,
                output_validator=output_validator,
                model_override=model_override,
            )
            entity_ids = {
                key: value
                for key, value in dict(business_scope or {}).items()
                if str(key).endswith("_id")
            }
            with capture_run_messages() as captured_messages:
                with self.instrumentation.start_run_span(
                    use_case_id=profile.use_case_id,
                    conversation_id=normalized_conversation_id,
                    invocation_id=execution_context.attempt_id,
                    business_entity_ids=entity_ids,
                ) as technical_trace:
                    trace_id = technical_trace.trace_id
                    technical_trace.set_agent_run_id(run_id)
                    session = AiAgentStreamSession(
                        factory=self,
                        profile=profile,
                        agent=agent,
                        dependencies=dependencies,
                        execution_context=execution_context,
                        conversation_id=normalized_conversation_id,
                        message_history=list(message_history or []),
                        captured_messages=captured_messages,
                        technical_trace=technical_trace,
                        output_validator=output_validator,
                        presentation_context=presentation,
                    )
                    try:
                        if presentation is not None:
                            # 消费方在整个调用范围内可见 presentation 上下文；
                            # 运行内部再次进入 factory 时派生 child。
                            with bind_presentation_context(presentation):
                                yield session
                        else:
                            yield session
                    finally:
                        await session.aclose_events()
        except AiAgentExecutionError as error:
            self._notify_pre_stream_failure(
                presentation,
                session,
                error,
                use_case_id=profile.use_case_id,
            )
            raise
        except Exception as exc:
            error = _safe_agent_error(
                exc,
                validator=output_validator,
                model_messages=captured_messages,
                conversation_id=normalized_conversation_id,
                task_run_id=execution_context.task_run_id,
                run_id=run_id,
                trace_id=trace_id,
            )
            self._notify_pre_stream_failure(
                presentation,
                session,
                error,
                use_case_id=profile.use_case_id,
            )
            raise error from None
        finally:
            if (
                session is not None
                and captured_messages
                and not session.history_persisted
            ):
                try:
                    self.message_store.save(
                        normalized_conversation_id,
                        list(captured_messages),
                    )
                except Exception as persistence_exc:
                    _logger.warning(
                        "流式运行捕获消息持久化失败：%s",
                        persistence_exc,
                    )

__all__ = [
    "AiAgentExecutionError",
    "AiAgentExecutionProfile",
    "AiAgentFactory",
    "AiAgentRunOutcome",
]
