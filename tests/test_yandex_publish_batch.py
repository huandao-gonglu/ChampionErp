"""同仓多 SKU 使用平台原生批量请求，逐项保留原检查点与确认标准。"""
from copy import deepcopy

import pytest

from erp_web.runtime_units import yandex_publish_batch as batch
from erp_web.marketplaces.yandex_http import YandexApiError
from tests.yandex_logistics_support import PLAN, install_logistics


@pytest.fixture
def native(monkeypatch):
    plan = {**PLAN, 'campaign_id': '333', 'authorization_campaign_id': '111'}
    install_logistics(monkeypatch, [plan])
    config = {'yandex': {'api_token': 'test', 'business_id': '222', 'campaign_id': '111',
                        'placement_type': 'FBS', 'stock_update_mode': 'business'}}
    payloads = [{'offer_id': f'SKU-{index}', 'business_id': '222', 'campaign_id': '333',
        'authorization_campaign_id': '111', 'catalog': {'offer': {'offerId': f'SKU-{index}', 'name': f'商品{index}'}},
        'offer_conditions': {'offer': {'offerId': f'SKU-{index}'}},
        'price': {'level': 'campaign', 'offers': [{'offerId': f'SKU-{index}', 'price': {'value': 123, 'currencyId': 'RUR'}}]},
        'stock': {'mode': 'business', 'warehouse_ids': [31], 'count': index+10, 'fulfillment_plan': deepcopy(plan)}}
        for index in range(4)]
    writes, dispatches, saves = [], [], []
    def request(method, path, token, body, **kwargs):
        writes.append((method, path, deepcopy(body)))
        return {'status': 'OK'}
    monkeypatch.setattr(batch.api, 'request_yandex_json', request)
    callbacks = {'on_dispatch': lambda indices: dispatches.append(indices),
                 'on_result': lambda index, value: saves.append((index, deepcopy(value)))}
    return payloads, config, writes, dispatches, saves, callbacks


def ready(results):
    for result in results:
        result['result']['checkpoint']['next_poll_at'] = 0
    return results


def test_four_skus_use_four_writes_and_three_confirmation_reads(native, monkeypatch):
    payloads, config, writes, dispatches, saves, callbacks = native
    results = batch.publish_many(payloads, config, **callbacks)
    for _ in range(3):
        results = batch.submit_many(ready(results), config, **callbacks)
    assert len(writes) == 4
    assert [path for _, path, _ in writes] == [
        '/v2/businesses/222/offer-mappings/update', '/v2/campaigns/333/offers/update',
        '/v2/campaigns/333/offer-prices/updates', '/v3/businesses/222/offers/stocks/update']
    assert len(writes[0][2]['offerMappings']) == len(writes[1][2]['offers']) == len(writes[2][2]['offers']) == 4
    assert writes[3][2]['skuItems'] == [{'sku': f'SKU-{i}', 'partnerWarehouseId': 31, 'count': i+10} for i in range(4)]
    assert dispatches == [[0, 1, 2, 3]] * 4 and len(saves) == 16
    assert all(result['result']['checkpoint']['completed_steps'] == ['offer_mapping', 'campaign_offer', 'price', 'stock'] for result in results)
    reads = []
    def mappings(token, business, ids, **kwargs):
        reads.append(('mapping', business, ids))
        return [{'offer': {'offerId': sku, 'cardStatus': 'HAS_CARD_CAN_UPDATE'}} for sku in ids]
    def campaigns(token, campaign, *, offer_ids, **kwargs):
        reads.append(('campaign', campaign, offer_ids))
        return [{'offerId': sku, 'status': 'PUBLISHED'} for sku in offer_ids]
    def quarantine(token, *, campaign_id, business_id, offer_ids, **kwargs):
        reads.append(('quarantine', campaign_id, offer_ids))
        return []
    monkeypatch.setattr(batch.api, 'fetch_yandex_offer_mapping', mappings)
    monkeypatch.setattr(batch.api, 'fetch_yandex_campaign_offer', campaigns)
    monkeypatch.setattr(batch.api, 'fetch_yandex_price_quarantine', quarantine)
    confirmed = batch.confirm_many(results, config)
    assert len(reads) == 3 and all(ids == [f'SKU-{i}' for i in range(4)] for _, _, ids in reads)
    assert [scope for _, scope, _ in reads] == ['222', '333', '333']
    assert all(result['status'] == 'real_publish_success' and result['campaign_id'] == '333' for result in confirmed)
    assert all(result['checkpoint']['evidence']['stock']['warehouse_ids'] == [31] for result in confirmed)
    assert len(writes) == 4


def test_chunk_limit_and_failed_batch_do_not_dispatch_later_chunks(native, monkeypatch):
    payloads, config, writes, dispatches, _, callbacks = native
    monkeypatch.setitem(batch.LIMITS, 'offer_mapping', 2)
    def reject(*_args, **_kwargs):
        raise YandexApiError('INVALID_CATEGORY', '类目不正确', http_status=200, retryable=False)
    monkeypatch.setattr(batch.api, 'request_yandex_json', reject)
    results = batch.publish_many(payloads, config, **callbacks)
    assert dispatches == [[0, 1]]
    assert all(not result['ok'] for result in results[:2])
    assert all(result['result']['checkpoint']['completed_steps'] == [] for result in results[2:])


def test_changed_warehouse_stops_before_any_dispatch(native):
    payloads, config, writes, dispatches, _, callbacks = native
    payloads[1]['stock']['warehouse_ids'] = [999]
    results = batch.publish_many(payloads, config, **callbacks)
    assert not writes and not dispatches
    assert results[1]['error_code'] == 'YANDEX_LOGISTICS_CHANGED'


def test_confirmation_uses_each_read_endpoints_own_limit(native, monkeypatch):
    payloads, config, writes, _, _, _ = native
    results = []
    for i in range(151):
        payload = {**deepcopy(payloads[0]), 'offer_id': f'SKU-{i}'}
        plan = batch.leaf._publish_plan(payload)
        checkpoint = batch.leaf.YandexPublishCheckpoint(offer_id=payload['offer_id'], business_id='222', campaign_id='333',
            phase='confirmation', completed_steps=['offer_mapping', 'campaign_offer', 'price', 'stock'])
        results.append(batch.leaf._pending_result(plan, checkpoint))
    reads = []
    def mappings(token, business, ids, **kwargs):
        reads.append(('mapping', len(ids)))
        return [{'offer': {'offerId': sku, 'cardStatus': 'HAS_CARD_CAN_UPDATE'}} for sku in ids]
    def offers(token, campaign, *, offer_ids, **kwargs):
        reads.append(('offers', len(offer_ids)))
        return [{'offerId': sku, 'status': 'PUBLISHED'} for sku in offer_ids]
    def quarantine(token, *, offer_ids, **kwargs):
        reads.append(('quarantine', len(offer_ids)))
        return []
    monkeypatch.setattr(batch.api, 'fetch_yandex_offer_mapping', mappings)
    monkeypatch.setattr(batch.api, 'fetch_yandex_campaign_offer', offers)
    monkeypatch.setattr(batch.api, 'fetch_yandex_price_quarantine', quarantine)
    confirmed = batch.confirm_many(results, config)
    assert reads == [('mapping', 100), ('mapping', 51), ('offers', 151), ('quarantine', 151)]
    assert all(result['status'] == 'real_publish_success' for result in confirmed)
    assert not writes


def test_group_retry_keeps_completed_steps_and_persists_before_batch_dispatch(native, monkeypatch):
    from erp_web.context import get_context
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
    payloads, config, writes, _, _, _ = native
    store, saved = get_context().products, {}
    monkeypatch.setattr(store, 'sku_publication', lambda draft, sku, target: deepcopy(saved.get(sku, {})))
    monkeypatch.setattr(store, 'save_sku_publication', lambda draft, sku, target, state: saved.update({sku: deepcopy(state)}))
    envelope = {'draft_id': 'native-draft', 'target_key': 'yandex:global', 'grouping': {'mode': 'independent'},
        'items': [{'sku_id': f'fact-{i}', 'sku': payload['offer_id'], 'fingerprint': str(i), 'payload': payload}
                  for i, payload in enumerate(payloads)]}
    reject_price = True
    def request(method, path, token, body, **kwargs):
        assert all(state['status'] == 'dispatching' for state in saved.values())
        writes.append((method, path, deepcopy(body)))
        if path.endswith('/offer-prices/updates') and reject_price:
            raise YandexApiError('PRICE_INVALID', '价格被平台拒绝', retryable=False, http_status=400)
        return {'status': 'OK'}
    monkeypatch.setattr(batch.api, 'request_yandex_json', request)
    adapter = SkuGroupPublishingAdapter(YandexPublishingAdapter())
    result = adapter.publish_payload(envelope, config)
    def advance(result):
        for state in result['sku_results']:
            state['result']['result']['checkpoint']['next_poll_at'] = 0
        return adapter.advance_submission(result, config)
    result = advance(result)
    result = advance(result)
    assert result['status'] == 'failed'
    assert all(state['result']['checkpoint']['completed_steps'] == ['offer_mapping', 'campaign_offer'] for state in result['sku_results'])
    assert len(writes) == 3
    reject_price = False
    result = adapter.publish_payload(envelope, config)
    assert len(writes) == 3
    result = advance(result)
    result = advance(result)
    assert result['status'] == 'pending_confirmation'
    assert len(writes) == 5
    assert writes[3][1].endswith('/offer-prices/updates') and writes[4][1].endswith('/offers/stocks/update')
    assert all(state['result']['result']['checkpoint']['completed_steps'] == ['offer_mapping', 'campaign_offer', 'price', 'stock'] for state in result['sku_results'])
