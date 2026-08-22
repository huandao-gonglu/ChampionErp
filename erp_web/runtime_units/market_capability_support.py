from __future__ import annotations

"""市场准备 Capability 共享的草稿定位、类目详情与持久化支撑。"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol

from erp_web.product_model import PLATFORMS
from erp_web.runtime_units.draft_publish_context import (
    draft_for_publish_target,
    draft_publish_targets,
    merge_target_listing_into_draft,
)
from erp_web.runtime_units.product_capabilities import ProductCapabilityStore
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)


class MarketPrepareStore(ProductCapabilityStore, Protocol):
    def save_product(self, data: dict[str, Any]) -> dict[str, Any]:
        ...

    def draft_workflow_status(
        self,
        product: dict[str, Any],
        platform: str = "mercadolibre",
    ) -> str:
        ...


CategoryRecordLoader = Callable[..., dict[str, Any]]
_PUBLISHED_STATUSES = frozenset(
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


def text(value: Any) -> str:
    return str(value or "").strip()


def raise_store_error(
    error: dict[str, Any] | None,
    *,
    default_code: str,
    default_message: str,
) -> None:
    if error is None:
        return
    raise BusinessCapabilityError(
        text(error.get("error_code")) or default_code,
        text(error.get("error")) or default_message,
    )


def load_draft(
    product_store: MarketPrepareStore,
    draft_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result, error, _status = product_store.load_draft_detail_from_index(draft_id)
    raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND",
        default_message="草稿不存在。",
    )
    draft = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    context = (
        result.get("productContext")
        if isinstance(result.get("productContext"), dict)
        else {}
    )
    product = context.get("raw") if isinstance(context.get("raw"), dict) else {}
    if not draft or not product:
        raise BusinessCapabilityError(
            "DRAFT_CONTEXT_INVALID",
            "草稿缺少关联商品上下文。",
        )
    loaded_id = text(draft.get("draft_id"))
    if loaded_id != draft_id:
        raise BusinessCapabilityError(
            "DRAFT_ID_MISMATCH",
            "草稿读取结果与请求的稳定 draft_id 不一致。",
        )
    return draft, product


def require_platform(platform: str) -> str:
    platform_key = text(platform).lower()
    if platform_key not in PLATFORMS:
        raise CapabilityInputRequired(
            "TARGET_PLATFORM_UNSUPPORTED",
            "目标平台不受支持。",
            key="target_platform",
            label="目标平台",
            reason="请选择当前已接入的目标平台。",
            options=sorted(PLATFORMS),
            input_type="select",
        )
    return platform_key


def assert_target_mutable(target_draft: Mapping[str, Any]) -> None:
    if any(
        text(target_draft.get(field)).lower() in _PUBLISHED_STATUSES
        for field in ("publish_status", "status")
    ):
        raise BusinessCapabilityError(
            "DRAFT_ALREADY_PUBLISHED",
            "目标草稿已经发布，不能再修改发布内容。",
        )


def invalidate_target_publish_preparation(
    *,
    product_store: ProductCapabilityStore,
    product: dict[str, Any],
    draft: dict[str, Any],
    target: dict[str, Any],
    target_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """业务内容变化后清空选中目标的旧预检/发布引用并重算工作流状态。"""

    next_draft = (
        merge_target_listing_into_draft(draft, target, target_draft)
        if target_draft is not None
        else draft
    )
    invalidated = merge_target_listing_into_draft(
        next_draft,
        target,
        deepcopy(_PUBLISH_PREPARATION_RESET),
    )
    platform = text(target.get("platform")).lower()
    projection = draft_for_publish_target(invalidated, target)
    workflow_status = product_store.draft_workflow_status(
        product_with_target(product, platform, projection),
        platform,
    )
    return merge_target_listing_into_draft(
        invalidated,
        target,
        {"status": workflow_status},
    )


def select_target(
    draft: dict[str, Any],
    *,
    platform: str,
    site: str = "",
) -> dict[str, Any]:
    platform_key = require_platform(platform)
    site_key = text(site).casefold()
    targets = [
        target
        for target in draft_publish_targets(draft)
        if text(target.get("platform")).lower() == platform_key
    ]
    if site_key:
        matched = [
            target
            for target in targets
            if text(target.get("site")).casefold() == site_key
        ]
        if not matched:
            site_options = [text(target.get("site")) for target in targets]
            raise CapabilityInputRequired(
                "DRAFT_TARGET_SITE_NOT_FOUND",
                "指定站点不属于当前目标草稿。",
                key="site",
                label="目标站点",
                reason="请从草稿已经保存的目标站点中选择。",
                options=site_options,
                input_type="select" if site_options else "text",
            )
        return matched[0]
    if not targets:
        platform_options = [
            text(target.get("platform"))
            for target in draft_publish_targets(draft)
            if text(target.get("platform"))
        ]
        raise CapabilityInputRequired(
            "DRAFT_TARGET_NOT_FOUND",
            "当前草稿没有指定平台的目标市场。",
            key="target_platform",
            label="目标平台",
            reason="请先为商品创建该平台的目标草稿。",
            options=platform_options,
            input_type="select" if platform_options else "text",
        )
    if len(targets) > 1:
        raise CapabilityInputRequired(
            "DRAFT_TARGET_SITE_AMBIGUOUS",
            "该平台草稿包含多个目标站点。",
            key="site",
            label="目标站点",
            reason="请明确选择要处理的站点。",
            options=[text(target.get("site")) for target in targets],
            input_type="select",
        )
    return targets[0]


def product_with_target(
    product: dict[str, Any],
    platform: str,
    target_draft: dict[str, Any],
) -> dict[str, Any]:
    projected = deepcopy(product)
    drafts = projected.get("drafts") if isinstance(projected.get("drafts"), dict) else {}
    projected["drafts"] = {**drafts, platform: deepcopy(target_draft)}
    return projected


def category_id(record: Mapping[str, Any]) -> str:
    return text(
        record.get("category_id")
        or record.get("subject_id")
        or record.get("type_id")
    )


def category_path(record: Mapping[str, Any]) -> str:
    direct = text(record.get("category_path"))
    if direct:
        return direct
    raw_path = (
        record.get("path_cn")
        if isinstance(record.get("path_cn"), list)
        else record.get("path_original")
        if isinstance(record.get("path_original"), list)
        else []
    )
    return " > ".join(text(item) for item in raw_path if text(item))


def category_schema(
    record: Mapping[str, Any],
    *,
    platform: str,
    site: str,
    selected_category_id: str,
) -> dict[str, Any]:
    attributes = (
        record.get("attributes")
        if isinstance(record.get("attributes"), Mapping)
        else {}
    )
    return {
        "version": max(1, int(record.get("version") or 1)),
        "platform": platform,
        "site": site,
        "category_id": selected_category_id,
        "category_path": category_path(record),
        "source": text(record.get("source")),
        "fetched_at": text(record.get("fetched_at")),
        "required": deepcopy(
            attributes.get("required")
            if isinstance(attributes.get("required"), list)
            else []
        ),
        "optional": deepcopy(
            attributes.get("optional")
            if isinstance(attributes.get("optional"), list)
            else []
        ),
    }


def load_category_record(
    loader: CategoryRecordLoader,
    *,
    platform: str,
    site: str,
    selected_category_id: str,
) -> dict[str, Any]:
    try:
        record = loader(
            platform,
            selected_category_id,
            site=site,
            include_attributes=True,
        )
    except (BusinessCapabilityError, CapabilityInputRequired):
        raise
    except Exception as exc:
        raise BusinessCapabilityError(
            "CATEGORY_DETAIL_LOAD_FAILED",
            f"类目详情读取失败：{exc}",
            retryable=True,
        ) from exc
    if not isinstance(record, dict) or category_id(record) != selected_category_id:
        raise BusinessCapabilityError(
            "CATEGORY_DETAIL_INVALID",
            "类目详情与选定 category_id 不一致。",
        )
    if bool(record.get("disabled")):
        raise CapabilityInputRequired(
            "CATEGORY_NO_LONGER_AVAILABLE",
            "选定类目已经不可用。",
            key="category_id",
            label="平台类目",
            reason="请选择一个当前可发布的类目。",
        )
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        raise BusinessCapabilityError(
            "CATEGORY_ATTRIBUTES_UNAVAILABLE",
            "类目详情没有返回可验证的属性定义。",
            retryable=True,
        )
    return record


def persist_target_projection(
    *,
    product_store: MarketPrepareStore,
    product: dict[str, Any],
    draft: dict[str, Any],
    target: dict[str, Any],
    updated_product: dict[str, Any],
    updated_target_draft: dict[str, Any],
) -> dict[str, Any]:
    platform = text(target.get("platform")).lower()
    merged_draft = merge_target_listing_into_draft(
        draft,
        target,
        updated_target_draft,
    )
    next_product = deepcopy(product)
    drafts = next_product.get("drafts") if isinstance(next_product.get("drafts"), dict) else {}
    next_product["drafts"] = {**drafts, platform: merged_draft}
    next_product["local_platform_categories"] = deepcopy(
        updated_product.get("local_platform_categories")
        if isinstance(updated_product.get("local_platform_categories"), dict)
        else product.get("local_platform_categories")
        if isinstance(product.get("local_platform_categories"), dict)
        else {}
    )
    existing_categories = (
        product.get("local_platform_categories")
        if isinstance(product.get("local_platform_categories"), dict)
        else {}
    )
    if merged_draft != draft or next_product["local_platform_categories"] != existing_categories:
        try:
            product_store.save_product(next_product)
        except Exception as exc:
            raise BusinessCapabilityError(
                "TARGET_DRAFT_SAVE_FAILED",
                f"目标草稿保存失败：{exc}",
                retryable=True,
            ) from exc
    saved_draft, _saved_product = load_draft(
        product_store,
        text(draft.get("draft_id")),
    )
    return saved_draft


__all__ = [
    "CategoryRecordLoader",
    "MarketPrepareStore",
    "assert_target_mutable",
    "category_path",
    "category_schema",
    "load_category_record",
    "load_draft",
    "invalidate_target_publish_preparation",
    "persist_target_projection",
    "product_with_target",
    "raise_store_error",
    "require_platform",
    "select_target",
    "text",
]
