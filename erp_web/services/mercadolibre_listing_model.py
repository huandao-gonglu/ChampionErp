from __future__ import annotations

"""Mercado Libre CBT 刊登模型的可信派生规则。"""

from typing import Any

from erp_web.schemas.mercadolibre import MercadoLibreListingModel

MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS: MercadoLibreListingModel = (
    "user_products"
)
MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS: MercadoLibreListingModel = (
    "traditional_global_items"
)
MERCADOLIBRE_TRADITIONAL_CONFIRMED_PAYLOAD_VERSION = 1


def mercadolibre_listing_model_from_user_tags(
    tags: Any,
    *,
    account_site_id: str,
) -> MercadoLibreListingModel | None:
    """只从 ``/users`` 身份的 site 与 tags 派生 CBT 刊登模型。

    区域账号不映射到任何模型，因为本项目不提供区域 ``/items`` 发布路径。
    ``user_product_seller`` 是第三方原始能力；没有该 tag 的 CBT 父账号仍使用
    已被真实发布记录验证过的传统 Global Items 合同。
    """

    if str(account_site_id or "").strip().upper() != "CBT":
        return None
    normalized_tags = {
        str(tag or "").strip().casefold()
        for tag in tags
        if str(tag or "").strip()
    } if isinstance(tags, (list, tuple, set, frozenset)) else set()
    if "user_product_seller" in normalized_tags:
        return MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
    return MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS


def require_mercadolibre_listing_model(value: Any) -> MercadoLibreListingModel:
    """校验可信配置或内部 payload 中的显式刊登模型。"""

    normalized = str(value or "").strip()
    if normalized == MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS:
        return MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS
    if normalized == MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS:
        return MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS
    raise RuntimeError(
        "MERCADOLIBRE_LISTING_MODEL_REQUIRED: 请重新验证授权并读取刊登模型"
    )


__all__ = [
    "MERCADOLIBRE_LISTING_MODEL_TRADITIONAL_GLOBAL_ITEMS",
    "MERCADOLIBRE_LISTING_MODEL_USER_PRODUCTS",
    "MERCADOLIBRE_TRADITIONAL_CONFIRMED_PAYLOAD_VERSION",
    "MercadoLibreListingModel",
    "mercadolibre_listing_model_from_user_tags",
    "require_mercadolibre_listing_model",
]
