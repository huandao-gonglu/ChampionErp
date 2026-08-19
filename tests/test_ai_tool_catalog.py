from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from erp_web.runtime_units.category_attribute_tools import (
    CATEGORY_ATTRIBUTE_TOOL_CATALOG,
    build_category_attribute_value_toolset,
)
from erp_web.runtime_units.collect_capabilities import collect_from_browser_tab
from erp_web.schemas.ai_tools import (
    AiToolCommand,
    AiToolSchemaError,
    validate_json_schema,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_attribute import CategoryAttributeValueLedger
from erp_web.services.ai_tool_catalog import AiToolBindingScope, AiToolCatalog
from erp_web.services.ai_tool_compiler import AiToolCompiler, AiToolCompilerError
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.ai_tool_runtime import AiToolRuntime


class NestedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=30)


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nested: NestedRequest
    optional_note: str | None = None

    @field_validator("optional_note")
    @classmethod
    def reject_blocked_note(cls, value: str | None) -> str | None:
        if value == "blocked":
            raise ValueError("blocked")
        return value


class LookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    tenant_id: str


@dataclass(frozen=True)
class LookupScope:
    prefix: str


@ai_tool(
    name="test_catalog_lookup",
    description="读取测试目录值",
    permission="test.read",
)
def lookup_catalog_value(
    request: LookupRequest,
    scope: Annotated[LookupScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> LookupResult:
    return LookupResult(
        value=f"{scope.prefix}:{request.nested.keyword}",
        tenant_id=execution.tenant_id,
    )


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class WriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved: bool


class PatternRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(pattern=r"^[a-z]+$")


class UnionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | int


class RecursiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    children: list[RecursiveRequest] = Field(default_factory=list)


@ai_tool(
    name="test_catalog_write",
    description="写入测试值",
    permission="test.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_id",),
    recovery_policy="idempotent",
)
def write_catalog_value(
    request: WriteRequest,
    execution: Annotated[AiExecutionContext, Injected()],
) -> WriteResult:
    del request, execution
    return WriteResult(saved=True)


def execution_context(
    *,
    permissions: frozenset[str] = frozenset({"test.read"}),
    allow_write: bool = False,
    idempotency_context: dict[str, str] | None = None,
) -> AiExecutionContext:
    return AiExecutionContext(
        task_run_id="task_catalog",
        attempt_id="attempt_catalog",
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        budget_profile="test.catalog",
        tenant_id="tenant-7",
        permissions=permissions,
        allow_write=allow_write,
        idempotency_context=idempotency_context or {},
    )


def test_compiler_expands_models_and_hides_all_injected_parameters() -> None:
    tool = AiToolCompiler.compile(lookup_catalog_value)
    schema = tool.definition.to_dict()["input_schema"]

    assert "$defs" not in json.dumps(schema)
    assert "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["nested"]["additionalProperties"] is False
    assert schema["properties"]["optional_note"]["type"] == ["string", "null"]
    assert "scope" not in schema["properties"]
    assert "execution" not in schema["properties"]
    assert tool.definition.injected_type_names == (
        "erp_web.schemas.ai_trace.AiExecutionContext",
        "test_ai_tool_catalog.LookupScope",
    )


def test_compiled_browser_collect_schema_matches_runtime_validation() -> None:
    """模型可见 Schema 与 request adapter 必须对同一输入给出相同结论。"""

    tool = AiToolCompiler.compile(collect_from_browser_tab)
    payload = {"platform_hint": "1688"}

    schema_accepts = True
    try:
        validate_json_schema(payload, tool.definition.input_schema)
    except AiToolSchemaError:
        schema_accepts = False

    runtime_accepts = True
    try:
        tool.request_adapter.validate_python(payload)
    except Exception:
        runtime_accepts = False

    assert schema_accepts is runtime_accepts


def test_decorator_rejects_incomplete_metadata_and_write_policy() -> None:
    with pytest.raises(ValueError, match="只能包含"):
        ai_tool(name="test.lookup", description="读取", permission="test.read")
    with pytest.raises(ValueError, match="description"):
        ai_tool(name="test_lookup", description="", permission="test.read")
    with pytest.raises(ValueError, match="approval_required"):
        ai_tool(
            name="test_write",
            description="写入",
            permission="test.write",
            side_effect="write",
            idempotency="required",
            idempotency_keys=("operation_id",),
        )
    with pytest.raises(ValueError, match="idempotency"):
        ai_tool(
            name="test_write",
            description="写入",
            permission="test.write",
            side_effect="write",
            approval_required=False,
        )


def test_catalog_binds_scope_and_exact_type_adapters() -> None:
    catalog = AiToolCatalog.compile((lookup_catalog_value,))
    toolset = catalog.bind(
        toolset_id="test.catalog",
        allowed_tools=("test_catalog_lookup",),
        scope=AiToolBindingScope.from_values(LookupScope("bound")),
        declared_permissions={"test.read"},
    )
    binding = toolset.get("test_catalog_lookup")
    assert binding is not None
    output = binding.executor(
        {"nested": {"keyword": "fan"}, "optional_note": None},
        execution_context(),
    )
    assert output == {"value": "bound:fan", "tenant_id": "tenant-7"}

    with pytest.raises(AiToolSchemaError) as invalid:
        binding.executor(
            {"nested": {"keyword": "fan"}, "optional_note": "blocked"},
            execution_context(),
        )
    assert invalid.value.code == "TOOL_INPUT_SCHEMA_INVALID"

    with pytest.raises(AiToolSchemaError) as injected_override:
        binding.executor(
            {
                "nested": {"keyword": "fan"},
                "scope": {"prefix": "evil"},
            },
            execution_context(),
        )
    assert injected_override.value.code == "TOOL_INPUT_SCHEMA_INVALID"

    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=execution_context(),
    )
    runtime_result = runtime.execute(
        AiToolCommand(
            call_id="validator-input",
            tool_name="test_catalog_lookup",
            tool_version="1",
            arguments={
                "nested": {"keyword": "fan"},
                "optional_note": "blocked",
            },
            round=1,
        )
    )
    assert runtime_result.error["code"] == "TOOL_INPUT_SCHEMA_INVALID"


def test_catalog_rejects_unknown_tools_permissions_and_scope_errors() -> None:
    catalog = AiToolCatalog.compile((lookup_catalog_value,))
    scope = AiToolBindingScope.from_values(LookupScope("bound"))
    with pytest.raises(ValueError, match="未收录"):
        catalog.bind(
            toolset_id="test.catalog",
            allowed_tools=("test.catalog.unknown",),
            scope=scope,
            declared_permissions={"test.read"},
        )
    with pytest.raises(ValueError, match="未声明"):
        catalog.bind(
            toolset_id="test.catalog",
            allowed_tools=("test_catalog_lookup",),
            scope=scope,
            declared_permissions=set(),
        )
    with pytest.raises(ValueError, match="缺少"):
        catalog.bind(
            toolset_id="test.catalog",
            allowed_tools=("test_catalog_lookup",),
            scope=AiToolBindingScope.from_values(),
            declared_permissions={"test.read"},
        )
    with pytest.raises(ValueError, match="重复提供"):
        AiToolBindingScope.from_values(LookupScope("one"), LookupScope("two"))


def test_catalog_rejects_duplicate_names_and_unapproved_write_profile() -> None:
    @ai_tool(
        name="test_catalog_lookup",
        description="重复测试目录值",
        permission="test.read",
    )
    def duplicate_lookup(request: LookupRequest) -> LookupResult:
        return LookupResult(value=request.nested.keyword, tenant_id="test")

    with pytest.raises(ValueError, match="重复收录"):
        AiToolCatalog.compile((lookup_catalog_value, duplicate_lookup))

    write_catalog = AiToolCatalog.compile((write_catalog_value,))
    with pytest.raises(ValueError, match="未允许写工具"):
        write_catalog.bind(
            toolset_id="test.write",
            allowed_tools=("test_catalog_write",),
            scope=AiToolBindingScope.from_values(),
            declared_permissions={"test.write"},
        )


def test_runtime_rejects_missing_trusted_idempotency_before_write_executor() -> None:
    toolset = AiToolCatalog.compile((write_catalog_value,)).bind(
        toolset_id="test.write",
        allowed_tools=("test_catalog_write",),
        scope=AiToolBindingScope.from_values(),
        declared_permissions={"test.write"},
        allow_write=True,
    )
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=execution_context(
            permissions=frozenset({"test.write"}),
            allow_write=True,
        ),
    )
    result = runtime.execute(
        AiToolCommand(
            call_id="write-missing-idempotency",
            tool_name="test_catalog_write",
            tool_version="1",
            arguments={"value": "demo"},
            round=1,
        )
    )
    assert result.ok is False
    assert result.error["code"] == "TOOL_IDEMPOTENCY_CONTEXT_REQUIRED"


def test_compiler_rejects_unsupported_signatures_and_schema_constraints() -> None:
    @ai_tool(
        name="test_catalog_pattern",
        description="不支持的 pattern",
        permission="test.read",
    )
    def pattern_tool(request: PatternRequest) -> LookupResult:
        return LookupResult(value=request.value, tenant_id="test")

    with pytest.raises(AiToolCompilerError, match="pattern"):
        AiToolCompiler.compile(pattern_tool)

    @ai_tool(
        name="test_catalog_union",
        description="受 Runtime 校验的一般 union",
        permission="test.read",
    )
    def union_tool(request: UnionRequest) -> LookupResult:
        return LookupResult(value=str(request.value), tenant_id="test")

    compiled_union = AiToolCompiler.compile(union_tool)
    union_schema = compiled_union.definition.input_schema["properties"]["value"]
    assert "anyOf" in union_schema
    validate_json_schema("sku-1", union_schema)
    validate_json_schema(7, union_schema)
    with pytest.raises(AiToolSchemaError):
        validate_json_schema([], union_schema)

    @ai_tool(
        name="test_catalog_recursive",
        description="递归模型",
        permission="test.read",
    )
    def recursive_tool(request: RecursiveRequest) -> LookupResult:
        return LookupResult(value=str(len(request.children)), tenant_id="test")

    with pytest.raises(AiToolCompilerError, match="递归"):
        AiToolCompiler.compile(recursive_tool)

    @ai_tool(
        name="test_catalog_multi",
        description="多个可见参数",
        permission="test.read",
    )
    def multi_tool(request: LookupRequest, second: LookupRequest) -> LookupResult:
        del second
        return LookupResult(value=request.nested.keyword, tenant_id="test")

    with pytest.raises(AiToolCompilerError, match="恰好有一个"):
        AiToolCompiler.compile(multi_tool)

    @ai_tool(
        name="test_catalog_async",
        description="异步函数",
        permission="test.read",
    )
    async def async_tool(request: LookupRequest) -> LookupResult:
        return LookupResult(value=request.nested.keyword, tenant_id="test")

    with pytest.raises(AiToolCompilerError, match="异步"):
        AiToolCompiler.compile(async_tool)


def test_compiler_reports_output_adapter_failures_with_stable_code() -> None:
    @ai_tool(
        name="test_catalog_invalid_output",
        description="返回无效类型",
        permission="test.read",
    )
    def invalid_output(request: LookupRequest) -> LookupResult:
        del request
        return {"value": "ok", "tenant_id": object()}  # type: ignore[return-value]

    toolset = AiToolCatalog.compile((invalid_output,)).bind(
        toolset_id="test.invalid-output",
        allowed_tools=("test_catalog_invalid_output",),
        scope=AiToolBindingScope.from_values(),
        declared_permissions={"test.read"},
    )
    runtime = AiToolRuntime(
        toolset=toolset,
        execution_context=execution_context(),
    )
    result = runtime.execute(
        AiToolCommand(
            call_id="invalid-output",
            tool_name="test_catalog_invalid_output",
            tool_version="1",
            arguments={"nested": {"keyword": "fan"}},
            round=1,
        )
    )
    assert result.error["code"] == "TOOL_OUTPUT_SCHEMA_INVALID"


def test_category_attribute_contract_snapshot_requires_explicit_version_upgrade() -> None:
    snapshot_path = Path(__file__).parent / "snapshots" / "ai_tool_contracts.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    tool = CATEGORY_ATTRIBUTE_TOOL_CATALOG.tools["category_attribute_values_search"]
    toolset = build_category_attribute_value_toolset(
        platform="ozon",
        category_record={},
        ledger=CategoryAttributeValueLedger.from_schema([]),
    )

    assert snapshot["category_attribute_values_search"] == {
        "version": tool.definition.version,
        "contract_fingerprint": tool.definition.contract_fingerprint,
    }
    payload = {
        "toolset_id": toolset.toolset_id,
        "tools": [
            {
                "name": definition.name,
                "contract_fingerprint": definition.contract_fingerprint,
            }
            for definition in sorted(toolset.definitions, key=lambda item: item.name)
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert snapshot["toolsets"]["category.attribute_values"] == fingerprint
