from __future__ import annotations

"""发布确认所绑定的确定性事实。

该模块不依赖队列、数据库或 Capability，供确认生成端与 worker 共同使用，
避免两端分别实现 digest 或店铺身份算法。
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class PublishStoreBinding:
    identity: str
    label: str


def resolve_publish_store_binding(
    platform: str,
    config: dict[str, Any],
) -> PublishStoreBinding:
    platform_key = str(platform or "").strip().lower()
    store = (
        config.get(platform_key)
        if isinstance(config.get(platform_key), dict)
        else {}
    )
    identity_keys = (
        ("user_id", "seller_id", "auth_masked_account", "shop_name")
        if platform_key == "mercadolibre"
        else (
            "client_id",
            "seller_id",
            "user_id",
            "auth_masked_account",
            "shop_name",
        )
    )
    identity_value = next(
        (
            str(store.get(key) or "").strip()
            for key in identity_keys
            if str(store.get(key) or "").strip()
        ),
        "",
    )
    if not identity_value:
        raise ValueError("当前店铺缺少可绑定发布确认的稳定账号身份。")
    identity_digest = hashlib.sha256(
        f"{platform_key}\0{identity_value}".encode("utf-8")
    ).hexdigest()
    label = str(
        store.get("shop_name")
        or store.get("nickname")
        or store.get("auth_masked_account")
        or "已授权账号"
    ).strip()
    return PublishStoreBinding(
        identity=f"{platform_key}:{identity_digest[:24]}",
        label=label,
    )


def canonical_publish_digest(
    *,
    product_id: str,
    draft_id: str,
    platform: str,
    site: str,
    store_identity: str,
    payload: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "product_id": str(product_id or "").strip(),
            "draft_id": str(draft_id or "").strip(),
            "platform": str(platform or "").strip().lower(),
            "site": str(site or "").strip().lower(),
            "store_identity": str(store_identity or "").strip(),
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "PublishStoreBinding",
    "canonical_publish_digest",
    "resolve_publish_store_binding",
]
