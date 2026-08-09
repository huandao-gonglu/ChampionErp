"""类目属性填充使用的只读枚举查询 ToolSet。"""

from __future__ import annotations

from typing import Any

from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.category_attribute import (
    CATEGORY_ATTRIBUTE_VALUE_PERMISSION,
    CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
    CategoryAttributeValueLedger,
)
from erp_web.services.ai_tool_registry import (
    AiToolSet,
    deadline_aware_tool_executor,
)

from .category_store import fetch_category_attribute_values


_VALUE_SCHEMA = {
    "type": "object",
    "required": ["dictionary_value_id", "value"],
    "properties": {
        "dictionary_value_id": {"type": "string", "maxLength": 160},
        "value": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "additionalProperties": False,
}

_LOOKUP_RESULT_SCHEMA = {
    "type": "object",
    "required": [
        "attribute_id",
        "query",
        "strict_enum",
        "allows_custom_value",
        "values",
        "error_code",
    ],
    "properties": {
        "attribute_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "query": {"type": "string", "maxLength": 255},
        "strict_enum": {"type": "boolean"},
        "allows_custom_value": {"type": "boolean"},
        "values": {"type": "array", "items": _VALUE_SCHEMA, "maxItems": 20},
        "error_code": {"type": "string", "maxLength": 80},
    },
    "additionalProperties": False,
}

CATEGORY_ATTRIBUTE_VALUE_TOOL_DEFINITIONS = (
    AiToolDefinition(
        name="search_attribute_values",
        version="1",
        description=(
            "批量查询当前类目属性的真实平台枚举值。每项使用目标市场语言的简短值或"
            "核心名搜索。strict_enum=true 时最终只能选择本工具返回的"
            "dictionary_value_id；allows_custom_value=true 时没有合适候选可填写自定义文本。"
        ),
        input_schema={
            "type": "object",
            "required": ["requests"],
            "properties": {
                "requests": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "required": ["attribute_id", "query"],
                        "properties": {
                            "attribute_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 160,
                            },
                            "query": {"type": "string", "maxLength": 255},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["results"],
            "properties": {
                "results": {
                    "type": "array",
                    "items": _LOOKUP_RESULT_SCHEMA,
                    "maxItems": 8,
                }
            },
            "additionalProperties": False,
        },
        required_permission=CATEGORY_ATTRIBUTE_VALUE_PERMISSION,
    ),
)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _local_option_values(
    definition: dict[str, Any],
    query: str,
) -> list[dict[str, str]]:
    normalized_query = query.casefold()
    values: list[dict[str, str]] = []
    for raw_value in definition.get("options") or []:
        value = _text(raw_value, 500)
        if not value or (normalized_query and normalized_query not in value.casefold()):
            continue
        values.append({"dictionary_value_id": "", "value": value})
        if len(values) >= 20:
            break
    return values


def _platform_values(
    platform: str,
    category_id: str,
    site: str,
    attribute_id: str,
    query: str,
    context: AiExecutionContext,
) -> list[dict[str, str]]:
    result = fetch_category_attribute_values(
        platform,
        category_id,
        attribute_id,
        site=site,
        query=query,
        limit=20,
        timeout_seconds=context.bounded_timeout_seconds(15),
    )
    values: list[dict[str, str]] = []
    for item in (
        result.get("values") if isinstance(result.get("values"), list) else []
    ):
        if not isinstance(item, dict):
            continue
        value_id = _text(item.get("id") or item.get("dictionary_value_id"), 160)
        value = _text(item.get("value") or item.get("name"), 500)
        if value_id and value:
            values.append({"dictionary_value_id": value_id, "value": value})
    return values[:20]


def build_category_attribute_value_toolset(
    *,
    platform: str,
    category_record: dict[str, Any] | None,
    ledger: CategoryAttributeValueLedger,
) -> AiToolSet:
    """绑定平台、类目和属性 allowlist，不把这些边界参数暴露给模型。"""

    record = category_record if isinstance(category_record, dict) else {}
    category_id = _text(record.get("category_id"), 160)
    site = _text(record.get("site"), 80)
    normalized_platform = _text(platform, 80).lower()

    def search_executor(
        arguments: dict[str, Any],
        context: AiExecutionContext,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for request in arguments["requests"]:
            context.bounded_timeout_seconds()
            attribute_id = _text(request.get("attribute_id"), 160)
            query = _text(request.get("query"), 255)
            definition = ledger.definition(attribute_id)
            if definition is None:
                raise RuntimeError("只能查询当前类目属性定义中的 attribute_id。")
            strict_enum = bool(definition.get("strict_enum"))
            has_options = bool(definition.get("options"))
            if not strict_enum and not has_options:
                raise RuntimeError("普通自定义属性没有可查询的枚举值。")
            ledger.record_attempt(attribute_id, query)
            error_code = ""
            try:
                values = (
                    _platform_values(
                        normalized_platform,
                        category_id,
                        site,
                        attribute_id,
                        query,
                        context,
                    )
                    if strict_enum
                    else _local_option_values(definition, query)
                )
            except Exception:
                values = []
                error_code = "ATTRIBUTE_VALUE_LOOKUP_FAILED"
                ledger.record_failure(attribute_id)
            ledger.add_values(attribute_id, values)
            results.append(
                {
                    "attribute_id": attribute_id,
                    "query": query,
                    "strict_enum": strict_enum,
                    "allows_custom_value": not strict_enum,
                    "values": values,
                    "error_code": error_code,
                }
            )
        context.bounded_timeout_seconds()
        return {"results": results}

    return AiToolSet.bind(
        CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
        CATEGORY_ATTRIBUTE_VALUE_TOOL_DEFINITIONS,
        {
            "search_attribute_values": deadline_aware_tool_executor(
                search_executor
            )
        },
    )


__all__ = [
    "CATEGORY_ATTRIBUTE_VALUE_TOOL_DEFINITIONS",
    "build_category_attribute_value_toolset",
]
