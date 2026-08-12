"""显式 AI 能力目录与 run-scoped ToolSet 绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Collection, Iterable, Mapping

from erp_web.schemas.ai_trace import AiExecutionContext

from .ai_tool_compiler import AiToolCompiler, CompiledAiTool
from .ai_tool_registry import AiToolBinding, AiToolSet


@dataclass(frozen=True)
class AiToolBindingScope:
    """不依赖 Agent dependencies 的可信领域对象集合。"""

    providers: Mapping[type[Any], Any]

    def __post_init__(self) -> None:
        copied: dict[type[Any], Any] = {}
        for provider_type, value in dict(self.providers).items():
            if not isinstance(provider_type, type):
                raise TypeError("Binding Scope provider key 必须是具体类型")
            if provider_type is AiExecutionContext:
                raise ValueError("AiExecutionContext 只能由 AiToolRuntime 动态注入")
            if not isinstance(value, provider_type):
                raise TypeError(
                    f"Binding Scope provider 与声明类型 {provider_type.__qualname__} 不匹配"
                )
            copied[provider_type] = value
        object.__setattr__(self, "providers", MappingProxyType(copied))

    @classmethod
    def from_values(cls, *values: Any) -> "AiToolBindingScope":
        providers: dict[type[Any], Any] = {}
        for value in values:
            provider_type = type(value)
            if provider_type in providers:
                raise ValueError(
                    f"Binding Scope 重复提供类型 {provider_type.__qualname__}"
                )
            providers[provider_type] = value
        return cls(providers)

    def require(self, required_type: type[Any]) -> Any:
        matches = [
            value
            for provider_type, value in self.providers.items()
            if provider_type is required_type or isinstance(value, required_type)
        ]
        if not matches:
            raise ValueError(
                f"Binding Scope 缺少 Injected provider：{required_type.__qualname__}"
            )
        if len(matches) != 1:
            raise ValueError(
                f"Binding Scope 为 {required_type.__qualname__} 提供了多个可信对象"
            )
        return matches[0]


@dataclass(frozen=True)
class AiToolCatalog:
    """只收录调用方显式给出的已编译能力函数。"""

    tools: Mapping[str, CompiledAiTool]

    def __post_init__(self) -> None:
        copied = dict(self.tools)
        for name, tool in copied.items():
            if name != tool.definition.name:
                raise ValueError(f"Catalog key {name} 与工具定义名称不一致")
        object.__setattr__(self, "tools", MappingProxyType(copied))

    @classmethod
    def compile(
        cls,
        functions: Iterable[Callable[..., Any]],
    ) -> "AiToolCatalog":
        compiled: dict[str, CompiledAiTool] = {}
        for function in functions:
            tool = AiToolCompiler.compile(function)
            if tool.definition.name in compiled:
                raise ValueError(f"Catalog 重复收录工具 {tool.definition.name}")
            compiled[tool.definition.name] = tool
        return cls(compiled)

    def bind(
        self,
        *,
        toolset_id: str,
        allowed_tools: Collection[str],
        scope: AiToolBindingScope,
        declared_permissions: Collection[str],
        allow_write: bool = False,
    ) -> AiToolSet:
        allowed_names = tuple(str(name or "").strip() for name in allowed_tools)
        if not allowed_names or any(not name for name in allowed_names):
            raise ValueError("场景 allowlist 不能为空")
        if len(allowed_names) != len(set(allowed_names)):
            raise ValueError("场景 allowlist 不得包含重复工具")
        missing = sorted(set(allowed_names) - self.tools.keys())
        if missing:
            raise ValueError(
                f"场景 allowlist 引用了 Catalog 未收录工具：{', '.join(missing)}"
            )
        permissions = frozenset(
            str(value or "").strip() for value in declared_permissions
        )
        bindings: dict[str, AiToolBinding] = {}
        for name in allowed_names:
            tool = self.tools[name]
            definition = tool.definition
            if definition.required_permission not in permissions:
                raise ValueError(
                    f"Execution Profile 未声明工具 {name} 所需权限 "
                    f"{definition.required_permission}"
                )
            if definition.side_effect == "write":
                if not allow_write:
                    raise ValueError(f"Execution Profile 未允许写工具 {name}")
                if (
                    definition.idempotency != "required"
                    or not definition.idempotency_keys
                    or AiExecutionContext not in tool.injected_parameters.values()
                ):
                    raise ValueError(f"写工具 {name} 的幂等或 execution 注入声明不完整")
            bound_providers: dict[type[Any], Any] = {}
            for injected_type in set(tool.injected_parameters.values()):
                if injected_type is AiExecutionContext:
                    continue
                bound_providers[injected_type] = scope.require(injected_type)
            bindings[name] = AiToolBinding(
                definition=definition,
                executor=tool.bind_executor(bound_providers),
            )
        return AiToolSet(toolset_id=toolset_id, bindings=bindings)


__all__ = ["AiToolBindingScope", "AiToolCatalog"]
