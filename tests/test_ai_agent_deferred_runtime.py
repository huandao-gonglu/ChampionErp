from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings

from erp_web.context import get_context
from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_factory import (
    AiAgentExecutionError,
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from erp_web.services.ai_model_factory import PydanticModelBinding
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)
from erp_web.stores.pydantic_message_store import PydanticMessageStore


class WriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved: bool


PROFILE = AiAgentExecutionProfile(
    use_case_id="inventory.adjust",
    output_type=WriteOutput,
    toolset_id="inventory.write",
    budget_profile="inventory.adjust.v1",
    permissions=frozenset({"inventory.write"}),
    approval_permission="inventory.approve",
    timeout_seconds=30,
    max_model_requests=4,
    max_tool_calls=2,
    max_tool_output_bytes=4096,
    retries=1,
    result_version="inventory_adjust.v1",
    allow_write=True,
)


def write_toolset(
    executor,
    *,
    version: str = "1",
    description: str = "保存库存数量",
) -> AiToolSet:
    definition = AiToolDefinition(
        name="save_inventory",
        version=version,
        description=description,
        input_schema={
            "type": "object",
            "required": ["sku", "quantity"],
            "properties": {
                "sku": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["saved"],
            "properties": {"saved": {"type": "boolean"}},
            "additionalProperties": False,
        },
        required_permission="inventory.write",
        side_effect="write",
        approval_required=True,
        idempotency="required",
        idempotency_keys=("operation_id",),
    )
    return AiToolSet.bind(
        "inventory.write",
        [definition],
        {"save_inventory": deadline_aware_tool_executor(executor)},
    )


def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
    tool_returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if not tool_returns:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "save_inventory",
                    {"sku": "sku-1", "quantity": 7},
                    tool_call_id="write-call-1",
                )
            ]
        )
    saved = isinstance(tool_returns[-1].content, dict)
    return ModelResponse(
        parts=[
            ToolCallPart(
                info.output_tools[0].name,
                {"saved": saved},
                tool_call_id="final-after-decision",
            )
        ]
    )


def factory(model: FunctionModel) -> AiAgentFactory:
    context = get_context()

    def binding(*args, **kwargs):
        del args, kwargs
        return PydanticModelBinding(
            model=model,
            model_settings=ModelSettings(temperature=0),
            model_id="test-write-model",
            model_name="test-write-model",
            provider_id="test",
            provider_family="test",
            api_style="chat_completions",
        )

    return AiAgentFactory(
        app_dir=context.paths.app_dir,
        app_config={},
        message_store=PydanticMessageStore(context.db),
        model_binding_factory=binding,
    )


RUN_SCOPE = {"store_id": "store-1", "sku_id": "sku-1"}
IDEMPOTENCY = {"operation_id": "operation-1"}


def start_deferred(agent_factory: AiAgentFactory, toolset: AiToolSet):
    return agent_factory.run_sync(
        profile=PROFILE,
        instructions="调用写工具保存库存。",
        user_prompt="将 sku-1 库存改为 7。",
        toolset=toolset,
        tenant_id="tenant-1",
        business_scope=RUN_SCOPE,
        idempotency_context=IDEMPOTENCY,
    )


def test_approval_persists_and_resumes_with_fresh_runtime_exactly_once() -> None:
    executions: list[AiExecutionContext] = []

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        assert arguments == {"sku": "sku-1", "quantity": 7}
        executions.append(context)
        return {"saved": True}

    first_factory = factory(FunctionModel(model_function))
    paused = start_deferred(first_factory, write_toolset(executor))

    assert paused.deferred is True
    assert paused.deferred_state_id
    assert executions == []
    pending = first_factory.state_store.load(paused.deferred_state_id)
    assert pending.status == "pending"
    assert pending.references["conversation_id"] == paused.conversation_id
    assert pending.references["toolset_contract_fingerprint"] == (
        write_toolset(executor).toolset_contract_fingerprint
    )
    assert "toolset_signature" not in pending.references
    assert pending.security.required_permissions == {
        "inventory.write",
        "inventory.approve",
    }
    paused_history = first_factory.message_store.get(paused.conversation_id)
    assert paused_history is not None
    assert paused_history.messages_json == ModelMessagesTypeAdapter.dump_json(
        paused.messages
    )

    restarted_factory = factory(FunctionModel(model_function))
    resumed = restarted_factory.resume_sync(
        state_id=paused.deferred_state_id,
        profile=PROFILE,
        instructions="调用写工具保存库存。",
        toolset=write_toolset(executor),
        approval_decisions={"write-call-1": True},
        approver_id="approver-1",
        tenant_id="tenant-1",
        permissions={"inventory.write", "inventory.approve"},
        business_scope=RUN_SCOPE,
        idempotency_context=IDEMPOTENCY,
    )

    assert resumed.output == WriteOutput(saved=True)
    assert resumed.conversation_id == paused.conversation_id
    assert resumed.attempt_id != paused.attempt_id
    assert len(executions) == 1
    assert executions[0].actor_id == "approver-1"
    assert executions[0].approved_tool_call_ids == {"write-call-1"}
    assert dict(executions[0].business_scope) == RUN_SCOPE
    assert dict(executions[0].idempotency_context) == IDEMPOTENCY
    ready = restarted_factory.state_store.load(paused.deferred_state_id)
    assert ready.status == "ready"
    assert ready.resume_result is not None
    assert ready.resume_result.output_payload == {"saved": True}
    resumed_history = restarted_factory.message_store.get(paused.conversation_id)
    assert resumed_history is not None
    assert resumed_history.messages_json == ModelMessagesTypeAdapter.dump_json(
        resumed.messages
    )
    assert len(resumed.messages) > len(paused.messages)

    replayed = factory(FunctionModel(model_function)).resume_sync(
        state_id=paused.deferred_state_id,
        profile=PROFILE,
        instructions="调用写工具保存库存。",
        toolset=write_toolset(executor),
        approval_decisions={"write-call-1": True},
        approver_id="approver-1",
        tenant_id="tenant-1",
        permissions={"inventory.write", "inventory.approve"},
        business_scope=RUN_SCOPE,
        idempotency_context=IDEMPOTENCY,
    )
    assert replayed.output == resumed.output
    assert replayed.run_id == resumed.run_id
    assert len(executions) == 1

    replayed.complete()
    assert restarted_factory.state_store.load(paused.deferred_state_id).status == "completed"

    with pytest.raises(AiAgentExecutionError) as duplicate:
        restarted_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor),
            approval_decisions={"write-call-1": True},
            approver_id="approver-1",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )
    assert duplicate.value.code == "AI_AGENT_STATE_ALREADY_CLAIMED"
    assert len(executions) == 1


@pytest.mark.parametrize("terminal_action", ["complete", "fail"])
def test_terminal_action_commits_durable_state_once(
    terminal_action: str,
) -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    agent_factory = factory(FunctionModel(model_function))
    paused = start_deferred(agent_factory, write_toolset(executor))
    resumed = agent_factory.resume_sync(
        state_id=paused.deferred_state_id,
        profile=PROFILE,
        instructions="调用写工具保存库存。",
        toolset=write_toolset(executor),
        approval_decisions={"write-call-1": True},
        approver_id="approver-1",
        tenant_id="tenant-1",
        permissions={"inventory.write", "inventory.approve"},
        business_scope=RUN_SCOPE,
        idempotency_context=IDEMPOTENCY,
    )
    if terminal_action == "complete":
        resumed.complete()
        expected_status = "completed"
    else:
        resumed.fail(
            AiAgentExecutionError(
                "BUSINESS_VALIDATION_FAILED",
                "业务终检失败。",
            )
        )
        expected_status = "failed"
    assert agent_factory.state_store.load(paused.deferred_state_id).status == expected_status
    assert executions == 1
    if terminal_action == "complete":
        resumed.complete()
    else:
        resumed.fail(AiAgentExecutionError("IGNORED", "重复终态调用。"))
    assert agent_factory.state_store.load(paused.deferred_state_id).status == expected_status


def test_denial_resumes_model_but_never_executes_write() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    agent_factory = factory(FunctionModel(model_function))
    paused = start_deferred(agent_factory, write_toolset(executor))
    resumed = agent_factory.resume_sync(
        state_id=paused.deferred_state_id,
        profile=PROFILE,
        instructions="调用写工具保存库存。",
        toolset=write_toolset(executor),
        approval_decisions={"write-call-1": False},
        approver_id="approver-2",
        tenant_id="tenant-1",
        permissions={"inventory.write", "inventory.approve"},
        business_scope=RUN_SCOPE,
        idempotency_context=IDEMPOTENCY,
    )

    assert executions == 0
    assert resumed.output == WriteOutput(saved=False)
    resumed.complete()
    denied = agent_factory.state_store.load(paused.deferred_state_id)
    assert denied.status == "denied"
    assert denied.approval_records[0].decision == "denied"


def test_provider_failure_after_write_becomes_in_doubt_and_is_never_replayed() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    def fail_after_write(messages: list[Any], info: AgentInfo) -> ModelResponse:
        tool_returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not tool_returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "save_inventory",
                        {"sku": "sku-1", "quantity": 7},
                        tool_call_id="write-call-1",
                    )
                ]
            )
        del info
        raise ModelAPIError("test-model", "不得泄露的 provider body")

    agent_factory = factory(FunctionModel(fail_after_write))
    paused = start_deferred(agent_factory, write_toolset(executor))

    with pytest.raises(AiAgentExecutionError) as captured:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor),
            approval_decisions={"write-call-1": True},
            approver_id="approver-1",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )

    assert captured.value.code == "AI_AGENT_STATE_EXECUTION_IN_DOUBT"
    assert captured.value.retryable is False
    assert captured.value.trace_id
    assert captured.value.run_id
    assert executions == 1
    assert agent_factory.state_store.load(paused.deferred_state_id).status == "in_doubt"
    failed_history = agent_factory.message_store.get(paused.conversation_id)
    assert failed_history is not None
    assert len(failed_history.model_messages()) > len(paused.messages)

    with pytest.raises(AiAgentExecutionError) as duplicate:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor),
            approval_decisions={"write-call-1": True},
            approver_id="approver-1",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )
    assert duplicate.value.code == "AI_AGENT_STATE_EXECUTION_IN_DOUBT"
    assert executions == 1


@pytest.mark.parametrize(
    ("permissions", "scope", "expected"),
    [
        (
            {"inventory.write"},
            RUN_SCOPE,
            "AI_AGENT_STATE_PERMISSION_DENIED",
        ),
        (
            {"inventory.write", "inventory.approve"},
            {"store_id": "other-store", "sku_id": "sku-1"},
            "AI_AGENT_STATE_SCOPE_MISMATCH",
        ),
    ],
)
def test_resume_revalidates_current_permission_and_scope(
    permissions: set[str],
    scope: dict[str, str],
    expected: str,
) -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    agent_factory = factory(FunctionModel(model_function))
    paused = start_deferred(agent_factory, write_toolset(executor))

    with pytest.raises(AiAgentExecutionError) as captured:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor),
            approval_decisions={"write-call-1": True},
            approver_id="approver-3",
            tenant_id="tenant-1",
            permissions=permissions,
            business_scope=scope,
            idempotency_context=IDEMPOTENCY,
        )

    assert captured.value.code == expected
    assert agent_factory.state_store.load(paused.deferred_state_id).status == "pending"
    assert executions == 0


def test_invalid_tool_arguments_are_rejected_before_approval_is_created() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    def invalid_model(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(
            parts=[ToolCallPart("save_inventory", {}, tool_call_id="invalid-write")]
        )

    agent_factory = factory(FunctionModel(invalid_model))
    with pytest.raises(AiAgentExecutionError) as captured:
        start_deferred(agent_factory, write_toolset(executor))

    assert captured.value.code == "TOOL_INPUT_SCHEMA_INVALID"
    assert str(captured.value) == "$ 缺少必填字段：sku, quantity"
    assert captured.value.retryable is False
    failed_history = agent_factory.message_store.get(captured.value.conversation_id)
    assert failed_history is not None
    assert failed_history.model_messages()
    assert executions == 0
    assert not list(agent_factory.state_store.root.glob("*.json"))


def test_resume_rejects_changed_tool_version_before_claim() -> None:
    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        raise AssertionError("工具版本不一致时不得执行")

    agent_factory = factory(FunctionModel(model_function))
    paused = start_deferred(agent_factory, write_toolset(executor))

    with pytest.raises(AiAgentExecutionError) as captured:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor, version="2"),
            approval_decisions={"write-call-1": True},
            approver_id="approver-4",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )

    assert captured.value.code == "AI_AGENT_STATE_TOOLSET_MISMATCH"
    assert agent_factory.state_store.load(paused.deferred_state_id).status == "pending"


def test_resume_rejects_same_version_contract_change_before_claim() -> None:
    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        raise AssertionError("工具契约不一致时不得执行")

    agent_factory = factory(FunctionModel(model_function))
    paused = start_deferred(agent_factory, write_toolset(executor))

    with pytest.raises(AiAgentExecutionError) as captured:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=write_toolset(executor, description="同版本但语义已变化"),
            approval_decisions={"write-call-1": True},
            approver_id="approver-4",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )

    assert captured.value.code == "AI_AGENT_STATE_TOOLSET_MISMATCH"
    assert agent_factory.state_store.load(paused.deferred_state_id).status == "pending"


def test_resume_rejects_legacy_name_version_signature() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"saved": True}

    agent_factory = factory(FunctionModel(model_function))
    toolset = write_toolset(executor)
    paused = start_deferred(agent_factory, toolset)
    state_path = agent_factory.state_store.state_path(paused.deferred_state_id)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["references"].pop("toolset_contract_fingerprint")
    payload["references"]["toolset_signature"] = toolset.legacy_toolset_signature
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(AiAgentExecutionError) as caught:
        agent_factory.resume_sync(
            state_id=paused.deferred_state_id,
            profile=PROFILE,
            instructions="调用写工具保存库存。",
            toolset=toolset,
            approval_decisions={"write-call-1": True},
            approver_id="approver-legacy",
            tenant_id="tenant-1",
            permissions={"inventory.write", "inventory.approve"},
            business_scope=RUN_SCOPE,
            idempotency_context=IDEMPOTENCY,
        )

    assert caught.value.code == "AI_AGENT_STATE_TOOLSET_MISMATCH"
    assert executions == 0
