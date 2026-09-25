"""一份草稿的成组局部修改；平台校验在锁外，复核和保存只做一次。"""

from typing import Annotated, Any

from erp_web.runtime_units.category_attribute_updates import AttributeUpdateValidation
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target, merge_target_listing_into_draft
from erp_web.runtime_units.product_attribute_patch import apply_attribute_patch, shared_attribute_fields
from erp_web.runtime_units.product_capabilities import (
    ProductCapabilityScope, ProductCapabilityStore, _assert_shared_draft_mutable,
    _assert_target_mutable, _load_draft, _raise_store_error, _select_target, _text,
)
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_changes import DraftChangeResult, DraftChangesApplyRequest, DraftChangesApplyResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _check_context(request: DraftChangesApplyRequest, draft: dict[str, Any], product: dict[str, Any]):
    if _text(draft.get("updated_at")) != request.expected_updated_at:
        raise BusinessCapabilityError("DRAFT_VERSION_CONFLICT", "草稿已被修改，本组未保存。请重新读取并计算差异，不要直接重放旧修改。")
    target = _select_target(draft, platform=request.platform, site=request.site)
    projection = draft_for_publish_target(draft, target)
    _assert_target_mutable(projection)
    if any(change.attributes for change in request.changes) and _text(projection.get("category_id")) != request.category_id:
        raise BusinessCapabilityError("CATEGORY_CHANGED", "草稿类目已变化，本组未保存，请重新查询属性。")
    rows = {row["sku_id"]: row for row in draft.get("sku_items", []) if row.get("selected")}
    active = {row["id"] for row in product.get("sku_items", []) if row.get("active", True)}
    for change in request.changes:
        if change.sku_id and (change.sku_id not in rows or change.sku_id not in active):
            raise BusinessCapabilityError("SKU_OUTSIDE_DRAFT", "本组包含不属于当前草稿的已选启用 SKU，未保存。")
        if change.package_dimensions is not None or change.stock is not None:
            _assert_shared_draft_mutable(draft)
            if rows[change.sku_id].get("publications"):
                raise BusinessCapabilityError("DRAFT_ALREADY_PUBLISHED", "SKU 已有发布记录，不能修改共享包装或库存。")
    return target, projection, rows


def apply_draft_changes(request: DraftChangesApplyRequest, *, product_store: ProductCapabilityStore,
                        execution: AiExecutionContext) -> DraftChangesApplyResult:
    # 混合修改不能借用包装/库存权限越过属性原有的权限边界。
    if any(change.attributes for change in request.changes) and "product.write" not in execution.permissions:
        raise BusinessCapabilityError("TOOL_PERMISSION_DENIED", "填写类目属性需要 product.write 权限。")
    execution.bounded_timeout_seconds()
    draft, product = _load_draft(product_store, request.draft_id)
    target, _, _ = _check_context(request, draft, product)
    platform, site = _text(target.get("platform")), _text(target.get("site"))
    if any(change.attributes for change in request.changes):
        validation = AttributeUpdateValidation(platform, site, request.category_id,
            timeout=lambda: execution.bounded_timeout_seconds(30))
        for change in request.changes:
            if change.attributes:
                validation.validate(change.attributes, sku_scope=bool(change.sku_id))

    # 网络请求不占商品锁；获得锁后严格复核读取版本与所有修改目标。
    with product_store.mutation_scope({"draft_id": request.draft_id}):
        execution.bounded_timeout_seconds()
        draft, product = _load_draft(product_store, request.draft_id)
        target, projection, rows = _check_context(request, draft, product)
        key = f"{platform}:{site}".lower()
        receipts = []
        for change in request.changes:
            row = rows.get(change.sku_id)
            changed_keys, fields = [], {}
            if change.attributes:
                existing = row.get("attributes_by_target", {}).get(key, {}) if row is not None else projection.get("attributes") or {}
                attributes, changed_keys = apply_attribute_patch(existing, change.attributes)
                fields = shared_attribute_fields(platform, change.attributes, sku_id=change.sku_id)
                if any(_text(draft.get(field)) != value for field, value in fields.items()):
                    _assert_shared_draft_mutable(draft)
                    for attr_id, field in (("BRAND", "brand"), ("MODEL", "model")):
                        if field in fields and _text(draft.get(field)) != fields[field] and attr_id not in changed_keys:
                            changed_keys.append(attr_id)
                    draft.update(fields)
                if changed_keys:
                    if row is not None:
                        row.setdefault("attributes_by_target", {})[key] = attributes
                    else:
                        # merge 会返回新对象；后续 SKU 修改必须使用新对象中的行。
                        draft = merge_target_listing_into_draft(draft, target, {"attributes": attributes})
                        rows = {item["sku_id"]: item for item in draft.get("sku_items", []) if item.get("selected")}
            package_patch = change.package_dimensions.model_dump(exclude_unset=True) if change.package_dimensions else {}
            package_changed = False
            if package_patch:
                dimensions = row.setdefault("overrides", {}).setdefault("package_dimensions", {})
                for field, value in package_patch.items():
                    text = str(value)
                    if dimensions.get(field) != text:
                        dimensions[field], package_changed = text, True
            stock_changed = change.stock is not None and row.get("stock") != str(change.stock)
            if stock_changed:
                row["stock"] = str(change.stock)
            receipts.append(DraftChangeResult(sku_id=change.sku_id,
                changed=bool(changed_keys or package_changed or stock_changed), changed_keys=changed_keys,
                draft_fields=fields))
        changed_count = sum(item.changed for item in receipts)
        saved = draft
        if changed_count:
            execution.bounded_timeout_seconds()
            result, error, _ = product_store.save_draft_content(draft)
            _raise_store_error(error, default_code="DRAFT_CHANGES_SAVE_FAILED", default_message="本组修改保存失败。")
            saved = result["draft"]
        saved_rows = {row["sku_id"]: row for row in saved.get("sku_items", [])}
        saved_common = draft_for_publish_target(saved, _select_target(saved, platform=platform, site=site)).get("attributes") or {}
        actual = []
        for change, receipt in zip(request.changes, receipts, strict=True):
            row = saved_rows.get(change.sku_id, {})
            attributes = row.get("attributes_by_target", {}).get(key, {}) if change.sku_id else saved_common
            dimensions = row.get("overrides", {}).get("package_dimensions", {})
            # 只返回本次字段的实际值，避免把整份草稿再次搬进模型或覆盖表单其他编辑。
            actual.append(receipt.model_copy(update={
                "attributes": {field: attributes[field] for field in change.attributes if field in attributes},
                "package_dimensions": {field: _text(dimensions.get(field)) for field in change.package_dimensions.model_fields_set} if change.package_dimensions else {},
                "stock": _text(row.get("stock")) if change.stock is not None else None,
                "draft_fields": {field: _text(saved.get(field)) for field in receipt.draft_fields},
            }))
        return DraftChangesApplyResult(draft_id=request.draft_id, platform=platform, site=site,
            category_id=_text(projection.get("category_id")), changed=bool(changed_count), changed_count=changed_count,
            previous_updated_at=request.expected_updated_at, updated_at=_text(saved.get("updated_at")), changes=actual)


@ai_tool(
    name="draft_changes_apply",
    description="一次提交同一草稿的多项局部修改：公共/SKU 类目属性、各 SKU 包装尺寸或库存，可混合。先用 draft_attributes_read(scope=sku) 取得事实和 updated_at；涉及属性再查询相应平台定义。用 Python 根据用户规则逐条计算期望值，比较全部已填/未填行，仅收集差异到 changes，使用读取时的 expected_updated_at 一次提交。不得逐 SKU 循环调用单项保存或先试写。全部校验通过后只保存草稿一次；任一错误或版本冲突则整组未保存。结果含每项实际已存字段与新版本，可直接按原规则核对，通常不需重复读取。包装 cm/kg，库存非负整数；不推断值，不修改其他字段。",
    permission="draft.write", side_effect="write", approval_required=False,
    idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual", version="1",
)
def draft_changes_apply(request: DraftChangesApplyRequest,
                        scope: Annotated[ProductCapabilityScope, Injected()],
                        execution: Annotated[AiExecutionContext, Injected()]) -> DraftChangesApplyResult:
    return apply_draft_changes(request, product_store=scope.products, execution=execution)


DRAFT_CHANGES_AI_CAPABILITIES = (draft_changes_apply,)

__all__ = ["DRAFT_CHANGES_AI_CAPABILITIES", "apply_draft_changes", "draft_changes_apply"]
