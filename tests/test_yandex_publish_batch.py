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


@pytest.mark.parametrize("code,expected", [
    ("PRICE_INVALID", "failed"),
    ("EXTERNAL_REQUEST_DEADLINE", "needs_attention"),
])
def test_group_retry_keeps_completed_steps_and_persists_before_batch_dispatch(native, monkeypatch, code, expected):
    from erp_web.context import get_context
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
    payloads, config, writes, _, _, _ = native
    store, saved = get_context().products, {}
    monkeypatch.setattr(store, 'sku_publication', lambda draft, sku, target: deepcopy(saved.get(sku, {})))
    monkeypatch.setattr(store, 'save_sku_publication', lambda draft, sku, target, state, **kw: saved.update({sku: deepcopy(state)}))
    envelope = {'draft_id': 'native-draft', 'target_key': 'yandex:global', 'grouping': {'mode': 'independent'},
        'items': [{'sku_id': f'fact-{i}', 'sku': payload['offer_id'], 'fingerprint': str(i), 'payload': payload}
                  for i, payload in enumerate(payloads)]}
    reject_price = True
    def request(method, path, token, body, **kwargs):
        assert all(state['status'] == 'dispatching' for state in saved.values())
        writes.append((method, path, deepcopy(body)))
        if path.endswith('/offer-prices/updates') and reject_price:
            raise YandexApiError(code, '价格请求受阻', retryable=False, http_status=400 if code == 'PRICE_INVALID' else 0)
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
    assert result['status'] == expected
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


@pytest.mark.parametrize('card_count', [4, 201])
def test_warning_details_are_batched_and_keep_per_sku_outcomes(native, monkeypatch, card_count):
    payloads, config, writes, _, _, _ = native
    results = []
    for i in range(card_count):
        plan = batch.leaf._publish_plan({**deepcopy(payloads[0]), 'offer_id': f'SKU-{i}'})
        checkpoint = batch.leaf.YandexPublishCheckpoint(offer_id=f'SKU-{i}', business_id='222', campaign_id='333',
            phase='confirmation', completed_steps=['offer_mapping', 'campaign_offer', 'price', 'stock'])
        results.append(batch.leaf._pending_result(plan, checkpoint))
    monkeypatch.setattr(batch.api, 'fetch_yandex_offer_mapping', lambda token, business, ids, **kw:
        [{'offer': {'offerId': sku, 'cardStatus': 'HAS_CARD_CAN_UPDATE_ERRORS'}} for sku in ids])
    monkeypatch.setattr(batch.api, 'fetch_yandex_campaign_offer', lambda token, campaign, *, offer_ids, **kw:
        [{'offerId': sku, 'status': 'PUBLISHED'} for sku in offer_ids])
    monkeypatch.setattr(batch.api, 'fetch_yandex_price_quarantine', lambda *a, **kw: [])
    calls = []
    warning = {'message': 'Недопустимые символы'}
    def cards(token, business, ids, **kw):
        calls.append((business, ids))
        return [{'offerId': sku, 'cardStatus': 'HAS_CARD_CAN_UPDATE_ERRORS', 'warnings': [warning],
                 'errors': [{'message': '真实错误'}] if sku == 'SKU-1' else []}
                for sku in reversed(ids) if sku != 'SKU-2']
    monkeypatch.setattr(batch.api, 'fetch_yandex_offer_cards', cards)
    confirmed = batch.confirm_many(results, config)
    assert [len(ids) for _, ids in calls] == ([4] if card_count == 4 else [200, 1])
    assert all(scope == '222' for scope, _ in calls)
    assert confirmed[0]['status'] == 'real_publish_success'
    assert confirmed[0]['warnings'] == [warning]
    assert confirmed[1]['status'] == 'real_publish_failed'
    assert confirmed[2]['check_error'] and confirmed[2]['status'] == results[2]['status']
    assert all(row['status'] == 'real_publish_success' for row in confirmed[3:])
    assert not writes


def test_retry_card_failure_only_restores_confirmation_without_replaying_writes(native, monkeypatch):
    from erp_web.context import get_context
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter
    payloads, config, writes, _, _, _ = native
    plan = batch.leaf._publish_plan(payloads[0])
    checkpoint = batch.leaf.YandexPublishCheckpoint(offer_id='SKU-0', business_id='222', campaign_id='333',
        phase='confirmation', completed_steps=['offer_mapping', 'campaign_offer', 'price', 'stock'])
    failure = batch.leaf._terminal_failure(plan, checkpoint, {'error_code': 'YANDEX_CARD_UPDATE_REJECTED'})
    previous = {'sku_id': 'fact-0', 'sku': 'SKU-0', 'fingerprint': 'original', 'status': 'failed', 'result': failure}
    saved = []
    store = get_context().products
    monkeypatch.setattr(store, 'sku_publication', lambda *args: deepcopy(previous))
    monkeypatch.setattr(store, 'save_sku_publication', lambda *args, **kw: saved.append(deepcopy(args[-1])))
    envelope = {'draft_id': 'native-draft', 'target_key': 'yandex:global', 'grouping': {'mode': 'independent'},
        'items': [{**previous, 'payload': payloads[0]}]}
    restored = SkuGroupPublishingAdapter(YandexPublishingAdapter()).publish_payload(envelope, config)
    assert restored['status'] == 'pending_confirmation'
    assert restored['sku_results'][0]['result']['result']['checkpoint']['completed_steps'] == checkpoint.completed_steps
    assert not writes


def test_manual_query_of_legacy_price_timeout_keeps_submission_separate(native, monkeypatch):
    from erp_web.context import get_context
    from erp_web.runtime_units.publish_adapter import YandexPublishingAdapter
    from erp_web.runtime_units.publishing_bus_core import PublishingBus
    from erp_web.runtime_units.sku_publish_adapter import SkuGroupPublishingAdapter

    payloads, config, writes, _, _, _ = native
    app = get_context()
    adapter = SkuGroupPublishingAdapter(YandexPublishingAdapter())
    app._publishing_bus = PublishingBus(app.db, {"yandex": adapter}, config_provider=lambda: config, task_queue=app.task_queue)
    envelope = {"draft_id": "timeout-draft", "target_key": "yandex:global", "grouping": {"mode": "independent"}}
    items, rows, stored = [], [], {}
    for index, payload in enumerate(payloads):
        sku = payload["offer_id"]
        plan = batch.leaf._publish_plan(payload)
        completed = list(batch.leaf.YANDEX_PUBLISH_STEPS[:2] if index < 2 else batch.leaf.YANDEX_PUBLISH_STEPS)
        checkpoint = batch.leaf.YandexPublishCheckpoint(offer_id=sku, business_id="222", campaign_id="333",
            completed_steps=completed, phase="price" if index < 2 else "confirmation")
        error = "EXTERNAL_REQUEST_DEADLINE" if index < 2 else "YANDEX_CAMPAIGN_NO_STOCKS"
        value = batch.leaf._terminal_failure(plan, checkpoint, {"error_code": error})
        row = {"sku": sku, "sku_id": f"fact-{index}", "fingerprint": str(index), "status": "failed", "result": value}
        if index == 3:
            row.update(status="published", result=batch.leaf._terminal_success(plan, checkpoint,
                       campaign_status="PUBLISHED", card_status="HAS_CARD_CAN_UPDATE"))
        rows.append(row)
        items.append({**{key: row[key] for key in ("sku", "sku_id", "fingerprint")}, "payload": payload})
        stored[row["sku_id"]] = deepcopy(row)
    monkeypatch.setattr(app.products, "sku_publication", lambda draft, sku, target: deepcopy(stored.get(sku, {})))
    def save(draft, sku, target, value, *, expected=None):
        if expected:
            assert stored[sku]["result"] == expected["result"]
        stored[sku] = deepcopy(value)
    monkeypatch.setattr(app.products, "save_sku_publication", save)
    reads = []
    def offers(token, campaign, *, offer_ids, **kw):
        reads.append(offer_ids)
        return [{"offerId": sku, "status": "NO_STOCKS" if sku == "SKU-1" else "PUBLISHED"} for sku in reversed(offer_ids)]
    monkeypatch.setattr(batch.api, "fetch_yandex_campaign_offer", offers)
    monkeypatch.setattr(batch.api, "fetch_yandex_offer_mapping", lambda token, scope, ids, **kw:
                        [{"offer": {"offerId": sku, "cardStatus": "HAS_CARD_CAN_UPDATE"}} for sku in ids])
    monkeypatch.setattr(batch.api, "fetch_yandex_price_quarantine", lambda *a, **kw: [])
    job_id = "legacy-price-timeout"
    task_id = f"publish:{job_id}:yandex"
    payload = {**envelope, "kind": "sku_group", "items": items}
    app.db.create_publish_job({"job_id": job_id, "idempotency_key": job_id, "status": "completed", "product": {},
        "approved_publications": {"yandex": {"payload": payload}},
        "platforms": {"yandex": {"status": "failed", "attempts": 1, "result": adapter._aggregate(envelope, rows)}}})
    service = app.task_queue
    before = service.detail(task_id)
    assert before["task"]["can_check"] and before["task"]["can_retry"]
    assert before["task"]["status"] == "needs_attention"
    assert not reads and not writes
    with service.store.connect() as conn:
        conn.execute("UPDATE confirmations SET deadline_at=0 WHERE id=?", (task_id,))
    after = service.check(task_id)
    assert after["checked"] and after["detail"]["attempts"] == 1
    assert after["task"]["confirmation"]["state"] == "needs_attention"
    checked = after["detail"]["result"]["sku_results"]
    assert [row["status"] for row in checked] == ["submission_paused", "submission_paused", "published", "published"]
    assert [row["result"]["checkpoint"]["evidence"]["campaign_observation"]["status"] for row in checked[:2]] == [
        "PUBLISHED", "NO_STOCKS"]
    assert reads == [["SKU-0", "SKU-1", "SKU-2"]] and not writes
    from erp_web.product_model.task_queue_model import can_check_publish_status, publish_can_retry
    finished_steps = {"sku_results": [rows[2], rows[3]]}
    assert can_check_publish_status("partial", finished_steps)
    assert not publish_can_retry("partial", finished_steps)
    # 即使成功 SKU 的草稿回执已移除，原任务的成功证据仍然排除它。
    stored.pop("fact-3")
    resumed = adapter.retry_submission(payload, after["detail"]["result"], config)
    assert [row["status"] for row in resumed["sku_results"]] == ["pending_submission"] * 2 + ["published"] * 2
    advanced = adapter.advance_submission(resumed, config)
    assert advanced["status"] == "pending_submission"
    assert len(writes) == 1 and writes[0][1].endswith("/offer-prices/updates")
    assert [offer["offerId"] for offer in writes[0][2]["offers"]] == ["SKU-0", "SKU-1"]


def test_batch_confirmation_preserves_campaign_errors_and_warnings(native, monkeypatch):
    payloads, config, writes, _, _, _ = native
    results = []
    for index in range(2):
        plan = batch.leaf._publish_plan(payloads[index])
        completed = list(batch.leaf.YANDEX_PUBLISH_STEPS if index == 0 else batch.leaf.YANDEX_PUBLISH_STEPS[:2])
        checkpoint = batch.leaf.YandexPublishCheckpoint(
            offer_id=plan['offer_id'], business_id='222', campaign_id='333',
            phase='confirmation' if index == 0 else 'price', completed_steps=completed,
        )
        results.append(batch.leaf._pending_result(plan, checkpoint))
    error_row = {
        'message': 'Товар не подходит для продажи из-за рубежа',
        'comment': 'Товары этой категории не могут продаваться из-за рубежа на Маркете',
    }
    warning_row = {'message': 'Проверьте категорию'}
    monkeypatch.setattr(batch.api, 'fetch_yandex_offer_mapping', lambda token, business, ids, **kw:
        [{'offer': {'offerId': sku, 'cardStatus': 'HAS_CARD_CAN_UPDATE'}} for sku in ids])
    monkeypatch.setattr(batch.api, 'fetch_yandex_campaign_offer', lambda token, campaign, *, offer_ids, **kw: [
        {'offerId': sku, 'status': 'DISABLED_AUTOMATICALLY', 'errors': [error_row], 'warnings': [warning_row]}
        for sku in offer_ids
    ])
    monkeypatch.setattr(batch.api, 'fetch_yandex_price_quarantine', lambda *a, **kw: [])

    confirmed = batch.confirm_many(results, config)

    assert not writes
    assert confirmed[0]['status'] == 'real_publish_failed'
    assert confirmed[0]['error_code'] == 'YANDEX_CAMPAIGN_DISABLED_AUTOMATICALLY'
    assert 'Товар не подходит для продажи из-за рубежа' in confirmed[0]['error']
    assert confirmed[0]['checkpoint']['evidence']['confirmation']['campaign_errors'] == [error_row]
    assert confirmed[0]['checkpoint']['evidence']['confirmation']['campaign_warnings'] == [warning_row]
    assert confirmed[0]['warnings'] == [warning_row]
    observation = confirmed[1]['result']['checkpoint']['evidence']['campaign_observation']
    assert observation['status'] == 'DISABLED_AUTOMATICALLY'
    assert observation['campaign_errors'] == [error_row]
    assert observation['campaign_warnings'] == [warning_row]
