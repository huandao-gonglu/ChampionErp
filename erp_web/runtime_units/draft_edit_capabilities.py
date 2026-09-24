"""草稿复制、SKU 勾选与包装资料能力，业务写入统一交给 ProductStore。"""

from typing import Annotated, Any

from erp_web.runtime_units.product_write_capabilities import ProductWriteCapabilityScope
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.draft_package import DraftSkuPackageUpdateRequest, DraftSkuPackageUpdateResult
from erp_web.schemas.product_write_capabilities import (
    DraftDuplicateRequest,
    DraftDuplicateResult,
    DraftSkuSelectionUpdateRequest,
    DraftSkuSelectionUpdateResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError


def _require_result(
    response: tuple[dict[str, Any], dict[str, Any] | None, int],
) -> dict[str, Any]:
    result, error, status = response
    if error is not None:
        raise BusinessCapabilityError(
            str(error.get("error_code") or ("DRAFT_NOT_FOUND" if status == 404 else "DRAFT_EDIT_FAILED")),
            str(error.get("error") or "草稿操作失败。"),
        )
    return result


@ai_tool(
    name="draft_duplicate",
    description=(
        "复制一份独立草稿，保留内容与 SKU 勾选并生成新身份，清除副本的发布记录。"
        "创建多份时逐次调用并等待回执；返回新 draft_id 后可继续读取、修改该副本。"
        "按 SKU 分开刊登时，复制后用 draft_sku_selection_update 设置每份的 SKU，保留原草稿。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def draft_duplicate(
    request: DraftDuplicateRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftDuplicateResult:
    # 原生 Tool Bridge 的持久回执按 tool_call_id 防重；不另外创建 Agent 重试协议。
    del execution
    result = _require_result(scope.products.duplicate_draft_from_index(request.draft_id))
    draft = result["draft"]
    return DraftDuplicateResult(
        source_draft_id=request.draft_id,
        draft_id=draft["draft_id"],
        product_id=draft["product_id"],
        platform=draft["platform"],
    )


@ai_tool(
    name="draft_sku_selection_update",
    description=(
        "设置指定草稿最终勾选的完整 SKU ID 集合，未列出的取消勾选，空列表表示全不选。"
        "先用 draft_read 确认 SKU ID；只修改这份草稿的发布选择，不删除商品 SKU、不修改其他草稿。"
        "可在复制草稿后仅选择一个 SKU 或任意分组，以形成独立刊登。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="idempotent",
    version="1",
)
def draft_sku_selection_update(
    request: DraftSkuSelectionUpdateRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftSkuSelectionUpdateResult:
    del execution
    result = _require_result(scope.products.update_draft_sku_selection(
        request.draft_id, list(request.selected_sku_ids),
    ))
    return DraftSkuSelectionUpdateResult(
        draft_id=request.draft_id,
        selected_count=len(request.selected_sku_ids),
        changed=result["changed"],
    )


@ai_tool(
    name="draft_sku_package_update",
    description=(
        "修改指定草稿中指定已选启用 SKU 的实际包装长宽高（cm）或重量（kg）。"
        "先用 draft_attributes_read(scope=sku) 读取 SKU ID 和现有尺寸；sku_ids 列出的 SKU 应用同一组明确值，"
        "不同尺寸分组调用。只更新 package_dimensions 中提供的字段，省略重量可保留各 SKU 原有重量。"
        "写入草稿 SKU 的 overrides.package_dimensions，不修改商品主档、草稿共用尺寸、平台属性或其他 SKU。"
        "返回已处理 SKU、字段值和 changed_count；这是包装资料写入口，不要用 draft_sku_attributes_update 写包装字段。"
    ),
    permission="draft.write",
    side_effect="write",
    approval_required=False,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="idempotent",
    version="1",
)
def draft_sku_package_update(
    request: DraftSkuPackageUpdateRequest,
    scope: Annotated[ProductWriteCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> DraftSkuPackageUpdateResult:
    del execution
    result = _require_result(scope.products.update_draft_sku_package(
        request.draft_id, list(request.sku_ids),
        request.package_dimensions.model_dump(exclude_unset=True),
    ))
    return DraftSkuPackageUpdateResult(
        draft_id=request.draft_id, sku_ids=request.sku_ids,
        package_dimensions=request.package_dimensions.model_dump(exclude_unset=True),
        changed_count=result["changed_count"], changed=result["changed_count"] > 0,
        updated_at=result["updated_at"],
    )


DRAFT_EDIT_AI_CAPABILITIES = (draft_duplicate, draft_sku_selection_update, draft_sku_package_update)

__all__ = ["DRAFT_EDIT_AI_CAPABILITIES", "draft_duplicate", "draft_sku_selection_update", "draft_sku_package_update"]
