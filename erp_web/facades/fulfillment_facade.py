"""跨境履约装配及 HTTP 入口；复用订单账号范围和采购事实。"""

from erp_web.context import get_context
from erp_web.schemas.fulfillment import BusCredentials, FulfillmentError, LabelUpload
from erp_web.services.fulfillment_label_service import deliver_label
from erp_web.services.crossborderbus_client import CrossborderBusClient
from erp_web.services.fulfillment_service import FulfillmentService
from erp_web.services.platform_label_service import PlatformLabelService
from erp_web.stores.fulfillment_store import FulfillmentStore


def create_service(context):
    orders = context.order_notifications
    store = FulfillmentStore(orders.store)
    labels = PlatformLabelService(context.config.load_store_config, context.config.load_app_config)
    return FulfillmentService(store, CrossborderBusClient(store), orders.accounts, lambda order_id: context.order_procurement.detail(order_id), label_provider=labels, purchase_progress=context.order_procurement.store.purchase_progress)


def settings():
    return get_context().fulfillment.settings()


def detail(order_id):
    return get_context().fulfillment.detail(order_id)


def services(section_id, warehouse_id):
    try:
        section, warehouse = int(section_id), int(warehouse_id)
    except (ValueError, TypeError):
        raise FulfillmentError("请选择有效的合作渠道和仓库。") from None
    if section <= 0 or warehouse <= 0:
        raise FulfillmentError("请选择有效的合作渠道和仓库。")
    return get_context().fulfillment.client.services(section, warehouse)


def command(action, body):
    service = get_context().fulfillment
    if action == "upload-label":
        request = LabelUpload.model_validate(body)
        current = service.detail(request.order_id)
        if not current["editable"] or current["revision"] != request.revision:
            raise FulfillmentError("履约资料已变化，请刷新订单后上传面单。")
        return deliver_label(get_context().config.load_app_config(), request.order_id, request.content_base64)
    if action == "authorize":
        request = BusCredentials.model_validate(body)
        service.client.authorize(request.client_secret, request.user_name, request.password)
        return service.settings()
    if action == "catalog":
        service.client.catalog()
        return service.settings()
    if action == "save-rule":
        return service.save_rule(body)
    if action == "delete-rule":
        allowed = {r["id"] for r in service.settings()["rules"]}
        if body["id"] not in allowed:
            raise ValueError("履约方案不存在或不属于当前账号。")
        service.store.delete_rule(body["id"])
        return service.settings()
    return service.command(action, body)
