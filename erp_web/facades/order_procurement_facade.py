"""订单采购装配；复用已授权店铺身份和唯一订单领域库。"""

from erp_web.context import get_context
from erp_web.runtime_units.order_source_bindings import bindings_from_publish_job
from erp_web.runtime_units.publish_confirmation import resolve_publish_store_binding
from erp_web.services.order_procurement_service import OrderProcurementService
from erp_web.stores.order_procurement_store import OrderProcurementStore


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

    return OrderProcurementService(store, orders.accounts, identity)


def detail(order_id=""):
    return get_context().order_procurement.detail(order_id)


def command(action, body):
    service = get_context().order_procurement
    return {
        "select-source": service.select_source,
        "record-purchase": service.record_purchase,
        "cancel-purchase": service.cancel_purchase,
    }[action](body)
