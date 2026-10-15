"""实际包裹与商品分配分离：同运单不重复报包裹，数量、来源和原图不丢失。"""

from copy import deepcopy

import pytest

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.schemas.order_procurement import order_line_key
from erp_web.schemas.orders import OrderLine
from erp_web.services.fulfillment_packages import group_domestic_packages
from tests.test_fulfillment import command, domain


def item(sku, *, waybill='YT123', quantity=1, platform=1, purchase='PO-123', image=''):
    return {'package_of': 1, 'num': quantity, 'describe': f'{sku} · 商品 · 银色',
            'img': image, 'logistics_order': waybill, 'from_platform': platform, 'from_order': purchase}


def test_grouping_preserves_quantities_details_images_and_input():
    items = [item('SKU-1', waybill=' yt123 ', quantity=2),
             item('SKU-2', image='https://host.example/2.jpg'),
             item('SKU-3', image='https://host.example/3.jpg')]
    before = deepcopy(items)
    package, = group_domestic_packages(items)
    assert items == before
    assert package['logistics_order'] == 'YT123'
    assert package['num'] == 4
    assert package['from_order'] == 'PO-123'
    assert package['from_platform'] == 1
    assert package['img'] == 'https://host.example/2.jpg'
    descriptions = package['describe'].splitlines()
    assert len(descriptions) == 3
    for source, description in zip(items, descriptions):
        assert source['describe'] in description
        assert f"数量：{source['num']} 件" in description
        assert source['img'] in description


@pytest.mark.parametrize('platform,purchase', [(1, 'PO-456'), (2, 'PO-123'), (2, 'PO-456')])
def test_shared_waybill_with_mixed_sources_does_not_invent_purchase_number(platform, purchase):
    package, = group_domestic_packages([item('SKU-1'), item('SKU-2', platform=platform, purchase=purchase)])
    assert package['num'] == 2
    assert 'from_order' not in package
    assert ('from_platform' in package) == (platform == 1)
    assert '采购单号：PO-123' in package['describe']
    assert f'采购单号：{purchase}' in package['describe']
    assert f"采购平台：{ {1: '1688', 2: '淘宝'}[platform]}" in package['describe']


def test_one_purchase_split_across_waybills_stays_two_packages():
    items = [item('SKU-1', quantity=2), item('SKU-1', waybill='SF456', quantity=3)]
    assert group_domestic_packages(items) == items
    assert group_domestic_packages([]) == []


@pytest.fixture
def shared_waybill(domain):
    service, bus, order, parcel, snapshot, _ = domain
    procurement = service.detail_provider.__self__
    added = [OrderLine(sku=f'SKU-{i}', remote_id=f'SKU-{i}', title=f'商品 {i}', quantity=1) for i in (2, 3)]
    order = order.model_copy(update={'items': [*order.items, *added]})
    snapshot(order)
    procurement.yandex_images_provider = lambda account, ids: {sku: f'https://platform.example/{sku}.jpg' for sku in ids}
    source = procurement.detail(order.identity)['lines'][0]['selection']['source']
    parcels = [parcel]
    for line in added:
        key = order_line_key(line)
        procurement.select_source({'order_id': order.identity, 'line_key': key, 'revision': 0,
                                  'source': {**source, 'source_sku_id': line.sku}})
        detail = procurement.record_purchase({'order_id': order.identity, 'line_key': key, 'revision': 1,
            'request_id': line.sku, 'purchase_order_number': 'PO-123', 'quantity': 1,
            'receiving_revision': 1, 'receiving_confirmed': True})
        record = next(row['records'][0] for row in detail['lines'] if row['selection']['line_key'] == key)
        parcels.append({**parcel, 'id': line.sku, 'line_key': key, 'purchase_record_id': record['id'], 'quantity': 1})
    command(service, order, 'label', label={'url': 'https://example.com/label.pdf', 'tracking_number': 'RU123'})
    command(service, order, 'parcels', parcels=parcels)
    return service, bus, order, parcels


def test_create_and_explicit_update_send_one_package_for_three_skus(shared_waybill):
    service, bus, order, parcels = shared_waybill
    delivered = []

    def deliver(url):
        delivered.append(url)
        return url.replace('platform.example', 'host.example')

    service.image_delivery = deliver
    assert not service.detail(order.identity)['blocked_reason']
    assert service.process_one(order)
    created = next(body['order_data'][0] for path, body in bus.calls if path.endswith('createOrder'))
    package, = created['package_list']
    assert package['num'] == 4
    assert package['logistics_order'] == 'SF123'
    assert package['from_order'] == 'PO-123'
    assert created['add_service'] == [{'id': 2}, {'id': 3}]
    assert len(delivered) == 3
    for sku in ('SKU-1', 'SKU-2', 'SKU-3'):
        assert sku in package['describe']
        assert f'https://host.example/{sku}.jpg' in package['describe']
    assert package['img'] == 'https://host.example/SKU-1.jpg'
    assert service.detail(order.identity)['parcels'] == parcels
    # 只有显式编辑包裹才更新远端；页面读取不改写历史报单。
    calls = len(bus.calls)
    service.detail(order.identity)
    assert len(bus.calls) == calls
    command(service, order, 'parcels', parcels=parcels)
    assert service.process_one(order)
    updated = next(body for path, body in bus.calls if path.endswith('updateOrderPackage'))
    assert updated['package_list'] == created['package_list']


def test_non_cover_image_failure_also_blocks_create(shared_waybill):
    service, bus, order, _ = shared_waybill

    def deliver(url):
        if 'SKU-2' in url:
            raise FulfillmentError('商品图片托管失败')
        return url.replace('platform.example', 'host.example')

    service.image_delivery = deliver
    service.process_one(order)
    assert not any(path.endswith('createOrder') for path, _ in bus.calls)
    assert not service.store.get(order.identity)['create_unknown']
