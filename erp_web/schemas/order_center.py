"""订单中心本地搜索与筛选的 HTTP 契约。"""

from datetime import date
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import TypeAdapter, ValidationError

from erp_web.schemas.fulfillment import FulfillmentStatus


class OrderCenterFilters(TypedDict, total=False):
    procurement_status: Literal["", "unpurchased", "partial", "purchased", "unknown"]
    declaration_status: Literal["", "unsubmitted", "submitting", "unknown", "created", "error", "cancelled"]
    warehouse_status: Literal[""] | FulfillmentStatus
    has_waybill: Literal["", "yes", "no"]
    domestic_status: Literal["", "waiting", "in_transit", "signed", "unsigned", "unknown"]
    domestic_carrier: str
    receiving_warehouse: str
    platform_carrier: str
    exception_type: Literal["", "any", "purchase", "label", "declaration", "warehouse"]
    deadline_from: str
    deadline_to: str
    deadline_status: Literal["", "today", "tomorrow", "overdue"]
    time_zone: str


ORDER_CENTER_FILTER_KEYS = frozenset(OrderCenterFilters.__annotations__)
_FILTER_ADAPTER = TypeAdapter(OrderCenterFilters)


def validate_order_center_filters(values) -> OrderCenterFilters:
    if set(values) - ORDER_CENTER_FILTER_KEYS:
        raise ValueError("订单筛选条件无效")
    try:
        result = _FILTER_ADAPTER.validate_python(values)
    except ValidationError as exc:
        raise ValueError("订单筛选条件无效，请检查输入及所选状态") from exc
    for key, value in result.items():
        if len(value) > 200:
            raise ValueError("订单筛选条件不能超过 200 个字符")
        result[key] = value.strip()
    for key in ("deadline_from", "deadline_to"):
        if value := result.get(key):
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("发货截止日期须使用 YYYY-MM-DD 格式") from exc
    if result.get("deadline_from") and result.get("deadline_to") and result["deadline_from"] > result["deadline_to"]:
        raise ValueError("发货截止日期的开始日期不能晚于结束日期")
    try:
        ZoneInfo(result.get("time_zone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("订单筛选时区无效") from exc
    return result


__all__ = ["ORDER_CENTER_FILTER_KEYS", "OrderCenterFilters", "validate_order_center_filters"]
