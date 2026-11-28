"""主对话提交用户自定义指令的局部修改，经本人确认后保存。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from erp_web.schemas.agent_instructions import UserAgentsDocument, UserAgentsSaveRequest, UserAgentsUpdateRequest, UserAgentsUpdateResult
from erp_web.schemas.ai_tools import ToolApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.requests import RequestValidationError
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.tool_approval import verify_execution_approval
from erp_web.services.user_agents import propose_user_agents_update

USER_AGENTS_UPDATE = "user_agents_update"


@dataclass(frozen=True)
class UserAgentsScope:
    read: Callable[[], UserAgentsDocument]
    save: Callable[[dict], UserAgentsDocument]


def _read(scope: UserAgentsScope) -> UserAgentsDocument:
    try:
        return scope.read()
    except ValueError as exc:
        raise BusinessCapabilityError("USER_AGENTS_READ_FAILED", str(exc)) from exc
    except OSError as exc:
        raise BusinessCapabilityError("USER_AGENTS_READ_FAILED", "无法读取 AI 自定义指令。") from exc


def _proposal(document: UserAgentsDocument, request: UserAgentsUpdateRequest) -> UserAgentsSaveRequest:
    try:
        return propose_user_agents_update(document, request)
    except RequestValidationError as exc:
        raise BusinessCapabilityError(exc.error_code, str(exc)) from exc
    except ValueError as exc:
        raise BusinessCapabilityError(
            "USER_AGENTS_INVALID_CONTENT", "修改后的自定义指令须为 UTF-8 且不超过 32 KiB。",
        ) from exc


def _snapshot(request: UserAgentsUpdateRequest, revision: str) -> ToolApprovalSnapshot:
    action = "替换一条" if request.current_text is not None else "新增一条"
    return ToolApprovalSnapshot(
        summary=f"为当前账号{action} AI 自定义指令，供后续对话使用。",
        approval_tier="local",
        canonical_payload={"revision": revision, "update": request.model_dump(mode="json")},
    )


def _prepare_update(request: UserAgentsUpdateRequest, scope: UserAgentsScope) -> ToolApprovalSnapshot:
    document = _read(scope)
    proposal = _proposal(document, request)
    return _snapshot(request, proposal.expected_revision)


@ai_tool(
    name=USER_AGENTS_UPDATE,
    description=(
        "仅当本轮用户明确要求记住、写入记忆或修改长期偏好时，提交当前账号的一条 AI 自定义指令，确认后写入 userAgents.md。"
        "text 填可跨对话复用的规则正文，保留适用范围和例外。新增时省略 current_text，更新时提供要替换的现有规则原文。"
        "现有正文已在用户自定义全局偏好指令中。根据实际回执报告保存结果；拒绝后停止。"
    ),
    permission="user_agents.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_prepare_update,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
)
def user_agents_update(
    request: UserAgentsUpdateRequest,
    scope: Annotated[UserAgentsScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> UserAgentsUpdateResult:
    document = _read(scope)
    verify_execution_approval(
        execution,
        snapshot=_snapshot(request, document["revision"]),
        capability_name=USER_AGENTS_UPDATE,
        capability_version="1",
        stale_code="USER_AGENTS_CHANGED",
    )
    proposal = _proposal(document, request)
    changed = proposal.content != document["content"]
    if changed:
        try:
            document = scope.save(proposal.model_dump())
        except RequestValidationError as exc:
            raise BusinessCapabilityError(exc.error_code, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise BusinessCapabilityError("USER_AGENTS_SAVE_FAILED", "无法保存 AI 自定义指令。") from exc
    return UserAgentsUpdateResult(changed=changed, text=request.text, revision=document["revision"])


USER_AGENTS_CAPABILITIES = (user_agents_update,)

__all__ = ["USER_AGENTS_CAPABILITIES", "USER_AGENTS_UPDATE", "UserAgentsScope"]
