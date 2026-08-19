from __future__ import annotations

"""采集、1688 清洗保存、浏览器 Tab 导入与目标平台认领 Capability。

领域逻辑仍由 ``source_collect_workflows`` / ``collect_helpers`` /
``collect_service`` 拥有；Capability 只做类型化编排。Cookie 与 1688 API
凭据一律由 Scope provider 从已保存配置解析，模型不得提供。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.collect_capabilities import (
    ClaimProductsRequest,
    ClaimProductsResult,
    Collect1688CleanRequest,
    Collect1688CleanResult,
    Collect1688Request,
    Collect1688Result,
    CollectBatchRequest,
    CollectBatchResult,
    CollectFromBrowserTabRequest,
    CollectFromBrowserTabResult,
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
    raise BusinessCapabilityError(code, message)


@dataclass(frozen=True)
class CollectCapabilityScope:
    """采集与认领能力的可信依赖边界。"""

    source_collector: Callable[[str, str, str, tuple[str, ...]], dict[str, Any]]
    batch_collector: Callable[
        [tuple[str, ...], str, str, tuple[str, ...]], dict[str, Any]
    ]
    browser_tab_collector: Callable[
        [str, str, str, tuple[str, ...], bool], dict[str, Any]
    ]
    online_1688_collector: Callable[[dict[str, Any]], dict[str, Any]]
    text_cleaner: Callable[[str, str], dict[str, Any]]
    claimer: Callable[[list[str], list[str] | None], dict[str, Any]]


SOURCE_COLLECT_TOOL = "source_collect"
COLLECT_BATCH_TOOL = "collect_batch"
COLLECT_FROM_BROWSER_TAB_TOOL = "collect_from_browser_tab"
COLLECT_1688_TOOL = "collect_1688"
COLLECT_1688_CLEAN_TOOL = "collect_1688_clean"
CLAIM_PRODUCTS_TOOL = "claim_products"


@ai_tool(
    name=SOURCE_COLLECT_TOOL,
    description="按链接采集源平台商品并保存到本地商品库。",
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
    name=COLLECT_FROM_BROWSER_TAB_TOOL,
    description="从已连接的调试浏览器标签页导入当前商品页。",
    permission="collect.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def collect_from_browser_tab(
    request: CollectFromBrowserTabRequest,
    scope: Annotated[CollectCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CollectFromBrowserTabResult:
    del execution
    try:
        result = scope.browser_tab_collector(
            request.tab_url,
            request.platform_hint,
            request.product_url,
            tuple(request.claim_platforms),
            request.save_only,
        )
    except BusinessCapabilityError:
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "BROWSER_TAB_COLLECT_FAILED",
            str(exc) or "浏览器标签页导入失败。",
        ) from exc
    _require_collect_ok(
        result,
        default_code="BROWSER_TAB_COLLECT_FAILED",
        default_message="浏览器标签页导入失败。",
    )
    return CollectFromBrowserTabResult(
        ok=bool(result.get("ok")),
        product=_dict_value(result.get("product")),
        image_pool=_dict_rows(result.get("imagePool")),
        diagnostics=_dict_value(result.get("diagnostics")),
        browser_status=_dict_value(result.get("browserStatus")),
        next_action=_text(result.get("next_action")),
        products_index=_dict_rows(result.get("productsIndex")),
    )


@ai_tool(
    name=COLLECT_1688_TOOL,
    description="采集 1688 商品，或清洗粘贴的 1688 页面文本并保存到商品。",
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
        "text": request.text,
        "save": request.save,
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
    name=COLLECT_1688_CLEAN_TOOL,
    description="清洗 1688 页面文本，提取价格/材质/规格等结构化信息（纯计算）。",
    permission="collect.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def collect_1688_clean(
    request: Collect1688CleanRequest,
    scope: Annotated[CollectCapabilityScope, Injected()],
) -> Collect1688CleanResult:
    cleaned = scope.text_cleaner(request.text, request.url)
    if not isinstance(cleaned, dict) or not cleaned.get("ok"):
        raise BusinessCapabilityError(
            "COLLECT_1688_CLEAN_FAILED",
            _text(cleaned.get("error") if isinstance(cleaned, dict) else "")
            or "1688 文本清洗失败。",
        )
    return Collect1688CleanResult(ok=True, cleaned=dict(cleaned))


@ai_tool(
    name=CLAIM_PRODUCTS_TOOL,
    description="把本地商品认领到目标平台，生成对应平台草稿。",
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
    platforms = list(request.platforms) if request.platforms else None
    try:
        result = scope.claimer(list(request.product_ids), platforms)
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
    collect_from_browser_tab,
    collect_1688,
    collect_1688_clean,
    claim_products,
)


__all__ = [
    "CLAIM_PRODUCTS_TOOL",
    "COLLECTION_AI_CAPABILITIES",
    "COLLECT_1688_CLEAN_TOOL",
    "COLLECT_1688_TOOL",
    "COLLECT_BATCH_TOOL",
    "COLLECT_FROM_BROWSER_TAB_TOOL",
    "CollectCapabilityScope",
    "SOURCE_COLLECT_TOOL",
    "claim_products",
    "collect_1688",
    "collect_1688_clean",
    "collect_batch",
    "collect_from_browser_tab",
    "source_collect",
]
