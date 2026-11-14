"""空库存不等于禁止写入；库存变更仍须指定仓库并以平台回读确认。"""
from copy import deepcopy
import time
from uuid import uuid4

import pytest

from erp_web.runtime_units import online_yandex
from erp_web.schemas.online_products import ChangeRequest
from tests.test_yandex_online_sync import Platform, adapter, service_for


class StockPlatform(Platform):
    def __init__(self, quantity):
        super().__init__(2)
        self.quantity = quantity
        self.omit_offer = False

    def request(self, path, body=None, **kwargs):
        if path.endswith('/stocks'):
            assert body['partnerWarehouseId'] == 2412479
            stocks = [] if self.quantity is None else [{'type': 'AVAILABLE', 'count': self.quantity}]
            offers = [] if self.omit_offer else [{'offerId': sku, 'stocks': stocks} for sku in body['offerIds']]
            return {'result': {'partnerWarehouseId': 2412479, 'offers': offers}}
        return super().request(path, body, **kwargs)


def stock_adapter(platform, *, availability='AVAILABLE', placement='FBS'):
    result = adapter(platform)
    result.mode = 'business'
    result.warehouses = [{'id': 2412479, 'name': 'GUOO-Economy',
                         'models': [{'apiAvailability': availability, 'placementType': placement}]}]
    return result


def stock_request(listing, quantity):
    return ChangeRequest(listing_id=listing.id, version=listing.version, operation='stock',
                         scope_id='2412479', changes={'quantity': quantity}, idempotency_key=uuid4().hex)


@pytest.mark.parametrize('quantity,omit_offer', [(None, False), (None, True), (0, False)])
def test_empty_and_zero_stock_keep_seller_warehouse_writable(quantity, omit_offer):
    platform = StockPlatform(quantity)
    platform.omit_offer = omit_offer
    listing = stock_adapter(platform).read('sku-0')
    assert listing.stocks[0].quantity is quantity
    assert listing.stocks[0].writable
    assert listing.capabilities['stock'].enabled


@pytest.mark.parametrize('availability,placement', [('UNAVAILABLE', 'FBS'), ('AVAILABLE', 'FBY')])
@pytest.mark.parametrize('quantity', [None, 0])
def test_non_writable_warehouses_still_reject_stock_changes(availability, placement, quantity):
    a = stock_adapter(StockPlatform(quantity), availability=availability, placement=placement)
    service = service_for(a)
    listing = service.store.save(a.read('sku-0'))
    assert not listing.stocks[0].writable
    assert listing.stocks[0].reason
    with pytest.raises(ValueError):
        service.change(stock_request(listing, 10).model_dump())
    assert not service.store.jobs('yandex', a.account_id)


@pytest.mark.parametrize('initial,target', [(None, 10), (0, 10), (None, 0)])
def test_stock_write_targets_selected_warehouse_and_finishes_on_success_receipt(monkeypatch, initial, target):
    platform = StockPlatform(initial)
    a = stock_adapter(platform)
    service = service_for(a)
    listing = service.store.save(a.read('sku-0'))
    calls = []

    def write(method, path, token, payload, **kwargs):
        calls.append((method, path, token, deepcopy(payload)))
        return {'status': 'OK'}

    monkeypatch.setattr(online_yandex.api, 'request_yandex_json', write)
    job = service.change(stock_request(listing, target).model_dump())['job']
    service.run_once()
    assert len(calls) == 1
    method, path, token, payload = calls[0]
    assert method == 'POST'
    assert token == 'test'
    assert path == '/v3/businesses/1/offers/stocks/update'
    assert payload == {'skuItems': [{'sku': 'sku-0', 'partnerWarehouseId': 2412479, 'count': target}]}
    assert service.store.job(job['id'])['status'] == 'confirmed'
    # 成功回执更新目标仓，无需额外平台回读。
    saved = service.store.get(listing.id)
    assert saved.stocks[0].quantity == target and saved.stocks[0].observed_at
    assert saved.sales_checked_at == listing.sales_checked_at
    with pytest.raises(ValueError):
        service.reconcile_once(job['id'])
    assert len(calls) == 1
