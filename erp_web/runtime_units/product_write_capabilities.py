from __future__ import annotations

"""商品/草稿保存、读取与删除 Capability（本地写入）。

删除能力要求审批：审批摘要与规范化参数由服务端快照函数生成，digest 绑定
冻结参数、步骤、任务版本与 Capability 版本；执行时重算快照并复核，防止
模型伪造审批或批准后目标漂移。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from erp_web.runtime_units.draft_category_resolution import (
    resolve_draft_category_pairs,
)
from erp_web.runtime_units.market_pricing_capability import (
    prepare_target_pricing,
)
from erp_web.schemas.ai_tools import TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.product_write_capabilities import (
    DraftDeleteRequest,
    DraftDeleteResult,
    DraftPricingApplyRequest,
    DraftPricingApplyResult,
    DraftReadRequest,
    DraftReadResult,
    DraftReadView,
    DraftSaveRequest,
    DraftSaveResult,
    DraftStockUpdateRequest,
    DraftStockUpdateResult,
    ProductDeleteRequest,
    ProductDeleteResult,
    ProductProfilePatchRequest,
    ProductProfilePatchResult,
    ProductSaveRequest,
    ProductSaveResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.services.task_approval import verify_execution_approval


class ProductDraftWriteStore(Protocol):
    def save_product_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_products_from_index(
        self,
        product_ids: list[Any],
    ) -> dict[str, Any]:
        ...

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

    def delete_draft_from_index(self, draft_id: Any) -> dict[str, Any]:
        ...

    def draft_workflow_status(
        self,
        product: dict[str, Any],
        platform: str = "mercadolibre",
    ) -> str:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bounded_text(value: Any, *, max_length: int = 4_000) -> str:
    """把面向模型的自由文本限制在单字段可控范围内。"""

    return _text(value)[:max_length]


def _ai_draft_product_context(value: Any) -> dict[str, Any]:
    """返回 ``draft_read`` 所需的精简商品上下文。

    ProductStore 的 ``productContext`` 同时服务前端编辑器，因此包含完整
    ``raw`` 商品与图片池。AI 工具若原样返回，会重复草稿关联商品并可能
    超过工具输出上限。这里是 AI Capability 的专属投影：保留判断草稿、
    定价和发布所需的事实，明确不暴露 ``raw``，并对图片元数据做有界化。
    """

    context = _dict_value(value)
    scalar_fields = (
        "product_id",
        "source_product_id",
        "title",
        "source_title",
        "source_platform",
        "source_url",
        "brand",
        "model",
        "sku",
        "stock",
        "cost",
        "source_price",
        "currency",
        "weight_kg",
    )
    compact: dict[str, Any] = {
        field: _bounded_text(context.get(field)) for field in scalar_fields
    }
    dimensions = _dict_value(context.get("dimensions"))
    compact["dimensions"] = {
        field: _bounded_text(dimensions.get(field), max_length=80)
        for field in ("length_cm", "width_cm", "height_cm")
    }

    image_pool = context.get("image_pool")
    images = image_pool if isinstance(image_pool, list) else []
    compact_images: list[dict[str, Any]] = []
    for value in images[:20]:
        image = _dict_value(value)
        compact_image: dict[str, Any] = {}
        for field in (
            "id",
            "url",
            "preview_url",
            "origin",
            "status",
            "mime_type",
        ):
            if field in image:
                compact_image[field] = _bounded_text(
                    image.get(field),
                    max_length=2_048,
                )
        for field in ("selected", "is_main", "order", "width", "height"):
            if field in image and isinstance(image[field], (bool, int, float)):
                compact_image[field] = image[field]
        platforms = image.get("platforms")
        if isinstance(platforms, list):
            compact_image["platforms"] = [
                _bounded_text(item, max_length=80) for item in platforms[:10]
            ]
        compact_images.append(compact_image)
    compact["image_pool"] = compact_images
    compact["image_count"] = len(images)
    return compact


def _id_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_text(item) for item in value if _text(item))


_DRAFT_VIEW_ATTRIBUTE_MAX = 200
_DRAFT_VIEW_TARGET_MAX = 8
_DRAFT_VIEW_TARGET_KEYS = (
    "platform",
    "site",
    "language",
    "category_id",
    "description_category_id",
    "category_path",
    "status",
    "publish_status",
)
_DRAFT_VIEW_PUBLISH_TASK_KEYS = (
    "status",
    "task_id",
    "offer_id",
    "external_id",
    "product_id",
    "operation",
    "updated_at",
)


def _bounded_dict_subset(
    value: Any,
    keys: tuple[str, ...],
    *,
    max_length: int = 4_000,
) -> dict[str, Any]:
    source = _dict_value(value)
    return {key: _bounded_text(source.get(key), max_length=max_length) for key in keys}


def _bounded_attribute_value(value: Any) -> Any:
    """属性取值有界化：字符串截断、字典限键、列表限长，杜绝无界枚举全集。"""

    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for inner_key, inner_value in list(value.items())[:20]:
            bounded[str(inner_key)[:80]] = _bounded_attribute_value(inner_value)
        return bounded
    if isinstance(value, (list, tuple)):
        return [_bounded_attribute_value(item) for item in list(value)[:20]]
    return None


def _ai_draft_read_view(value: Any) -> DraftReadView:
    """完整草稿 → 类型化有界视图（draft_read 专属投影）。

    视图只保留排查与下一步决策所需的业务事实；平台类目规则、完整图片列表、
    发布日志等无界内容一律不进入模型上下文。
    """

    draft = _dict_value(value)
    attributes = draft.get("attributes") if isinstance(draft.get("attributes"), dict) else {}
    bounded_attributes: dict[str, Any] = {}
    for index, (attr_id, attr_value) in enumerate(attributes.items()):
        if index >= _DRAFT_VIEW_ATTRIBUTE_MAX:
            break
        bounded_attributes[_text(attr_id)[:80]] = _bounded_attribute_value(attr_value)
    images = draft.get("images") if isinstance(draft.get("images"), list) else []
    validation_errors = (
        draft.get("validation_errors")
        if isinstance(draft.get("validation_errors"), list)
        else []
    )
    bounded_errors: list[Any] = []
    for item in validation_errors[:50]:
        if isinstance(item, dict):
            bounded_errors.append(_bounded_dict_subset(item, ("code", "field", "message", "severity"), max_length=500))
        else:
            bounded_errors.append(_bounded_text(item, max_length=500))
    target_sites = (
        draft.get("target_sites") if isinstance(draft.get("target_sites"), list) else []
    )
    bounded_targets: list[dict[str, Any]] = []
    for site in target_sites[:_DRAFT_VIEW_TARGET_MAX]:
        if isinstance(site, dict):
            bounded_targets.append(_bounded_dict_subset(site, _DRAFT_VIEW_TARGET_KEYS))
    pricing = draft.get("pricing") if isinstance(draft.get("pricing"), dict) else {}
    pricing_targets = pricing.get("targets") if isinstance(pricing.get("targets"), dict) else {}
    pricing_summary: dict[str, Any] = {}
    for target_key, target_value in list(pricing_targets.items())[:16]:
        applied = (
            target_value.get("applied_price")
            if isinstance(target_value, dict)
            and isinstance(target_value.get("applied_price"), dict)
            else {}
        )
        pricing_summary[_text(target_key)[:80]] = {
            "listing_currency": _bounded_text(
                (target_value or {}).get("listing_currency"), max_length=16
            )
            if isinstance(target_value, dict)
            else "",
            "amount": _bounded_text(applied.get("amount"), max_length=40),
            "currency": _bounded_text(applied.get("currency"), max_length=16),
        }
    return DraftReadView(
        draft_id=_bounded_text(draft.get("draft_id"), max_length=160),
        product_id=_bounded_text(draft.get("product_id"), max_length=160),
        source_product_id=_bounded_text(draft.get("source_product_id"), max_length=160),
        platform=_bounded_text(draft.get("platform"), max_length=80),
        site=_bounded_text(draft.get("site"), max_length=80),
        status=_bounded_text(draft.get("status"), max_length=80),
        publish_status=_bounded_text(draft.get("publish_status"), max_length=80),
        title=_bounded_text(draft.get("title")),
        description=_bounded_text(draft.get("description"), max_length=8_000),
        brand=_bounded_text(draft.get("brand"), max_length=200),
        model=_bounded_text(draft.get("model"), max_length=200),
        sku=_bounded_text(draft.get("sku"), max_length=160),
        upc=_bounded_text(draft.get("upc"), max_length=80),
        stock=_bounded_text(draft.get("stock"), max_length=40),
        language=_bounded_text(draft.get("language"), max_length=40),
        category_id=_bounded_text(draft.get("category_id"), max_length=160),
        description_category_id=_bounded_text(
            draft.get("description_category_id"), max_length=160
        ),
        category_path=_bounded_text(draft.get("category_path"), max_length=500),
        attributes=bounded_attributes,
        image_count=len(images),
        validation_errors=tuple(bounded_errors),
        category_precheck=_dict_value(draft.get("category_precheck")),
        last_precheck=_dict_value(draft.get("last_precheck")),
        last_publish_task=_bounded_dict_subset(
            draft.get("last_publish_task"), _DRAFT_VIEW_PUBLISH_TASK_KEYS
        ),
        pricing_summary=pricing_summary,
        target_sites=tuple(bounded_targets),
    )


# 写回执 changed_fields 的有界化：键名截断 + 总数上限，保证 receipt 永远
# 落在设计预算内（<= 8 KiB），不会因异常输入膨胀成无界输出。
_CHANGED_FIELD_MAX_NAME = 80
_CHANGED_FIELD_MAX_COUNT = 200


def _changed_field_names(
    patch: Any,
    *,
    ignore: frozenset[str],
) -> tuple[str, ...]:
    """从模型提交的 patch 派生有界、去重、排序的变更字段名。"""

    if not isinstance(patch, dict):
        return ()
    names = sorted(
        {
            str(key)[:_CHANGED_FIELD_MAX_NAME]
            for key in patch
            if str(key) not in ignore
        }
    )
    return tuple(names[:_CHANGED_FIELD_MAX_COUNT])


@dataclass(frozen=True)
class ProductWriteCapabilityScope:
    """商品/草稿写入的可信商品存储边界。"""

    products: ProductDraftWriteStore


PRODUCT_SAVE_TOOL = "product_save"
PRODUCT_DELETE_TOOL = "product_delete"
PRODUCT_PROFILE_PATCH_TOOL = "product_profile_patch"
DRAFT_READ_TOOL = "draft_read"
DRAFT_SAVE_TOOL = "draft_save"
DRAFT_DELETE_TOOL = "draft_delete"
DRAFT_STOCK_UPDATE_TOOL = "draft_stock_update"
DRAFT_PRICING_APPLY_TOOL = "draft_pricing_apply"


def _normalized_ids(ids: Any) -> list[str]:
    return sorted(
        {item for item in (_text(value) for value in ids) if item}
    )


def _state_token(value: dict[str, Any] | None) -> str:
    """返回不泄露资源正文的稳定状态指纹；空资源有明确哨兵。"""

    if not value:
        return "missing"
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _product_delete_states(
    ids: list[str],
    scope: ProductWriteCapabilityScope,
) -> dict[str, str]:
    states: dict[str, str] = {}
    for product_id in ids:
        loaded = scope.products.load_product_from_index(product_id, "")
        exact = (
            loaded
            if _text(loaded.get("product_id")) == product_id
            else {}
        )
        states[product_id] = _state_token(exact)
    return states


def _draft_delete_states(
    ids: list[str],
    scope: ProductWriteCapabilityScope,
) -> dict[str, str]:
    states: dict[str, str] = {}
    for draft_id in ids:
        result, error, _status = scope.products.load_draft_detail_from_index(draft_id)
        draft = result.get("draft") if error is None and isinstance(result, dict) else {}
        states[draft_id] = _state_token(draft if isinstance(draft, dict) else {})
    return states


def _product_delete_approval_snapshot(
    request: ProductDeleteRequest,
    scope: ProductWriteCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的删除审批快照；模型不提供摘要也不提供参数。"""

    ids = _normalized_ids(request.product_ids)
    preview = "、".join(ids[:5])
    more = f"（另有 {len(ids) - 5} 个）" if len(ids) > 5 else ""
    return TaskApprovalSnapshot(
        summary=f"删除 {len(ids)} 个本地商品：{preview}{more}",
        canonical_payload={
            "product_ids": ids,
            "resource_states": _product_delete_states(ids, scope),
        },
    )


def _draft_delete_approval_snapshot(
    request: DraftDeleteRequest,
    scope: ProductWriteCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的草稿删除审批快照；模型不提供摘要也不提供参数。"""

    ids = _normalized_ids(request.draft_ids)
    preview = "、".join(ids[:5])
    more = f"（另有 {len(ids) - 5} 个）" if len(ids) > 5 else ""
    return TaskApprovalSnapshot(
        summary=f"删除 {len(ids)} 个本地草稿：{preview}{more}",
        canonical_payload={
            "draft_ids": ids,
            "resource_states": _draft_delete_states(ids, scope),
        },
    )


@ai_tool(
    name=PRODUCT_SAVE_TOOL,
    description="保存/更新本地商品主档（不含草稿内容）。",
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_save(
    request: ProductSaveRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductSaveResult:
    del execution
    patch = request.product.model_dump(mode="json", exclude_unset=True)
    saved = scope.products.save_product_profile(patch)
    # 写回执只投影紧凑 mutation receipt；完整 saved 对象不得进入 Tool Result。
    return ProductSaveResult(
        product_id=_text(saved.get("product_id"))[:160],
        changed_fields=_changed_field_names(
            patch,
            ignore=frozenset({"product_id"}),
        ),
        updated_at=_text(saved.get("updated_at"))[:64],
        changed=True,
    )


@ai_tool(
    name=PRODUCT_DELETE_TOOL,
    description=(
        "删除本地商品；破坏性操作，需要人工在受信界面批准后才会执行。"
    ),
    permission="product.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_product_delete_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_delete(
    request: ProductDeleteRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductDeleteResult:
    snapshot = _product_delete_approval_snapshot(request, scope)
    verify_execution_approval(
        execution,
        snapshot=snapshot,
        capability_name=PRODUCT_DELETE_TOOL,
        capability_version="1",
        stale_code="PRODUCT_DELETE_APPROVAL_STALE",
    )
    result = scope.products.delete_products_from_index(list(request.product_ids))
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "PRODUCT_DELETE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "商品删除失败。",
        )
    return ProductDeleteResult(
        deleted=int(result.get("deleted") or 0),
        deleted_ids=_id_tuple(result.get("deletedIds")),
        missing_ids=_id_tuple(result.get("missingIds")),
    )


@ai_tool(
    name=DRAFT_READ_TOOL,
    description="按 draft_id 读取草稿的类型化有界视图与精简关联商品上下文。",
    permission="draft.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="2",
)
def draft_read(
    request: DraftReadRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
) -> DraftReadResult:
    result, error, _status = scope.products.load_draft_detail_from_index(
        request.draft_id
    )
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND",
        default_message="草稿不存在。",
    )
    return DraftReadResult(
        draft=_ai_draft_read_view(result.get("draft")),
        product_context=_ai_draft_product_context(result.get("productContext")),
    )


@ai_tool(
    name=DRAFT_SAVE_TOOL,
    description="保存草稿详情；Ozon 目标会自动解析实时类目对。",
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_save(
    request: DraftSaveRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftSaveResult:
    del execution
    patch = dict(request.draft)
    try:
        resolved = resolve_draft_category_pairs(dict(patch))
    except (RuntimeError, TimeoutError, ValueError) as exc:
        raise BusinessCapabilityError(
            "OZON_CATEGORY_PAIR_RESOLVE_FAILED",
            str(exc) or "Ozon 类目对解析失败。",
        ) from exc
    result, error, status = scope.products.save_draft_detail(resolved)
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND" if status == 404 else "DRAFT_SAVE_FAILED",
        default_message="草稿不存在。" if status == 404 else "草稿保存失败。",
    )
    # 写回执只投影紧凑 mutation receipt：禁止返回完整 draft、
    # product_context（含 raw）、index、图片池或完整类目 Schema。
    saved_draft = _dict_value(result.get("draft"))
    return DraftSaveResult(
        draft_id=_text(saved_draft.get("draft_id"))[:160],
        product_id=_text(saved_draft.get("product_id"))[:160],
        platform=_text(saved_draft.get("platform"))[:40],
        changed_fields=_changed_field_names(
            patch,
            ignore=frozenset({"draft_id", "draftId"}),
        ),
        updated_at=_text(saved_draft.get("updated_at"))[:64],
        changed=True,
    )


@ai_tool(
    name=DRAFT_DELETE_TOOL,
    description=(
        "删除本地草稿；破坏性操作，需要人工在受信界面批准后才会执行。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_draft_delete_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_delete(
    request: DraftDeleteRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftDeleteResult:
    snapshot = _draft_delete_approval_snapshot(request, scope)
    verify_execution_approval(
        execution,
        snapshot=snapshot,
        capability_name=DRAFT_DELETE_TOOL,
        capability_version="1",
        stale_code="DRAFT_DELETE_APPROVAL_STALE",
    )
    result = scope.products.delete_draft_from_index(list(request.draft_ids))
    if not isinstance(result, dict) or not result.get("ok"):
        raise BusinessCapabilityError(
            "DRAFT_DELETE_FAILED",
            _text(result.get("error") if isinstance(result, dict) else "")
            or "草稿删除失败。",
        )
    return DraftDeleteResult(
        deleted=int(result.get("deleted") or 0),
        deleted_ids=_id_tuple(result.get("deletedDraftIds")),
        missing_ids=_id_tuple(result.get("missingIds")),
        affected_product_ids=_id_tuple(result.get("affectedProductIds")),
    )


@ai_tool(
    name=PRODUCT_PROFILE_PATCH_TOOL,
    description=(
        "按部分补丁更新本地商品主档：只提供要改的字段，未提供字段保持原值。"
        "发布草稿的库存/售价不属于此能力，请用对应草稿 focused write。"
    ),
    permission="product.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def product_profile_patch(
    request: ProductProfilePatchRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> ProductProfilePatchResult:
    del execution
    patch = request.product.model_dump(mode="json", exclude_unset=True)
    saved = scope.products.save_product_profile(patch)
    return ProductProfilePatchResult(
        product_id=_text(saved.get("product_id"))[:160],
        changed_fields=_changed_field_names(
            patch,
            ignore=frozenset({"product_id"}),
        ),
        updated_at=_text(saved.get("updated_at"))[:64],
        changed=True,
    )


@ai_tool(
    name=DRAFT_STOCK_UPDATE_TOOL,
    description=(
        "更新平台草稿的库存；发布流程中的库存以平台草稿为 owner，"
        "商品主档库存只是默认值，不能用 product 主档库存替代草稿库存。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_stock_update(
    request: DraftStockUpdateRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftStockUpdateResult:
    del execution
    result, error, status = scope.products.save_draft_detail(
        {"draft_id": request.draft_id, "stock": request.stock}
    )
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND" if status == 404 else "DRAFT_SAVE_FAILED",
        default_message="草稿不存在。" if status == 404 else "草稿库存更新失败。",
    )
    saved_draft = _dict_value(result.get("draft"))
    return DraftStockUpdateResult(
        draft_id=_text(saved_draft.get("draft_id"))[:160],
        stock=_text(saved_draft.get("stock"))[:40] or request.stock,
        updated_at=_text(saved_draft.get("updated_at"))[:64],
        changed=True,
    )


@ai_tool(
    name=DRAFT_PRICING_APPLY_TOOL,
    description=(
        "把确定性核价结果持久化为平台草稿的最终售价；只计算不应用不会落库。"
        "pricing_input 与 draft_prepare_for_market.pricing_input 同形。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_pricing_apply(
    request: DraftPricingApplyRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftPricingApplyResult:
    del execution
    result, error, _status = scope.products.load_draft_detail_from_index(
        request.draft_id
    )
    _raise_store_error(
        error,
        default_code="DRAFT_NOT_FOUND",
        default_message="草稿不存在。",
    )
    draft = _dict_value(result.get("draft"))
    platform = request.target_platform or _text(draft.get("platform"))
    if not platform:
        raise BusinessCapabilityError(
            "DRAFT_PLATFORM_MISSING",
            "无法确定草稿的目标平台。",
        )
    try:
        applied = prepare_target_pricing(
            target_draft_id=request.draft_id,
            target_platform=platform,
            site=request.site,
            pricing_input=dict(request.pricing_input),
            product_store=scope.products,  # type: ignore[arg-type]
        )
    except CapabilityInputRequired:
        raise
    applied_price = applied.get("applied_price")
    amount = _text(
        applied_price.get("amount") if isinstance(applied_price, dict) else ""
    )
    currency = _text(
        applied_price.get("currency") if isinstance(applied_price, dict) else ""
    )
    return DraftPricingApplyResult(
        draft_id=request.draft_id,
        target_key=_text(applied.get("target_key"))[:120],
        applied_price=f"{amount} {currency}".strip()[:80],
        fingerprint=_text(applied.get("calculation_fingerprint"))[:160],
        changed=bool(amount),
    )


PRODUCT_WRITE_AI_CAPABILITIES = (
    product_save,
    product_delete,
    product_profile_patch,
)

DRAFT_WRITE_AI_CAPABILITIES = (
    draft_read,
    draft_save,
    draft_delete,
    draft_stock_update,
    draft_pricing_apply,
)


__all__ = [
    "DRAFT_DELETE_TOOL",
    "DRAFT_PRICING_APPLY_TOOL",
    "DRAFT_READ_TOOL",
    "DRAFT_SAVE_TOOL",
    "DRAFT_STOCK_UPDATE_TOOL",
    "DRAFT_WRITE_AI_CAPABILITIES",
    "PRODUCT_DELETE_TOOL",
    "PRODUCT_PROFILE_PATCH_TOOL",
    "PRODUCT_SAVE_TOOL",
    "PRODUCT_WRITE_AI_CAPABILITIES",
    "ProductDraftWriteStore",
    "ProductWriteCapabilityScope",
    "draft_delete",
    "draft_pricing_apply",
    "draft_read",
    "draft_save",
    "draft_stock_update",
    "product_delete",
    "product_profile_patch",
    "product_save",
]
