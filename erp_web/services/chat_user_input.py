"""将需要人在对话中回答的问题交给原生 Deferred 工具。"""

from typing import Annotated

from pydantic import Field, StringConstraints
from pydantic_ai import CallDeferred, RunContext, Tool
from pydantic_ai.tools import ToolDefinition

from erp_web.services.ai_agent_dependencies import AiAgentDependencies

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Option = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


def request_user_input(
    title: Title, question: Question, options: Annotated[list[Option], Field(max_length=8)] | None = None,
) -> str:
    """在当前对话等待用户填写、选择或确认。title 是具体的摘要标题，question 是完整问题，options 是可选答案。"""
    raise CallDeferred()


async def prepare_user_input(ctx: RunContext[AiAgentDependencies], definition: ToolDefinition) -> ToolDefinition | None:
    support = ctx.deps.tool_runtime.run_support
    return definition if getattr(support, "web_input", False) else None


USER_INPUT_TOOL = Tool(request_user_input, prepare=prepare_user_input)
