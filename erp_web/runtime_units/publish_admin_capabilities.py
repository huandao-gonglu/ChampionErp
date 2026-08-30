from __future__ import annotations

"""发布管理 Capability：非 Mercado 直接发布与 Mercado User Product 暂停。

两者都会对外部平台产生真实影响，因此全部是 task + approval 能力：
审批摘要与规范化参数由服务端快照函数生成（含商品标题等服务端事实），
digest 绑定冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照
复核，防止模型伪造审批或批准后目标漂移。领域逻辑仍由 ``runtime_api`` /
``publish_mercadolibre`` 拥有，Capability 只做类型化编排。
"""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Any

from erp_web.schemas.ai_tools import TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.publish_admin_capabilities import (
    MercadoLibreUserProductPauseRequest,
    MercadoLibreUserProductPauseResult,
    ProductPublishDirectRequest,
    ProductPublishDirectResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.task_approval import verify_execution_approval


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _state_fingerprint(value: Any) -> str:
    """返回不泄露商品正文的稳定状态指纹，用于绑定破坏性审批。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_PUBLISH_CONFIG_SECRET_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "app_secret",
        "client_secret",
        "code_verifier",
        "cookie",
        "password",
        "refresh_token",
    }
)


def _non_secret_publish_config(
    config: dict[str, Any],
    platform: str,
) -> dict[str, Any]:
    """冻结会影响 payload 的配置，同时禁止把凭据写进审批快照。"""

    def sanitized(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): sanitized(item)
                for key, item in value.items()
                if str(key).strip().lower() not in _PUBLISH_CONFIG_SECRET_FIELDS
            }
        if isinstance(value, list):
            return [sanitized(item) for item in value]
        return value

    store = config.get(platform) if isinstance(config.get(platform), dict) else {}
    listing = config.get("listing") if isinstance(config.get("listing"), dict) else {}
    return {
        "listing": sanitized(listing),
        "store": sanitized(store),
    }


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
    user_product_pauser: Callable[[str], dict[str, Any]]


PRODUCT_PUBLISH_DIRECT_TOOL = "product_publish_direct"
MERCADOLIBRE_USER_PRODUCT_PAUSE_TOOL = "mercadolibre_user_product_pause"


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
    """发布目标审批快照：冻结发布目标与完整商品状态指纹。"""

    product = _load_product(scope, product_id)
    config = scope.store_config_loader()
    config = config if isinstance(config, dict) else {}
    title = _text(product.get("title")) or "（无标题）"
    return TaskApprovalSnapshot(
        summary=f"{action}：《{title}》({product_id}) → {platform}",
        canonical_payload={
            "action": action,
            "platform": platform,
            "product_id": product_id,
            # 只把摘要哈希写入审批，不暴露商品正文；草稿的销售国家、核价、
            # 图片或属性在批准后发生任何变化，执行侧重算都会判定 stale。
            "product_fingerprint": _state_fingerprint(product),
            "publish_config_fingerprint": _state_fingerprint(
                _non_secret_publish_config(config, platform)
            ),
            "title": title,
        },
    )


def _publish_direct_approval_snapshot(
    request: ProductPublishDirectRequest,
    scope: PublishAdminCapabilityScope,
) -> TaskApprovalSnapshot:
    platform = _text(request.platform).lower()
    if not platform:
        raise BusinessCapabilityError(
            "PUBLISH_DIRECT_PLATFORM_REQUIRED",
            "直接发布必须显式指定非 Mercado Libre 平台。",
        )
    if platform == "mercadolibre":
        raise BusinessCapabilityError(
            "MERCADOLIBRE_PUBLISH_BUS_REQUIRED",
            (
                "Mercado Libre User Products 只能通过预览、人工确认与"
                " PublishingBus 持久队列发布。"
            ),
        )
    return _publish_target_snapshot(
        scope,
        product_id=request.product_id,
        platform=platform,
        action="直接同步发布商品",
    )


def _mercadolibre_user_product_pause_approval_snapshot(
    request: MercadoLibreUserProductPauseRequest,
    scope: PublishAdminCapabilityScope,
) -> TaskApprovalSnapshot:
    del scope
    return TaskApprovalSnapshot(
        summary=(
            "暂停 Mercado Siteless User Product "
            f"{request.siteless_user_product_id}"
        ),
        canonical_payload={
            "siteless_user_product_id": request.siteless_user_product_id,
            "platform": "mercadolibre",
        },
    )


@ai_tool(
    name=PRODUCT_PUBLISH_DIRECT_TOOL,
    description=(
        "直接同步发布商品到非 Mercado Libre 目标平台（真实调用平台接口）；"
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
    platform = _text(request.platform).lower()
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
    name=MERCADOLIBRE_USER_PRODUCT_PAUSE_TOOL,
    description=(
        "暂停 Mercado Siteless User Product 及其市场刊登；"
        "需要人工在受信界面批准后才会执行。"
    ),
    permission="platform.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_mercadolibre_user_product_pause_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def mercadolibre_user_product_pause(
    request: MercadoLibreUserProductPauseRequest,
    scope: Annotated[PublishAdminCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> MercadoLibreUserProductPauseResult:
    verify_execution_approval(
        execution,
        snapshot=_mercadolibre_user_product_pause_approval_snapshot(
            request,
            scope,
        ),
        capability_name=MERCADOLIBRE_USER_PRODUCT_PAUSE_TOOL,
        capability_version="1",
        stale_code="USER_PRODUCT_PAUSE_APPROVAL_STALE",
    )
    try:
        result = scope.user_product_pauser(request.siteless_user_product_id)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        # 暂停请求已发往平台后，任何异常都不能按普通可重试失败处理。
        raise BusinessCapabilityError(
            "USER_PRODUCT_PAUSE_OUTCOME_UNKNOWN",
            f"User Product 暂停请求已发出，平台侧结果未知：{exc}",
            retryable=False,
            details={"outcome_unknown": True},
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        if isinstance(result, dict) and result.get("outcome_unknown") is True:
            details = _dict_value(result.get("details"))
            details["outcome_unknown"] = True
            raise BusinessCapabilityError(
                "USER_PRODUCT_PAUSE_OUTCOME_UNKNOWN",
                _text(result.get("error"))
                or "User Product 暂停请求已发出，平台侧结果未知。",
                retryable=False,
                details=details,
            )
        raise BusinessCapabilityError(
            _text(result.get("error_code") if isinstance(result, dict) else "")
            or "USER_PRODUCT_PAUSE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "Mercado User Product 暂停失败。",
        )
    return MercadoLibreUserProductPauseResult(
        ok=True,
        platform="mercadolibre",
        siteless_user_product_id=request.siteless_user_product_id,
        status=_text(result.get("status")),
        message=_text(result.get("message")),
    )


PUBLISH_ADMIN_AI_CAPABILITIES = (
    product_publish_direct,
    mercadolibre_user_product_pause,
)


__all__ = [
    "MERCADOLIBRE_USER_PRODUCT_PAUSE_TOOL",
    "PRODUCT_PUBLISH_DIRECT_TOOL",
    "PUBLISH_ADMIN_AI_CAPABILITIES",
    "PublishAdminCapabilityScope",
    "mercadolibre_user_product_pause",
    "product_publish_direct",
]
