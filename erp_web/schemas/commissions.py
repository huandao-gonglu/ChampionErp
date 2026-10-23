"""官方佣金报价与纯核价引擎之间的契约。"""
from collections.abc import Callable
from typing import Any, Literal, NotRequired, TypedDict, Protocol


class CommissionRule(TypedDict):
    kind: Literal["relative", "absolute"]
    value: str
    price_from: str
    price_to: str | None
    minimum: str | None
    maximum: str | None


class CommissionQuote(TypedDict):
    # 按本次售价计算的有效费率，包含官方固定收费和封顶／保底的影响。
    percent: str
    evidence: dict[str, Any]
    rule: NotRequired[CommissionRule]


class CommissionResolver(Protocol):
    def __call__(self, common: dict, target: dict, price: dict, *, force_official: bool = False) -> CommissionQuote: ...
OzonCategoryLoader = Callable[[], list[dict[str, Any]]]

__all__ = ["CommissionQuote", "CommissionResolver", "CommissionRule", "OzonCategoryLoader"]
