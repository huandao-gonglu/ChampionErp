"""核价适配层的物流结果；报价证据可直接进入现有 calculation_basis。"""
from typing import Callable, NotRequired, TypedDict


class ShippingResolution(TypedDict):
    amount: str
    currency: str
    minimum_price_cny: str
    billable_g: str
    evidence: dict
    candidates: list[dict]
    error: NotRequired[str]


ShippingResolver = Callable[[dict, dict], ShippingResolution]

__all__ = ['ShippingResolution', 'ShippingResolver']
