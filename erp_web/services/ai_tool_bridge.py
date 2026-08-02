"""把 ERP ToolSet 安全转换为 Pydantic AI FunctionToolset。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext, Tool

from erp_web.schemas.ai_tools import AiToolCommand, AiToolDefinition

from .ai_agent_dependencies import AiAgentDependencies
from .ai_tool_registry import AiToolSet


class AiToolBridgeError(RuntimeError):
    """ERP Runtime 拒绝或无法完成一次 Pydantic tool call。"""

    def __init__(self, *, code: str, tool_name: str, tool_call_id: str) -> None:
        self.code = str(code or "TOOL_EXECUTION_FAILED")
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        super().__init__(f"工具 {tool_name} 执行失败（{self.code}）。")


@dataclass(frozen=True)
class PydanticToolBridge:
    """固定 Pydantic schema 与 ERP Runtime allowlist 的一一对应关系。"""

    toolset: AiToolSet

    def _require_runtime_binding(self, dependencies: AiAgentDependencies) -> None:
        if dependencies.tool_runtime.toolset is not self.toolset:
            raise AiToolBridgeError(
                code="TOOLSET_BINDING_MISMATCH",
                tool_name=self.toolset.toolset_id,
                tool_call_id="unbound",
            )

    def execute(
        self,
        *,
        dependencies: AiAgentDependencies,
        tool_name: str,
        tool_call_id: str,
        arguments: Mapping[str, Any],
        round_number: int,
    ) -> Any:
        """唯一执行入口；不会读取或调用 ToolSet binding.executor。"""

        self._require_runtime_binding(dependencies)
        call_id = str(tool_call_id or "").strip()
        if not call_id:
            raise AiToolBridgeError(
                code="TOOL_CALL_INVALID",
                tool_name=tool_name,
                tool_call_id="missing",
            )
        binding = self.toolset.get(tool_name)
        if binding is None:
            raise AiToolBridgeError(
                code="TOOL_NOT_ALLOWED",
                tool_name=tool_name,
                tool_call_id=call_id,
            )
        result = dependencies.tool_runtime.execute(
            AiToolCommand(
                call_id=call_id,
                tool_name=tool_name,
                tool_version=binding.definition.version,
                arguments=dict(arguments),
                round=max(1, int(round_number)),
            )
        )
        payload = result.to_dict()
        if not result.ok:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_code = str(error.get("code") or "TOOL_EXECUTION_FAILED")
            if error_code == "TOOL_APPROVAL_REQUIRED":
                raise ApprovalRequired(
                    metadata={
                        "use_case_id": dependencies.use_case_id,
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                    }
                )
            raise AiToolBridgeError(
                code=error_code,
                tool_name=tool_name,
                tool_call_id=call_id,
            )
        return payload.get("output")

    def _pydantic_tool(
        self,
        definition: AiToolDefinition,
    ) -> Tool[AiAgentDependencies]:
        def invoke(
            ctx: RunContext[AiAgentDependencies],
            **arguments: Any,
        ) -> Any:
            return self.execute(
                dependencies=ctx.deps,
                tool_name=definition.name,
                tool_call_id=str(ctx.tool_call_id or ""),
                arguments=arguments,
                round_number=ctx.run_step,
            )

        invoke.__name__ = definition.name
        return Tool.from_schema(
            invoke,
            name=definition.name,
            description=definition.description,
            json_schema=definition.to_dict()["input_schema"],
            takes_ctx=True,
            sequential=True,
        )

    def as_toolset(self) -> FunctionToolset[AiAgentDependencies]:
        return FunctionToolset(
            tools=[self._pydantic_tool(item) for item in self.toolset.definitions],
            id=self.toolset.toolset_id,
        )


def build_pydantic_toolset(
    toolset: AiToolSet,
) -> FunctionToolset[AiAgentDependencies]:
    return PydanticToolBridge(toolset).as_toolset()


__all__ = [
    "AiToolBridgeError",
    "PydanticToolBridge",
    "build_pydantic_toolset",
]
