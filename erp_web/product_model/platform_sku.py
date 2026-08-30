"""平台草稿 SKU 的稳定生成规则。"""

from __future__ import annotations

import hashlib
import re
from typing import Any


_PLATFORM_PREFIXES = {
    "mercadolibre": "ML",
    "ozon": "OZ",
    "yandex": "YDX",
}

_PLACEHOLDER_SKUS = {
    "-",
    "/",
    "0",
    "00",
    "default",
    "general",
    "n/a",
    "na",
    "none",
    "null",
    "other",
    "unknown",
    "其他",
    "其它",
    "无",
    "未知",
}

_REMOTE_STATUSES = {
    "published",
    "real_publish_success",
    "success",
}


def is_placeholder_sku(value: Any) -> bool:
    """判断采集或 AI 产生的值是否只是占位符。"""

    return str(value or "").strip().casefold() in _PLACEHOLDER_SKUS


def _identity_token(value: Any, *, length: int) -> str:
    text = str(value or "").strip()
    compact = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    if compact:
        return compact[:length]
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:length].upper()


def draft_has_remote_listing(draft: dict[str, Any] | None) -> bool:
    """已发布草稿必须保留原 SKU，避免编辑时意外创建新刊登。"""

    current = draft if isinstance(draft, dict) else {}
    statuses = {
        str(current.get("status") or "").strip().lower(),
        str(current.get("publish_status") or "").strip().lower(),
    }
    if statuses & _REMOTE_STATUSES:
        return True
    publication = (
        current.get("publication")
        if isinstance(current.get("publication"), dict)
        else {}
    )
    if publication.get("siteless_user_product_id") or publication.get("parent_item_id"):
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


def generated_platform_sku(
    platform: str,
    *,
    product_id: Any,
    draft_id: Any,
) -> str:
    """按草稿身份生成短、稳定且只含安全字符的卖家 SKU。"""

    platform_key = str(platform or "").strip().lower()
    prefix = _PLATFORM_PREFIXES.get(platform_key, "ERP")
    product_token = _identity_token(product_id, length=8)
    draft_token = _identity_token(draft_id, length=12)
    if not draft_token:
        return ""
    if product_token:
        return f"{prefix}-{product_token}-{draft_token}"
    return f"{prefix}-{draft_token}"


def resolve_platform_draft_sku(
    draft: dict[str, Any] | None,
    platform: str,
    *,
    product_id: Any = "",
) -> str:
    """保留有效人工 SKU；为未发布草稿生成稳定 SKU。"""

    current = draft if isinstance(draft, dict) else {}
    existing = str(current.get("sku") or "").strip()
    if existing and (
        not is_placeholder_sku(existing)
        or draft_has_remote_listing(current)
    ):
        return existing
    if draft_has_remote_listing(current):
        return existing
    return generated_platform_sku(
        platform,
        product_id=(
            current.get("source_product_id")
            or current.get("product_id")
            or product_id
        ),
        draft_id=current.get("draft_id") or current.get("draftId"),
    )


__all__ = [
    "draft_has_remote_listing",
    "generated_platform_sku",
    "is_placeholder_sku",
    "resolve_platform_draft_sku",
]
