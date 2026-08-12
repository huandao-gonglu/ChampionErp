"""AI Tool 的 dependency-light 声明元数据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, ParamSpec, TypeVar, cast

from erp_web.schemas.ai_tools import normalize_ai_tool_name


ToolFunctionP = ParamSpec("ToolFunctionP")
ToolResultT = TypeVar("ToolResultT")
ToolFunctionT = Callable[ToolFunctionP, ToolResultT]
AiToolSideEffect = Literal["none", "write"]
AiToolIdempotency = Literal["none", "required"]
_AI_TOOL_METADATA_ATTRIBUTE = "__ai_tool_metadata__"


@dataclass(frozen=True)
class Injected:
    """标记由可信绑定作用域或 Runtime 提供的函数参数。"""


@dataclass(frozen=True)
class AiToolMetadata:
    name: str
    description: str
    permission: str
    side_effect: AiToolSideEffect
    approval_required: bool
    idempotency: AiToolIdempotency
    idempotency_keys: tuple[str, ...]
    version: str


def _required_text(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} 不能为空")
    return normalized


def ai_tool(
    *,
    name: str,
    description: str,
    permission: str,
    side_effect: AiToolSideEffect = "none",
    approval_required: bool | None = None,
    idempotency: AiToolIdempotency = "none",
    idempotency_keys: tuple[str, ...] = (),
    version: str = "1",
) -> Callable[[ToolFunctionT], ToolFunctionT]:
    """给类型化能力函数附加不可变契约，不执行注册或领域逻辑。"""

    normalized_name = normalize_ai_tool_name(name)
    normalized_description = _required_text(description, label="tool.description")
    normalized_permission = _required_text(permission, label="tool.permission")
    normalized_version = _required_text(version, label="tool.version")
    if side_effect not in {"none", "write"}:
        raise ValueError("tool.side_effect 只允许 none 或 write")
    if idempotency not in {"none", "required"}:
        raise ValueError("tool.idempotency 只允许 none 或 required")
    normalized_keys = tuple(
        _required_text(value, label="tool.idempotency_keys")
        for value in idempotency_keys
    )
    if len(normalized_keys) != len(set(normalized_keys)):
        raise ValueError("tool.idempotency_keys 不得重复")
    if side_effect == "write":
        if approval_required is None:
            raise ValueError("写工具必须显式声明 approval_required")
        if idempotency != "required" or not normalized_keys:
            raise ValueError(
                "写工具必须声明 required idempotency 和非空 idempotency_keys"
            )
    elif idempotency != "none" or normalized_keys:
        raise ValueError("只读工具不得声明写入幂等策略")
    normalized_approval = (
        bool(approval_required) if approval_required is not None else False
    )
    metadata = AiToolMetadata(
        name=normalized_name,
        description=normalized_description,
        permission=normalized_permission,
        side_effect=side_effect,
        approval_required=normalized_approval,
        idempotency=idempotency,
        idempotency_keys=normalized_keys,
        version=normalized_version,
    )

    def decorate(function: ToolFunctionT) -> ToolFunctionT:
        if not callable(function):
            raise TypeError("@ai_tool 只能修饰可调用函数")
        if hasattr(function, _AI_TOOL_METADATA_ATTRIBUTE):
            raise ValueError(f"函数 {function.__qualname__} 已声明 @ai_tool")
        setattr(function, _AI_TOOL_METADATA_ATTRIBUTE, metadata)
        return function

    return decorate


def get_ai_tool_metadata(function: Callable[..., object]) -> AiToolMetadata:
    metadata = getattr(function, _AI_TOOL_METADATA_ATTRIBUTE, None)
    if not isinstance(metadata, AiToolMetadata):
        raise ValueError(f"函数 {function.__qualname__} 未声明合法 @ai_tool 元数据")
    return cast(AiToolMetadata, metadata)


__all__ = [
    "AiToolMetadata",
    "Injected",
    "ai_tool",
    "get_ai_tool_metadata",
]
