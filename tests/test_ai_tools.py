from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from erp_web.schemas.ai_tools import (
    AiToolCommand,
    AiToolDefinition,
    AiToolExecutionError,
    AiToolResult,
    AiToolSchemaError,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)
from erp_web.services.ai_tool_runtime import AiToolRuntime


def tool_definition(
    *,
    name: str = "lookup_item",
    permission: str = "catalog.read",
    side_effect: str = "none",
) -> AiToolDefinition:
    return AiToolDefinition(
        name=name,
        version="1",
        description="按 ID 读取测试数据",
        input_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["item_id"],
            "properties": {"item_id": {"type": "string"}},
            "additionalProperties": False,
        },
        required_permission=permission,
        side_effect=side_effect,
        approval_required=False,
        idempotency="required" if side_effect == "write" else "none",
        idempotency_keys=("operation_id",) if side_effect == "write" else (),
    )


def tool_command(
    call_id: str = "call_1",
    *,
    item_id: str = "sku-1",
    tool_name: str = "lookup_item",
) -> AiToolCommand:
    return AiToolCommand(
        call_id=call_id,
        tool_name=tool_name,
        tool_version="1",
        arguments={"item_id": item_id},
        round=1,
    )


def execution_context(
    *,
    permissions: frozenset[str] = frozenset({"catalog.read"}),
    expired: bool = False,
    allow_write: bool = False,
) -> AiExecutionContext:
    now = datetime.now(timezone.utc)
    return AiExecutionContext(
        task_run_id="task_test",
        attempt_id="attempt_test",
        deadline_at=now - timedelta(seconds=1) if expired else now + timedelta(seconds=30),
        budget_profile="test.default",
        permissions=permissions,
        idempotency_context={"operation_id": "operation-test"},
        allow_write=allow_write,
    )


def runtime(
    executor,
    *,
    permissions: frozenset[str] = frozenset({"catalog.read"}),
    max_tool_calls: int = 4,
    expired: bool = False,
    before_executor=None,
    side_effect: str = "none",
) -> AiToolRuntime:
    definition = tool_definition(side_effect=side_effect)
    toolset = AiToolSet.bind(
        "test.read",
        [definition],
        {definition.name: deadline_aware_tool_executor(executor)},
    )
    return AiToolRuntime(
        toolset=toolset,
        execution_context=execution_context(
            permissions=permissions,
            expired=expired,
            allow_write=side_effect == "write",
        ),
        max_tool_calls=max_tool_calls,
        before_executor=before_executor,
    )


def test_ai_tool_schema_rejects_invalid_definitions_commands_and_arguments() -> None:
    with pytest.raises(AiToolSchemaError, match="只能包含"):
        tool_definition(name="catalog.lookup_item")
    with pytest.raises(AiToolSchemaError, match="side_effect"):
        AiToolDefinition(
            name="bad",
            version="1",
            description="bad",
            input_schema={},
            output_schema={},
            required_permission="test.read",
            side_effect="network",  # type: ignore[arg-type]
        )
    with pytest.raises(AiToolSchemaError, match="round"):
        AiToolCommand(
            call_id="bad-round",
            tool_name="lookup_item",
            tool_version="1",
            arguments={"item_id": "sku-1"},
            round=0,
        )
    invalid_arguments = runtime(
        lambda arguments, context: arguments
    ).execute(
        AiToolCommand(
            call_id="call_missing",
            tool_name="lookup_item",
            tool_version="1",
            arguments={},
            round=1,
        )
    )
    assert invalid_arguments.ok is False
    assert invalid_arguments.error["code"] == "TOOL_INPUT_SCHEMA_INVALID"
    assert "缺少必填字段" in invalid_arguments.error["message"]

    with pytest.raises(AiToolSchemaError, match=r"input_schema.*type"):
        AiToolDefinition(
            name="invalid_schema",
            version="1",
            description="无效 schema",
            input_schema={"type": "dictionary"},
            output_schema={},
            required_permission="test.read",
        )


def test_ai_tool_definition_and_result_round_trip_through_dicts() -> None:
    definition = tool_definition()
    command = tool_command()
    result = AiToolResult(
        call_id=command.call_id,
        tool_name=command.tool_name,
        ok=True,
        output={"item_id": "sku-1"},
        duration_ms=12,
    )

    assert AiToolDefinition.from_dict(definition.to_dict()) == definition
    assert AiToolResult.from_dict(result.to_dict()) == result


def test_toolset_requires_exact_explicit_definition_executor_binding() -> None:
    definition = tool_definition()
    with pytest.raises(ValueError, match="缺少 executor"):
        AiToolSet.bind("test.read", [definition], {})
    with pytest.raises(ValueError, match="未定义 executor"):
        AiToolSet.bind(
            "test.read",
            [definition],
            {
                definition.name: lambda arguments, context: arguments,
                "dynamic_extra": lambda arguments, context: arguments,
            },
        )
    toolset = AiToolSet.bind(
        "test.read",
        [definition],
        {
            definition.name: deadline_aware_tool_executor(
                lambda arguments, context: arguments
            )
        },
    )
    with pytest.raises(TypeError):
        toolset.bindings["another"] = toolset.bindings[definition.name]  # type: ignore[index]
    with pytest.raises(TypeError):
        definition.input_schema["type"] = "array"  # type: ignore[index]
    with pytest.raises(TypeError):
        tool_command().arguments["item_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(ValueError, match="cooperative deadline"):
        AiToolSet.bind(
            "test.unbounded",
            [definition],
            {definition.name: lambda arguments, context: arguments},
        )


def test_runtime_rejects_unregistered_tool_and_missing_permission() -> None:
    unregistered = runtime(lambda arguments, context: arguments).execute(
        tool_command(tool_name="unknown")
    )
    assert unregistered.ok is False
    assert unregistered.error["code"] == "TOOL_NOT_ALLOWED"

    denied = runtime(
        lambda arguments, context: arguments,
        permissions=frozenset(),
    ).execute(tool_command())
    assert denied.ok is False
    assert denied.error["code"] == "TOOL_PERMISSION_DENIED"


def test_runtime_deduplicates_call_ids_and_same_tool_arguments() -> None:
    executions: list[str] = []

    def executor(arguments, context):
        del context
        executions.append(arguments["item_id"])
        return {"item_id": arguments["item_id"]}

    tool_runtime = runtime(executor)
    first = tool_runtime.execute(tool_command())
    repeated_id = tool_runtime.execute(tool_command())
    repeated_arguments = tool_runtime.execute(tool_command("call_2"))

    assert first.ok is True
    assert repeated_id.deduplicated is True
    assert repeated_arguments.deduplicated is True
    assert repeated_arguments.call_id == "call_2"
    assert executions == ["sku-1"]


def test_runtime_persists_execution_checkpoint_only_immediately_before_executor() -> None:
    checkpoints: list[str] = []
    executions: list[str] = []

    tool_runtime = runtime(
        lambda arguments, context: (
            executions.append(arguments["item_id"])
            or {"item_id": arguments["item_id"]}
        ),
        before_executor=lambda command: checkpoints.append(command.call_id),
        side_effect="write",
    )

    invalid = tool_runtime.execute(
        AiToolCommand(
            call_id="invalid",
            tool_name="lookup_item",
            tool_version="1",
            arguments={},
            round=1,
        )
    )
    assert invalid.error["code"] == "TOOL_INPUT_SCHEMA_INVALID"
    assert checkpoints == []

    assert tool_runtime.execute(tool_command()).ok is True
    assert tool_runtime.execute(tool_command()).deduplicated is True
    assert checkpoints == ["call_1"]
    assert executions == ["sku-1"]


def test_runtime_aborts_before_side_effect_when_checkpoint_persistence_fails() -> None:
    executions = 0

    def executor(arguments, context):
        nonlocal executions
        del arguments, context
        executions += 1
        return {"item_id": "sku-1"}

    def fail_checkpoint(command: AiToolCommand) -> None:
        del command
        raise OSError("不得泄露的存储错误")

    result = runtime(
        executor,
        before_executor=fail_checkpoint,
        side_effect="write",
    ).execute(tool_command())

    assert result.ok is False
    assert result.error == {
        "code": "TOOL_EXECUTION_CHECKPOINT_FAILED",
        "message": "工具执行前检查点无法持久化",
        "retryable": True,
    }
    assert executions == 0


def test_runtime_preserves_public_execution_error_without_code_enumeration() -> None:
    def executor(arguments, context):
        del arguments, context
        raise AiToolExecutionError(
            "DOMAIN_CUSTOM_FAILURE",
            "领域服务暂时不可用。",
            retryable=True,
        )

    result = runtime(executor).execute(tool_command())

    assert result.error == {
        "code": "DOMAIN_CUSTOM_FAILURE",
        "message": "领域服务暂时不可用。",
        "retryable": True,
    }


def test_runtime_hides_unknown_execution_exception_details() -> None:
    def executor(arguments, context):
        del arguments, context
        raise RuntimeError("internal-runtime-secret-token")

    result = runtime(executor).execute(tool_command())

    assert result.error == {
        "code": "TOOL_EXECUTION_FAILED",
        "message": "工具执行失败，请稍后重试。",
        "retryable": True,
    }
    assert "internal-runtime-secret-token" not in str(result.to_dict())


def test_runtime_rejects_same_call_id_with_different_arguments() -> None:
    executions = 0

    def executor(arguments, context):
        nonlocal executions
        del context
        executions += 1
        return arguments

    tool_runtime = runtime(executor)
    assert tool_runtime.execute(tool_command()).ok is True
    conflict = tool_runtime.execute(tool_command(item_id="sku-2"))

    assert conflict.ok is False
    assert conflict.error["code"] == "TOOL_CALL_INVALID"
    assert executions == 1


def test_runtime_enforces_deadline_and_call_budget() -> None:
    expired_result = runtime(
        lambda arguments, context: arguments,
        expired=True,
    ).execute(tool_command())
    assert expired_result.error["code"] == "TASK_DEADLINE_EXCEEDED"

    tool_runtime = runtime(
        lambda arguments, context: {"item_id": arguments["item_id"]},
        max_tool_calls=1,
    )
    assert tool_runtime.execute(tool_command()).ok is True
    over_budget = tool_runtime.execute(tool_command("call_2", item_id="sku-2"))
    assert over_budget.ok is False
    assert over_budget.error["code"] == "TOOL_CALL_BUDGET_EXCEEDED"


def test_runtime_validates_output_schema() -> None:
    result = runtime(
        lambda arguments, context: {"unexpected": arguments["item_id"]}
    ).execute(tool_command())

    assert result.ok is False
    assert result.error["code"] == "TOOL_OUTPUT_SCHEMA_INVALID"


def test_runtime_preserves_json_arrays_for_validation_and_executor() -> None:
    definition = AiToolDefinition(
        name="join_tags",
        version="1",
        description="合并标签",
        input_schema={
            "type": "object",
            "required": ["tags"],
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["tags"],
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "additionalProperties": False,
        },
        required_permission="catalog.read",
    )
    received: list[list[str]] = []

    def execute(arguments, context):
        del context
        received.append(arguments["tags"])
        return {"tags": arguments["tags"]}

    tool_runtime = AiToolRuntime(
        toolset=AiToolSet.bind(
            "test.arrays",
            [definition],
            {definition.name: deadline_aware_tool_executor(execute)},
        ),
        execution_context=execution_context(),
    )
    result = tool_runtime.execute(
        AiToolCommand(
            call_id="call_arrays",
            tool_name=definition.name,
            tool_version="1",
            arguments={"tags": ["one", "two"]},
            round=1,
        )
    )

    assert result.ok is True
    assert received == [["one", "two"]]
    assert result.to_dict()["output"] == {"tags": ["one", "two"]}


def test_execution_context_bounds_cooperative_io_timeout() -> None:
    now = datetime.now(timezone.utc)
    context = AiExecutionContext(
        task_run_id="task_deadline",
        attempt_id="attempt_deadline",
        deadline_at=now + timedelta(seconds=3),
        budget_profile="test.default",
    )

    assert context.bounded_timeout_seconds(10, now=now) == pytest.approx(3)
    assert context.bounded_timeout_seconds(1, now=now) == pytest.approx(1)
    with pytest.raises(TimeoutError, match="deadline"):
        context.bounded_timeout_seconds(now=now + timedelta(seconds=4))
