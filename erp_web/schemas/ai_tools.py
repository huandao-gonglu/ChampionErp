"""AI 工具协议的稳定内部契约与轻量 JSON Schema 校验。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence


AI_TOOL_PROTOCOL_VERSION = "1"
AiToolSideEffect = Literal["none", "write"]
AiToolTurnType = Literal["tool_calls", "final"]
_JSON_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "integer", "number", "string", "array", "object"}
)


class AiToolSchemaError(ValueError):
    """工具协议或工具输入输出不符合冻结契约。"""

    def __init__(self, message: str, *, code: str = "TOOL_CALL_INVALID") -> None:
        self.code = code
        super().__init__(message)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _copy_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(
            json.dumps(
                _thaw_json(value),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise AiToolSchemaError(f"{label} 必须是可序列化 JSON：{exc}") from exc


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AiToolSchemaError(f"{label} 必须是对象")
    return {str(key): item for key, item in value.items()}


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AiToolSchemaError(f"{label} 必须是非空字符串")
    return value.strip()


def _require_exact_fields(
    payload: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = sorted(required - payload.keys())
    if missing:
        raise AiToolSchemaError(f"{label} 缺少字段：{', '.join(missing)}")
    unexpected = sorted(payload.keys() - required - optional)
    if unexpected:
        raise AiToolSchemaError(f"{label} 包含未知字段：{', '.join(unexpected)}")


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        # 协议对象会把 JSON array 冻结成 tuple；它与边界上的 list 语义等价。
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, Mapping)
    raise AiToolSchemaError(
        f"不支持的 JSON Schema type：{expected}",
        code="TOOL_SCHEMA_INVALID",
    )


def _schema_non_negative_integer(
    schema: Mapping[str, Any],
    keyword: str,
    *,
    path: str,
) -> int | None:
    value = schema.get(keyword)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AiToolSchemaError(
            f"{path}.{keyword} 必须是非负整数",
            code="TOOL_SCHEMA_INVALID",
        )
    return value


def validate_json_schema_definition(
    schema: Mapping[str, Any],
    *,
    path: str = "$schema",
) -> None:
    """预验证 V1 支持的 JSON Schema 子集，避免错误拖到首次工具调用。"""

    if not isinstance(schema, Mapping):
        raise AiToolSchemaError(
            f"{path} 必须是对象",
            code="TOOL_SCHEMA_INVALID",
        )
    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            [expected_type]
            if isinstance(expected_type, str)
            else list(expected_type)
            if isinstance(expected_type, Sequence)
            and not isinstance(expected_type, (str, bytes))
            else []
        )
        if (
            not expected_types
            or not all(isinstance(item, str) for item in expected_types)
            or any(item not in _JSON_SCHEMA_TYPES for item in expected_types)
            or len(set(expected_types)) != len(expected_types)
        ):
            raise AiToolSchemaError(
                f"{path}.type 包含无效或重复类型",
                code="TOOL_SCHEMA_INVALID",
            )

    enum_values = schema.get("enum")
    if enum_values is not None:
        if (
            not isinstance(enum_values, Sequence)
            or isinstance(enum_values, (str, bytes))
            or not enum_values
        ):
            raise AiToolSchemaError(
                f"{path}.enum 必须是非空数组",
                code="TOOL_SCHEMA_INVALID",
            )
        serialized_values = [
            json.dumps(
                _thaw_json(item),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            for item in enum_values
        ]
        if len(set(serialized_values)) != len(serialized_values):
            raise AiToolSchemaError(
                f"{path}.enum 不得包含重复值",
                code="TOOL_SCHEMA_INVALID",
            )

    required = schema.get("required")
    if required is not None:
        if (
            not isinstance(required, Sequence)
            or isinstance(required, (str, bytes))
            or not all(isinstance(item, str) and item for item in required)
            or len(set(required)) != len(required)
        ):
            raise AiToolSchemaError(
                f"{path}.required 必须是不重复的非空字符串数组",
                code="TOOL_SCHEMA_INVALID",
            )

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise AiToolSchemaError(
                f"{path}.properties 必须是对象",
                code="TOOL_SCHEMA_INVALID",
            )
        for name, child_schema in properties.items():
            if not isinstance(name, str) or not name:
                raise AiToolSchemaError(
                    f"{path}.properties 的字段名必须是非空字符串",
                    code="TOOL_SCHEMA_INVALID",
                )
            validate_json_schema_definition(
                child_schema,
                path=f"{path}.properties.{name}",
            )

    items = schema.get("items")
    if items is not None:
        validate_json_schema_definition(items, path=f"{path}.items")

    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        validate_json_schema_definition(
            additional,
            path=f"{path}.additionalProperties",
        )

    bound_pairs = (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    )
    for minimum_key, maximum_key in bound_pairs:
        minimum = _schema_non_negative_integer(schema, minimum_key, path=path)
        maximum = _schema_non_negative_integer(schema, maximum_key, path=path)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise AiToolSchemaError(
                f"{path}.{minimum_key} 不得大于 {maximum_key}",
                code="TOOL_SCHEMA_INVALID",
            )

    for keyword in ("minimum", "maximum"):
        value = schema.get(keyword)
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise AiToolSchemaError(
                f"{path}.{keyword} 必须是数字",
                code="TOOL_SCHEMA_INVALID",
            )
    if (
        schema.get("minimum") is not None
        and schema.get("maximum") is not None
        and schema["minimum"] > schema["maximum"]
    ):
        raise AiToolSchemaError(
            f"{path}.minimum 不得大于 maximum",
            code="TOOL_SCHEMA_INVALID",
        )


def validate_json_schema(value: Any, schema: Mapping[str, Any], *, path: str = "$") -> None:
    """校验工具边界使用的 JSON Schema 子集。

    V1 支持 object/array/基础类型、required、properties、
    additionalProperties、enum/const 及常用长度和数值边界。未知关键字按
    JSON Schema 的约定忽略，避免把这个小型校验器误当成通用 schema engine。
    """

    if not isinstance(schema, Mapping):
        raise AiToolSchemaError(
            f"{path} 的 schema 必须是对象",
            code="TOOL_SCHEMA_INVALID",
        )
    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = (
            [expected_type]
            if isinstance(expected_type, str)
            else list(expected_type)
            if isinstance(expected_type, Sequence) and not isinstance(expected_type, (str, bytes))
            else []
        )
        if not expected_types or not all(isinstance(item, str) for item in expected_types):
            raise AiToolSchemaError(
                f"{path} 的 schema.type 无效",
                code="TOOL_SCHEMA_INVALID",
            )
        if not any(_matches_json_type(value, item) for item in expected_types):
            raise AiToolSchemaError(
                f"{path} 类型不符合 schema，期望 {' | '.join(expected_types)}"
            )

    if "const" in schema and value != schema["const"]:
        raise AiToolSchemaError(f"{path} 必须等于 schema.const")
    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, Sequence) or isinstance(enum_values, (str, bytes)):
            raise AiToolSchemaError(
                f"{path} 的 schema.enum 必须是数组",
                code="TOOL_SCHEMA_INVALID",
            )
        if value not in enum_values:
            raise AiToolSchemaError(f"{path} 不在允许枚举中")

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        if (
            not isinstance(required, Sequence)
            or isinstance(required, (str, bytes))
            or not all(isinstance(item, str) for item in required)
        ):
            raise AiToolSchemaError(
                f"{path} 的 schema.required 必须是字符串数组",
                code="TOOL_SCHEMA_INVALID",
            )
        if not isinstance(properties, Mapping):
            raise AiToolSchemaError(
                f"{path} 的 schema.properties 必须是对象",
                code="TOOL_SCHEMA_INVALID",
            )
        missing = [key for key in required if key not in value]
        if missing:
            raise AiToolSchemaError(f"{path} 缺少必填字段：{', '.join(missing)}")
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_json_schema(item, properties[key], path=child_path)
            elif additional is False:
                raise AiToolSchemaError(f"{child_path} 是未允许字段")
            elif isinstance(additional, Mapping):
                validate_json_schema(item, additional, path=child_path)
        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if min_properties is not None and len(value) < int(min_properties):
            raise AiToolSchemaError(f"{path} 字段数少于 minProperties")
        if max_properties is not None and len(value) > int(max_properties):
            raise AiToolSchemaError(f"{path} 字段数超过 maxProperties")

    if isinstance(value, list):
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, Mapping):
                raise AiToolSchemaError(
                    f"{path} 的 schema.items 必须是对象",
                    code="TOOL_SCHEMA_INVALID",
                )
            for index, item in enumerate(value):
                validate_json_schema(item, items, path=f"{path}[{index}]")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise AiToolSchemaError(f"{path} 元素数少于 minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise AiToolSchemaError(f"{path} 元素数超过 maxItems")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise AiToolSchemaError(f"{path} 长度少于 minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise AiToolSchemaError(f"{path} 长度超过 maxLength")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise AiToolSchemaError(f"{path} 小于 minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise AiToolSchemaError(f"{path} 大于 maximum")


@dataclass(frozen=True)
class AiToolDefinition:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    required_permission: str
    side_effect: AiToolSideEffect = "none"
    approval_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_string(self.name, label="tool.name"))
        object.__setattr__(self, "version", _require_string(self.version, label="tool.version"))
        object.__setattr__(
            self,
            "description",
            _require_string(self.description, label="tool.description"),
        )
        object.__setattr__(
            self,
            "required_permission",
            _require_string(self.required_permission, label="tool.required_permission"),
        )
        if self.side_effect not in {"none", "write"}:
            raise AiToolSchemaError("tool.side_effect 只允许 none 或 write")
        if not isinstance(self.approval_required, bool):
            raise AiToolSchemaError("tool.approval_required 必须是布尔值")
        input_schema = _require_object(self.input_schema, label="tool.input_schema")
        output_schema = _require_object(self.output_schema, label="tool.output_schema")
        input_schema = _copy_json(input_schema, label="tool.input_schema")
        output_schema = _copy_json(output_schema, label="tool.output_schema")
        validate_json_schema_definition(
            input_schema,
            path=f"tool.{self.name}.input_schema",
        )
        validate_json_schema_definition(
            output_schema,
            path=f"tool.{self.name}.output_schema",
        )
        object.__setattr__(
            self,
            "input_schema",
            _freeze_json(input_schema),
        )
        object.__setattr__(
            self,
            "output_schema",
            _freeze_json(output_schema),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": _copy_json(self.input_schema, label="tool.input_schema"),
            "output_schema": _copy_json(self.output_schema, label="tool.output_schema"),
            "required_permission": self.required_permission,
            "side_effect": self.side_effect,
            "approval_required": self.approval_required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiToolDefinition":
        data = _require_object(payload, label="AiToolDefinition")
        _require_exact_fields(
            data,
            required={
                "name",
                "version",
                "description",
                "input_schema",
                "output_schema",
                "required_permission",
                "side_effect",
                "approval_required",
            },
            optional=set(),
            label="AiToolDefinition",
        )
        return cls(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            input_schema=data["input_schema"],
            output_schema=data["output_schema"],
            required_permission=data["required_permission"],
            side_effect=data["side_effect"],
            approval_required=data["approval_required"],
        )


@dataclass(frozen=True)
class AiToolCall:
    call_id: str
    tool_name: str
    tool_version: str
    arguments: Mapping[str, Any]
    round: int
    protocol_version: str = AI_TOOL_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != AI_TOOL_PROTOCOL_VERSION:
            raise AiToolSchemaError(
                f"不支持的 tool protocol_version：{self.protocol_version}",
                code="TOOL_PROTOCOL_UNSUPPORTED",
            )
        object.__setattr__(self, "call_id", _require_string(self.call_id, label="call.call_id"))
        object.__setattr__(
            self,
            "tool_name",
            _require_string(self.tool_name, label="call.tool_name"),
        )
        object.__setattr__(
            self,
            "tool_version",
            _require_string(self.tool_version, label="call.tool_version"),
        )
        if not isinstance(self.round, int) or isinstance(self.round, bool) or self.round < 1:
            raise AiToolSchemaError("call.round 必须是大于等于 1 的整数")
        arguments = _require_object(self.arguments, label="call.arguments")
        object.__setattr__(
            self,
            "arguments",
            _freeze_json(_copy_json(arguments, label="call.arguments")),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiToolCall":
        data = _require_object(payload, label="AiToolCall")
        _require_exact_fields(
            data,
            required={
                "protocol_version",
                "call_id",
                "tool_name",
                "tool_version",
                "arguments",
                "round",
            },
            optional=set(),
            label="AiToolCall",
        )
        return cls(
            protocol_version=data["protocol_version"],
            call_id=data["call_id"],
            tool_name=data["tool_name"],
            tool_version=data["tool_version"],
            arguments=data["arguments"],
            round=data["round"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments": _copy_json(self.arguments, label="call.arguments"),
            "round": self.round,
        }


@dataclass(frozen=True)
class AiToolResult:
    call_id: str
    tool_name: str
    ok: bool
    output: Any = None
    error: Mapping[str, Any] | None = None
    duration_ms: int = 0
    deduplicated: bool = False
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "call_id", _require_string(self.call_id, label="result.call_id"))
        object.__setattr__(
            self,
            "tool_name",
            _require_string(self.tool_name, label="result.tool_name"),
        )
        if not isinstance(self.ok, bool):
            raise AiToolSchemaError("result.ok 必须是布尔值")
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise AiToolSchemaError("result.duration_ms 必须是非负整数")
        if not isinstance(self.deduplicated, bool) or not isinstance(self.truncated, bool):
            raise AiToolSchemaError("result deduplicated/truncated 必须是布尔值")
        if self.ok:
            if self.error is not None:
                raise AiToolSchemaError("成功的 AiToolResult 不得包含 error")
            object.__setattr__(
                self,
                "output",
                _freeze_json(_copy_json(self.output, label="result.output")),
            )
        else:
            error = _require_object(self.error, label="result.error")
            _require_string(error.get("code"), label="result.error.code")
            _require_string(error.get("message"), label="result.error.message")
            object.__setattr__(
                self,
                "error",
                _freeze_json(_copy_json(error, label="result.error")),
            )
            object.__setattr__(self, "output", None)

    def as_deduplicated(self, *, call_id: str | None = None) -> "AiToolResult":
        return replace(
            self,
            call_id=call_id or self.call_id,
            duration_ms=0,
            deduplicated=True,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiToolResult":
        data = _require_object(payload, label="AiToolResult")
        _require_exact_fields(
            data,
            required={
                "call_id",
                "tool_name",
                "ok",
                "output",
                "error",
                "duration_ms",
                "deduplicated",
                "truncated",
            },
            optional=set(),
            label="AiToolResult",
        )
        return cls(
            call_id=data["call_id"],
            tool_name=data["tool_name"],
            ok=data["ok"],
            output=data["output"],
            error=data["error"],
            duration_ms=data["duration_ms"],
            deduplicated=data["deduplicated"],
            truncated=data["truncated"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "ok": self.ok,
            "output": _copy_json(self.output, label="result.output"),
            "error": _copy_json(self.error, label="result.error"),
            "duration_ms": self.duration_ms,
            "deduplicated": self.deduplicated,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class AiToolTurn:
    type: AiToolTurnType
    calls: tuple[AiToolCall, ...] = ()
    result: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        calls = tuple(self.calls)
        object.__setattr__(self, "calls", calls)
        if self.type == "tool_calls":
            if self.result is not None:
                raise AiToolSchemaError("tool_calls turn 不得同时包含 final result")
            if not calls:
                raise AiToolSchemaError("tool_calls turn 至少需要一个 call")
            if not all(isinstance(call, AiToolCall) for call in calls):
                raise AiToolSchemaError("turn.calls 必须全部是 AiToolCall")
            return
        if self.type == "final":
            if calls:
                raise AiToolSchemaError("final turn 不得同时包含 tool calls")
            result = _require_object(self.result, label="turn.result")
            object.__setattr__(
                self,
                "result",
                _freeze_json(_copy_json(result, label="turn.result")),
            )
            return
        raise AiToolSchemaError(
            f"未知 AiToolTurn type：{self.type}",
            code="MODEL_RESPONSE_SCHEMA_INVALID",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AiToolTurn":
        data = _require_object(payload, label="AiToolTurn")
        turn_type = data.get("type")
        if turn_type == "tool_calls":
            _require_exact_fields(
                data,
                required={"type", "calls"},
                optional=set(),
                label="AiToolTurn",
            )
            raw_calls = data["calls"]
            if not isinstance(raw_calls, list):
                raise AiToolSchemaError("AiToolTurn.calls 必须是数组")
            return cls(
                type="tool_calls",
                calls=tuple(AiToolCall.from_dict(item) for item in raw_calls),
            )
        if turn_type == "final":
            _require_exact_fields(
                data,
                required={"type", "result"},
                optional=set(),
                label="AiToolTurn",
            )
            return cls(type="final", result=data["result"])
        raise AiToolSchemaError(
            f"未知 AiToolTurn type：{turn_type}",
            code="MODEL_RESPONSE_SCHEMA_INVALID",
        )

    @classmethod
    def final(cls, result: Mapping[str, Any]) -> "AiToolTurn":
        return cls(type="final", result=dict(result))

    def to_dict(self) -> dict[str, Any]:
        if self.type == "tool_calls":
            return {"type": "tool_calls", "calls": [call.to_dict() for call in self.calls]}
        return {"type": "final", "result": _copy_json(self.result, label="turn.result")}


def validate_ai_tool_definition(payload: Mapping[str, Any]) -> AiToolDefinition:
    return AiToolDefinition.from_dict(payload)


def validate_ai_tool_call(payload: Mapping[str, Any]) -> AiToolCall:
    return AiToolCall.from_dict(payload)


def validate_ai_tool_result(payload: Mapping[str, Any]) -> AiToolResult:
    return AiToolResult.from_dict(payload)


def validate_ai_tool_turn(payload: Mapping[str, Any]) -> AiToolTurn:
    return AiToolTurn.from_dict(payload)


__all__ = [
    "AI_TOOL_PROTOCOL_VERSION",
    "AiToolCall",
    "AiToolDefinition",
    "AiToolResult",
    "AiToolSchemaError",
    "AiToolSideEffect",
    "AiToolTurn",
    "AiToolTurnType",
    "validate_ai_tool_call",
    "validate_ai_tool_definition",
    "validate_ai_tool_result",
    "validate_ai_tool_turn",
    "validate_json_schema",
    "validate_json_schema_definition",
]
