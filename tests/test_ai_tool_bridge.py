from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
from pydantic_ai import Agent, ApprovalRequired
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from erp_web.schemas.ai_tools import AiToolDefinition, AiToolExecutionError
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_dependencies import AiAgentDependencies
from erp_web.services.ai_tool_bridge import AiToolBridgeError, PydanticToolBridge
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)
from erp_web.services.ai_tool_runtime import AiToolRuntime


ToolExecutor = Callable[[dict[str, Any], AiExecutionContext], Any]


def tool_definition(
    *,
    permission: str = "catalog.read",
    side_effect: str = "none",
    approval_required: bool = False,
    output_schema: dict[str, Any] | None = None,
) -> AiToolDefinition:
    return AiToolDefinition(
        name="lookup_item",
        version="1",
        description="按 ID 读取测试数据",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {
                "item_id": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string"}},
            "additionalProperties": False,
        },
        required_permission=permission,
        side_effect=side_effect,  # type: ignore[arg-type]
        approval_required=approval_required,
        idempotency="required" if side_effect == "write" else "none",
        idempotency_keys=("request_id",) if side_effect == "write" else (),
    )


def execution_context(
    *,
    permissions: set[str] | frozenset[str] = frozenset({"catalog.read"}),
    business_scope: dict[str, str] | None = None,
    allow_write: bool = False,
    approved_tool_call_ids: set[str] | frozenset[str] = frozenset(),
    expired: bool = False,
) -> AiExecutionContext:
    now = datetime.now(timezone.utc)
    return AiExecutionContext(
        task_run_id="task_tool_bridge",
        attempt_id="attempt_tool_bridge",
        deadline_at=now - timedelta(seconds=1)
        if expired
        else now + timedelta(seconds=30),
        budget_profile="test.tool_bridge",
        actor_id="user-17",
        tenant_id="tenant-42",
        permissions=frozenset(permissions),
        business_scope=business_scope or {},
        idempotency_context={"request_id": "request-9"},
        allow_write=allow_write,
        approved_tool_call_ids=frozenset(approved_tool_call_ids),
    )


def bind_toolset(
    definition: AiToolDefinition,
    executor: ToolExecutor,
    *,
    toolset_id: str = "test.tool_bridge",
) -> AiToolSet:
    return AiToolSet.bind(
        toolset_id,
        [definition],
        {definition.name: deadline_aware_tool_executor(executor)},
    )


def bind_dependencies(
    toolset: AiToolSet,
    context: AiExecutionContext,
    *,
    max_output_bytes: int = 64 * 1024,
) -> tuple[AiAgentDependencies, AiToolRuntime]:
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=context,
        max_output_bytes=max_output_bytes,
    )
    dependencies = AiAgentDependencies(
        use_case_id="test.tool_bridge",
        execution_context=context,
        tool_runtime=runtime,
    )
    return dependencies, runtime


def execute_bridge(
    bridge: PydanticToolBridge,
    dependencies: AiAgentDependencies,
    *,
    call_id: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    return bridge.execute(
        dependencies=dependencies,
        tool_name="lookup_item",
        tool_call_id=call_id,
        arguments=arguments or {"item_id": "sku-1"},
        round_number=1,
    )


def test_real_agent_function_model_round_trip_preserves_schema_and_tool_call_id() -> None:
    executions: list[tuple[dict[str, Any], AiExecutionContext]] = []

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        executions.append((arguments, context))
        return {"item_id": arguments["item_id"]}

    definition = tool_definition()
    toolset = bind_toolset(definition, executor)
    context = execution_context(business_scope={"platform": "mercadolibre"})
    dependencies, runtime = bind_dependencies(toolset, context)
    bridge = PydanticToolBridge(toolset)
    model_turns = 0
    returned_tool_parts: list[ToolReturnPart] = []

    def model_function(
        messages: list[ModelRequest | ModelResponse],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_turns
        model_turns += 1
        if model_turns == 1:
            assert len(agent_info.function_tools) == 1
            pydantic_definition = agent_info.function_tools[0]
            assert pydantic_definition.name == definition.name
            assert pydantic_definition.description == definition.description
            assert (
                pydantic_definition.parameters_json_schema
                == definition.to_dict()["input_schema"]
            )
            assert pydantic_definition.sequential is True
            assert pydantic_definition.toolset_id == toolset.toolset_id
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        definition.name,
                        {"item_id": "sku-agent"},
                        tool_call_id="pydantic-call-17",
                    )
                ]
            )

        returned_tool_parts.extend(
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        return ModelResponse(parts=[TextPart("工具调用完成")])

    agent = Agent(
        FunctionModel(model_function),
        deps_type=AiAgentDependencies,
        toolsets=[bridge.as_toolset()],
    )

    result = agent.run_sync("读取 sku-agent", deps=dependencies)

    assert result.output == "工具调用完成"
    assert model_turns == 2
    assert len(executions) == 1
    assert executions[0] == ({"item_id": "sku-agent"}, context)
    assert runtime.unique_call_count == 1
    assert len(returned_tool_parts) == 1
    assert returned_tool_parts[0].tool_call_id == "pydantic-call-17"
    assert returned_tool_parts[0].content == {"item_id": "sku-agent"}


def test_bridge_routes_permission_denial_through_runtime_without_executor_call() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"item_id": "unreachable"}

    toolset = bind_toolset(tool_definition(), executor)
    dependencies, runtime = bind_dependencies(
        toolset,
        execution_context(permissions=frozenset()),
    )

    with pytest.raises(AiToolBridgeError) as captured:
        execute_bridge(PydanticToolBridge(toolset), dependencies, call_id="denied-call")

    assert captured.value.code == "TOOL_PERMISSION_DENIED"
    assert captured.value.tool_call_id == "denied-call"
    assert executions == 0
    assert runtime.unique_call_count == 1


def test_bridge_keeps_business_scope_out_of_model_schema_and_passes_it_to_executor() -> None:
    received_scopes: list[dict[str, str]] = []
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        executions += 1
        received_scopes.append(dict(context.business_scope))
        return {"item_id": arguments["item_id"]}

    definition = tool_definition()
    toolset = bind_toolset(definition, executor)
    dependencies, runtime = bind_dependencies(
        toolset,
        execution_context(
            business_scope={"platform": "mercadolibre", "site": "MLM"}
        ),
    )
    bridge = PydanticToolBridge(toolset)
    exposed_schema = definition.to_dict()["input_schema"]

    assert set(exposed_schema["properties"]) == {"item_id"}
    assert "business_scope" not in str(exposed_schema)
    assert "tenant_id" not in str(exposed_schema)
    with pytest.raises(AiToolBridgeError) as injected:
        execute_bridge(
            bridge,
            dependencies,
            call_id="scope-injection",
            arguments={
                "item_id": "sku-1",
                "business_scope": {"site": "evil"},
                "tenant_id": "evil",
            },
        )
    assert injected.value.code == "TOOL_INPUT_SCHEMA_INVALID"
    assert executions == 0

    assert execute_bridge(
        bridge,
        dependencies,
        call_id="scope-valid",
    ) == {"item_id": "sku-1"}
    assert executions == 1
    assert received_scopes == [{"platform": "mercadolibre", "site": "MLM"}]
    assert runtime.unique_call_count == 2


def test_bridge_enforces_write_and_approval_before_executor() -> None:
    execution_call_ids: list[str] = []

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        execution_call_ids.append(context.attempt_id)
        return {"item_id": arguments["item_id"]}

    definition = tool_definition(
        permission="catalog.write",
        side_effect="write",
        approval_required=True,
    )
    toolset = bind_toolset(definition, executor)
    bridge = PydanticToolBridge(toolset)

    write_denied, denied_runtime = bind_dependencies(
        toolset,
        execution_context(
            permissions={"catalog.write"},
            allow_write=False,
            approved_tool_call_ids={"write-denied"},
        ),
    )
    with pytest.raises(AiToolBridgeError) as denied:
        execute_bridge(bridge, write_denied, call_id="write-denied")
    assert denied.value.code == "TOOL_WRITE_NOT_ALLOWED"
    assert denied_runtime.unique_call_count == 1
    assert execution_call_ids == []

    approval_missing, approval_runtime = bind_dependencies(
        toolset,
        execution_context(
            permissions={"catalog.write"},
            allow_write=True,
        ),
    )
    with pytest.raises(ApprovalRequired) as approval:
        execute_bridge(bridge, approval_missing, call_id="approval-missing")
    assert approval.value.metadata == {
        "use_case_id": "test.tool_bridge",
        "tool_name": "lookup_item",
        "tool_call_id": "approval-missing",
    }
    assert approval_runtime.unique_call_count == 1
    assert execution_call_ids == []

    approved, approved_runtime = bind_dependencies(
        toolset,
        execution_context(
            permissions={"catalog.write"},
            allow_write=True,
            approved_tool_call_ids={"write-approved"},
        ),
    )
    assert execute_bridge(
        bridge,
        approved,
        call_id="write-approved",
    ) == {"item_id": "sku-1"}
    assert approved_runtime.unique_call_count == 1
    assert len(execution_call_ids) == 1


def test_bridge_preserves_runtime_idempotency_across_distinct_call_ids() -> None:
    executions: list[str] = []

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del context
        executions.append(arguments["item_id"])
        return {"item_id": arguments["item_id"]}

    toolset = bind_toolset(tool_definition(), executor)
    dependencies, runtime = bind_dependencies(
        toolset,
        execution_context(),
    )
    bridge = PydanticToolBridge(toolset)

    first = execute_bridge(bridge, dependencies, call_id="call-one")
    second = execute_bridge(bridge, dependencies, call_id="call-two")

    assert first == second == {"item_id": "sku-1"}
    assert executions == ["sku-1"]
    assert runtime.unique_call_count == 2


def test_bridge_enforces_deadline_and_output_limit() -> None:
    deadline_executions = 0

    def deadline_executor(
        arguments: dict[str, Any], context: AiExecutionContext
    ) -> Any:
        nonlocal deadline_executions
        del arguments, context
        deadline_executions += 1
        return {"item_id": "unreachable"}

    deadline_toolset = bind_toolset(tool_definition(), deadline_executor)
    expired_dependencies, expired_runtime = bind_dependencies(
        deadline_toolset,
        execution_context(expired=True),
    )
    with pytest.raises(AiToolBridgeError) as expired:
        execute_bridge(
            PydanticToolBridge(deadline_toolset),
            expired_dependencies,
            call_id="expired-call",
        )
    assert expired.value.code == "TASK_DEADLINE_EXCEEDED"
    assert expired_runtime.unique_call_count == 1
    assert deadline_executions == 0

    size_executions = 0

    def large_executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal size_executions
        del arguments, context
        size_executions += 1
        return {"payload": "x" * 100}

    large_definition = tool_definition(
        output_schema={
            "type": "object",
            "required": ["payload"],
            "properties": {"payload": {"type": "string"}},
            "additionalProperties": False,
        }
    )
    large_toolset = bind_toolset(large_definition, large_executor)
    large_dependencies, large_runtime = bind_dependencies(
        large_toolset,
        execution_context(),
        max_output_bytes=32,
    )
    with pytest.raises(AiToolBridgeError) as too_large:
        execute_bridge(
            PydanticToolBridge(large_toolset),
            large_dependencies,
            call_id="large-output",
        )
    assert too_large.value.code == "TOOL_OUTPUT_TOO_LARGE"
    assert large_runtime.unique_call_count == 1
    assert size_executions == 1


def test_bridge_preserves_public_error_without_code_enumeration() -> None:
    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        del arguments, context
        raise AiToolExecutionError(
            "DOMAIN_CUSTOM_FAILURE",
            "领域服务暂时不可用。",
            retryable=True,
        )

    toolset = bind_toolset(tool_definition(), executor)
    dependencies, _ = bind_dependencies(toolset, execution_context())

    with pytest.raises(AiToolBridgeError) as captured:
        execute_bridge(
            PydanticToolBridge(toolset),
            dependencies,
            call_id="public-failure",
        )

    assert captured.value.code == "DOMAIN_CUSTOM_FAILURE"
    assert str(captured.value) == "领域服务暂时不可用。"
    assert captured.value.retryable is True


def test_bridge_error_does_not_expose_unknown_runtime_message() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del arguments, context
        executions += 1
        raise RuntimeError("internal-runtime-secret-token")

    toolset = bind_toolset(tool_definition(), executor)
    dependencies, runtime = bind_dependencies(
        toolset,
        execution_context(),
    )

    with pytest.raises(AiToolBridgeError) as captured:
        execute_bridge(
            PydanticToolBridge(toolset),
            dependencies,
            call_id="failed-call",
        )

    assert captured.value.code == "TOOL_EXECUTION_FAILED"
    assert str(captured.value) == "工具执行失败，请稍后重试。"
    assert captured.value.retryable is True
    assert "internal-runtime-secret-token" not in str(captured.value)
    assert "internal-runtime-secret-token" not in repr(captured.value)
    assert executions == 1
    assert runtime.unique_call_count == 1


def test_bridge_rejects_structurally_equal_but_distinct_toolset_binding() -> None:
    executions = 0

    def executor(arguments: dict[str, Any], context: AiExecutionContext) -> Any:
        nonlocal executions
        del context
        executions += 1
        return {"item_id": arguments["item_id"]}

    definition = tool_definition()
    bridge_toolset = bind_toolset(definition, executor)
    runtime_toolset = bind_toolset(definition, executor)
    assert bridge_toolset is not runtime_toolset
    dependencies, runtime = bind_dependencies(
        runtime_toolset,
        execution_context(),
    )

    with pytest.raises(AiToolBridgeError) as captured:
        execute_bridge(
            PydanticToolBridge(bridge_toolset),
            dependencies,
            call_id="mismatched-toolset",
        )

    assert captured.value.code == "TOOLSET_BINDING_MISMATCH"
    assert captured.value.tool_call_id == "unbound"
    assert executions == 0
    assert runtime.unique_call_count == 0
