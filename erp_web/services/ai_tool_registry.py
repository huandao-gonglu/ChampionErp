"""不可变 ToolSet 与 definition/executor 绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from erp_web.schemas.ai_tools import AiToolDefinition
from erp_web.schemas.ai_trace import AiExecutionContext


AiToolExecutor = Callable[[dict[str, Any], AiExecutionContext], Any]
_DEADLINE_AWARE_ATTRIBUTE = "__ai_tool_deadline_aware__"


def deadline_aware_tool_executor(executor: AiToolExecutor) -> AiToolExecutor:
    """显式确认 executor 会把 context deadline 应用于全部阻塞调用。

    这是注册守卫，不是线程取消器。Runtime 不接受未声明该契约的真实 executor，
    避免把事后超时检测误认为可中止同步阻塞调用。
    """

    if not callable(executor):
        raise TypeError("executor 必须可调用")

    @wraps(executor)
    def guarded(
        arguments: dict[str, Any],
        context: AiExecutionContext,
    ) -> Any:
        context.bounded_timeout_seconds()
        return executor(arguments, context)

    setattr(guarded, _DEADLINE_AWARE_ATTRIBUTE, True)
    return guarded


@dataclass(frozen=True)
class AiToolBinding:
    definition: AiToolDefinition
    executor: AiToolExecutor
    # 审批工具专属：arguments → 服务端审批快照；非审批工具为 None。
    approval_preparer: Callable[[dict[str, Any]], Any] | None = None

    def __post_init__(self) -> None:
        if not callable(self.executor):
            raise TypeError(f"工具 {self.definition.name} 的 executor 必须可调用")
        if not getattr(self.executor, _DEADLINE_AWARE_ATTRIBUTE, False):
            raise ValueError(
                f"工具 {self.definition.name} 的 executor 未声明 cooperative deadline 契约"
            )
        if self.definition.approval_required and not callable(
            self.approval_preparer
        ):
            raise ValueError(
                f"审批工具 {self.definition.name} 缺少服务端审批准备器"
            )
        if not self.definition.approval_required and self.approval_preparer is not None:
            raise ValueError(
                f"非审批工具 {self.definition.name} 不得携带审批准备器"
            )


@dataclass(frozen=True)
class AiToolSet:
    """一个 AI profile 可见的完整工具 allowlist。"""

    toolset_id: str
    bindings: Mapping[str, AiToolBinding]

    def __post_init__(self) -> None:
        if not self.toolset_id:
            raise ValueError("toolset_id 不能为空")
        copied = dict(self.bindings)
        for name, binding in copied.items():
            if not isinstance(binding, AiToolBinding):
                raise TypeError(f"ToolSet {self.toolset_id} 的 binding 类型无效")
            if name != binding.definition.name:
                raise ValueError(
                    f"ToolSet key {name} 与 definition {binding.definition.name} 不一致"
                )
        object.__setattr__(self, "bindings", MappingProxyType(copied))

    @classmethod
    def bind(
        cls,
        toolset_id: str,
        definitions: Iterable[AiToolDefinition],
        executors: Mapping[str, AiToolExecutor],
        approval_preparers: Mapping[
            str, Callable[[dict[str, Any]], Any]
        ]
        | None = None,
    ) -> "AiToolSet":
        preparers = dict(approval_preparers or {})
        definition_map: dict[str, AiToolDefinition] = {}
        for definition in definitions:
            if definition.name in definition_map:
                raise ValueError(f"ToolSet {toolset_id} 重复定义工具 {definition.name}")
            definition_map[definition.name] = definition
        missing = sorted(definition_map.keys() - executors.keys())
        extra = sorted(executors.keys() - definition_map.keys())
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"缺少 executor：{', '.join(missing)}")
            if extra:
                parts.append(f"存在未定义 executor：{', '.join(extra)}")
            raise ValueError(f"ToolSet {toolset_id} 绑定不完整；{'；'.join(parts)}")
        unknown_preparers = sorted(set(preparers.keys()) - definition_map.keys())
        if unknown_preparers:
            raise ValueError(
                f"ToolSet {toolset_id} 审批准备器对应未定义工具：{', '.join(unknown_preparers)}"
            )
        return cls(
            toolset_id=toolset_id,
            bindings={
                name: AiToolBinding(
                    definition,
                    executors[name],
                    preparers.get(name),
                )
                for name, definition in definition_map.items()
            },
        )

    @property
    def definitions(self) -> tuple[AiToolDefinition, ...]:
        return tuple(binding.definition for binding in self.bindings.values())

    def get(self, tool_name: str) -> AiToolBinding | None:
        return self.bindings.get(tool_name)

__all__ = [
    "AiToolBinding",
    "AiToolExecutor",
    "AiToolSet",
    "deadline_aware_tool_executor",
]
