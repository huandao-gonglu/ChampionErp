from __future__ import annotations

"""商品事实读取、幂等属性设置与确定性草稿图片准备。"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.product_model import (
    draft_image_refs_from_assets,
    normalize_draft_image_refs,
    normalize_image_pool,
)
from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    draft_publish_targets,
    merge_target_listing_into_draft,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.product_capabilities import (
    ProductAttributesUpdateRequest,
    ProductAttributesUpdateResult,
    ProductDraftFacts,
    ProductFacts,
    ProductImagesPrepareRequest,
    ProductImagesPrepareResult,
    ProductReadRequest,
    ProductReadResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


class ProductCapabilityStore(Protocol):
    def load_product_from_index(
        self,
        product_id: str = "",
        file_path: str = "",
    ) -> dict[str, Any]:
        ...

    def load_draft_detail_from_index(
        self,
        draft_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        ...

    def save_draft_detail(
        self,
        draft_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int]:
        ...

    def draft_workflow_status(
        self,
        product: dict[str, Any],
        platform: str = "mercadolibre",
    ) -> str:
        ...


_PUBLISHED_DRAFT_STATUSES = frozenset(
    {"published", "real_publish_success", "success"}
)
_PUBLISH_PREPARATION_RESET: dict[str, Any] = {
    "validation_errors": [],
    "category_precheck": {},
    "last_precheck": {},
    "last_precheck_target": {},
    "last_publish_task": {},
    "publish_status": "",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any, *, limit: int = 100) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value[:limit] if _text(item)]


def _raise_store_error(
    error: dict[str, Any] | None,
    *,
    default_code: str,
    default_message: str,
) -> None:
    if error is None:
        return
    raise BusinessCapabilityError(
        _text(error.get("error_code")) or default_code,
        _text(error.get("error")) or default_message,
    )


def _load_draft(
    product_store: ProductCapabilityStore,
    draft_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result, error, _status = product_store.load_draft_detail_from_index(draft_id)
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND",
        default_message="草稿不存在。",
    )
    draft = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    product_context = (
        result.get("productContext")
        if isinstance(result.get("productContext"), dict)
        else {}
    )
    product = (
        product_context.get("raw")
        if isinstance(product_context.get("raw"), dict)
        else {}
    )
    if not draft or not product:
        raise BusinessCapabilityError(
            "DRAFT_CONTEXT_INVALID",
            "草稿缺少关联商品上下文。",
        )
    return draft, product


def _select_target(
    draft: dict[str, Any],
    *,
    platform: str = "",
    site: str = "",
) -> dict[str, Any]:
    targets = draft_publish_targets(draft)
    if not targets:
        raise CapabilityInputRequired(
            "DRAFT_TARGET_MISSING",
            "当前草稿没有目标市场。",
            key="target_platform",
            label="目标平台",
            reason="请先为草稿选择目标平台和站点。",
        )
    platform_key = _text(platform).lower()
    site_key = _text(site).lower()
    candidates = [
        target
        for target in targets
        if (not platform_key or _text(target.get("platform")).lower() == platform_key)
        and (not site_key or _text(target.get("site")).lower() == site_key)
    ]
    if not candidates:
        raise CapabilityInputRequired(
            "DRAFT_TARGET_NOT_FOUND",
            "指定目标不属于当前草稿。",
            key="target_platform",
            label="目标平台",
            reason="请从草稿已有目标中选择。",
            options=[
                f"{_text(target.get('platform'))}:{_text(target.get('site'))}"
                for target in targets
            ],
        )
    if len(candidates) > 1:
        candidate_platforms = list(
            dict.fromkeys(
                _text(target.get("platform")).lower()
                for target in candidates
                if _text(target.get("platform"))
            )
        )
        require_platform = not platform_key and len(candidate_platforms) > 1
        input_key = "platform" if require_platform else "site"
        options = (
            candidate_platforms
            if require_platform
            else list(
                dict.fromkeys(
                    _text(target.get("site"))
                    for target in candidates
                    if _text(target.get("site"))
                )
            )
        )
        raise CapabilityInputRequired(
            "DRAFT_TARGET_AMBIGUOUS",
            "当前草稿包含多个目标市场，无法确定要操作哪一个。",
            key=input_key,
            label="目标平台" if require_platform else "目标站点",
            reason=(
                "请明确选择一个目标平台。"
                if require_platform
                else "请明确选择一个目标站点。"
            ),
            options=options,
            input_type="select",
        )
    return candidates[0]


def _is_published(subject: dict[str, Any]) -> bool:
    return any(
        _text(subject.get(field)).lower() in _PUBLISHED_DRAFT_STATUSES
        for field in ("publish_status", "status")
    )


def _assert_target_mutable(target: dict[str, Any]) -> None:
    if _is_published(target):
        raise BusinessCapabilityError(
            "DRAFT_ALREADY_PUBLISHED",
            "目标草稿已经发布，不能再修改发布内容。",
        )


def _assert_shared_draft_mutable(draft: dict[str, Any]) -> None:
    raw_targets = (
        draft.get("target_sites")
        if isinstance(draft.get("target_sites"), list)
        else draft.get("targetSites")
        if isinstance(draft.get("targetSites"), list)
        else []
    )
    subjects = [draft, *(item for item in raw_targets if isinstance(item, dict))]
    if any(_is_published(subject) for subject in subjects):
        raise BusinessCapabilityError(
            "DRAFT_ALREADY_PUBLISHED",
            "草稿存在已发布目标，不能修改所有目标共享的发布内容。",
        )


def _workflow_status_after_mutation(
    *,
    product_store: ProductCapabilityStore,
    product: dict[str, Any],
    draft: dict[str, Any],
    target: dict[str, Any],
) -> str:
    platform = _text(target.get("platform")).lower()
    product_for_status = deepcopy(product)
    previews = (
        dict(product_for_status.get("publish_preview"))
        if isinstance(product_for_status.get("publish_preview"), dict)
        else {}
    )
    previews.pop(platform, None)
    product_for_status["publish_preview"] = previews
    drafts = (
        dict(product_for_status.get("drafts"))
        if isinstance(product_for_status.get("drafts"), dict)
        else {}
    )
    drafts[platform] = draft_for_publish_target(draft, target)
    product_for_status["drafts"] = drafts
    return product_store.draft_workflow_status(product_for_status, platform)


def _invalidate_target_publish_preparation(
    draft: dict[str, Any],
    target: dict[str, Any],
    *,
    product: dict[str, Any],
    product_store: ProductCapabilityStore,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = merge_target_listing_into_draft(
        draft,
        target,
        {
            **(updates or {}),
            **deepcopy(_PUBLISH_PREPARATION_RESET),
        },
    )
    refreshed_target = _select_target(
        merged,
        platform=_text(target.get("platform")),
        site=_text(target.get("site")),
    )
    status = _workflow_status_after_mutation(
        product_store=product_store,
        product=product,
        draft=merged,
        target=refreshed_target,
    )
    return merge_target_listing_into_draft(
        merged,
        refreshed_target,
        {"status": status},
    )


def _draft_facts(
    draft: dict[str, Any],
    *,
    platform: str = "",
    site: str = "",
) -> ProductDraftFacts:
    targets = draft_publish_targets(draft)
    target = (
        _select_target(draft, platform=platform, site=site)
        if targets
        else None
    )
    target_draft = draft_for_publish_target(draft, target) if target else draft
    attributes = (
        target_draft.get("attributes")
        if isinstance(target_draft.get("attributes"), dict)
        else {}
    )
    images = normalize_draft_image_refs(target_draft.get("images"))
    return ProductDraftFacts(
        draft_id=_text(draft.get("draft_id")),
        product_id=_text(draft.get("source_product_id") or draft.get("product_id")),
        platform=_text(target_draft.get("platform")).lower(),
        site=_text(target_draft.get("site")),
        language=_text(target_draft.get("language")),
        workflow_status=_text(target_draft.get("status")),
        publish_status=_text(target_draft.get("publish_status")),
        title=_text(target_draft.get("title")),
        has_description=bool(_text(target_draft.get("description"))),
        category_id=_text(target_draft.get("category_id")),
        category_path=_text(target_draft.get("category_path")),
        attribute_ids=sorted(_text(key) for key in attributes if _text(key)),
        image_asset_ids=[_text(item.get("asset_id")) for item in images],
        listing_currency=_text(target_draft.get("listing_currency")).upper(),
        price=_text(target_draft.get("price")),
        stock=_text(target_draft.get("stock")),
    )


def _product_facts(product: dict[str, Any]) -> ProductFacts:
    product_id = _text(product.get("product_id"))
    if not product_id:
        raise BusinessCapabilityError(
            "PRODUCT_CONTEXT_INVALID",
            "商品缺少 product_id。",
        )
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    source_images = source.get("images") if isinstance(source.get("images"), list) else []
    workflow_statuses = (
        product.get("workflow_statuses")
        if isinstance(product.get("workflow_statuses"), dict)
        else {}
    )
    return ProductFacts(
        product_id=product_id,
        name=_text(product.get("name") or source.get("title")),
        brand=_text(product.get("brand") or source.get("brand")),
        model=_text(product.get("model") or source.get("model")),
        sku=_text(product.get("sku")),
        stock=_text(product.get("stock")),
        cost=_text(product.get("cost") or product.get("source_price_cny_for_cost")),
        description=_text(product.get("description") or source.get("description"))[:4000],
        materials=_text_list(
            product.get("materials") or source.get("material") or []
        ),
        selling_points=_text_list(product.get("selling_points")),
        package_includes=_text_list(product.get("package_includes")),
        dimensions=_text(product.get("dimensions")),
        weight_kg=_text(product.get("weight_kg") or source.get("weight_kg")),
        source_platform=_text(source.get("source_platform")),
        source_url=_text(source.get("source_url")),
        source_image_count=len(pool or source_images),
        workflow_statuses={
            _text(key): _text(value)
            for key, value in workflow_statuses.items()
            if _text(key)
        },
    )


def read_product(
    request: ProductReadRequest,
    *,
    product_store: ProductCapabilityStore,
) -> ProductReadResult:
    """读取紧凑商品/草稿事实，且不接受 Store 的缺省商品回退。"""

    draft: dict[str, Any] | None = None
    if request.draft_id:
        draft, product = _load_draft(product_store, request.draft_id)
        loaded_product_id = _text(product.get("product_id"))
        if request.product_id and loaded_product_id != request.product_id:
            raise BusinessCapabilityError(
                "DRAFT_PRODUCT_MISMATCH",
                "草稿与指定商品不一致。",
            )
    else:
        product = product_store.load_product_from_index(request.product_id, "")
        if _text(product.get("product_id")) != request.product_id:
            raise BusinessCapabilityError(
                "PRODUCT_NOT_FOUND",
                "商品不存在。",
            )
        if request.platform:
            drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
            candidate = drafts.get(request.platform.lower())
            draft = candidate if isinstance(candidate, dict) else None
    return ProductReadResult(
        product=_product_facts(product),
        draft=(
            _draft_facts(
                draft,
                platform=request.platform,
                site=request.site,
            )
            if draft is not None
            else None
        ),
    )


def update_product_attributes(
    request: ProductAttributesUpdateRequest,
    *,
    product_store: ProductCapabilityStore,
) -> ProductAttributesUpdateResult:
    """按键设置目标属性；相同请求不会产生第二次持久化写入。"""

    draft, product = _load_draft(product_store, request.draft_id)
    target = _select_target(
        draft,
        platform=request.platform,
        site=request.site,
    )
    _assert_target_mutable(target)
    target_draft = draft_for_publish_target(draft, target)
    existing = (
        target_draft.get("attributes")
        if isinstance(target_draft.get("attributes"), dict)
        else {}
    )
    attributes = dict(existing)
    changed_keys: list[str] = []
    for key, value in request.updates.items():
        if key not in attributes or attributes[key] != value:
            changed_keys.append(key)
        attributes[key] = value
    if not changed_keys:
        return ProductAttributesUpdateResult(
            draft_id=request.draft_id,
            platform=_text(target.get("platform")),
            site=_text(target.get("site")),
            attributes=attributes,
            changed_keys=[],
            changed=False,
        )

    merged = _invalidate_target_publish_preparation(
        draft,
        target,
        product=product,
        product_store=product_store,
        updates={"attributes": attributes},
    )
    result, error, _status = product_store.save_draft_detail(merged)
    _raise_store_error(
        error,
        default_code="PRODUCT_ATTRIBUTES_UPDATE_FAILED",
        default_message="草稿属性保存失败。",
    )
    saved = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    saved_target = _select_target(
        saved,
        platform=_text(target.get("platform")),
        site=_text(target.get("site")),
    )
    saved_projection = draft_for_publish_target(saved, saved_target)
    saved_attributes = (
        saved_projection.get("attributes")
        if isinstance(saved_projection.get("attributes"), dict)
        else {}
    )
    return ProductAttributesUpdateResult(
        draft_id=_text(saved.get("draft_id")) or request.draft_id,
        platform=_text(saved_target.get("platform")),
        site=_text(saved_target.get("site")),
        attributes=saved_attributes,
        changed_keys=changed_keys,
        changed=True,
    )


def _persisted_image_pool(product: dict[str, Any]) -> list[dict[str, Any]]:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = normalize_image_pool(
        source.get("image_pool") if isinstance(source.get("image_pool"), list) else [],
        "source",
    )
    return [
        item
        for item in pool
        if _text(item.get("id"))
        and _text(item.get("status")).lower() not in {"empty", "failed", "error", "pending"}
    ]


def _prepared_image_items(
    product: dict[str, Any],
    platform: str,
    requested_ids: list[str],
) -> list[dict[str, Any]]:
    pool = _persisted_image_pool(product)
    by_id = {_text(item.get("id")): item for item in pool}
    if requested_ids:
        unique_ids = list(dict.fromkeys(requested_ids))
        missing = [asset_id for asset_id in unique_ids if asset_id not in by_id]
        if missing:
            raise CapabilityInputRequired(
                "PRODUCT_IMAGE_ASSET_NOT_FOUND",
                f"图片资产不存在或尚未准备完成：{missing}。",
                key="asset_ids",
                label="图片资产",
                reason="请从当前商品已就绪的图片池中选择。",
                options=list(by_id),
                input_type="string_list",
            )
        return [by_id[asset_id] for asset_id in unique_ids]

    platform_key = _text(platform).lower()
    platform_items = [
        item
        for item in pool
        if platform_key
        and platform_key
        in {_text(value).lower() for value in item.get("platforms") or []}
    ]
    candidates = platform_items or pool
    selected = [item for item in candidates if bool(item.get("selected"))]
    chosen = selected or candidates
    return sorted(
        chosen,
        key=lambda item: (
            0 if bool(item.get("is_main")) else 1,
            int(item.get("order") or 0),
            _text(item.get("id")),
        ),
    )


def prepare_product_images(
    request: ProductImagesPrepareRequest,
    *,
    product_store: ProductCapabilityStore,
) -> ProductImagesPrepareResult:
    """把持久化图片池中的确定资产集合覆盖到草稿图片引用。"""

    draft, product = _load_draft(product_store, request.draft_id)
    _assert_shared_draft_mutable(draft)
    platform = _text(draft.get("platform")).lower()
    items = _prepared_image_items(product, platform, request.asset_ids)
    if not items:
        raise CapabilityInputRequired(
            "PRODUCT_IMAGES_REQUIRED",
            "当前商品没有可用于发布的已就绪图片。",
            key="asset_ids",
            label="商品图片",
            reason="请先导入或生成图片，再选择要用于发布的图片资产。",
            input_type="string_list",
        )
    desired = draft_image_refs_from_assets(items)
    existing = normalize_draft_image_refs(draft.get("images"))
    if desired == existing:
        return ProductImagesPrepareResult(
            draft_id=request.draft_id,
            platform=platform,
            image_asset_ids=[_text(item.get("asset_id")) for item in existing],
            image_count=len(existing),
            changed=False,
        )

    updated = {**deepcopy(draft), "images": desired}
    targets = draft_publish_targets(updated)
    if targets:
        for target in targets:
            updated = _invalidate_target_publish_preparation(
                updated,
                target,
                product=product,
                product_store=product_store,
            )
    else:
        updated.update(deepcopy(_PUBLISH_PREPARATION_RESET))
        updated["status"] = "claimed"
    result, error, _status = product_store.save_draft_detail(updated)
    _raise_store_error(
        error,
        default_code="PRODUCT_IMAGES_PREPARE_FAILED",
        default_message="草稿图片保存失败。",
    )
    saved = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    refs = normalize_draft_image_refs(saved.get("images"))
    if not refs:
        raise BusinessCapabilityError(
            "PRODUCT_IMAGES_PREPARE_INCOMPLETE",
            "图片准备完成后草稿仍没有有效图片引用。",
        )
    return ProductImagesPrepareResult(
        draft_id=_text(saved.get("draft_id")) or request.draft_id,
        platform=_text(saved.get("platform")) or platform,
        image_asset_ids=[_text(item.get("asset_id")) for item in refs],
        image_count=len(refs),
        changed=True,
    )


@dataclass(frozen=True)
class ProductCapabilityScope:
    """商品 Capability 的可信商品存储边界。"""

    products: ProductCapabilityStore


PRODUCT_READ_TOOL = "product_read"
PRODUCT_ATTRIBUTES_UPDATE_TOOL = "product_attributes_update"
PRODUCT_IMAGES_PREPARE_TOOL = "product_images_prepare"


@ai_tool(
    name=PRODUCT_READ_TOOL,
    description="读取可信商品与草稿事实；支持按 product_id 或 draft_id 查询。",
    permission="product.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def product_read(
    request: ProductReadRequest,
    scope: Annotated[ProductCapabilityScope, Injected()],
) -> ProductReadResult:
    return read_product(request, product_store=scope.products)


@ai_tool(
    name=PRODUCT_ATTRIBUTES_UPDATE_TOOL,
    description="按目标值设置草稿平台属性；相同请求不会重复写入。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_attributes_update(
    request: ProductAttributesUpdateRequest,
    scope: Annotated[ProductCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductAttributesUpdateResult:
    del execution
    return update_product_attributes(request, product_store=scope.products)


@ai_tool(
    name=PRODUCT_IMAGES_PREPARE_TOOL,
    description="把已就绪的图片资产集合覆盖到草稿图片引用。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_images_prepare(
    request: ProductImagesPrepareRequest,
    scope: Annotated[ProductCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductImagesPrepareResult:
    del execution
    return prepare_product_images(request, product_store=scope.products)


PRODUCT_AI_CAPABILITIES = (
    product_read,
    product_attributes_update,
    product_images_prepare,
)


__all__ = [
    "PRODUCT_AI_CAPABILITIES",
    "PRODUCT_ATTRIBUTES_UPDATE_TOOL",
    "PRODUCT_IMAGES_PREPARE_TOOL",
    "PRODUCT_READ_TOOL",
    "ProductCapabilityScope",
    "ProductCapabilityStore",
    "prepare_product_images",
    "product_attributes_update",
    "product_images_prepare",
    "product_read",
    "read_product",
    "update_product_attributes",
]
