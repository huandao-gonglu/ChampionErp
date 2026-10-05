"""订单通知装配及 HTTP/AI 共用的有界本地读取入口。"""

from __future__ import annotations

from erp_web.context import get_context
from erp_web.runtime_units.order_notifications import parse_notification
from erp_web.runtime_units.orders_mercadolibre import MercadoLibreOrderAdapter
from erp_web.runtime_units.orders_ozon import OzonOrderAdapter
from erp_web.runtime_units.orders_yandex import YandexOrderAdapter
from erp_web.schemas.orders import PLATFORMS, OrderView
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.stores.order_notification_migration import import_historical_notifications
from erp_web.stores.order_notification_store import OrderNotificationStore


def create_order_notification_service(context, *, start_worker=True):
    store = OrderNotificationStore(
        context.paths.data_dir / "order-notifications.sqlite3"
    )
    import_historical_notifications(store, context.paths.db_path)
    return OrderNotificationService(
        store,
        context.config.load_store_config,
        adapters={
            "mercadolibre": MercadoLibreOrderAdapter,
            "ozon": OzonOrderAdapter,
            "yandex": YandexOrderAdapter,
        },
        parser=parse_notification,
        start_worker=start_worker,
    )


def read_orders(*, platform="", state="", limit=50, offset=0, q=""):
    if platform and platform not in PLATFORMS:
        raise ValueError("不支持的平台")
    if state and state not in {
        "pending_shipment",
        "processing",
        "shipped",
        "delivered",
        "cancelled",
        "unknown",
    }:
        raise ValueError("订单状态无效")
    service = get_context().order_notifications
    result = service.store.read(
        service.accounts(),
        platform=platform,
        state=state,
        limit=max(1, min(int(limit), 100)),
        offset=max(0, int(offset)),
        query=str(q).strip()[:200],
    )
    procurement = get_context().order_procurement
    for row in result["items"]:
        row["procurement_status"] = procurement.store.progress(
            OrderView.model_validate(row)
        )
    result["pagination"] = {
        "limit": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "total": result["total"],
    }
    return result


def summary():
    service = get_context().order_notifications
    return service.store.summary(service.accounts())


def receive_notification(platform, token, body):
    return get_context().order_notifications.receive(platform, token, body)


def integrations():
    return get_context().order_notifications.integrations()


def command(action, body):
    service = get_context().order_notifications
    if action == "sync":
        platform = str(body.get("platform") or "")
        if platform and platform not in PLATFORMS:
            raise ValueError("不支持的平台")
        return service.sync(platform)
    if action == "configure":
        return service.configure(str(body.get("public_url") or ""))
    if action == "retry":
        service.store.retry(int(body.get("event_id") or 0), service.accounts())
    elif action == "acknowledge":
        service.store.acknowledge(int(body.get("through_id") or 0), service.accounts())
    else:
        raise ValueError("不支持的订单通知操作")
    return {"ok": True}
