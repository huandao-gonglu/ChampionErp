"""核价适配层的物流结果；报价证据可直接进入现有 calculation_basis。"""
from typing import Callable, NotRequired, TypedDict


class ShippingResolution(TypedDict):
    amount: str
    currency: str
    minimum_price_cny: str
    billable_g: str
    evidence: dict
    candidates: list[dict]
    minimum_price_exclusive: NotRequired[bool]
    maximum_price_cny: NotRequired[str]
    electronic_service: NotRequired[dict[str, str]]
    error: NotRequired[str]


ShippingResolver = Callable[[dict, dict], ShippingResolution]

__all__ = ['ShippingResolution', 'ShippingResolver']
