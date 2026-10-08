"""订单通知装配及 HTTP/AI 共用的有界本地读取入口。"""

from __future__ import annotations

from erp_web.context import get_context
from erp_web.runtime_units.order_notifications import parse_notification, order_request_scopes
from erp_web.runtime_units.orders_mercadolibre import MercadoLibreOrderAdapter
from erp_web.runtime_units.orders_ozon import OzonOrderAdapter
from erp_web.runtime_units.orders_yandex import YandexOrderAdapter
from erp_web.schemas.orders import PLATFORMS, OrderView, OrderSyncStatus
from erp_web.services.order_notification_service import OrderNotificationService
from erp_web.services.order_progress_sync_service import sync_order_progress
from erp_web.stores.order_notification_migration import import_historical_notifications
from erp_web.stores.order_notification_store import OrderNotificationStore


def create_order_notification_service(context, *, start_worker=True):
    store = OrderNotificationStore(
        context.paths.data_dir / "order-notifications.sqlite3"
    )
    import_historical_notifications(store, context.paths.db_path)
    def refresh_progress(order_id, checkpoint):
        return sync_order_progress(context.order_procurement, context.fulfillment,
                                   lambda: context.config.load_app_config().get("1688_api", {}),
                                   order_id, checkpoint, cancel=context.order_notifications.stop_event)

    service = OrderNotificationService(
        store,
        context.config.load_store_config,
        adapters={
            "mercadolibre": MercadoLibreOrderAdapter,
            "ozon": OzonOrderAdapter,
            "yandex": YandexOrderAdapter,
        },
        parser=parse_notification,
        external_store=context.external_requests.store,
        request_scopes=order_request_scopes,
        progress_sync=refresh_progress,
        auto_sync_interval_provider=lambda: context.config.system_settings()["orders_auto_sync_interval_hours"] * 3600,
        start_worker=start_worker,
    )
    return service


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
    orders = procurement.present_orders(
        [OrderView.model_validate(row) for row in result["items"]]
    )
    fulfillment_summaries = get_context().fulfillment.store.summaries(orders)
    result["items"] = [
        {
            **order.model_dump(mode="json"),
            "procurement_status": procurement.store.progress(order),
            "fulfillment_summary": fulfillment_summaries.get(order.id),
        }
        for order in orders
    ]
    result["sync_status"] = [row.model_dump(mode="json") for row in _sync_status(service)]
    result["pagination"] = {
        "limit": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "total": result["total"],
    }
    return result


def _sync_status(service):
    return [OrderSyncStatus.model_validate(row) for row in service.sync_status()]


def summary():
    service = get_context().order_notifications
    result = service.store.summary(service.accounts())
    result["sync_status"] = [row.model_dump(mode="json") for row in _sync_status(service)]
    result["attention_count"] = sum(row["status"] in {"failed", "retry", "blocked", "cooldown"} for row in result["sync_status"])
    return result


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
        return service.sync(platform, automatic=body.get("automatic", False))
    if action == "configure":
        return service.configure(str(body.get("public_url") or ""))
    if action == "retry":
        service.store.retry(int(body.get("event_id") or 0), service.accounts())
    elif action == "acknowledge":
        service.store.acknowledge(int(body.get("through_id") or 0), service.accounts())
    else:
        raise ValueError("不支持的订单通知操作")
    return {"ok": True}
