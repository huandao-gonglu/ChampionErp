"""订单采购装配；复用已授权店铺身份和唯一订单领域库。"""

import logging

from erp_web.services.alibaba_authorization_service import alibaba_runtime_config
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

    def identity(platform, account_id=""):
        try:
            config = context.config.load_store_config()
            if platform == "yandex" and account_id:
                config = {**config, "yandex": {**config["yandex"], "campaign_id": account_id}}
            return resolve_publish_store_binding(
                platform, config
            ).identity
        except ValueError:
            return ""

    online = OnlineProductStore(context.db)

    def yandex_images(account_id, offer_ids):
        config = context.config.load_store_config().get("yandex") or {}
        business = str(config.get("business_id") or "")
        from erp_web.schemas.orders import account_in_scope
        if not business or not account_in_scope(orders.accounts(), "yandex", account_id):
            return {}
        # 在线目录缓存以企业和所选发布店铺标识；商品图片本身属于企业商品。
        campaign = str(config.get("campaign_id") or "")
        return online.thumbnails("yandex", f"{business}:{campaign}", offer_ids)

    return OrderProcurementService(store, orders.accounts, identity, yandex_images_provider=yandex_images)


def detail(order_id=""):
    context = get_context()
    result = context.order_procurement.detail(order_id)
    order = result["order"]
    order["shop_name"] = next((shop.name for shop in context.order_notifications.shops()
                               if shop.platform == order["platform"] and shop.account_id == order["account_id"]),
                              order["account_id"])
    return result


def query_purchase(body, *, context=None):
    context = context or get_context()
    return query_alibaba_purchase(
        context.order_procurement, alibaba_runtime_config(context.config), body,
    )


def sync_purchase(body, *, once=False, context=None):
    context = context or get_context()
    return sync_alibaba_purchase(context.order_procurement, context.fulfillment,
                                lambda: alibaba_runtime_config(context.config), body, once=once)


def command(action, body, *, context=None):
    context = context or get_context()
    service = context.order_procurement
    result = {
        "select-source": service.select_source,
        "record-purchase": service.record_purchase,
        "cancel-purchase": service.cancel_purchase,
    }[action](body)
    if action == "record-purchase":
        record = next((r for r in result["purchases"] if r["request_id"] == body["request_id"]), None)
        if record and supported(PurchaseRecord.model_validate(record)):
            try:
                return sync_purchase({"order_id": body["order_id"], "record_id": record["id"]}, once=True, context=context)
            except Exception:
                logging.getLogger(__name__).error("采购登记已保存，自动同步未完成")
                return service.detail(body["order_id"])
    return result
