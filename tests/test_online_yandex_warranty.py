"""Yandex 在线保修期限：现有商品的局部更新、并发保护与真实回读。"""
from copy import deepcopy
from uuid import uuid4

import pytest

from erp_web.runtime_units import online_yandex
from erp_web.runtime_units.online_change_confirmation import yandex_change
from erp_web.schemas.online_products import ChangeRequest, OnlineListing, snapshot_version
from erp_web.services.online_product_changes import confirmation, validate_changes
from tests.test_yandex_online_sync import Platform, adapter, service_for

PERIOD = {"timePeriod": 12, "timeUnit": "MONTH", "comment": "Гарантия продавца"}


def request_for(listing, period=PERIOD):
    return ChangeRequest(listing_id=listing.id, version=listing.version or snapshot_version(listing), operation="content",
                         changes={"guarantee_period": deepcopy(period)}, idempotency_key=uuid4().hex)


def test_sync_and_old_snapshots_expose_warranty_without_changing_empty_versions():
    platform = Platform(2)
    a = adapter(platform)
    empty = a.read('sku-0')
    old = empty.model_dump()
    old['content'].pop('guarantee_period')
    old['capabilities']['content']['fields'].remove('guarantee_period')
    restored = OnlineListing.model_validate(old)
    assert 'guarantee_period' in restored.capabilities['content'].fields
    assert restored.content['guarantee_period'] == {}
    assert snapshot_version(restored) == snapshot_version(empty)
    platform.rows[0]['offer']['guaranteePeriod'] = deepcopy(PERIOD)
    current = a.read('sku-0')
    assert current.content['guarantee_period'] == PERIOD
    assert snapshot_version(current) != snapshot_version(empty)
    old['snapshot']['offer']['guaranteePeriod'] = deepcopy(PERIOD)
    assert OnlineListing.model_validate(old).content['guarantee_period'] == PERIOD


@pytest.mark.parametrize('period', [None, {}, {'timePeriod': 12}, {'timePeriod': -1, 'timeUnit': 'MONTH'},
    {'timePeriod': 1.5, 'timeUnit': 'MONTH'}, {'timePeriod': True, 'timeUnit': 'MONTH'},
    {'timePeriod': 12, 'timeUnit': 'INVALID'}, {**PERIOD, 'comment': '长' * 501}, {**PERIOD, 'unsupported': True}])
def test_invalid_online_warranty_is_rejected_before_enqueue(period):
    a = adapter(Platform(2))
    service = service_for(a)
    listing = service.store.save(a.read('sku-0'))
    with pytest.raises(ValueError, match='保修'):
        service.change(request_for(listing, period).model_dump())
    assert not service.store.jobs('yandex', a.account_id)


def test_other_platform_cannot_send_yandex_warranty():
    listing = adapter(Platform(2)).read('sku-0')
    listing.platform = 'mercadolibre'
    with pytest.raises(ValueError, match='仅支持 Yandex'):
        validate_changes(listing, request_for(listing))


def test_online_warranty_updates_existing_offer_and_confirms_only_after_readback(monkeypatch):
    platform = Platform(2)
    a = adapter(platform)
    service = service_for(a)
    listing = service.store.save(a.read('sku-0'))
    original = deepcopy(platform.rows[0]['offer'])
    calls = []
    def update(token, business, offer):
        calls.append(deepcopy(offer))
        assert token == 'test' and business == '1'
        platform.rows[0]['offer'].update(offer)
        return {'status': 'OK'}
    monkeypatch.setattr(online_yandex.api, 'update_yandex_offer_mapping', update)
    request = request_for(listing, {**PERIOD, 'timePeriod': '12'})
    job = service.change(request.model_dump())['job']
    assert service.change(request.model_dump())['job']['id'] == job['id']
    assert not calls
    service.run_once()
    assert calls == [{'offerId': 'sku-0', 'guaranteePeriod': PERIOD}]
    assert {k: platform.rows[0]['offer'][k] for k in original} == original
    assert service.store.job(job['id'])['status'] == 'waiting_confirmation'
    result = service.reconcile_once(job['id'])['job']
    assert result['status'] == 'confirmed'
    assert result['result']['confirmation'] == {'guarantee_period': True}
    assert result['result']['evidence']['observed'] == {'guarantee_period': PERIOD}
    assert len(calls) == 1
    saved = service.store.get(listing.id)
    assert saved.content['guarantee_period'] == PERIOD
    assert saved.synced_at == listing.synced_at


@pytest.mark.parametrize('observed', [None, {**PERIOD, 'timePeriod': 6}, {**PERIOD, 'comment': 'Другая гарантия'}])
def test_missing_or_different_platform_warranty_never_confirms_old_value(observed):
    platform = Platform(2)
    platform.rows[0]['offer']['guaranteePeriod'] = deepcopy(PERIOD)
    a = adapter(platform)
    listing = a.read('sku-0')
    request = request_for(listing)
    platform.rows[0]['offer'].pop('guaranteePeriod')
    if observed is not None:
        platform.rows[0]['offer']['guaranteePeriod'] = observed
    fresh = yandex_change(a, listing, request)
    assert fresh.content['guarantee_period'] == (observed or {})
    assert confirmation(fresh, request) == {'guarantee_period': False}
    assert listing.content['guarantee_period'] == PERIOD


def test_platform_warranty_change_after_confirmation_blocks_write(monkeypatch):
    platform = Platform(2)
    a = adapter(platform)
    service = service_for(a)
    listing = service.store.save(a.read('sku-0'))
    job = service.change(request_for(listing).model_dump())['job']
    platform.rows[0]['offer']['guaranteePeriod'] = {**PERIOD, 'timePeriod': 6}
    calls = []
    monkeypatch.setattr(online_yandex.api, 'update_yandex_offer_mapping', lambda *args: calls.append(args))
    service.run_once()
    assert not calls
    assert service.store.job(job['id'])['status'] == 'failed'
    assert '平台商品已变化' in service.store.job(job['id'])['result']['error']


