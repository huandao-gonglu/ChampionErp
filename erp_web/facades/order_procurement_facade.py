"""订单采购装配；复用已授权店铺身份和唯一订单领域库。"""

import logging

from erp_web.context import get_context
from erp_web.services.alibaba_purchase_sync_service import sync_purchase as sync_alibaba_purchase
from erp_web.services.alibaba_purchase_assignment import supported
from erp_web.schemas.order_procurement import PurchaseRecord
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from erp_web.runtime_units.publish_confirmation import resolve_publish_store_binding
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.stores.order_procurement_store import OrderProcurementStore
from erp_web.stores.online_product_store import OnlineProductStore
from erp_web.services.alibaba_purchase_query_service import query_purchase as query_alibaba_purchase


def create_service(context):
    orders = context.order_notifications
    store = OrderProcurementStore(orders.store)
    # 历史发布任务已有冻结商品和受信店铺身份，幂等回填，不依赖可编辑草稿。
    cursor = ""
    while True:
        jobs, cursor = context.db.list_publish_jobs(limit=100, cursor=cursor)
        for job in jobs:
            store.add_bindings(bindings_from_publish_job(job))
        if not cursor:
            break

    def identity(platform):
        try:
            return resolve_publish_store_binding(
                platform, context.config.load_store_config()
            ).identity
        except ValueError:
            return ""

    online = OnlineProductStore(context.db)

    def yandex_images(account_id, offer_ids):
        config = context.config.load_store_config().get("yandex") or {}
        business = str(config.get("business_id") or "")
        campaign = str(config.get("campaign_id") or "")
        if not business or campaign != account_id:
            return {}
        return online.thumbnails("yandex", f"{business}:{campaign}", offer_ids)

    return OrderProcurementService(store, orders.accounts, identity, yandex_images_provider=yandex_images)


def detail(order_id=""):
    return get_context().order_procurement.detail(order_id)


def query_purchase(body):
    context = get_context()
    return query_alibaba_purchase(
        context.order_procurement, context.config.load_app_config().get("1688_api", {}), body,
    )


def sync_purchase(body, *, once=False):
    context = get_context()
    return sync_alibaba_purchase(context.order_procurement, context.fulfillment,
                                lambda: context.config.load_app_config().get("1688_api", {}), body, once=once)


def command(action, body):
    service = get_context().order_procurement
    result = {
        "select-source": service.select_source,
        "record-purchase": service.record_purchase,
        "cancel-purchase": service.cancel_purchase,
    }[action](body)
    if action == "record-purchase":
        record = next((r for line in result["lines"] for r in line["records"] if r["request_id"] == body["request_id"]), None)
        if record and supported(PurchaseRecord.model_validate(record)):
            try:
                return sync_purchase({"order_id": body["order_id"], "record_id": record["id"]}, once=True)
            except Exception:
                logging.getLogger(__name__).error("采购登记已保存，自动同步未完成")
                return service.detail(body["order_id"])
    return result
