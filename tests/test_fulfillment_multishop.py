"""多店铺履约查询覆盖地址设置和后台扫描使用的真实数据库范围。"""

import pytest

from erp_web.schemas.orders import OrderSnapshot
from erp_web.stores.fulfillment_store import FulfillmentStore
from erp_web.stores.order_notification_store import OrderNotificationStore
from tests.test_fulfillment import domain


@pytest.mark.parametrize(('accounts', 'expected'), [
    ({'yandex': '4'}, {('yandex', '4')}),
    ({'yandex': ('4',)}, {('yandex', '4')}),
    ({'yandex': ('4', '6')}, {('yandex', '4'), ('yandex', '6')}),
    ({'yandex': ('4', '6'), 'ozon': '4'}, {('yandex', '4'), ('yandex', '6'), ('ozon', '4')}),
    ({'yandex': ()}, set()),
    ({}, set()),
])
def test_snapshots_read_exact_platform_and_shop_scope(tmp_path, accounts, expected):
    orders = OrderNotificationStore(tmp_path / 'orders.sqlite3')
    store = FulfillmentStore(orders)
    with orders.connect() as conn:
        for platform, account in [('yandex', '4'), ('yandex', '6'), ('yandex', '99'), ('ozon', '4')]:
            order = OrderSnapshot(platform=platform, account_id=account, order_id='123',
                                  fulfillment='FBS', status='PROCESSING', state='pending_shipment')
            conn.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?)',
                         (order.identity, platform, account, order.state, order.model_dump_json(), 0, ''))
        conn.commit()

    result = store.snapshots(accounts)
    assert {(order.platform, order.account_id) for order in result} == expected
    assert len(result) == len(expected)


def test_warehouse_settings_include_all_authorized_shops_without_platform_writes(domain):
    service, bus, first, _, snapshot, accounts = domain
    for account in ['6', '99']:
        snapshot(first.model_copy(update={'account_id': account}))
    accounts['yandex'] = ('4', '6')

    settings = service.settings()

    assert settings['ok']
    assert {(row['platform'], row['account_id']) for row in settings['delivery_choices']} == {
        ('yandex', '4'), ('yandex', '6'),
    }
    assert [row['account_id'] for row in settings['rules']] == ['4']
    assert bus.calls == []
