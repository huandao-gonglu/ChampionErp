"""分段费用的定价求解；金额按分离散，候选价仍须取得官方回执。"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from erp_web.schemas.commissions import CommissionRule

CENT = Fraction(1, 100)
HALF_CENT = CENT / 2


def fraction(value) -> Fraction:
    return Fraction(str(value))


def ceil_cent(value: Fraction) -> Fraction:
    scaled = value * 100
    return Fraction(-(-scaled.numerator // scaled.denominator), 100)


def money(value: Fraction) -> Decimal:
    """仅序列化已经取整到分的售价。"""
    return Decimal(value.numerator * 100 // value.denominator) / 100


def _rounded_fee(value: Fraction) -> Fraction:
    scaled = value * 100 + Fraction(1, 2)
    return Fraction(scaled.numerator // scaled.denominator, 100)


@dataclass(frozen=True)
class PricingGoal:
    # 两种自动定价统一为 retained × 售价 - 佣金 >= required。
    retained: Fraction
    required: Fraction
    floor: Fraction = CENT

    def meets(self, price, fee) -> bool:
        amount = fraction(price)
        return amount >= self.floor and self.retained * amount - fraction(fee) >= self.required

    def proportional_price(self, rate: Fraction) -> Decimal:
        denominator = self.retained - rate
        if denominator <= 0:
            raise ValueError("平台费用合计 + 定价利润目标必须小于 100%")
        return money(max(CENT, ceil_cent(self.floor), ceil_cent(self.required / denominator)))

    def solve_rule(self, rule: CommissionRule) -> Decimal:
        """分别求固定、保底、比例、封顶区间，选择已知档位内最低达标分价。"""
        if self.retained <= 0:
            raise ValueError("支付手续费、其他费用和定价利润目标合计必须小于 100%")
        lower = fraction(rule["price_from"])
        upper = fraction(rule["price_to"]) if rule["price_to"] is not None else None
        first = max(CENT, ceil_cent(self.floor), (lower // CENT + 1) * CENT)
        value = fraction(rule["value"])
        minimum = fraction(rule["minimum"]) if rule["minimum"] is not None else Fraction(0)
        maximum = fraction(rule["maximum"]) if rule["maximum"] is not None else None
        segments = []
        if rule["kind"] == "absolute" or value == 0:
            fee = max(value if rule["kind"] == "absolute" else Fraction(0), minimum)
            segments.append((Fraction(0), None, Fraction(0), min(fee, maximum) if maximum is not None else fee))
        else:
            rate = value / 100
            segments.append((Fraction(0), minimum / rate, Fraction(0), minimum))
            segments.append((minimum / rate, maximum / rate if maximum is not None else None, rate, Fraction(0)))
            if maximum is not None:
                segments.append((maximum / rate, None, Fraction(0), maximum))
        candidates = []
        for start, end, rate, fixed in segments:
            start = max(first, ceil_cent(start))
            end = min(end, upper) if end is not None and upper is not None else (upper if end is None else end)
            candidate = self._segment_price(start, end, rate, fixed)
            if candidate is not None and (upper is None or candidate < upper):
                candidates.append(candidate)
        if candidates:
            return money(min(candidates))
        if upper is not None:
            # API 只返回命中的档位。边界和相邻档位必须重新批量获取，不能外推。
            return money(max(first, ceil_cent(upper)))
        raise ValueError("当前费用规则下无法达到定价利润目标")

    def _segment_price(self, start, end, rate, fixed):
        if end is not None and start > end:
            return None
        if rate == 0:
            candidate = max(start, ceil_cent((self.required + _rounded_fee(fixed)) / self.retained))
            return candidate if end is None or candidate <= end else None
        if self.meets(start, _rounded_fee(rate * start)):
            return start
        slope = self.retained - rate
        if slope > 0:
            # 费用舍入误差最多半分，真正的最低解只可能位于解析解附近的这个区间。
            start = max(start, ceil_cent((self.required - HALF_CENT) / slope))
            guaranteed = ceil_cent((self.required + HALF_CENT) / slope)
            end = min(end, guaranteed) if end is not None else guaranteed
        elif slope < 0:
            last_possible = (self.required - HALF_CENT) / slope
            end = min(end, last_possible) if end is not None else last_possible
        else:
            if self.required >= HALF_CENT:
                return None
            # 零斜率时舍入形状周期性重复，只需检查一个有限周期。
            period_end = start + CENT * rate.denominator
            end = min(end, period_end) if end is not None else period_end
        candidate = start
        while candidate <= end:
            fee = _rounded_fee(rate * candidate)
            if self.meets(candidate, fee):
                return candidate
            # 费用不递减；在此下界之前的每一个分价都不可能达标。
            # 只修正费用舍入，不反推有效费率、不重跑核价流程或发请求。
            candidate = max(candidate + CENT, ceil_cent((self.required + fee) / self.retained))
        return None


__all__ = ["PricingGoal", "fraction", "ceil_cent", "money"]
