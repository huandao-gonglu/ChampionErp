"""采购收货仓测试装配，所有远端仓库调用均为替身。"""
from erp_web.schemas.receiving_warehouse import delivery_key
from erp_web.schemas.order_handover import OrderHandoverSnapshot
from erp_web.schemas.orders import OrderSnapshot
from erp_web.services.receiving_warehouse_service import ReceivingWarehouseService
from erp_web.stores.fulfillment_store import FulfillmentStore

ADDRESS = {'fullName': '仓库收件人', 'mobile': '13800000000', 'phone': '', 'provinceText': '浙江省', 'cityText': '义乌市', 'areaText': '义乌市', 'townText': '', 'address': '仓库路 1 号', 'postCode': ''}


class WarehouseBus:
    def __init__(self, store):
        self.store = store
        self.account = 'bus-a'

    def identity(self):
        return self.account

    def catalog(self):
        value = {'identity': self.account, 'sections': [{'section_id': 1, 'section_name': 'Yandex', 'storehouse_list': [{'id': 10, 'name': 'A仓'}, {'id': 20, 'name': 'B仓'}]}]}
        self.store.set_setting('catalog', value)
        return value


def receiving_service(procurement, bus=None):
    store = FulfillmentStore(procurement.store.orders)
    return ReceivingWarehouseService(store.receiving, bus or WarehouseBus(store), procurement.accounts_provider, procurement.detail)


def assign_receiving(service, order, **overrides):
    if not order.handover:
        order.handover = OrderHandoverSnapshot(state='ready', shipments=[{'shipment_id': 'test-import', 'shipment_type': 'IMPORT', 'destination': {'id': 'cel-yiwu', 'name': 'CEL Yiwu'}}])
        with service.store.orders.connect() as conn:
            row = conn.execute('SELECT snapshot FROM orders WHERE id=?', (order.identity,)).fetchone()
            latest = OrderSnapshot.model_validate_json(row[0])
            if not latest.handover:
                latest.handover = order.handover
                conn.execute('UPDATE orders SET snapshot=? WHERE id=?', (latest.model_dump_json(), order.identity))
                conn.commit()
    warehouse_id = overrides.pop('warehouse_id', 10)
    address = overrides.pop('address', ADDRESS)
    overrides.pop('transport_service', None)
    current = service.detail(order.identity)
    carrier = overrides.pop('carrier', current['carrier_detected'] or 'CEL')
    configurations = service.logistics.store.list(service.client.identity())
    configured = next((a for a in configurations if a['warehouse_id'] == warehouse_id and a['address'] == address), None)
    if not configured:
        settings = service.logistics.save({'revision': 0, 'warehouse_id': warehouse_id, 'name': '测试收货地址', 'address': address,
            'rules': [{'platform': 'yandex', 'carrier': carrier}]})
        configured = next(a for a in settings['addresses'] if a['warehouse_id'] == warehouse_id and a['address'] == address)
    return service.save({'order_id': order.identity, 'revision': current['revision'], 'section_id': 1,
                         'address_id': configured['id'], 'address_revision': configured['revision'],
                         'delivery_key': delivery_key(order), 'confirmed': True, 'existing_purchase_confirmed': True, 'fulfillment_revision': current.get('fulfillment_revision'), **overrides})
