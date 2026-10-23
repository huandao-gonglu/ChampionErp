"""退货净损失按最终成交件数分摊；纯本地估算，不查询平台。"""
from fractions import Fraction

from erp_web.services.pricing_solver import fraction


def return_loss(values: dict, shipping_amount, shipping_currency: str) -> tuple[Fraction, Fraction]:
    """返回单次损失与每件成交分摊；佣金全退，其他固定成本不自动重复计入。"""
    rate = fraction(values.get("return_rate_percent", 0)) / 100
    if not rate:
        return Fraction(0), Fraction(0)
    shipping = fraction(shipping_amount)
    if shipping_currency == "USD":
        shipping *= fraction(values["usd_cny_rate"])
    # 国内、国际发货运费与包装按不退估算；退回费用及其他不退费用显式填写。
    loss = shipping + sum((fraction(values[key]) for key in (
        "freight_cny", "international_freight_cny", "prep_fee_cny",
    )), Fraction(0)) + fraction(values.get("return_handling_cost_cny", 0))
    if values.get("return_disposition", "discard") == "discard":
        loss += fraction(values["cost_cny"])
    return loss, rate * loss / (1 - rate)


__all__ = ["return_loss"]
