"""物流设置：合作仓同步、地址维护和可复用承接规则。"""
from erp_web.schemas.logistics import CARRIERS, LogisticsAddressRequest
from erp_web.schemas.orders import utc_iso
from erp_web.services.fulfillment_routing import is_yandex_section
from erp_web.stores.logistics_store import LogisticsStore


class LogisticsSettingsService:
    def __init__(self, orders, client):
        self.store = LogisticsStore(orders)
        self.client = client

    def catalog(self):
        catalog = self.client.store.setting('catalog', {})
        return catalog if catalog.get('identity') == self.client.identity() else {'sections': []}

    def detail(self, refresh=False):
        if refresh:
            self.client.catalog()
        catalog = self.catalog()
        warehouses = {}
        for section in catalog.get('sections', []):
            for warehouse in section['storehouse_list']:
                entry = warehouses.setdefault(warehouse['id'], {**warehouse, 'sections': []})
                entry['sections'].append({'id': section['section_id'], 'name': section['section_name'], 'platform': 'yandex' if is_yandex_section(section) else ''})
        return {'ok': True, 'authorized': bool(self.client.identity()), 'warehouses': list(warehouses.values()),
                'addresses': self.store.list(self.client.identity()),
                'carriers': [{'value': k, 'label': v, 'platform': 'yandex'} for k, v in CARRIERS.items()]}

    def save(self, body):
        request = LogisticsAddressRequest.model_validate(body)
        identity = self.client.identity()
        if not identity:
            raise ValueError('请先完成跨境巴士授权')
        detail = self.detail(refresh=True)
        warehouse = next((w for w in detail['warehouses'] if w['id'] == request.warehouse_id), None)
        old = next((a for a in detail['addresses'] if a['id'] == request.id), None)
        if not warehouse and (request.enabled or not old or request.warehouse_id != old['warehouse_id']):
            raise ValueError('仓库不在当前账号的合作范围内')
        if request.rules and warehouse and not any(s['platform'] == 'yandex' for s in warehouse['sections']):
            raise ValueError('此合作仓未开通 Yandex 平台')
        self.store.save(request, identity, warehouse['name'] if warehouse else old['warehouse_name'], utc_iso(), self.client.identity)
        return self.detail()
