# -*- coding: utf-8 -*-
from __future__ import annotations

import urllib.parse
from typing import Any

from erp_web import marketplaces as publisher

from .category_store import read_json, write_json
from .collect_helpers import collect_time_iso
from .product_store import load_store_config, save_store_config
from .publish_mercadolibre import ensure_mercadolibre_auth_ready
from .runtime_common import OUTPUT_DIR

ORDER_NOTIFICATIONS_PATH = OUTPUT_DIR / "mercadolibre_order_notifications.json"


def _load_order_notifications() -> list[dict[str, Any]]:
    value = read_json(ORDER_NOTIFICATIONS_PATH, [])
    return value if isinstance(value, list) else []


def load_mercadolibre_order_notifications(limit: int = 20) -> list[dict[str, Any]]:
    notifications = _load_order_notifications()
    return notifications[: max(1, min(int(limit or 20), 100))]


def _save_order_notifications(items: list[dict[str, Any]]) -> None:
    write_json(ORDER_NOTIFICATIONS_PATH, items[:200])


def _mercadolibre_user_id(config: dict[str, Any], token: str) -> str:
    store = config.setdefault("mercadolibre", {})
    user_id = str(store.get("user_id") or store.get("seller_id") or "").strip()
    if user_id:
        return user_id
    me = publisher.request_json("GET", "https://api.mercadolibre.com/users/me", token)
    if not isinstance(me, dict):
        raise RuntimeError("Mercado Libre users/me 返回异常")
    user_id = str(me.get("id") or "").strip()
    if user_id:
        store["user_id"] = user_id
        store["seller_id"] = user_id
        save_store_config(config)
    return user_id


def _order_resource_url(resource: str) -> str:
    text = str(resource or "").strip()
    if text.startswith("https://api.mercadolibre.com/"):
        return text
    if text.startswith("/"):
        return f"https://api.mercadolibre.com{text}"
    if text.startswith("orders/") or text.startswith("orders?") or text.startswith("orders/search"):
        return f"https://api.mercadolibre.com/{text}"
    raise ValueError("Mercado Libre notification resource 不属于订单 API")


def _normalize_order_item(value: Any) -> dict[str, str]:
    record = value if isinstance(value, dict) else {}
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    return {
        "item_id": str(item.get("id") or record.get("item_id") or "").strip(),
        "title": str(item.get("title") or record.get("title") or "").strip(),
        "seller_sku": str(item.get("seller_sku") or item.get("seller_custom_field") or "").strip(),
        "quantity": str(record.get("quantity") or ""),
    }


def mercadolibre_order_summary(order: dict[str, Any]) -> dict[str, Any]:
    order_items = [_normalize_order_item(item) for item in order.get("order_items") or []]
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
    payments = order.get("payments") if isinstance(order.get("payments"), list) else []
    payment_statuses = [
        str(item.get("status") or "").strip()
        for item in payments
        if isinstance(item, dict) and str(item.get("status") or "").strip()
    ]
    return {
        "id": str(order.get("id") or order.get("order_id") or "").strip(),
        "status": str(order.get("status") or "").strip(),
        "status_detail": str(order.get("status_detail") or "").strip(),
        "date_created": str(order.get("date_created") or "").strip(),
        "date_closed": str(order.get("date_closed") or "").strip(),
        "last_updated": str(order.get("last_updated") or "").strip(),
        "total_amount": order.get("total_amount") or 0,
        "paid_amount": order.get("paid_amount") or 0,
        "currency_id": str(order.get("currency_id") or "").strip(),
        "buyer_id": str(buyer.get("id") or "").strip(),
        "buyer_nickname": str(buyer.get("nickname") or "").strip(),
        "shipping_id": str(shipping.get("id") or "").strip(),
        "shipping_status": str(shipping.get("status") or "").strip(),
        "payment_statuses": payment_statuses,
        "items": order_items,
        "item_titles": [item["title"] for item in order_items if item.get("title")],
        "item_ids": [item["item_id"] for item in order_items if item.get("item_id")],
        "raw": order,
    }


def _orders_from_search_response(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        raw_results = value.get("results")
        if isinstance(raw_results, list):
            return [item for item in raw_results if isinstance(item, dict)]
        if isinstance(value.get("orders"), list):
            return [item for item in value["orders"] if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def mercadolibre_recent_orders(limit: int = 10, offset: int = 0) -> dict[str, Any]:
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        return {
            "ok": False,
            "error": auth.get("message") or "Mercado Libre 授权不可用",
            "error_code": auth.get("error_code") or "AUTH_INVALID",
            "next_action": auth.get("next_action") or "请先完成授权测试",
            "items": [],
            "notifications": load_mercadolibre_order_notifications(),
        }
    token = str(auth.get("token") or "").strip()
    user_id = _mercadolibre_user_id(config, token)
    if not user_id:
        raise RuntimeError("Mercado Libre seller id 为空，请先测试授权。")
    page_size = max(1, min(int(limit or 10), 50))
    current_offset = max(0, int(offset or 0))
    query = urllib.parse.urlencode({"seller": user_id, "limit": page_size, "offset": current_offset})
    response = publisher.request_json("GET", f"https://api.mercadolibre.com/orders/search/recent?{query}", token)
    paging = response.get("paging") if isinstance(response, dict) and isinstance(response.get("paging"), dict) else {}
    return {
        "ok": True,
        "platform": "mercadolibre",
        "user_id": user_id,
        "items": [mercadolibre_order_summary(item) for item in _orders_from_search_response(response)],
        "notifications": load_mercadolibre_order_notifications(),
        "pagination": {
            "limit": page_size,
            "offset": current_offset,
            "total": int(paging.get("total") or 0),
        },
        "checked_at": collect_time_iso(),
    }


def fetch_mercadolibre_order_resource(resource: str) -> dict[str, Any]:
    config = load_store_config()
    auth = ensure_mercadolibre_auth_ready(config)
    if not auth.get("ok"):
        raise RuntimeError(str(auth.get("message") or "Mercado Libre 授权不可用"))
    token = str(auth.get("token") or "").strip()
    order = publisher.request_json("GET", _order_resource_url(resource), token)
    if not isinstance(order, dict):
        raise RuntimeError("Mercado Libre order resource 返回异常")
    return mercadolibre_order_summary(order)


def record_mercadolibre_order_notification(body: dict[str, Any]) -> dict[str, Any]:
    topic = str(body.get("topic") or body.get("type") or "").strip()
    resource = str(body.get("resource") or "").strip()
    notification = {
        "topic": topic,
        "resource": resource,
        "user_id": str(body.get("user_id") or body.get("userId") or "").strip(),
        "application_id": str(body.get("application_id") or body.get("applicationId") or "").strip(),
        "attempts": body.get("attempts") or 0,
        "sent": str(body.get("sent") or "").strip(),
        "received_at": collect_time_iso(),
        "raw": body,
    }
    order: dict[str, Any] | None = None
    error = ""
    if topic == "orders_v2" and resource:
        try:
            order = fetch_mercadolibre_order_resource(resource)
        except Exception as exc:
            error = str(exc)
    if order:
        notification["order"] = order
        notification["order_id"] = str(order.get("id") or "")
    if error:
        notification["error"] = error
    notifications = _load_order_notifications()
    notifications.insert(0, notification)
    _save_order_notifications(notifications)
    return {"ok": not bool(error), "notification": notification, "order": order, "error": error}


__all__ = [
    "fetch_mercadolibre_order_resource",
    "load_mercadolibre_order_notifications",
    "mercadolibre_order_summary",
    "mercadolibre_recent_orders",
    "record_mercadolibre_order_notification",
]
