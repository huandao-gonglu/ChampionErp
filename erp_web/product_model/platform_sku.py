"""草稿及 SKU 远端刊登状态判断。"""

from __future__ import annotations

from typing import Any


_REMOTE_STATUSES = {
    "published",
    "real_publish_success",
    "success",
}


def draft_has_remote_listing(draft: dict[str, Any] | None) -> bool:
    """已发布草稿必须保留原 SKU，避免编辑时意外创建新刊登。"""

    current = draft if isinstance(draft, dict) else {}
    statuses = {
        str(current.get("status") or "").strip().lower(),
        str(current.get("publish_status") or "").strip().lower(),
    }
    if statuses & _REMOTE_STATUSES:
        return True
    if any(row.get("publications") for row in current.get("sku_items", [])):
        return True
    task = (
        current.get("last_publish_task")
        if isinstance(current.get("last_publish_task"), dict)
        else {}
    )
    return any(
        task.get(key) not in (None, "", 0)
        for key in ("external_id", "item_id", "product_id", "offer_id")
    )


__all__ = ["draft_has_remote_listing"]
