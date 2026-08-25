from __future__ import annotations

"""Mercado Libre 授权账号与子市场映射的数据形状。"""

from typing import TypedDict


class MercadoLibreMarketplaceBinding(TypedDict):
    """Global Selling 父账号可操作的一个本地站点与物流组合。"""

    seller_id: str
    site_id: str
    logistic_type: str
    business_model: str
    pricing_model: str
    user_product: bool


class MercadoLibreMarketplaceUser(TypedDict):
    """``/marketplace/users/{user_id}`` 的内部规范化结果。"""

    user_id: str
    site_id: str
    marketplace_bindings: list[MercadoLibreMarketplaceBinding]


__all__ = [
    "MercadoLibreMarketplaceBinding",
    "MercadoLibreMarketplaceUser",
]
