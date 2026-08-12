"""Pydantic Agent 的集中装配与同步运行入口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic_ai import (
    Agent,
    RunContext,
    ToolApproved,
    ToolDenied,
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
    DeferredToolRequests,
    ModelMessage,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_agent_dependencies import AiAgentDependencies
from .ai_agent_instrumentation import AiAgentInstrumentation
from .ai_agent_observability import (
    AGENT_REQUEST_EVENT,
    AGENT_TRANSCRIPT_EVENT,
    build_agent_request_observation,
    build_agent_transcript_observation,
    sanitize_ai_work_value,
)
from .ai_agent_state_store import (
    AiAgentApprovalRecord,
    AiAgentStateError,
    AiAgentStateStore,
)
from .ai_invocation import AiWorkRecorder, ConversationAiWorkRecorder
from .ai_model_factory import (
    AiModelFactoryError,
    PydanticModelBinding,
    create_pydantic_model_binding_for_use_case,
)
from .ai_model_errors import model_http_error_payload, safe_model_error_text
from .ai_tool_bridge import AiToolBridgeError, build_pydantic_toolset
from .ai_tool_registry import AiToolSet
from .ai_tool_runtime import AiToolRuntime


OutputT = TypeVar("OutputT")
OutputValidator = Callable[[RunContext[AiAgentDependencies], OutputT], OutputT]
ModelBindingFactory = Callable[..., PydanticModelBinding]


async def _record_agent_stream_events(
    recorder: AiWorkRecorder,
    events: Any,
) -> None:
    """把 Pydantic Agent 模型流投影为 AI Work 的推理与正文事件。"""

    async for event in events:
        if isinstance(event, PartStartEvent):
            if isinstance(event.part, ThinkingPart):
                recorder.emit_reasoning_delta(event.part.content)
            elif isinstance(event.part, TextPart):
                recorder.emit_text_delta(event.part.content)
        elif isinstance(event, PartDeltaEvent):
            if isinstance(event.delta, ThinkingPartDelta):
                recorder.emit_reasoning_delta(event.delta.content_delta or "")
            elif isinstance(event.delta, TextPartDelta):
                recorder.emit_text_delta(event.delta.content_delta)
        elif isinstance(event, PartEndEvent) and isinstance(
            event.part,
            ThinkingPart,
        ):
            recorder.finish_reasoning_message()


def _agent_event_stream_handler(
    model: Model,
    recorder: AiWorkRecorder,
) -> Callable[..., Any] | None:
    # FunctionModel 可以只实现同步 request；这种测试/本地模型若强制挂载
    # event_stream_handler，Pydantic 会切换到其未实现的 request_stream。
    if hasattr(model, "stream_function") and getattr(model, "stream_function") is None:
        return None

    async def handle(_context: Any, events: Any) -> None:
        await _record_agent_stream_events(recorder, events)

    return handle


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
    approval_permission: str = ""

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

    output: OutputT | DeferredToolRequests
    conversation_id: str
    task_run_id: str
    attempt_id: str
    run_id: str
    trace_id: str
    usage: dict[str, int]
    messages: list[ModelMessage] = field(repr=False)
    recorder: AiWorkRecorder = field(repr=False)
    deferred_state_id: str = ""
    resume_claim_id: str = ""
    _state_store: AiAgentStateStore | None = field(default=None, repr=False)
    _terminal: bool = field(default=False, init=False, repr=False)

    @property
    def deferred(self) -> bool:
        return isinstance(self.output, DeferredToolRequests)

    def complete(self, summary: Mapping[str, Any]) -> None:
        if self._terminal:
            return
        # 先落业务投影，再提交 durable state 终态。若进程在两步之间退出，
        # ready 结果仍可安全重放，且不会再次执行 model/tool；反向顺序会留下
        # completed 但缺少 RUN_FINISHED、之后也无法补写的不可恢复窗口。
        projected = sanitize_ai_work_value(dict(summary))
        if not isinstance(projected, dict):
            projected = {}
        self.recorder.finish(projected)
        if self._state_store is not None and self.deferred_state_id:
            envelope = self._state_store.load(self.deferred_state_id)
            if envelope.status == "ready":
                self._state_store.mark_completed(
                    self.deferred_state_id,
                    claim_id=self.resume_claim_id,
                )
        self._terminal = True

    def fail(self, error: AiAgentExecutionError) -> None:
        if self._terminal:
            return
        # 与 complete() 使用相同的可恢复提交顺序：投影失败时保持原状态，
        # 调用方可重试；投影成功而状态尚未提交时，durable ready/pending 状态
        # 仍保留恢复依据，而不会出现终态已锁死但 AI Work 永久缺失 RUN_ERROR。
        self.recorder.fail(error)
        if self._state_store is not None and self.deferred_state_id:
            envelope = self._state_store.load(self.deferred_state_id)
            if envelope.status in {"pending", "resuming", "ready"}:
                self._state_store.mark_failed(
                    self.deferred_state_id,
                    claim_id=self.resume_claim_id,
                )
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


def _dump_typed_output(output_type: type[OutputT], output: OutputT) -> Any:
    try:
        return TypeAdapter(output_type).dump_python(output, mode="json")
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_RESULT_INVALID",
            "Agent 恢复结果无法持久化。",
        ) from None


def _load_typed_output(output_type: type[OutputT], payload: Any) -> OutputT:
    try:
        return TypeAdapter(output_type).validate_python(payload)
    except Exception:
        raise AiAgentStateError(
            "AI_AGENT_STATE_RESULT_INVALID",
            "Agent 恢复结果损坏或与当前 profile 不一致。",
        ) from None


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
        message = f"HTTP {provider_error['status_code']}: {provider_error['message']}"
        if provider_error["request_id"]:
            message += f" (request_id={provider_error['request_id']})"
        status_code = int(provider_error["status_code"])
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
    if isinstance(exc, AiAgentStateError):
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


class AiAgentFactory:
    """唯一 Pydantic Agent 装配 owner。"""

    def __init__(
        self,
        *,
        app_dir: Path | str,
        app_config: dict[str, Any] | None,
        journal: Any,
        model_binding_factory: ModelBindingFactory = (
            create_pydantic_model_binding_for_use_case
        ),
        instrumentation: AiAgentInstrumentation | None = None,
        state_store: AiAgentStateStore | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.app_config = dict(app_config or {})
        self.journal = journal
        self.model_binding_factory = model_binding_factory
        self.instrumentation = instrumentation or AiAgentInstrumentation(
            self.app_dir / "data" / "logs" / "ai_traces" / "agent_spans.jsonl"
        )
        self.state_store = state_store or AiAgentStateStore(self.app_dir)

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
        """集中装配初始运行和恢复运行共用的唯一 Agent 定义。"""

        agent: Agent[AiAgentDependencies, OutputT] = Agent(
            model_override or binding.model,
            output_type=[profile.output_type, DeferredToolRequests],
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

    def _replay_ready_outcome(
        self,
        *,
        state_id: str,
        profile: AiAgentExecutionProfile[OutputT],
        approver_id: str,
        tenant_id: str,
        permissions: frozenset[str] | set[str] | tuple[str, ...],
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
    ) -> AiAgentRunOutcome[OutputT]:
        """只读取 durable ready 结果，不再次调用 model 或工具。"""

        envelope = self.state_store.load_ready_for_replay(
            state_id,
            use_case_id=profile.use_case_id,
            profile_version=profile.result_version,
            toolset_id=profile.toolset_id,
            tenant_id=tenant_id,
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            permissions=permissions,
        )
        result = envelope.resume_result
        claim = envelope.resume_claim
        if result is None or claim is None:
            raise AiAgentStateError(
                "AI_AGENT_STATE_RESULT_INVALID",
                "Agent 恢复结果损坏或与当前 profile 不一致。",
            )
        output = _load_typed_output(profile.output_type, result.output_payload)
        conversation_id = str(envelope.references.get("conversation_id") or "")
        execution_context = AiExecutionContext(
            task_run_id=str(envelope.references.get("task_run_id") or ""),
            attempt_id=result.attempt_id,
            deadline_at=envelope.deadline_at,
            budget_profile=profile.budget_profile,
            actor_id=approver_id,
            tenant_id=tenant_id,
            permissions=frozenset(permissions),
            business_scope=business_scope,
            idempotency_context=idempotency_context,
            allow_write=profile.allow_write,
        )
        conversation = self.journal.resume_conversation(
            conversation_id,
            trace_context={
                **execution_context.trace_payload(),
                "trace_id": result.trace_id,
                "run_id": result.run_id,
            },
        )
        recorder = ConversationAiWorkRecorder(conversation, execution_context)
        recorder.record(
            "AGENT_RESUME_RESULT_REPLAYED",
            state_id=state_id,
            trace_id=result.trace_id,
            run_id=result.run_id,
        )
        return AiAgentRunOutcome(
            output=output,
            conversation_id=conversation_id,
            task_run_id=execution_context.task_run_id,
            attempt_id=result.attempt_id,
            run_id=result.run_id,
            trace_id=result.trace_id,
            usage={key: int(value) for key, value in result.usage.items()},
            messages=list(result.message_history),
            recorder=recorder,
            deferred_state_id=state_id,
            resume_claim_id=claim.claim_id,
            _state_store=self.state_store,
        )

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
        input_summary: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
        model_override: Model | None = None,
    ) -> AiAgentRunOutcome[OutputT]:
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
        conversation = None
        recorder: AiWorkRecorder | None = None
        run_id = execution_context.attempt_id
        trace_id = ""
        captured_messages: list[ModelMessage] = []
        transcript_recorded = False
        try:
            binding = self.model_binding_factory(
                self.app_dir,
                self.app_config,
                profile.use_case_id,
                timeout_seconds=effective_timeout,
                default_timeout_seconds=profile.timeout_seconds,
            )
            conversation = self.journal.start_conversation(
                use_case_id=profile.use_case_id,
                capability="agent",
                provider_id=binding.provider_id,
                model={
                    "id": binding.model_id,
                    "provider_id": binding.provider_id,
                    "model": binding.model_name,
                },
                required_capabilities=["chat", "json", "tool_calling"],
                timeout_seconds=max(1, int(effective_timeout)),
                input_payload=dict(input_summary or {}),
                trace_context=execution_context.trace_payload(),
            )
            recorder = ConversationAiWorkRecorder(conversation, execution_context)
            runtime = AiToolRuntime(
                toolset=toolset,
                execution_context=execution_context,
                recorder=recorder,
                max_tool_calls=profile.max_tool_calls,
                max_output_bytes=profile.max_tool_output_bytes,
            )
            dependencies = AiAgentDependencies(
                use_case_id=profile.use_case_id,
                execution_context=execution_context,
                recorder=recorder,
                tool_runtime=runtime,
                use_case_state=use_case_state,
                invocation_id=execution_context.attempt_id,
                ai_work_id=conversation.conversation_id,
            )
            agent = self._build_agent(
                profile=profile,
                binding=binding,
                instructions=instructions,
                toolset=toolset,
                output_validator=output_validator,
                model_override=model_override,
            )
            recorder.emit_custom(
                AGENT_REQUEST_EVENT,
                build_agent_request_observation(
                    instructions=instructions,
                    user_prompt=user_prompt,
                    output_type=profile.output_type,
                    toolset=toolset,
                    model_settings=binding.model_settings,
                    max_model_requests=profile.max_model_requests,
                    max_tool_calls=profile.max_tool_calls,
                    timeout_seconds=effective_timeout,
                ),
            )
            entity_ids = {
                key: value
                for key, value in dict(business_scope or {}).items()
                if str(key).endswith("_id")
            }
            with capture_run_messages() as captured_messages:
                with self.instrumentation.start_run_span(
                    use_case_id=profile.use_case_id,
                    ai_work_task_id=conversation.conversation_id,
                    invocation_id=execution_context.attempt_id,
                    business_entity_ids=entity_ids,
                ) as technical_trace:
                    trace_id = technical_trace.trace_id
                    technical_trace.set_agent_run_id(run_id)
                    result = agent.run_sync(
                        user_prompt,
                        deps=dependencies,
                        conversation_id=conversation.conversation_id,
                        run_id=run_id,
                        usage_limits=UsageLimits(
                            request_limit=profile.max_model_requests,
                            tool_calls_limit=profile.max_tool_calls,
                        ),
                        event_stream_handler=_agent_event_stream_handler(
                            model_override or binding.model,
                            recorder,
                        ),
                    )
                    recorder.finish_assistant_message()
                    run_id = str(result.run_id or "")
                    execution_context.bounded_timeout_seconds()
                    technical_trace.set_agent_run_id(run_id)
            recorder.emit_custom(
                AGENT_TRANSCRIPT_EVENT,
                build_agent_transcript_observation(captured_messages),
            )
            transcript_recorded = True
            recorder.record(
                "AGENT_TRACE_LINKED",
                trace_id=technical_trace.trace_id,
                run_id=run_id,
            )
            messages = list(result.all_messages())
            deferred_state_id = ""
            if isinstance(result.output, DeferredToolRequests):
                required_permissions = set(profile.permissions)
                if profile.approval_permission:
                    required_permissions.add(profile.approval_permission)
                envelope = self.state_store.create_pending(
                    use_case_id=profile.use_case_id,
                    profile_version=profile.result_version,
                    toolset_id=profile.toolset_id,
                    deadline_at=execution_context.deadline_at,
                    actor_id=execution_context.actor_id,
                    tenant_id=execution_context.tenant_id,
                    required_permissions=required_permissions,
                    business_scope=execution_context.business_scope,
                    idempotency_context=execution_context.idempotency_context,
                    message_history=messages,
                    deferred_requests=result.output,
                    references={
                        "conversation_id": conversation.conversation_id,
                        "task_run_id": execution_context.task_run_id,
                        "run_id": run_id,
                        "trace_id": technical_trace.trace_id,
                        "toolset_contract_fingerprint": (
                            toolset.toolset_contract_fingerprint
                        ),
                    },
                )
                deferred_state_id = envelope.state_id
                recorder.emit(
                    "RUN_DEFERRED",
                    state_id=envelope.state_id,
                    approval_count=len(result.output.approvals),
                    external_call_count=len(result.output.calls),
                )
            return AiAgentRunOutcome(
                output=result.output,
                conversation_id=conversation.conversation_id,
                task_run_id=execution_context.task_run_id,
                attempt_id=execution_context.attempt_id,
                run_id=run_id,
                trace_id=technical_trace.trace_id,
                usage=_safe_usage(result.usage),
                messages=messages,
                recorder=recorder,
                deferred_state_id=deferred_state_id,
                _state_store=self.state_store,
            )
        except Exception as exc:
            if recorder is not None and captured_messages and not transcript_recorded:
                recorder.emit_custom(
                    AGENT_TRANSCRIPT_EVENT,
                    build_agent_transcript_observation(captured_messages),
                )
            error = _safe_agent_error(
                exc,
                validator=output_validator,
                model_messages=captured_messages,
                conversation_id=(conversation.conversation_id if conversation else ""),
                task_run_id=execution_context.task_run_id,
                run_id=run_id,
                trace_id=trace_id,
            )
            if recorder is not None:
                recorder.fail(error)
            raise error from None

    def resume_sync(
        self,
        *,
        state_id: str,
        profile: AiAgentExecutionProfile[OutputT],
        instructions: str,
        toolset: AiToolSet,
        approval_decisions: Mapping[str, bool] | None = None,
        external_results: Mapping[str, Any] | None = None,
        approver_id: str,
        tenant_id: str,
        permissions: frozenset[str] | set[str] | tuple[str, ...],
        business_scope: Mapping[str, str],
        idempotency_context: Mapping[str, str],
        use_case_state: Any = None,
        output_validator: OutputValidator[OutputT] | None = None,
        model_override: Model | None = None,
    ) -> AiAgentRunOutcome[OutputT]:
        """以新 dependencies/Runtime 恢复一次持久化 deferred Agent run。"""

        if toolset.toolset_id != profile.toolset_id:
            raise AiAgentExecutionError(
                "TOOLSET_BINDING_MISMATCH",
                "Agent execution profile 与 ToolSet 不一致。",
            )
        conversation = None
        recorder: AiWorkRecorder | None = None
        claimed = False
        denied = False
        ready_persisted = False
        envelope = None
        claim_id = ""
        run_id = ""
        trace_id = ""
        captured_messages: list[ModelMessage] = []
        transcript_recorded = False
        try:
            now = datetime.now(timezone.utc)
            envelope = self.state_store.load(state_id)
            stored_contract_fingerprint = str(
                envelope.references.get("toolset_contract_fingerprint") or ""
            )
            legacy_toolset_signature = str(
                envelope.references.get("toolset_signature") or ""
            )
            contract_mismatch = bool(
                stored_contract_fingerprint
                and stored_contract_fingerprint
                != toolset.toolset_contract_fingerprint
            )
            legacy_mismatch = bool(
                not stored_contract_fingerprint
                and legacy_toolset_signature
                and legacy_toolset_signature != toolset.legacy_toolset_signature
            )
            if contract_mismatch or legacy_mismatch:
                raise AiAgentStateError(
                    "AI_AGENT_STATE_TOOLSET_MISMATCH",
                    "Agent 恢复工具集与持久化状态不一致。",
                )
            if envelope.status == "resuming":
                claim = envelope.resume_claim
                if claim is None or now >= claim.lease_expires_at:
                    envelope = self.state_store.recover_expired_claim(
                        state_id,
                        now=now,
                    )
            if envelope.status == "ready":
                return self._replay_ready_outcome(
                    state_id=state_id,
                    profile=profile,
                    approver_id=approver_id,
                    tenant_id=tenant_id,
                    permissions=permissions,
                    business_scope=business_scope,
                    idempotency_context=idempotency_context,
                )
            if envelope.status == "in_doubt":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_EXECUTION_IN_DOUBT",
                    "Agent 恢复期间可能已经执行写工具，禁止自动重放。",
                )
            if envelope.status != "pending":
                raise AiAgentStateError(
                    "AI_AGENT_STATE_ALREADY_CLAIMED",
                    "Agent 恢复状态已被领取或已经结束。",
                )
            approval_ids = {
                str(part.tool_call_id or "")
                for part in envelope.deferred_requests.approvals
            }
            decisions = {
                str(call_id): bool(decision)
                for call_id, decision in dict(approval_decisions or {}).items()
            }
            if set(decisions) != approval_ids:
                raise AiAgentStateError(
                    "AI_AGENT_STATE_APPROVAL_REQUIRED",
                    "Agent 恢复所需的工具审批尚未全部决定。",
                )
            approval_records = [
                AiAgentApprovalRecord(
                    tool_call_id=call_id,
                    decision="approved" if decision else "denied",
                    actor_id=approver_id,
                    decided_at=now,
                )
                for call_id, decision in decisions.items()
            ]
            resume_kwargs = {
                "use_case_id": profile.use_case_id,
                "profile_version": profile.result_version,
                "toolset_id": profile.toolset_id,
                "tenant_id": tenant_id,
                "business_scope": business_scope,
                "idempotency_context": idempotency_context,
                "permissions": permissions,
                "approval_records": approval_records,
                "now": now,
            }
            denied = any(not decision for decision in decisions.values())
            deferred_results = envelope.deferred_requests.build_results(
                approvals={
                    call_id: ToolApproved()
                    if decision
                    else ToolDenied("工具审批已拒绝。")
                    for call_id, decision in decisions.items()
                },
                calls=dict(external_results or {}),
                metadata={
                    call_id: {"approval_actor_id": approver_id}
                    for call_id in decisions
                },
            )
            binding = self.model_binding_factory(
                self.app_dir,
                self.app_config,
                profile.use_case_id,
                timeout_seconds=max(
                    0.001,
                    (envelope.deadline_at - now).total_seconds(),
                ),
                default_timeout_seconds=profile.timeout_seconds,
            )
            if denied:
                envelope = self.state_store.mark_denied(state_id, **resume_kwargs)
            else:
                envelope = self.state_store.claim_for_resume(
                    state_id,
                    lease_seconds=min(
                        300.0,
                        max(0.001, (envelope.deadline_at - now).total_seconds()),
                    ),
                    **resume_kwargs,
                )
                if envelope.resume_claim is None:
                    raise AiAgentStateError(
                        "AI_AGENT_STATE_CLAIM_MISMATCH",
                        "Agent 恢复 claim 未正确持久化。",
                    )
                claim_id = envelope.resume_claim.claim_id
                claimed = True

            approved_ids = frozenset(
                call_id for call_id, decision in decisions.items() if decision
            )
            execution_context = AiExecutionContext(
                task_run_id=str(envelope.references.get("task_run_id") or ""),
                attempt_id=f"attempt_{uuid4().hex}",
                deadline_at=envelope.deadline_at,
                budget_profile=profile.budget_profile,
                actor_id=approver_id,
                tenant_id=tenant_id,
                permissions=frozenset(permissions),
                business_scope=business_scope,
                idempotency_context=idempotency_context,
                approved_tool_call_ids=approved_ids,
                allow_write=profile.allow_write,
            )
            run_id = execution_context.attempt_id
            conversation_id = str(envelope.references.get("conversation_id") or "")
            conversation = self.journal.resume_conversation(
                conversation_id,
                trace_context=execution_context.trace_payload(),
            )
            recorder = ConversationAiWorkRecorder(conversation, execution_context)
            runtime = AiToolRuntime(
                toolset=toolset,
                execution_context=execution_context,
                recorder=recorder,
                max_tool_calls=profile.max_tool_calls,
                max_output_bytes=profile.max_tool_output_bytes,
                before_executor=(
                    lambda command: self.state_store.mark_tool_execution_started(
                        state_id,
                        claim_id=claim_id,
                    )
                )
                if claimed
                else None,
            )
            dependencies = AiAgentDependencies(
                use_case_id=profile.use_case_id,
                execution_context=execution_context,
                recorder=recorder,
                tool_runtime=runtime,
                use_case_state=use_case_state,
                invocation_id=execution_context.attempt_id,
                ai_work_id=conversation_id,
            )
            agent = self._build_agent(
                profile=profile,
                binding=binding,
                instructions=instructions,
                toolset=toolset,
                output_validator=output_validator,
                model_override=model_override,
            )
            recorder.emit_custom(
                AGENT_REQUEST_EVENT,
                build_agent_request_observation(
                    instructions=instructions,
                    user_prompt=None,
                    output_type=profile.output_type,
                    toolset=toolset,
                    model_settings=binding.model_settings,
                    max_model_requests=profile.max_model_requests,
                    max_tool_calls=profile.max_tool_calls,
                    timeout_seconds=execution_context.bounded_timeout_seconds(),
                    mode="resume",
                    message_history_count=len(envelope.message_history),
                ),
            )
            entity_ids = {
                key: value
                for key, value in dict(business_scope).items()
                if str(key).endswith("_id")
            }
            with capture_run_messages() as captured_messages:
                with self.instrumentation.start_run_span(
                    use_case_id=profile.use_case_id,
                    ai_work_task_id=conversation_id,
                    invocation_id=execution_context.attempt_id,
                    business_entity_ids=entity_ids,
                ) as technical_trace:
                    trace_id = technical_trace.trace_id
                    technical_trace.set_agent_run_id(run_id)
                    result = agent.run_sync(
                        None,
                        message_history=envelope.message_history,
                        deferred_tool_results=deferred_results,
                        deps=dependencies,
                        conversation_id=conversation_id,
                        run_id=execution_context.attempt_id,
                        usage_limits=UsageLimits(
                            request_limit=profile.max_model_requests,
                            tool_calls_limit=profile.max_tool_calls,
                        ),
                        event_stream_handler=_agent_event_stream_handler(
                            model_override or binding.model,
                            recorder,
                        ),
                    )
                    recorder.finish_assistant_message()
                    run_id = str(result.run_id or "")
                    execution_context.bounded_timeout_seconds()
                    technical_trace.set_agent_run_id(run_id)
            messages = list(result.all_messages())
            recorder.emit_custom(
                AGENT_TRANSCRIPT_EVENT,
                build_agent_transcript_observation(captured_messages),
            )
            transcript_recorded = True
            recorder.record(
                "AGENT_TRACE_LINKED",
                trace_id=technical_trace.trace_id,
                run_id=run_id,
            )
            usage = _safe_usage(result.usage)
            if isinstance(result.output, DeferredToolRequests):
                if denied:
                    raise AiAgentStateError(
                        "AI_AGENT_STATE_DENIED",
                        "工具审批已拒绝，本次 Agent 运行已经结束。",
                    )
                self.state_store.replace_pending_after_resume(
                    state_id,
                    claim_id=claim_id,
                    message_history=messages,
                    deferred_requests=result.output,
                )
                claim_id = ""
                recorder.emit(
                    "RUN_DEFERRED",
                    state_id=state_id,
                    approval_count=len(result.output.approvals),
                    external_call_count=len(result.output.calls),
                )
            elif not denied:
                self.state_store.mark_resume_ready(
                    state_id,
                    claim_id=claim_id,
                    message_history=messages,
                    output_payload=_dump_typed_output(
                        profile.output_type,
                        result.output,
                    ),
                    run_id=run_id,
                    attempt_id=execution_context.attempt_id,
                    trace_id=technical_trace.trace_id,
                    usage=usage,
                )
                ready_persisted = True
            return AiAgentRunOutcome(
                output=result.output,
                conversation_id=conversation_id,
                task_run_id=execution_context.task_run_id,
                attempt_id=execution_context.attempt_id,
                run_id=run_id,
                trace_id=technical_trace.trace_id,
                usage=usage,
                messages=messages,
                recorder=recorder,
                deferred_state_id=state_id,
                resume_claim_id=claim_id,
                _state_store=self.state_store,
            )
        except Exception as exc:
            if recorder is not None and captured_messages and not transcript_recorded:
                recorder.emit_custom(
                    AGENT_TRANSCRIPT_EVENT,
                    build_agent_transcript_observation(captured_messages),
                )
            task_run_id = (
                str(envelope.references.get("task_run_id") or "")
                if envelope is not None
                else ""
            )
            conversation_id = (
                str(envelope.references.get("conversation_id") or "")
                if envelope is not None
                else ""
            )
            error = _safe_agent_error(
                exc,
                validator=output_validator,
                model_messages=captured_messages,
                conversation_id=conversation_id,
                task_run_id=task_run_id,
                run_id=run_id,
                trace_id=trace_id,
            )
            if claimed and claim_id:
                try:
                    if ready_persisted:
                        terminal = self.state_store.mark_failed(
                            state_id,
                            claim_id=claim_id,
                        )
                    elif error.retryable:
                        terminal = self.state_store.release_claim_for_retry(
                            state_id,
                            claim_id=claim_id,
                        )
                    else:
                        terminal = self.state_store.mark_failed(
                            state_id,
                            claim_id=claim_id,
                        )
                    if terminal.status == "in_doubt":
                        error = AiAgentExecutionError(
                            "AI_AGENT_STATE_EXECUTION_IN_DOUBT",
                            "Agent 恢复期间可能已经执行写工具，禁止自动重放。",
                            conversation_id=conversation_id,
                            task_run_id=task_run_id,
                            run_id=run_id,
                            trace_id=trace_id,
                        )
                    elif terminal.status != "pending":
                        error.retryable = False
                except AiAgentStateError as state_exc:
                    error = _safe_agent_error(
                        state_exc,
                        validator=None,
                        conversation_id=conversation_id,
                        task_run_id=task_run_id,
                        run_id=run_id,
                        trace_id=trace_id,
                    )
                    error.retryable = False
            elif denied:
                error.retryable = False
            if recorder is not None:
                recorder.fail(error)
            raise error from None


__all__ = [
    "AiAgentExecutionError",
    "AiAgentExecutionProfile",
    "AiAgentFactory",
    "AiAgentRunOutcome",
]
