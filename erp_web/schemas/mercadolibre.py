from __future__ import annotations

"""Mercado Libre 授权账号、Siteless UP 与市场投影的数据形状。"""

from typing import Any, Literal, TypedDict


MercadoLibreListingModel = Literal[
    "user_products",
    "traditional_global_items",
]


class MercadoLibreMarketplaceBinding(TypedDict):
    """Global Selling 父账号可操作的一个本地站点与物流组合。"""

    seller_id: str
    site_id: str
    logistic_type: str
    business_model: str
    pricing_model: str
    # 标准 Remote mapping 的官方响应可能不返回此字段；父账号
    # ``user_product_seller`` 才是 User Products 的主要能力标志。
    # ``None`` 表示远端未声明，只有显式 ``False`` 才代表该 operation 不可用。
    user_product: bool | None


class MercadoLibreMarketplaceUser(TypedDict):
    """``/marketplace/users/{user_id}`` 的内部规范化结果。"""

    user_id: str
    site_id: str
    marketplace_bindings: list[MercadoLibreMarketplaceBinding]


class MercadoLibreMarketPublication(TypedDict, total=False):
    """一个 Siteless User Product 在具体 marketplace operation 上的刊登。"""

    site_id: str
    seller_id: str
    logistic_type: str
    item_id: str
    user_product_id: str
    status: str
    price: float | str
    net_proceeds: float | str
    free_shipping: bool
    sale_terms: list[dict[str, Any]]
    currency_id: str
    listing_type_id: str
    error: dict[str, Any] | list[Any] | str
    last_operation: dict[str, Any]
    updated_at: str


class MercadoLibrePublication(TypedDict, total=False):
    """内部商品对应的 Mercado Siteless User Product 聚合。"""

    model: str
    account_user_id: str
    parent_item_id: str
    parent_user_product_id: str
    siteless_user_product_id: str
    siteless_family_id: str
    seller_id: str
    family_name: str
    status: str
    markets: list[MercadoLibreMarketPublication]
    confirmed_payload: dict[str, Any]
    error: dict[str, Any] | list[Any] | str
    last_operation: dict[str, Any]
    updated_at: str


__all__ = [
    "MercadoLibreListingModel",
    "MercadoLibreMarketPublication",
    "MercadoLibreMarketplaceBinding",
    "MercadoLibreMarketplaceUser",
    "MercadoLibrePublication",
]
