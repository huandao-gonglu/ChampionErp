"""销售订单的 1688 采购装配。"""
import logging

from erp_web.context import get_context
from erp_web.services.alibaba_self_purchase_service import AlibabaSelfPurchaseService, account_scope
from erp_web.stores.order_address_note_store import OrderAddressNoteStore
from erp_web.services.alibaba_purchase_sync_service import sync_purchases


def create_service(context):
    return AlibabaSelfPurchaseService(context.alibaba_self_purchases,
        lambda: context.config.load_app_config().get("1688_api", {}), context.order_procurement,
        address_notes=OrderAddressNoteStore(context.order_notifications.store), receiving=context.fulfillment.receiving)


def options(query):
    return create_service(get_context()).options(str(query.get("order_id") or ""), str(query.get("line_key") or ""))


def addresses(query):
    return create_service(get_context()).addresses(str(query.get("order_id") or ""), str(query.get("line_key") or ""))


def batch_options(query):
    return create_service(get_context()).batch_options(str(query.get('order_id') or ''))


def command(action, body):
    context = get_context()
    service = create_service(context)
    result = {"batch-preview": service.batch_preview, "preview": service.preview, "parse-address": service.parse_address, "create": service.create,
            "reconcile": service.reconcile, "cashier": service.cashier,
            "payment-status": service.payment_status, "pay": service.pay}[action](body)
    record = result.get('record') or {}
    if action in {'create', 'reconcile'} and record.get('state') == 'created' and record.get('purchase_record_ids'):
        try:
            order_id = service.store.get(record['id'], account_scope(service.config_provider()))['payload']['binding']['order_id']
            sync_purchases(context.order_procurement, context.fulfillment, service.config_provider,
                           order_id, record['purchase_record_ids'], once=action == 'create')
        except Exception:
            logging.getLogger(__name__).error('批量采购已回填，采购进度同步未完成')
    return result
