"""明确收费规则的本地求值；计算结果只是候选，不能替代官方回执。"""
from decimal import Decimal, ROUND_HALF_UP

from erp_web.schemas.commissions import CommissionRule


def rule_fee(rule: CommissionRule, price: Decimal) -> Decimal | None:
    lower = Decimal(rule["price_from"])
    upper = Decimal(rule["price_to"]) if rule["price_to"] is not None else None
    # 档位边界的包含关系不能靠猜测，交给该价格的官方查询确认。
    if price <= lower or (upper is not None and price >= upper):
        return None
    value = Decimal(rule["value"])
    fee = price * value / 100 if rule["kind"] == "relative" else value
    if rule["minimum"] is not None:
        fee = max(fee, Decimal(rule["minimum"]))
    if rule["maximum"] is not None:
        fee = min(fee, Decimal(rule["maximum"]))
    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


__all__ = ["rule_fee"]
