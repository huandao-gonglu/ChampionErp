"""地址备注仅访问本地订单库，店铺和原地址由订单快照确定。"""

from erp_web.context import get_context
from erp_web.schemas.order_address_notes import AddressNoteWrite
from erp_web.stores.order_address_note_store import OrderAddressNoteStore


def read(order_id="", shipment_id="", address=""):
    orders = get_context().order_notifications
    return OrderAddressNoteStore(orders.store).read(
        order_id, shipment_id, orders.accounts(), address=address,
    ).model_dump(mode="json")


def save(body):
    request = AddressNoteWrite.model_validate(body)
    orders = get_context().order_notifications
    return OrderAddressNoteStore(orders.store).save(request, orders.accounts()).model_dump(mode="json")
