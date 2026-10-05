"""平台回调的纯解析：账号校验、资源白名单与官方握手响应。"""

from __future__ import annotations

import re
from typing import Any

from erp_web.schemas.orders import OrderEvent, configured_accounts, timestamp, utc_iso

OZON_TOPICS = frozenset(
    {
        "TYPE_NEW_POSTING",
        "TYPE_POSTING_CANCELLED",
        "TYPE_STATE_CHANGED",
        "TYPE_CUTOFF_DATE_CHANGED",
        "TYPE_DELIVERY_DATE_CHANGED",
        "TYPE_FBO_POSTING_NEW",
        "TYPE_FBO_POSTING_CANCELLED",
        "TYPE_FBO_POSTING_STATE_CHANGED",
        "TYPE_FBO_POSTING_DELIVERY_DATE_CHANGED",
        "TYPE_ORDER_NEW",
        "TYPE_ORDER_CANCELLED",
        "TYPE_ORDER_STATE_CHANGED",
    }
)
YANDEX_TOPICS = frozenset(
    {
        "ORDER_CREATED",
        "ORDER_CANCELLED",
        "ORDER_STATUS_UPDATED",
        "ORDER_UPDATED",
        "ORDER_CANCELLATION_REQUEST",
        "ORDER_RETURN_CREATED",
        "ORDER_RETURN_STATUS_UPDATED",
    }
)


def identifier(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("订单或账号标识无效")
    result = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", result):
        raise ValueError("订单或账号标识无效")
    return result


def parse_notification(platform: str, body: dict[str, Any], config: dict[str, Any]):
    account = configured_accounts(config).get(platform)
    if not account:
        raise ValueError("请先配置并授权该平台账号")
    store = config[platform]
    topic_key = {
        "mercadolibre": "topic",
        "ozon": "message_type",
        "yandex": "notificationType",
    }[platform]
    if not isinstance(body.get(topic_key), str) or not body[topic_key]:
        raise ValueError("通知缺少合法的事件类型")
    if platform == "mercadolibre":
        topic = str(body.get("topic") or "")
        response = {"ok": True}
        if identifier(body.get("user_id")) != account or str(
            body.get("application_id") or ""
        ) != str(store.get("app_id") or ""):
            raise ValueError("回调账号或应用与当前授权不一致")
        if topic not in {"orders_v2", "shipments"}:
            return None, response
        resource = str(body.get("resource") or "")
        prefix = "orders" if topic == "orders_v2" else "shipments"
        if not re.fullmatch(rf"/{prefix}/[0-9]+", resource):
            raise ValueError("回调资源不是受支持的订单或物流路径")
        occurred = str(body.get("sent") or "")
    elif platform == "ozon":
        topic = str(body.get("message_type") or "")
        response = {"version": "1.0", "name": "Champion ERP", "time": utc_iso()}
        if topic == "TYPE_PING":
            return None, response
        if str(body.get("seller_id") or "") != str(store.get("seller_id") or account):
            raise ValueError("Ozon 回调卖家与当前账号不一致")
        if topic not in OZON_TOPICS:
            return None, response
        # 订单级事件由同步任务展开为 FBS/FBO 发运单，避免混用订单号和 posting_number。
        resource = identifier(
            body.get("order_id")
            if topic.startswith("TYPE_ORDER_")
            else body.get("posting_number")
        )
        occurred = str(
            body.get("changed_state_date")
            or body.get("created_at")
            or body.get("time")
            or ""
        )
    elif platform == "yandex":
        topic = str(body.get("notificationType") or "")
        response = {"version": "1.0.0", "name": "Champion ERP", "time": utc_iso()}
        if topic == "PING":
            return None, response
        if str(body.get("campaignId") or "") != account:
            raise ValueError("Yandex 回调店铺与当前账号不一致")
        if topic not in YANDEX_TOPICS:
            return None, response
        resource = identifier(body.get("orderId"))
        if not resource.isdigit() or int(resource) <= 0:
            raise ValueError("Yandex 订单号必须为正整数")
        occurred = str(
            body.get("updatedAt")
            or body.get("cancelledAt")
            or body.get("createdAt")
            or ""
        )
    else:
        raise ValueError("不支持的平台")
    if occurred:
        timestamp(occurred)
    return OrderEvent(
        platform=platform,
        account_id=account,
        topic=topic,
        resource=resource,
        occurred_at=occurred,
        payload=body,
    ), response
