"""一份草稿的成组局部修改；平台校验在锁外，复核和保存只做一次。"""

from typing import Annotated, Any

from erp_web.runtime_units.category_attribute_updates import AttributeUpdateValidation
from erp_web.runtime_units.draft_publish_context import draft_for_publish_target, merge_target_listing_into_draft
from erp_web.runtime_units.product_attribute_patch import apply_attribute_patch
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
        if change.stock is not None:
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
                if changed_keys:
                    if row is not None:
                        row.setdefault("attributes_by_target", {})[key] = attributes
                    else:
                        # merge 会返回新对象；后续 SKU 修改必须使用新对象中的行。
                        draft = merge_target_listing_into_draft(draft, target, {"attributes": attributes})
                        rows = {item["sku_id"]: item for item in draft.get("sku_items", []) if item.get("selected")}
            stock_changed = change.stock is not None and row.get("stock") != str(change.stock)
            if stock_changed:
                row["stock"] = str(change.stock)
            receipts.append(DraftChangeResult(sku_id=change.sku_id,
                changed=bool(changed_keys or stock_changed), changed_keys=changed_keys,
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
            # 只返回本次字段的实际值，避免把整份草稿再次搬进模型或覆盖表单其他编辑。
            actual.append(receipt.model_copy(update={
                "attributes": {field: attributes[field] for field in change.attributes if field in attributes},
                "stock": _text(row.get("stock")) if change.stock is not None else None,
                "draft_fields": {field: _text(saved.get(field)) for field in receipt.draft_fields},
            }))
        return DraftChangesApplyResult(draft_id=request.draft_id, platform=platform, site=site,
            category_id=_text(projection.get("category_id")), changed=bool(changed_count), changed_count=changed_count,
            previous_updated_at=request.expected_updated_at, updated_at=_text(saved.get("updated_at")), changes=actual)


@ai_tool(
    name="draft_changes_apply",
    description="一次提交同一草稿和平台/站点的多项公共/SKU 类目属性及可售库存。明确全部所需定义，用 draft_attributes_read(scope=sku) 一次取得并保留事实与 updated_at，据此补齐候选；在同一脚本中计算本轮全部相关属性，每个 SKU 的颜色、规格等差异合入同一个 attributes，可同时设置 stock。不逐属性重读或提交整批，不逐 SKU 调用单项保存，不先试写；同一草稿的多个写工具（含类目匹配、文案生成、属性保存与核价）须串行执行并沿用上一步回执的最新 updated_at，禁止对同一草稿并发写入。全部校验通过后只保存一次；任一错误或版本冲突则整组未保存。changes 回执含全部提交项的实际保存值及 updated_at，直接核对并更新脚本数据，无需重读确认；changed_count=0 表示值已一致。确需核实局部结果时按 sku_ids 补读。完整准备应在相关属性与事实保存完后统一核价和预检，窄范围修改不扩展核价。成本、包装、条码及运输事实用 product_sku_facts_update；不猜值或修改无关字段。",
    permission="draft.write", side_effect="write", approval_required=False,
    idempotency="required", idempotency_keys=("operation_key",), recovery_policy="manual", version="1",
)
def draft_changes_apply(request: DraftChangesApplyRequest,
                        scope: Annotated[ProductCapabilityScope, Injected()],
                        execution: Annotated[AiExecutionContext, Injected()]) -> DraftChangesApplyResult:
    return apply_draft_changes(request, product_store=scope.products, execution=execution)


DRAFT_CHANGES_AI_CAPABILITIES = (draft_changes_apply,)

__all__ = ["DRAFT_CHANGES_AI_CAPABILITIES", "apply_draft_changes", "draft_changes_apply"]
