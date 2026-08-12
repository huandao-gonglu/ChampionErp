"""把受限类型化能力编译为稳定 AI Tool 契约。"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from types import MappingProxyType
from typing import Annotated, Any, Callable, Mapping, get_args, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter

from erp_web.schemas.ai_tools import AiToolDefinition, AiToolSchemaError
from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_tool_declaration import AiToolMetadata, Injected, get_ai_tool_metadata
from .ai_tool_registry import AiToolExecutor, deadline_aware_tool_executor


_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "minimum",
        "maximum",
        "title",
        "description",
        "default",
        "examples",
        "anyOf",
        "$ref",
    }
)
_NON_ASSERTION_KEYWORDS = frozenset(
    {"title", "description", "default", "examples"}
)


class AiToolCompilerError(ValueError):
    """工具函数无法无损编译到当前 Runtime 契约。"""


def _qualified_type_name(value_type: type[Any]) -> str:
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _is_model_type(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, BaseModel)


def _injected_type(annotation: Any) -> type[Any] | None:
    if get_origin(annotation) is not Annotated:
        return None
    parts = get_args(annotation)
    markers = [item for item in parts[1:] if isinstance(item, Injected)]
    if not markers:
        return None
    if len(markers) != 1 or len(parts) != 2:
        raise AiToolCompilerError(
            "Injected 参数必须使用 Annotated[T, Injected()] 的唯一标记"
        )
    value_type = parts[0]
    if not isinstance(value_type, type):
        raise AiToolCompilerError("Injected 参数 T 必须是可绑定的具体类型")
    return value_type


def _copy_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise AiToolCompilerError(f"{label} 不是稳定 JSON：{exc}") from exc


def _compile_schema(raw_schema: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    """展开本地引用并只保留 Runtime 能等价执行的关键字。"""

    raw = _copy_json(dict(raw_schema), label=label)
    definitions = raw.pop("$defs", {})
    raw.pop("$schema", None)
    raw.pop("$id", None)
    if not isinstance(definitions, dict):
        raise AiToolCompilerError(f"{label}.$defs 必须是对象")

    def resolve(node: Any, *, path: str, stack: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise AiToolCompilerError(f"{path} 必须是 Schema 对象")
        unknown = sorted(set(node) - _SCHEMA_KEYWORDS)
        if unknown:
            raise AiToolCompilerError(
                f"{path} 包含 Runtime 不支持的 Schema 关键字：{', '.join(unknown)}"
            )

        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise AiToolCompilerError(f"{path} 只允许本地 $defs 引用")
            definition_name = reference.removeprefix("#/$defs/")
            if not definition_name or "/" in definition_name:
                raise AiToolCompilerError(f"{path} 的 $ref 路径无效")
            if definition_name in stack:
                raise AiToolCompilerError(f"{path} 包含递归 Model 引用")
            target = definitions.get(definition_name)
            if target is None:
                raise AiToolCompilerError(f"{path} 引用了不存在的 $defs")
            resolved = resolve(
                target,
                path=f"{label}.$defs.{definition_name}",
                stack=(*stack, definition_name),
            )
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            for key, value in siblings.items():
                if key in resolved and resolved[key] != value:
                    raise AiToolCompilerError(f"{path} 的 $ref sibling 无法无损合并")
                resolved[key] = value
            node = resolved

        if "anyOf" in node:
            branches = node.get("anyOf")
            if not isinstance(branches, list) or len(branches) != 2:
                raise AiToolCompilerError(f"{path}.anyOf 只支持简单 nullable union")
            compiled_branches = [
                resolve(branch, path=f"{path}.anyOf[{index}]", stack=stack)
                for index, branch in enumerate(branches)
            ]
            nullable = [
                branch
                for branch in compiled_branches
                if branch.get("type") == "null"
                and set(branch).issubset({"type", *_NON_ASSERTION_KEYWORDS})
            ]
            non_null = [branch for branch in compiled_branches if branch not in nullable]
            if len(nullable) != 1 or len(non_null) != 1:
                raise AiToolCompilerError(f"{path}.anyOf 无法规范化为 nullable type")
            normalized = dict(non_null[0])
            branch_type = normalized.get("type")
            if not isinstance(branch_type, str) or branch_type == "null":
                raise AiToolCompilerError(f"{path}.anyOf 的非空分支缺少简单 type")
            normalized["type"] = [branch_type, "null"]
            for key, value in node.items():
                if key == "anyOf":
                    continue
                if key in normalized and normalized[key] != value:
                    raise AiToolCompilerError(f"{path}.anyOf sibling 无法无损合并")
                normalized[key] = value
            node = normalized

        result = dict(node)
        properties = result.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise AiToolCompilerError(f"{path}.properties 必须是对象")
            result["properties"] = {
                str(name): resolve(
                    child,
                    path=f"{path}.properties.{name}",
                    stack=stack,
                )
                for name, child in properties.items()
            }
        if "items" in result:
            result["items"] = resolve(
                result["items"],
                path=f"{path}.items",
                stack=stack,
            )
        additional = result.get("additionalProperties")
        if isinstance(additional, dict):
            result["additionalProperties"] = resolve(
                additional,
                path=f"{path}.additionalProperties",
                stack=stack,
            )
        if not any(key in result for key in ("type", "enum", "const")):
            raise AiToolCompilerError(
                f"{path} 缺少 Runtime 可执行的明确类型或枚举约束"
            )
        return result

    compiled = resolve(raw, path=label, stack=())
    if compiled.get("type") != "object":
        raise AiToolCompilerError(f"{label} 根 Schema 必须是 object")
    if compiled.get("additionalProperties") is not False:
        raise AiToolCompilerError(
            f"{label} 对应的 Pydantic Model 必须声明 extra='forbid'"
        )
    return compiled


@dataclass(frozen=True)
class CompiledAiTool:
    function: Callable[..., BaseModel]
    metadata: AiToolMetadata
    definition: AiToolDefinition
    request_parameter: str
    request_type: type[BaseModel]
    result_type: type[BaseModel]
    request_adapter: TypeAdapter[Any]
    result_adapter: TypeAdapter[Any]
    injected_parameters: Mapping[str, type[Any]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "injected_parameters",
            MappingProxyType(dict(self.injected_parameters)),
        )

    def bind_executor(self, providers: Mapping[type[Any], Any]) -> AiToolExecutor:
        bound_providers = dict(providers)

        def execute(
            arguments: dict[str, Any],
            execution: AiExecutionContext,
        ) -> Any:
            try:
                request = self.request_adapter.validate_python(arguments)
            except Exception:
                raise AiToolSchemaError(
                    "工具输入未通过精确类型校验。",
                    code="TOOL_INPUT_SCHEMA_INVALID",
                ) from None
            keyword_arguments: dict[str, Any] = {self.request_parameter: request}
            for parameter_name, injected_type in self.injected_parameters.items():
                keyword_arguments[parameter_name] = (
                    execution
                    if injected_type is AiExecutionContext
                    else bound_providers[injected_type]
                )
            result = self.function(**keyword_arguments)
            try:
                validated = self.result_adapter.validate_python(result)
                dumped = self.result_adapter.dump_python(validated, mode="json")
                json.dumps(dumped, ensure_ascii=False, allow_nan=False)
            except Exception:
                raise AiToolSchemaError(
                    "工具输出未通过精确类型或 JSON 序列化校验。",
                    code="TOOL_OUTPUT_SCHEMA_INVALID",
                ) from None
            return dumped

        return deadline_aware_tool_executor(execute)


class AiToolCompiler:
    """第一阶段受限同步函数签名编译器。"""

    @classmethod
    def compile(cls, function: Callable[..., Any]) -> CompiledAiTool:
        metadata = get_ai_tool_metadata(function)
        if inspect.iscoroutinefunction(function):
            raise AiToolCompilerError("第一阶段不支持异步 AI Tool 函数")
        signature = inspect.signature(function)
        try:
            hints = get_type_hints(function, include_extras=True)
        except Exception:
            raise AiToolCompilerError("AI Tool 函数包含无法解析的类型注解") from None

        request_parameter = ""
        request_type: type[BaseModel] | None = None
        injected_parameters: dict[str, type[Any]] = {}
        for parameter in signature.parameters.values():
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                raise AiToolCompilerError("AI Tool 不支持位置专用参数、*args 或 **kwargs")
            annotation = hints.get(parameter.name, inspect.Signature.empty)
            if annotation is inspect.Signature.empty:
                raise AiToolCompilerError(f"参数 {parameter.name} 缺少类型注解")
            injected_type = _injected_type(annotation)
            if injected_type is not None:
                injected_parameters[parameter.name] = injected_type
                continue
            if request_parameter:
                raise AiToolCompilerError("AI Tool 必须恰好有一个模型可见 request 参数")
            if parameter.name != "request":
                raise AiToolCompilerError("模型可见参数必须命名为 request")
            if not _is_model_type(annotation):
                raise AiToolCompilerError("request 必须是 Pydantic BaseModel")
            request_parameter = parameter.name
            request_type = annotation

        result_type = hints.get("return", inspect.Signature.empty)
        if not request_parameter or request_type is None:
            raise AiToolCompilerError("AI Tool 必须恰好有一个模型可见 request 参数")
        if not _is_model_type(result_type):
            raise AiToolCompilerError("AI Tool 返回类型必须是 Pydantic BaseModel")
        if (
            metadata.side_effect == "write"
            and AiExecutionContext not in injected_parameters.values()
        ):
            raise AiToolCompilerError("写工具必须注入 AiExecutionContext")

        request_adapter = TypeAdapter(request_type)
        result_adapter = TypeAdapter(result_type)
        try:
            input_schema = _compile_schema(
                request_adapter.json_schema(mode="validation"),
                label=f"tool.{metadata.name}.input_schema",
            )
            output_schema = _compile_schema(
                result_adapter.json_schema(mode="serialization"),
                label=f"tool.{metadata.name}.output_schema",
            )
        except AiToolCompilerError:
            raise
        except Exception:
            raise AiToolCompilerError("Pydantic Schema 无法编译") from None
        injected_type_names = tuple(
            sorted({_qualified_type_name(item) for item in injected_parameters.values()})
        )
        definition = AiToolDefinition(
            name=metadata.name,
            version=metadata.version,
            description=metadata.description,
            input_schema=input_schema,
            output_schema=output_schema,
            required_permission=metadata.permission,
            side_effect=metadata.side_effect,
            approval_required=metadata.approval_required,
            idempotency=metadata.idempotency,
            idempotency_keys=metadata.idempotency_keys,
            injected_type_names=injected_type_names,
        )
        return CompiledAiTool(
            function=function,
            metadata=metadata,
            definition=definition,
            request_parameter=request_parameter,
            request_type=request_type,
            result_type=result_type,
            request_adapter=request_adapter,
            result_adapter=result_adapter,
            injected_parameters=injected_parameters,
        )


__all__ = ["AiToolCompiler", "AiToolCompilerError", "CompiledAiTool"]
