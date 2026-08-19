from __future__ import annotations

"""发布管理 Capability：直接发布、真实终态确认、远端商品关闭。

三者都会对外部平台产生真实影响，因此全部是 task + approval 能力：
审批摘要与规范化参数由服务端快照函数生成（含商品标题等服务端事实），
digest 绑定冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照
复核，防止模型伪造审批或批准后目标漂移。领域逻辑仍由 ``runtime_api`` /
``publish_mercadolibre`` 拥有，Capability 只做类型化编排。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.schemas.ai_tools import TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.publish_admin_capabilities import (
    PlatformItemCloseRequest,
    PlatformItemCloseResult,
    ProductPublishDirectRequest,
    ProductPublishDirectResult,
    PublishRealConfirmRequest,
    PublishRealConfirmResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.task_approval import verify_execution_approval


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PublishAdminCapabilityScope:
    """发布管理能力的可信依赖边界。"""

    direct_publisher: Callable[
        [dict[str, Any], str, dict[str, Any]], dict[str, Any]
    ]
    product_loader: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any] | None, int],
    ]
    store_config_loader: Callable[[], dict[str, Any]]
    real_publisher: Callable[[dict[str, Any], bool], dict[str, Any]]
    item_closer: Callable[[str], dict[str, Any]]


PRODUCT_PUBLISH_DIRECT_TOOL = "product_publish_direct"
PUBLISH_REAL_CONFIRM_TOOL = "publish_real_confirm"
PLATFORM_ITEM_CLOSE_TOOL = "platform_item_close"


def _load_product(
    scope: PublishAdminCapabilityScope,
    product_id: str,
) -> dict[str, Any]:
    product, error, _status = scope.product_loader({"product_id": product_id})
    if error is not None:
        raise BusinessCapabilityError(
            _text(error.get("error_code")) or "PRODUCT_NOT_FOUND",
            _text(error.get("error")) or "商品不存在。",
        )
    return product


def _publish_target_snapshot(
    scope: PublishAdminCapabilityScope,
    *,
    product_id: str,
    platform: str,
    action: str,
) -> TaskApprovalSnapshot:
    """发布目标审批快照：冻结 product_id/platform 与商品标题等服务端事实。"""

    product = _load_product(scope, product_id)
    title = _text(product.get("title")) or "（无标题）"
    return TaskApprovalSnapshot(
        summary=f"{action}：《{title}》({product_id}) → {platform}",
        canonical_payload={
            "action": action,
            "platform": platform,
            "product_id": product_id,
            "title": title,
        },
    )


def _publish_direct_approval_snapshot(
    request: ProductPublishDirectRequest,
    scope: PublishAdminCapabilityScope,
) -> TaskApprovalSnapshot:
    platform = _text(request.platform).lower() or "mercadolibre"
    return _publish_target_snapshot(
        scope,
        product_id=request.product_id,
        platform=platform,
        action="直接同步发布商品",
    )


def _publish_real_confirm_approval_snapshot(
    request: PublishRealConfirmRequest,
    scope: PublishAdminCapabilityScope,
) -> TaskApprovalSnapshot:
    return _publish_target_snapshot(
        scope,
        product_id=request.product_id,
        platform="mercadolibre",
        action="确认 Mercado Libre 真实发布终态",
    )


def _platform_item_close_approval_snapshot(
    request: PlatformItemCloseRequest,
    scope: PublishAdminCapabilityScope,
) -> TaskApprovalSnapshot:
    del scope
    platform = _text(request.platform).lower() or "mercadolibre"
    return TaskApprovalSnapshot(
        summary=f"关闭 {platform} 远端商品 {request.item_id}",
        canonical_payload={
            "item_id": request.item_id,
            "platform": platform,
        },
    )


@ai_tool(
    name=PRODUCT_PUBLISH_DIRECT_TOOL,
    description=(
        "直接同步发布商品到目标平台（真实调用平台接口）；"
        "需要人工在受信界面批准后才会执行。"
    ),
    permission="product.publish",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_publish_direct_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_publish_direct(
    request: ProductPublishDirectRequest,
    scope: Annotated[PublishAdminCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductPublishDirectResult:
    platform = _text(request.platform).lower() or "mercadolibre"
    verify_execution_approval(
        execution,
        snapshot=_publish_direct_approval_snapshot(request, scope),
        capability_name=PRODUCT_PUBLISH_DIRECT_TOOL,
        capability_version="1",
        stale_code="PUBLISH_DIRECT_APPROVAL_STALE",
    )
    product = _load_product(scope, request.product_id)
    try:
        result = scope.direct_publisher(
            product,
            platform,
            scope.store_config_loader(),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_DIRECT_FAILED",
            str(exc) or "直接发布失败。",
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        status = _text(result.get("status") if isinstance(result, dict) else "")
        code = (
            "PUBLISH_DIRECT_NOT_READY"
            if status == "not_ready"
            else "PUBLISH_DIRECT_FAILED"
        )
        raise BusinessCapabilityError(
            code,
            _text(result.get("error") if isinstance(result, dict) else "")
            or "直接发布失败。",
        )
    return ProductPublishDirectResult(
        ok=True,
        status=_text(result.get("status")) or "published",
        platform=platform,
        product_id=request.product_id,
        message=_text(result.get("message")),
        result=_dict_value(result.get("result")),
    )


@ai_tool(
    name=PUBLISH_REAL_CONFIRM_TOOL,
    description=(
        "确认 Mercado Libre 真实发布终态（真实调用平台接口）；"
        "需要人工在受信界面批准后才会执行。"
    ),
    permission="product.publish",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_publish_real_confirm_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def publish_real_confirm(
    request: PublishRealConfirmRequest,
    scope: Annotated[PublishAdminCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> PublishRealConfirmResult:
    verify_execution_approval(
        execution,
        snapshot=_publish_real_confirm_approval_snapshot(request, scope),
        capability_name=PUBLISH_REAL_CONFIRM_TOOL,
        capability_version="1",
        stale_code="PUBLISH_REAL_CONFIRM_APPROVAL_STALE",
    )
    product = _load_product(scope, request.product_id)
    try:
        result = scope.real_publisher(product, True)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "PUBLISH_REAL_CONFIRM_FAILED",
            str(exc) or "真实发布确认失败。",
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        status = _text(result.get("status") if isinstance(result, dict) else "")
        if status == "not_ready":
            code = "PUBLISH_REAL_CONFIRM_NOT_READY"
        else:
            code = "PUBLISH_REAL_CONFIRM_FAILED"
        raise BusinessCapabilityError(
            code,
            _text(result.get("error") if isinstance(result, dict) else "")
            or "真实发布确认失败。",
        )
    return PublishRealConfirmResult(
        ok=True,
        status=_text(result.get("status")) or "real_publish_success",
        product_id=request.product_id,
        payload_path=_text(result.get("payload_path")),
        message=_text(result.get("message")),
        result=_dict_value(result.get("result")),
    )


@ai_tool(
    name=PLATFORM_ITEM_CLOSE_TOOL,
    description=(
        "关闭/下架平台远端商品（真实调用平台接口）；"
        "需要人工在受信界面批准后才会执行。"
    ),
    permission="platform.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_platform_item_close_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def platform_item_close(
    request: PlatformItemCloseRequest,
    scope: Annotated[PublishAdminCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> PlatformItemCloseResult:
    platform = _text(request.platform).lower() or "mercadolibre"
    if platform != "mercadolibre":
        raise BusinessCapabilityError(
            "PLATFORM_ITEM_CLOSE_UNSUPPORTED",
            f"暂不支持关闭该平台的远端商品：{platform}",
        )
    verify_execution_approval(
        execution,
        snapshot=_platform_item_close_approval_snapshot(request, scope),
        capability_name=PLATFORM_ITEM_CLOSE_TOOL,
        capability_version="1",
        stale_code="ITEM_CLOSE_APPROVAL_STALE",
    )
    try:
        result = scope.item_closer(request.item_id)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        # 关闭请求已发往平台：任何异常（含超时）都不能按普通可重试失败处理，
        # 否则自动重试可能重复关闭/误报终态；上报为结果未知，交由人工核对。
        raise BusinessCapabilityError(
            "ITEM_CLOSE_OUTCOME_UNKNOWN",
            f"远端商品关闭请求已发出，平台侧结果未知：{exc}",
            retryable=False,
            details={"outcome_unknown": True},
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            _text(result.get("error_code") if isinstance(result, dict) else "")
            or "ITEM_CLOSE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "远端商品关闭失败。",
        )
    return PlatformItemCloseResult(
        ok=True,
        platform=platform,
        item_id=request.item_id,
        status=_text(result.get("status")),
        message=_text(result.get("message")),
    )


PUBLISH_ADMIN_AI_CAPABILITIES = (
    product_publish_direct,
    publish_real_confirm,
    platform_item_close,
)


__all__ = [
    "PLATFORM_ITEM_CLOSE_TOOL",
    "PRODUCT_PUBLISH_DIRECT_TOOL",
    "PUBLISH_ADMIN_AI_CAPABILITIES",
    "PUBLISH_REAL_CONFIRM_TOOL",
    "PublishAdminCapabilityScope",
    "platform_item_close",
    "product_publish_direct",
    "publish_real_confirm",
]
