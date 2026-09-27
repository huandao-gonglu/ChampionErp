"""Mercado User Products 身份闭包校验，供远端同步复用。"""
from __future__ import annotations
import re
from typing import Any
from erp_web.product_model import canonicalize_mercadolibre_siteless_user_product_id

def validate_user_product_mapping(
    response: Any,
    publication: dict[str, Any],
) -> dict[str, Any]:
    """严格校验官方 mapping 单元素数组并投影身份字段。"""

    if not isinstance(response, list):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_RESPONSE_INVALID: mapping 响应必须是顶层数组"
        )
    if len(response) != 1:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_CARDINALITY_INVALID: "
            "mapping 响应必须且只能包含一个对象"
        )
    if not isinstance(response[0], dict):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_RESPONSE_INVALID: mapping 数组元素必须是对象"
        )

    body = dict(response[0])
    expected_siteless_id = canonicalize_mercadolibre_siteless_user_product_id(
        publication.get("siteless_user_product_id")
    )
    if not re.fullmatch(r"U\d+", expected_siteless_id):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_LOCAL_ID_INVALID: "
            "本地 publication 缺少合法的 U{id} Siteless 身份"
        )
    raw_remote_id = str(body.get("siteless_user_product_id") or "").strip()
    if not raw_remote_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_MISSING: "
            "mapping 响应缺少 siteless_user_product_id"
        )
    remote_siteless_id = canonicalize_mercadolibre_siteless_user_product_id(
        raw_remote_id
    )
    if not re.fullmatch(r"U\d+", remote_siteless_id):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_INVALID: "
            "mapping 响应的 Siteless 身份格式无效"
        )
    if remote_siteless_id != expected_siteless_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITELESS_ID_MISMATCH: "
            f"expected={expected_siteless_id}, actual={remote_siteless_id}"
        )

    expected_owner_id = str(publication.get("account_user_id") or "").strip()
    remote_owner_id = str(body.get("owner_id") or "").strip()
    if not remote_owner_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_OWNER_ID_MISSING: mapping 响应缺少 owner_id"
        )
    if not expected_owner_id or remote_owner_id != expected_owner_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_OWNER_ID_MISMATCH: "
            f"expected={expected_owner_id or '<missing>'}, actual={remote_owner_id}"
        )

    parent_item_id = str(body.get("item_id") or "").strip()
    parent_user_product_id = str(body.get("user_product_id") or "").strip()
    if not parent_item_id or not parent_user_product_id:
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_PARENT_ID_MISSING: "
            "mapping 响应缺少 CBT parent item/user-product 身份"
        )
    if (
        canonicalize_mercadolibre_siteless_user_product_id(
            parent_user_product_id
        )
        != expected_siteless_id
    ):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_PARENT_ID_MISMATCH: "
            "mapping 的 CBT parent_user_product_id 与 Siteless 身份不一致"
        )

    raw_markets = body.get("site_items")
    if not isinstance(raw_markets, list):
        raise RuntimeError(
            "MERCADOLIBRE_MAPPING_SITE_ITEMS_INVALID: "
            "mapping 响应的 site_items 必须是数组"
        )
    site_items: list[dict[str, Any]] = []
    for raw in raw_markets:
        if not isinstance(raw, dict):
            raise RuntimeError(
                "MERCADOLIBRE_MAPPING_SITE_ITEMS_INVALID: "
                "mapping site_items 元素必须是对象"
            )
        site_id = str(raw.get("site_id") or "").strip().upper()
        item_id = str(raw.get("item_id") or "").strip()
        if not site_id or site_id == "CBT" or not item_id:
            raise RuntimeError(
                "MERCADOLIBRE_MAPPING_SITE_ITEM_IDENTITY_INVALID: "
                "mapping 子市场缺少合法的 site_id/item_id"
            )
        site_items.append(
            {
                **raw,
                "site_id": site_id,
                "item_id": item_id,
            }
        )
    return {
        **body,
        "account_user_id": remote_owner_id,
        "parent_item_id": parent_item_id,
        "parent_user_product_id": parent_user_product_id,
        "siteless_user_product_id": remote_siteless_id,
        "site_items": site_items,
    }

