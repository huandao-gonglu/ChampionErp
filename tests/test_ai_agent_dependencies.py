from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from erp_web.schemas.ai_tools import AiToolCommand, AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_agent_dependencies import AiAgentDependencies
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)
from erp_web.services.ai_tool_runtime import AiToolRuntime


def _toolset() -> AiToolSet:
    definition = AiToolDefinition(
        name="read_scope",
        version="1",
        description="返回当前请求的执行边界",
        input_schema={
            "type": "object",
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["tenant_id", "actor_id", "store_id"],
            "properties": {
                "tenant_id": {"type": "string"},
                "actor_id": {"type": "string"},
                "store_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        required_permission="catalog.read",
    )

    def execute(
        _arguments: dict[str, Any],
        context: AiExecutionContext,
    ) -> dict[str, str]:
        return {
            "tenant_id": context.tenant_id,
            "actor_id": context.actor_id,
            "store_id": context.business_scope["store_id"],
        }

    return AiToolSet.bind(
        "test.dependencies",
        [definition],
        {definition.name: deadline_aware_tool_executor(execute)},
    )


def _execution_context(
    *,
    run: str,
    tenant_id: str,
    actor_id: str,
    store_id: str,
) -> AiExecutionContext:
    return AiExecutionContext.create(
        timeout_seconds=120,
        budget_profile="test.agent",
        task_run_id=f"task_{run}",
        attempt_id=f"attempt_{run}",
        workflow_run_id=f"workflow_{run}",
        parent_task_run_id=f"parent_{run}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        permissions={"catalog.read", "inventory.read"},
        business_scope={"store_id": store_id, "locale": "zh-CN"},
        idempotency_context={"request_id": f"request_{run}"},
        approved_tool_call_ids={f"approved_{run}"},
        allow_write=True,
    )


def _dependencies(
    *,
    run: str,
    tenant_id: str,
    actor_id: str,
    store_id: str,
    toolset: AiToolSet | None = None,
) -> AiAgentDependencies:
    context = _execution_context(
        run=run,
        tenant_id=tenant_id,
        actor_id=actor_id,
        store_id=store_id,
    )
    runtime = AiToolRuntime(
        toolset=toolset or _toolset(),
        execution_context=context,
    )
    state = {"run": run, "secret": f"state-secret-{run}"}
    return AiAgentDependencies(
        use_case_id=" category.product_match ",
        execution_context=context,
        tool_runtime=runtime,
        use_case_state=state,
        invocation_id=f"invocation_{run}",
    )


def _tool_command() -> AiToolCommand:
    return AiToolCommand(
        call_id="same_pydantic_call_id",
        tool_name="read_scope",
        tool_version="1",
        arguments={},
        round=1,
    )


def test_dependencies_transmit_the_complete_request_execution_boundary() -> None:
    dependencies = _dependencies(
        run="one",
        tenant_id="tenant-one",
        actor_id="user-one",
        store_id="store-one",
    )
    context = dependencies.execution_context

    assert dependencies.use_case_id == "category.product_match"
    assert dependencies.invocation_id == "invocation_one"
    assert dependencies.user_id == "user-one"
    assert dependencies.tenant_id == "tenant-one"
    assert dependencies.permissions == frozenset(
        {"catalog.read", "inventory.read"}
    )
    assert dependencies.permissions is context.permissions
    assert dependencies.business_scope == {
        "store_id": "store-one",
        "locale": "zh-CN",
    }
    assert dependencies.business_scope is context.business_scope
    assert dependencies.deadline_at is context.deadline_at
    assert dependencies.approved_tool_call_ids == frozenset({"approved_one"})
    assert dependencies.approved_tool_call_ids is context.approved_tool_call_ids
    assert dependencies.idempotency_context == {"request_id": "request_one"}
    assert dependencies.idempotency_context is context.idempotency_context
    assert dependencies.use_case_state == {
        "run": "one",
        "secret": "state-secret-one",
    }


def test_execution_context_copies_and_freezes_security_mappings() -> None:
    permissions = {"catalog.read"}
    business_scope = {"store_id": "store-one"}
    idempotency_context = {"request_id": "request-one"}
    approved_ids = {"call-one"}
    context = AiExecutionContext.create(
        timeout_seconds=120,
        budget_profile="test.agent",
        tenant_id="tenant-one",
        permissions=permissions,
        business_scope=business_scope,
        idempotency_context=idempotency_context,
        approved_tool_call_ids=approved_ids,
    )

    permissions.add("admin")
    business_scope["store_id"] = "store-overwritten"
    idempotency_context["request_id"] = "request-overwritten"
    approved_ids.add("call-two")

    assert context.permissions == frozenset({"catalog.read"})
    assert context.business_scope == {"store_id": "store-one"}
    assert context.idempotency_context == {"request_id": "request-one"}
    assert context.approved_tool_call_ids == frozenset({"call-one"})
    with pytest.raises(TypeError):
        context.business_scope["store_id"] = "forbidden"  # type: ignore[index]
    with pytest.raises(TypeError):
        context.idempotency_context["request_id"] = "forbidden"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        context.tenant_id = "forbidden"  # type: ignore[misc]


def test_each_agent_run_owns_independent_runtime_dedupe_and_use_case_state() -> None:
    toolset = _toolset()
    first = _dependencies(
        run="first",
        tenant_id="tenant-first",
        actor_id="user-first",
        store_id="store-first",
        toolset=toolset,
    )
    second = _dependencies(
        run="second",
        tenant_id="tenant-second",
        actor_id="user-second",
        store_id="store-second",
        toolset=toolset,
    )

    first_result = first.tool_runtime.execute(_tool_command())

    assert first_result.ok is True
    assert first_result.to_dict()["output"] == {
        "tenant_id": "tenant-first",
        "actor_id": "user-first",
        "store_id": "store-first",
    }
    assert first.tool_runtime.unique_call_count == 1
    assert second.tool_runtime.unique_call_count == 0

    second_result = second.tool_runtime.execute(_tool_command())

    assert second_result.ok is True
    assert second_result.deduplicated is False
    assert second_result.to_dict()["output"] == {
        "tenant_id": "tenant-second",
        "actor_id": "user-second",
        "store_id": "store-second",
    }
    assert second.tool_runtime.unique_call_count == 1
    assert first.execution_context is not second.execution_context
    assert first.tool_runtime is not second.tool_runtime
    assert first.use_case_state is not second.use_case_state


def test_dependencies_default_invocation_id_stays_bound_to_context() -> None:
    context = _execution_context(
        run="default",
        tenant_id="tenant-default",
        actor_id="user-default",
        store_id="store-default",
    )
    runtime = AiToolRuntime(
        toolset=_toolset(),
        execution_context=context,
    )

    dependencies = AiAgentDependencies(
        use_case_id="category.product_match",
        execution_context=context,
        tool_runtime=runtime,
    )

    assert dependencies.invocation_id == context.attempt_id


def test_dependencies_repr_exposes_ids_but_not_security_or_business_payloads() -> None:
    dependencies = _dependencies(
        run="repr",
        tenant_id="tenant-repr",
        actor_id="user-repr",
        store_id="store-secret",
    )

    rendered = repr(dependencies)

    assert rendered == (
        "AiAgentDependencies(use_case_id='category.product_match', "
        "invocation_id='invocation_repr', user_id='user-repr', "
        "tenant_id='tenant-repr')"
    )
    assert "catalog.read" not in rendered
    assert "approved_repr" not in rendered
    assert "request_repr" not in rendered
    assert "store-secret" not in rendered
    assert "state-secret-repr" not in rendered


def test_dependencies_reject_runtime_execution_context_identity_mismatch() -> None:
    bound_context = _execution_context(
        run="bound",
        tenant_id="tenant-bound",
        actor_id="user-bound",
        store_id="store-bound",
    )
    supplied_context = _execution_context(
        run="supplied",
        tenant_id="tenant-supplied",
        actor_id="user-supplied",
        store_id="store-supplied",
    )
    runtime = AiToolRuntime(
        toolset=_toolset(),
        execution_context=bound_context,
    )

    with pytest.raises(ValueError, match="execution context 不一致"):
        AiAgentDependencies(
            use_case_id="category.product_match",
            execution_context=supplied_context,
            tool_runtime=runtime,
        )
