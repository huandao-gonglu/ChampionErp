"""ERP 工具定义、执行命令、结果与轻量 JSON Schema 校验。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, JsonValue


AiToolSideEffect = Literal["none", "write"]
AiToolIdempotency = Literal["none", "required"]
AiToolExecutionMode = Literal["sync", "persistent_job"]
AiToolRecoveryPolicy = Literal["manual", "retry_safe", "idempotent"]
AI_TOOL_EXECUTION_MODES = ("sync", "persistent_job")
AI_TOOL_RECOVERY_POLICIES = ("manual", "retry_safe", "idempotent")
TOOL_INPUT_REQUIRED = "TOOL_INPUT_REQUIRED"
TOOL_APPROVAL_REQUIRED = "TOOL_APPROVAL_REQUIRED"
_JSON_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "integer", "number", "string", "array", "object"}
)
_AI_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AiToolSchemaError(ValueError):
    """工具定义、命令或输入输出不符合冻结契约。"""

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


def normalize_ai_tool_name(value: Any, *, label: str = "tool.name") -> str:
    """规范化 Provider 可见工具名，并执行跨 Provider 的最小公共约束。"""

    normalized = _require_string(value, label=label)
    if _AI_TOOL_NAME_PATTERN.fullmatch(normalized) is None:
        raise AiToolSchemaError(
            f"{label} 只能包含字母、数字、下划线或短划线，且长度不得超过 64"
        )
    return normalized


class AiToolExecutionError(RuntimeError):
    """executor 可安全公开给模型和用户的结构化错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = _require_string(code, label="tool_error.code")
        if not isinstance(retryable, bool):
            raise AiToolSchemaError("tool_error.retryable 必须是布尔值")
        self.retryable = retryable
        if details is None:
            frozen_details: Mapping[str, Any] | None = None
        else:
            frozen_details = _freeze_json(
                _copy_json(dict(details), label="tool_error.details")
            )
        self.details = frozen_details
        super().__init__(_require_string(message, label="tool_error.message"))


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
        # 冻结对象会把 JSON array 收敛成 tuple；它与边界上的 list 语义等价。
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

    one_of = schema.get("oneOf")
    if one_of is not None:
        if (
            not isinstance(one_of, Sequence)
            or isinstance(one_of, (str, bytes))
            or len(one_of) < 1
        ):
            raise AiToolSchemaError(
                f"{path}.oneOf 必须是非空数组",
                code="TOOL_SCHEMA_INVALID",
            )
        for index, branch in enumerate(one_of):
            validate_json_schema_definition(
                branch,
                path=f"{path}.oneOf[{index}]",
            )
    discriminator = schema.get("discriminator")
    if discriminator is not None:
        if not isinstance(discriminator, Mapping):
            raise AiToolSchemaError(
                f"{path}.discriminator 必须是对象",
                code="TOOL_SCHEMA_INVALID",
            )
        property_name = discriminator.get("propertyName")
        if not isinstance(property_name, str) or not property_name:
            raise AiToolSchemaError(
                f"{path}.discriminator.propertyName 必须是非空字符串",
                code="TOOL_SCHEMA_INVALID",
            )

    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        validate_json_schema_definition(
            additional,
            path=f"{path}.additionalProperties",
        )

    property_names = schema.get("propertyNames")
    if property_names is not None:
        validate_json_schema_definition(
            property_names,
            path=f"{path}.propertyNames",
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

    one_of = schema.get("oneOf")
    if one_of is not None:
        if (
            not isinstance(one_of, Sequence)
            or isinstance(one_of, (str, bytes))
            or not one_of
        ):
            raise AiToolSchemaError(
                f"{path} 的 schema.oneOf 必须是数组",
                code="TOOL_SCHEMA_INVALID",
            )
        discriminator = schema.get("discriminator")
        if not isinstance(discriminator, Mapping):
            raise AiToolSchemaError(
                f"{path} 的 oneOf 只支持带 discriminator 的可判别 union",
                code="TOOL_SCHEMA_INVALID",
            )
        property_name = discriminator.get("propertyName")
        if not isinstance(property_name, str) or not property_name:
            raise AiToolSchemaError(
                f"{path} 的 discriminator.propertyName 无效",
                code="TOOL_SCHEMA_INVALID",
            )
        if not isinstance(value, Mapping):
            raise AiToolSchemaError(
                f"{path} 必须是对象才能按 discriminator 选择 union 分支"
            )
        discriminator_value = value.get(property_name)
        selected: Mapping[str, Any] | None = None
        for branch in one_of:
            if not isinstance(branch, Mapping):
                raise AiToolSchemaError(
                    f"{path} 的 oneOf 分支必须是对象",
                    code="TOOL_SCHEMA_INVALID",
                )
            branch_property = (
                branch.get("properties", {}).get(property_name)
                if isinstance(branch.get("properties"), Mapping)
                else None
            )
            if (
                isinstance(branch_property, Mapping)
                and "const" in branch_property
                and branch_property["const"] == discriminator_value
            ):
                selected = branch
                break
        if selected is None:
            raise AiToolSchemaError(
                f"{path}.{property_name} 的值不匹配任何允许的 union 分支"
            )
        validate_json_schema(value, selected, path=path)

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        property_names = schema.get("propertyNames")
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
        if property_names is not None and not isinstance(property_names, Mapping):
            raise AiToolSchemaError(
                f"{path} 的 schema.propertyNames 必须是对象",
                code="TOOL_SCHEMA_INVALID",
            )
        missing = [key for key in required if key not in value]
        if missing:
            raise AiToolSchemaError(f"{path} 缺少必填字段：{', '.join(missing)}")
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if property_names is not None:
                validate_json_schema(
                    key,
                    property_names,
                    path=f"{child_path}（键名）",
                )
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
    idempotency: AiToolIdempotency = "none"
    idempotency_keys: tuple[str, ...] = ()
    injected_type_names: tuple[str, ...] = ()
    execution_mode: AiToolExecutionMode = "sync"
    recovery_policy: AiToolRecoveryPolicy = "manual"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize_ai_tool_name(self.name))
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
        if self.idempotency not in {"none", "required"}:
            raise AiToolSchemaError("tool.idempotency 只允许 none 或 required")
        if self.execution_mode not in set(AI_TOOL_EXECUTION_MODES):
            raise AiToolSchemaError(
                "tool.execution_mode 只允许 sync 或 persistent_job"
            )
        if self.recovery_policy not in set(AI_TOOL_RECOVERY_POLICIES):
            raise AiToolSchemaError(
                "tool.recovery_policy 只允许 manual、retry_safe 或 idempotent"
            )
        if not isinstance(self.idempotency_keys, Sequence) or isinstance(
            self.idempotency_keys,
            (str, bytes),
        ):
            raise AiToolSchemaError("tool.idempotency_keys 必须是字符串数组")
        idempotency_keys = tuple(
            _require_string(value, label="tool.idempotency_keys")
            for value in self.idempotency_keys
        )
        if len(idempotency_keys) != len(set(idempotency_keys)):
            raise AiToolSchemaError("tool.idempotency_keys 不得重复")
        if not isinstance(self.injected_type_names, Sequence) or isinstance(
            self.injected_type_names,
            (str, bytes),
        ):
            raise AiToolSchemaError("tool.injected_type_names 必须是字符串数组")
        injected_type_names = tuple(
            sorted(
                {
                    _require_string(value, label="tool.injected_type_names")
                    for value in self.injected_type_names
                }
            )
        )
        if self.side_effect == "write":
            if self.idempotency != "required" or not idempotency_keys:
                raise AiToolSchemaError(
                    "写工具必须声明 required idempotency 和非空 idempotency_keys"
                )
        elif self.idempotency != "none" or idempotency_keys:
            raise AiToolSchemaError("只读工具不得声明写入幂等策略")
        object.__setattr__(self, "idempotency_keys", idempotency_keys)
        object.__setattr__(self, "injected_type_names", injected_type_names)
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
            "idempotency": self.idempotency,
            "idempotency_keys": list(self.idempotency_keys),
            "injected_type_names": list(self.injected_type_names),
            "execution_mode": self.execution_mode,
            "recovery_policy": self.recovery_policy,
        }

    @property
    def contract_fingerprint(self) -> str:
        """覆盖可恢复工具契约的规范化 SHA-256 指纹。"""

        payload = self.to_dict()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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
                "idempotency",
                "idempotency_keys",
                "injected_type_names",
                "execution_mode",
                "recovery_policy",
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
            idempotency=data["idempotency"],
            idempotency_keys=tuple(data["idempotency_keys"]),
            injected_type_names=tuple(data["injected_type_names"]),
            execution_mode=data["execution_mode"],
            recovery_policy=data["recovery_policy"],
        )


@dataclass(frozen=True)
class AiToolCommand:
    """Runtime 内部执行命令；不承担模型或 Provider 的 wire protocol。"""

    call_id: str
    tool_name: str
    tool_version: str
    arguments: Mapping[str, Any]
    round: int

    def __post_init__(self) -> None:
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

    def arguments_dict(self) -> dict[str, Any]:
        """向 Runtime/executor 提供可变的 JSON 参数副本。"""

        return _copy_json(self.arguments, label="call.arguments")


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
            if not isinstance(error.get("retryable"), bool):
                raise AiToolSchemaError("result.error.retryable 必须是布尔值")
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


def validate_ai_tool_definition(payload: Mapping[str, Any]) -> AiToolDefinition:
    return AiToolDefinition.from_dict(payload)


def validate_ai_tool_result(payload: Mapping[str, Any]) -> AiToolResult:
    return AiToolResult.from_dict(payload)


class JobReferenceResult(BaseModel):
    """persistent_job Capability 的统一同步返回：已提交 Job 的可信引用。

    ``job_type`` 是领域无关的 Job 类别标识；Controller 通过 Job Status
    Reader 注册表按 ``job_type`` 解析状态读取器，不直接依赖领域模块。
    """

    model_config = ConfigDict(extra="forbid")

    result_version: Literal["job_reference.v1"] = "job_reference.v1"
    job_id: str = Field(min_length=1, max_length=200)
    job_type: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "pending", "running", "retrying"] = "queued"
    summary: str = Field(default="", max_length=2000)


# 领域无关的 Job 类别常量；Job Status Reader 注册表按它们解析读取器。
PUBLISH_JOB_TYPE = "publish"
PRODUCT_RESEARCH_JOB_TYPE = "product_research"


class TaskApprovalSnapshot(BaseModel):
    """服务端生成的审批冻结快照：人类可读摘要 + 规范化执行参数。

    审批展示内容与执行绑定都由它派生；模型不能提交最终用于展示的审批
    摘要。由 approval-required Capability 声明的快照函数在任务创建与执行
    复核两个时点分别重算。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=2000)
    canonical_payload: dict[str, JsonValue] = Field(
        default_factory=dict,
        max_length=100,
    )


class AiToolRequiredInput(BaseModel):
    """needs_input 标准错误中携带的类型化待补字段。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    input_type: Literal["text", "select", "json_object", "string_list"] = "text"
    options: list[str] = Field(default_factory=list, max_length=100)


__all__ = [
    "AI_TOOL_EXECUTION_MODES",
    "AI_TOOL_RECOVERY_POLICIES",
    "AiToolCommand",
    "AiToolDefinition",
    "AiToolExecutionError",
    "AiToolExecutionMode",
    "AiToolIdempotency",
    "AiToolRecoveryPolicy",
    "AiToolRequiredInput",
    "AiToolResult",
    "AiToolSchemaError",
    "AiToolSideEffect",
    "JobReferenceResult",
    "PRODUCT_RESEARCH_JOB_TYPE",
    "PUBLISH_JOB_TYPE",
    "TOOL_APPROVAL_REQUIRED",
    "TOOL_INPUT_REQUIRED",
    "TaskApprovalSnapshot",
    "normalize_ai_tool_name",
    "validate_ai_tool_definition",
    "validate_ai_tool_result",
    "validate_json_schema",
    "validate_json_schema_definition",
]
