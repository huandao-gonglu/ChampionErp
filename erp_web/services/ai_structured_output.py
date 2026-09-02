"""Pydantic 类型化输出契约的纯适配工具。

API 模型由 ``ai_direct_request_service`` 把这里生成的 Schema 交给 Pydantic
Direct Model。CLI/浏览器连接目前没有可交给 Pydantic AI 的 ``Model`` 实现，
因此只在这两个明确的非 API 边界附加同一份自动生成的 Schema；一旦它们提供
Pydantic ``Model`` 适配器，应删除该提示式分支并统一走 Direct Model。
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError


OutputT = TypeVar("OutputT")


def output_adapter(output_type: type[OutputT]) -> TypeAdapter[OutputT]:
    return TypeAdapter(output_type)


def object_json_schema(adapter: TypeAdapter[Any]) -> dict[str, Any]:
    schema = adapter.json_schema(mode="validation")
    if schema.get("type") != "object":
        raise TypeError("结构化输出类型必须生成 object JSON Schema。")
    return schema


def validate_structured_output(
    adapter: TypeAdapter[OutputT],
    value: Any,
) -> OutputT:
    try:
        return adapter.validate_python(value)
    except ValidationError as exc:
        paths = sorted(
            {
                ".".join(str(part) for part in error.get("loc", ()))
                for error in exc.errors(include_url=False)
                if error.get("loc")
            }
        )
        detail = "、".join(paths) or "返回值"
        raise ValueError(f"AI 结构化输出不符合 Schema：{detail}。") from None


def prompted_schema_instruction(schema: dict[str, Any]) -> str:
    """为暂时无法接入 Pydantic Model 的非 API 连接生成 Schema 指令。"""

    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return (
        "最终响应必须严格匹配下面由 Pydantic 生成的 JSON Schema；"
        "不要增加 Schema 未声明的字段：\n" + encoded
    )


__all__ = [
    "object_json_schema",
    "output_adapter",
    "prompted_schema_instruction",
    "validate_structured_output",
]
