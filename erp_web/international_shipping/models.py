"""国际物流模块的独立参数与结果；不依赖 ERP、HTTP 或数据库。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Callable


def number(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}必须是有效数字') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f'{label}必须是有限的{"正" if positive else "非负"}数')
    return result


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.01'), rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Package:
    weight_g: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    battery: bool = False
    liquid: bool = False

    def __post_init__(self) -> None:
        for name in ('weight_g', 'length_cm', 'width_cm', 'height_cm'):
            object.__setattr__(self, name, number(getattr(self, name), name, positive=True))
        for name in ('battery', 'liquid'):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f'{name}必须是布尔值')

    @property
    def edges(self) -> tuple[Decimal, Decimal, Decimal]:
        return self.length_cm, self.width_cm, self.height_cm


# 返回当前核价模式下的售价 CNY。第三个参数是渠道最低货值；手动售价不得被抬高。
PriceForShipping = Callable[[Decimal, str, Decimal], Decimal]


@dataclass(frozen=True)
class Candidate:
    route_id: str
    route: str
    amount: Decimal
    currency: str
    billable_g: Decimal
    minimum_price_cny: Decimal = Decimal(0)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = asdict(self)
        for name in ('amount', 'billable_g', 'minimum_price_cny'):
            result[name] = str(result[name])
        return result


@dataclass
class QuoteResult:
    candidates: list[Candidate] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def to_dict(self) -> dict:
        return {'candidates': [item.to_dict() for item in self.candidates], 'rejected': self.rejected}
