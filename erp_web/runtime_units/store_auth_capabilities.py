from __future__ import annotations

"""店铺授权状态只读 Capability：脱敏 checklist 与授权有效性检查。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.schemas.store_auth_capabilities import (
    StoreAuthCheckRequest,
    StoreAuthCheckResult,
    StoreAuthChecklistRequest,
    StoreAuthChecklistResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


_SECRET_KEYS = frozenset(
    {
        "config",
        "token",
        "access_token",
        "refresh_token",
        "app_id",
        "app_secret",
        "client_id",
        "client_secret",
        "source_key",
        "cookie",
        "alibaba_cookie",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _public_details(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key not in {"ok", "message"} and key.lower() not in _SECRET_KEYS
    }


@dataclass(frozen=True)
class StoreAuthCapabilityScope:
    """授权状态查询的可信依赖边界。"""

    checklist_loader: Callable[[], dict[str, Any]]
    auth_tester: Callable[[str, str], dict[str, Any]]


STORE_AUTH_CHECKLIST_TOOL = "store_auth_checklist"
STORE_AUTH_CHECK_TOOL = "store_auth_check"


@ai_tool(
    name=STORE_AUTH_CHECKLIST_TOOL,
    description="读取 Mercado Libre 授权脱敏 checklist（缺失项与下一步）。",
    permission="store_auth.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def store_auth_checklist(
    request: StoreAuthChecklistRequest,
    scope: Annotated[StoreAuthCapabilityScope, Injected()],
) -> StoreAuthChecklistResult:
    del request
    checklist = scope.checklist_loader()
    if not isinstance(checklist, dict):
        raise BusinessCapabilityError(
            "STORE_AUTH_CHECKLIST_UNAVAILABLE",
            "授权 checklist 不可读。",
        )
    return StoreAuthChecklistResult(checklist=dict(checklist))


@ai_tool(
    name=STORE_AUTH_CHECK_TOOL,
    description="检查目标平台店铺授权是否有效（在线校验，只读诊断）。",
    permission="store_auth.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def store_auth_check(
    request: StoreAuthCheckRequest,
    scope: Annotated[StoreAuthCapabilityScope, Injected()],
) -> StoreAuthCheckResult:
    platform = _text(request.platform).lower()
    try:
        raw = scope.auth_tester(platform, request.scope)
    except RuntimeError as exc:
        return StoreAuthCheckResult(
            platform=platform,
            ok=False,
            message=_text(exc),
        )
    if not isinstance(raw, dict):
        raise BusinessCapabilityError(
            "STORE_AUTH_CHECK_UNAVAILABLE",
            "授权校验返回格式无效。",
        )
    return StoreAuthCheckResult(
        platform=platform,
        ok=bool(raw.get("ok")),
        message=_text(raw.get("message") or raw.get("error")),
        details=_public_details(raw),
    )


STORE_AUTH_AI_CAPABILITIES = (
    store_auth_checklist,
    store_auth_check,
)


__all__ = [
    "STORE_AUTH_CHECKLIST_TOOL",
    "STORE_AUTH_CHECK_TOOL",
    "STORE_AUTH_AI_CAPABILITIES",
    "StoreAuthCapabilityScope",
    "store_auth_check",
    "store_auth_checklist",
]
