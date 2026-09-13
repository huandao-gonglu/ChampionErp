"""可组合的草稿复制与 SKU 勾选能力，业务写入统一交给 ProductStore。"""

from typing import Annotated, Any

from erp_web.runtime_units.product_write_capabilities import ProductWriteCapabilityScope
from erp_web.schemas.ai_trace import AiExecutionContext
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


DRAFT_EDIT_AI_CAPABILITIES = (draft_duplicate, draft_sku_selection_update)

__all__ = ["DRAFT_EDIT_AI_CAPABILITIES", "draft_duplicate", "draft_sku_selection_update"]
