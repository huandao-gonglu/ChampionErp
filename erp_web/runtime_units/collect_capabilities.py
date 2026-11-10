from __future__ import annotations

"""URL 采集与目标平台认领 Capability。

领域逻辑由 ``source_collect_workflows`` 与 ``collect_helpers`` 拥有。
浏览器使用桌面会话，1688 API 使用当前账号授权。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.collect_capabilities import (
    ClaimProductsRequest,
    ClaimProductsResult,
    Collect1688Request,
    Collect1688Result,
    CollectBatchRequest,
    CollectBatchResult,
    SourceCollectRequest,
    SourceCollectResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _require_collect_ok(
    result: dict[str, Any],
    *,
    default_code: str,
    default_message: str,
) -> None:
    diagnostics = (
        result.get("diagnostics")
        if isinstance(result.get("diagnostics"), dict)
        else {}
    )
    if result.get("ok") or diagnostics.get("partial_success"):
        return
    code = (
        _text(result.get("error_code") or diagnostics.get("error_code"))
        or default_code
    )
    message = (
        _text(
            result.get("error")
            or result.get("next_action")
            or diagnostics.get("error_message")
        )
        or default_message
    )
    raise BusinessCapabilityError(code, message, details={"outcome_unknown": True} if code == "DESKTOP_RESULT_UNKNOWN" else None)


@dataclass(frozen=True)
class CollectCapabilityScope:
    """采集与认领能力的可信依赖边界。"""

    source_collector: Callable[[str, str, str, tuple[str, ...]], dict[str, Any]]
    batch_collector: Callable[
        [tuple[str, ...], str, str, tuple[str, ...]], dict[str, Any]
    ]
    online_1688_collector: Callable[[dict[str, Any]], dict[str, Any]]
    claimer: Callable[[list[str], list[dict[str, str]] | None], dict[str, Any]]


SOURCE_COLLECT_TOOL = "source_collect"
COLLECT_BATCH_TOOL = "collect_batch"
COLLECT_1688_TOOL = "collect_1688"
CLAIM_PRODUCTS_TOOL = "claim_products"


@ai_tool(
    name=SOURCE_COLLECT_TOOL,
    description="按链接采集源平台商品并保存到本地采集箱。",
    permission="collect.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def source_collect(
    request: SourceCollectRequest,
    scope: Annotated[CollectCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> SourceCollectResult:
    del execution
    try:
        result = scope.source_collector(
            request.url,
            request.mode or "browser",
            request.platform,
            tuple(request.claim_platforms),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "SOURCE_COLLECT_FAILED",
            str(exc) or "商品采集失败。",
        ) from exc
    _require_collect_ok(
        result,
        default_code="SOURCE_COLLECT_FAILED",
        default_message="商品采集失败。",
    )
    product = _dict_value(result.get("product"))
    return SourceCollectResult(
        ok=bool(result.get("ok")),
        product_id=_text(product.get("product_id")),
        product=product,
        diagnostics=_dict_value(result.get("diagnostics")),
        next_action=_text(result.get("next_action")),
        message=_text(result.get("message")),
        products_index=_dict_rows(result.get("productsIndex")),
    )


@ai_tool(
    name=COLLECT_BATCH_TOOL,
    description="批量按链接采集多个源平台商品。",
    permission="collect.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def collect_batch(
    request: CollectBatchRequest,
    scope: Annotated[CollectCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CollectBatchResult:
    del execution
    try:
        result = scope.batch_collector(
            tuple(request.urls),
            request.mode or "browser",
            request.platform,
            tuple(request.claim_platforms),
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "COLLECT_BATCH_FAILED",
            str(exc) or "批量采集失败。",
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "COLLECT_BATCH_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "批量采集失败。",
        )
    return CollectBatchResult(
        ok=True,
        total=int(result.get("total") or 0),
        success_count=int(result.get("success_count") or 0),
        partial_count=int(result.get("partial_count") or 0),
        failed_count=int(result.get("failed_count") or 0),
        items=_dict_rows(result.get("items")),
        products_index=_dict_rows(result.get("productsIndex")),
    )


@ai_tool(
    name=COLLECT_1688_TOOL,
    description="通过桌面浏览器采集指定链接的 1688 商品。",
    permission="collect.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def collect_1688(
    request: Collect1688Request,
    scope: Annotated[CollectCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> Collect1688Result:
    del execution
    body: dict[str, Any] = {
        "url": request.url,
    }
    if request.claim_platforms:
        body["platforms"] = list(request.claim_platforms)
    try:
        result = scope.online_1688_collector(body)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "COLLECT_1688_FAILED",
            str(exc) or "1688 采集失败。",
        ) from exc
    _require_collect_ok(
        result,
        default_code="COLLECT_1688_FAILED",
        default_message="1688 采集失败。",
    )
    cleaned_keys = (
        "source_price_cny",
        "source_material",
        "source_weight_kg",
        "materials",
        "dimensions",
        "colors",
        "package_includes",
        "clean_source_text",
        "source_attributes",
    )
    cleaned = {
        key: value for key, value in result.items() if key in cleaned_keys
    }
    return Collect1688Result(
        ok=bool(result.get("ok", True)),
        product=_dict_value(result.get("product")),
        cleaned=cleaned,
        diagnostics=_dict_value(result.get("diagnostics")),
        next_action=_text(result.get("next_action")),
        message=_text(result.get("message")),
        products_index=_dict_rows(result.get("productsIndex")),
    )


@ai_tool(
    name=CLAIM_PRODUCTS_TOOL,
    description="把本地商品认领到销售市场生成草稿。所有平台和市场须一次调用 all_markets=true，由服务端按可选市场和语言分组（同语言跨平台共用草稿）；指定市场使用 targets。只创建草稿，不准备文案或发布。",
    permission="collect.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def claim_products(
    request: ClaimProductsRequest,
    scope: Annotated[CollectCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ClaimProductsResult:
    del execution
    targets = None if request.all_markets else [target.model_dump() for target in request.targets]
    try:
        result = scope.claimer(list(request.product_ids), targets)
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "CLAIM_PRODUCTS_FAILED",
            str(exc) or "商品认领失败。",
        ) from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "CLAIM_PRODUCTS_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "商品认领失败。",
        )
    return ClaimProductsResult(
        ok=True,
        claimed_count=int(result.get("claimed_count") or 0),
        items=_dict_rows(result.get("items")),
        products_index=_dict_rows(result.get("productsIndex")),
        drafts_index=_dict_rows(result.get("draftsIndex")),
    )


COLLECTION_AI_CAPABILITIES = (
    source_collect,
    collect_batch,
    collect_1688,
    claim_products,
)


__all__ = [
    "CLAIM_PRODUCTS_TOOL",
    "COLLECTION_AI_CAPABILITIES",
    "COLLECT_1688_TOOL",
    "COLLECT_BATCH_TOOL",
    "CollectCapabilityScope",
    "SOURCE_COLLECT_TOOL",
    "claim_products",
    "collect_1688",
    "collect_batch",
    "source_collect",
]
