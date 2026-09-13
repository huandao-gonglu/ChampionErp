"""从可信复制回执扩展草稿业务权限，不存储 Agent 执行状态。"""

from pydantic import ValidationError

from erp_web.context import AppContext
from erp_web.schemas.product_write_capabilities import DraftDuplicateResult


def authorized_draft_ids(
    context: AppContext,
    conversation_id: str,
    selected_ids: set[str],
) -> set[str]:
    """仅纳入同会话从当前所选草稿复制的后代，兄弟草稿不会自动获得权限。"""
    allowed = set(selected_ids)
    if not allowed or not conversation_id:
        return allowed
    receipts = context.agent_calls.completed_tool_receipts(
        conversation_id, "draft_duplicate"
    )
    for receipt in receipts:
        source_id = receipt["arguments"].get("draft_id")
        if source_id not in allowed:
            continue
        try:
            copied = DraftDuplicateResult.model_validate(receipt["output"])
        except ValidationError:
            # 失败或结果未知的调用不能扩大写入范围。
            continue
        if copied.source_draft_id != source_id:
            continue
        source = context.products.draft_record(source_id)
        target = context.products.draft_record(copied.draft_id)
        if (
            source.get("product_id") == copied.product_id
            and target.get("product_id") == copied.product_id
        ):
            allowed.add(copied.draft_id)
    return allowed


__all__ = ["authorized_draft_ids"]
