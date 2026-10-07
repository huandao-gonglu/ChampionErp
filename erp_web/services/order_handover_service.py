"""同步订单时批量读取交货批次，归属校验后随订单快照一起保存。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from erp_web.schemas.order_handover import (
    HandoverWarehouse,
    OrderHandoverSnapshot,
    OrderHandoverShipment,
)
from erp_web.schemas.orders import OrderDataError, utc_iso


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OrderDataError("平台交货信息中的标识无效")
    text = str(value)
    if not text.isascii() or not text.isdigit() or int(text) <= 0:
        raise OrderDataError("平台交货信息中的标识无效")
    return text


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OrderDataError("平台交货信息的文本格式无效")
    return value.strip()


def _time(value):
    value = _text(value)
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError:
            raise OrderDataError("平台交货时间缺少有效时区或格式无效") from None
    return value


def _warehouse(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OrderDataError("平台交货仓库格式无效")
    return HandoverWarehouse(
        id=_identifier(value["id"]) if value.get("id") is not None else "",
        name=_text(value.get("name")),
        address=_text(value.get("address")),
    )


def _shipment(row, order_id):
    if not isinstance(row, dict) or not isinstance(row.get("orderIds"), list):
        raise OrderDataError("平台交货批次缺少订单归属，暂不展示地址")
    if order_id not in {_identifier(value) for value in row["orderIds"]}:
        raise OrderDataError("平台交货批次与当前订单不一致，暂不展示地址")
    shipment = OrderHandoverShipment(
        shipment_id=_identifier(row.get("id")),
        shipment_type=_text(row.get("shipmentType")),
        status=_text(row.get("status")),
        planned_from=_time(row.get("planIntervalFrom")),
        planned_to=_time(row.get("planIntervalTo")),
        origin=_warehouse(row.get("warehouse")),
        destination=_warehouse(row.get("warehouseTo")),
    )
    if shipment.planned_from and shipment.planned_to:
        if datetime.fromisoformat(shipment.planned_from.replace("Z", "+00:00")) > datetime.fromisoformat(shipment.planned_to.replace("Z", "+00:00")):
            raise OrderDataError("平台交货时间范围无效")
    return shipment


def enrich_yandex_handover(snapshots, *, account, api_token, request):
    """每页订单共用一次批次搜索；完整校验分页后才返回可持久化快照。"""
    account = _identifier(account)
    eligible = {}
    days = []
    result = []
    for order in snapshots:
        if order.platform != "yandex" or order.account_id != account:
            raise OrderDataError("交货信息查询越过当前店铺范围")
        handover = None
        if (order.delivery.fulfillment_model or order.fulfillment).upper() == "FBS":
            if not order.shipment_deadline:
                handover = OrderHandoverSnapshot(
                    state="unavailable", message="平台尚未提供发货日期", checked_at=utc_iso(),
                )
            else:
                try:
                    days.append(date.fromisoformat(order.shipment_deadline[:10]))
                except ValueError:
                    raise OrderDataError("订单发货日期无效，请重新同步订单") from None
                eligible[_identifier(order.order_id)] = order
        result.append(order.model_copy(update={"handover": handover}))
    if not eligible:
        return result

    # 按本页订单的发货日期扩大前后七天，仍以 orderIds 严格关联。
    body = {
        "dateFrom": (min(days) - timedelta(days=7)).isoformat(),
        "dateTo": (max(days) + timedelta(days=7)).isoformat(),
        "orderIds": [int(value) for value in eligible],
    }
    cursor, seen, shipments = "", set(), {}
    memberships = {}
    for _ in range(10):
        query = {"limit": 30}
        if cursor:
            query["pageToken"] = cursor
        data = request(
            "PUT", f"/v2/campaigns/{account}/first-mile/shipments", api_token,
            body, query=query, timeout_seconds=15,
        )
        payload = data.get("result")
        if not isinstance(payload, dict) or not isinstance(payload.get("shipments"), list):
            raise OrderDataError("平台交货批次响应不完整，请稍后重试")
        for row in payload["shipments"]:
            if not isinstance(row, dict) or not isinstance(row.get("orderIds"), list):
                raise OrderDataError("平台交货批次缺少订单归属，暂不展示地址")
            related = {_identifier(value) for value in row["orderIds"]} & eligible.keys()
            if not related:
                raise OrderDataError("平台交货批次与当前订单不一致，暂不展示地址")
            shipment = _shipment(row, next(iter(related)))
            previous = shipments.get(shipment.shipment_id)
            if previous is not None and (previous != shipment or memberships[shipment.shipment_id] != related):
                raise OrderDataError("平台交货批次在分页期间发生变化，请重新同步订单")
            shipments[shipment.shipment_id] = shipment
            memberships[shipment.shipment_id] = related
        paging = payload.get("paging") or {}
        if not isinstance(paging, dict):
            raise OrderDataError("平台交货批次分页无效")
        cursor = _text(paging.get("nextPageToken"))
        if not cursor:
            break
        if cursor in seen or not payload["shipments"]:
            raise OrderDataError("平台交货批次分页未前进，请稍后重试")
        seen.add(cursor)
    else:
        raise OrderDataError("平台交货批次数量超出读取范围，请在平台核对")

    checked = utc_iso()
    for order in result:
        if order.order_id not in eligible:
            continue
        matches = [value for key, value in shipments.items() if order.order_id in memberships[key]]
        order.handover = OrderHandoverSnapshot(
            state="ready" if matches else "unavailable", checked_at=checked,
            message="" if matches else "尚未找到该订单的交货批次",
            shipments=sorted(matches, key=lambda row: (row.planned_from, row.shipment_id)),
        )
    return result
