"""销售订单的 1688 采购装配。"""
from erp_web.context import get_context
from erp_web.services.alibaba_self_purchase_service import AlibabaSelfPurchaseService
from erp_web.stores.order_address_note_store import OrderAddressNoteStore


def create_service(context):
    return AlibabaSelfPurchaseService(context.alibaba_self_purchases,
        lambda: context.config.load_app_config().get("1688_api", {}), context.order_procurement,
        address_notes=OrderAddressNoteStore(context.order_notifications.store))


def options(query):
    return create_service(get_context()).options(str(query.get("order_id") or ""), str(query.get("line_key") or ""))


def addresses(query):
    return create_service(get_context()).addresses(str(query.get("order_id") or ""), str(query.get("line_key") or ""))


def command(action, body):
    service = create_service(get_context())
    return {"preview": service.preview, "parse-address": service.parse_address, "create": service.create,
            "reconcile": service.reconcile, "cashier": service.cashier}[action](body)
