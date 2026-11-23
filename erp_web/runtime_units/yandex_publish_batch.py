"""Yandex 同作用范围合批请求，复用逐 SKU 检查点和结果解释。"""
from collections import defaultdict
from copy import deepcopy
import time

from erp_web.marketplaces import yandex_http as api
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.runtime_units import publish_yandex as leaf


LIMITS = {'offer_mapping': 100, 'campaign_offer': 500, 'price': 500, 'stock': 2000}
READ_LIMITS = {'mappings': 100, 'offers': 200, 'quarantine': 200, 'cards': 200}


def _send(step, entries, token, config):
    plan, checkpoint, _ = entries[0]
    timeout = leaf._timeout_seconds(config)
    if step == 'offer_mapping':
        path = f'/v2/businesses/{checkpoint.business_id}/offer-mappings/update'
        body = {'offerMappings': [{'offer': plan['catalog_offer']} for plan, _, _ in entries]}
    elif step == 'campaign_offer':
        path = f'/v2/campaigns/{checkpoint.campaign_id}/offers/update'
        body = {'offers': [plan['campaign_offer'] for plan, _, _ in entries]}
    elif step == 'price':
        return api.update_yandex_price(token, business_id=checkpoint.business_id if plan['price']['level'] == 'business' else '',
            campaign_id=checkpoint.campaign_id if plan['price']['level'] == 'campaign' else '',
            offers=[offer for plan, _, _ in entries for offer in plan['price']['offers']], timeout_seconds=timeout)
    elif plan['stock']['mode'] == 'business':
        path = f'/v3/businesses/{checkpoint.business_id}/offers/stocks/update'
        body = {'skuItems': [{'sku': checkpoint.offer_id, 'partnerWarehouseId': int(plan['stock']['warehouse_ids'][0]),
                             'count': plan['stock']['count']} for plan, checkpoint, _ in entries]}
    else:
        path = f'/v2/campaigns/{checkpoint.campaign_id}/offers/stocks'
        body = {'skus': [{'sku': checkpoint.offer_id, 'items': [{'count': plan['stock']['count']}]} for plan, checkpoint, _ in entries]}
    return api.request_yandex_json('PUT' if step == 'stock' and plan['stock']['mode'] == 'campaign_warehouses' else 'POST',
                                   path, token, body, timeout_seconds=timeout)


def _groups(results, config):
    groups = defaultdict(list)
    for index, result in enumerate(results):
        plan, checkpoint, token = leaf._load_submission(result, config)
        step = leaf._next_pending_step(checkpoint)
        if step is None or checkpoint.next_poll_at > time.time():
            continue
        campaign = checkpoint.campaign_id if step == 'campaign_offer' or step == 'price' and plan['price']['level'] == 'campaign' or step == 'stock' and plan['stock']['mode'] == 'campaign_warehouses' else ''
        groups[(step, checkpoint.business_id, campaign, plan['price']['level'], plan['stock']['mode'])].append((plan, checkpoint, index))
    return groups, token if results else ''


def submit_many(results, config, *, on_dispatch, on_result):
    values = deepcopy(results)
    groups, token = _groups(values, config)
    for (step, _, _, price_level, _), entries in groups.items():
        limit = 2000 if step == 'price' and price_level == 'campaign' else LIMITS[step]
        for start in range(0, len(entries), limit):
            batch = entries[start:start + limit]
            # 全批校验在发送之前完成，不能将不可执行成员混入平台请求。
            for plan, checkpoint, index in batch:
                try:
                    leaf._assert_logistics_plan(plan, config)
                except ValueError as exc:
                    values[index] = leaf._terminal_failure(plan, checkpoint, {'error_code': 'YANDEX_LOGISTICS_CHANGED', 'summary': str(exc), 'retryable': False})
                    on_result(index, values[index])
                    return values
            response, failure = None, None
            skip = step == 'stock' and batch[0][0]['stock']['mode'] == 'none'
            if not skip:
                on_dispatch([index for _, _, index in batch])
                try:
                    response = _send(step, batch, token, config)
                except Exception as exc:
                    failure = exc

            def received():
                if failure is not None:
                    raise failure
                return response

            stopped = False
            for plan, checkpoint, index in batch:
                try:
                    value = leaf._execute_mutation(step, plan, checkpoint, token, config, response=None if skip else received)
                    value = value if value is not None else leaf._pending_result(plan, checkpoint)
                except Exception as exc:
                    mapped = leaf.map_yandex_publish_error(exc)
                    unknown = not isinstance(exc, PublishAdapterError) or exc.details.get('outcome_unknown') or exc.details.get('remote_write_dispatched')
                    value = {**values[index], 'ok': False, 'status': 'outcome_unknown' if unknown else 'failed',
                             'error': mapped.get('summary') or str(exc), 'error_map': mapped}
                values[index] = value
                on_result(index, value)
                stopped |= not value.get('ok') or value.get('status') in ('outcome_unknown', 'needs_attention')
            if stopped:
                return values
    return values


def publish_many(payloads, config, *, on_dispatch, on_result):
    results = []
    for payload in payloads:
        plan = leaf._publish_plan(payload)
        _, authorization_campaign, business = leaf._yandex_publish_credentials(config)
        leaf._assert_plan_binding(plan, authorization_campaign, business)
        checkpoint = leaf.YandexPublishCheckpoint(phase='offer_mapping', offer_id=plan['offer_id'],
            campaign_id=plan['campaign_id'], business_id=plan['business_id'])
        results.append(leaf._pending_result(plan, checkpoint))
    return submit_many(results, config, on_dispatch=on_dispatch, on_result=on_result)


def confirm_many(results, config):
    values = deepcopy(results)
    groups = defaultdict(list)
    loaded, facts = [], {}
    for index, result in enumerate(results):
        plan, checkpoint, token = leaf._load_submission(result, config)
        loaded.append((plan, checkpoint, token))
        facts[index] = {}
        if leaf._next_pending_step(checkpoint) is not None:
            if 'campaign_offer' not in checkpoint.completed_steps:
                raise ValueError('此 SKU 尚无店铺上架条件回执')
            groups[('offers', checkpoint.campaign_id)].append(index)
            continue
        groups[('mappings', checkpoint.business_id)].append(index)
        groups[('offers', checkpoint.campaign_id)].append(index)
        level = plan['price']['level']
        groups[('quarantine:' + level, checkpoint.business_id if level == 'business' else checkpoint.campaign_id)].append(index)
    timeout = leaf._timeout_seconds(config)
    for (kind, scope), indices in groups.items():
        limit = READ_LIMITS[kind.split(':')[0]]
        for start in range(0, len(indices), limit):
            batch = indices[start:start + limit]
            ids = list(dict.fromkeys(loaded[index][1].offer_id for index in batch))
            token = loaded[batch[0]][2]
            try:
                if kind == 'mappings':
                    rows = api.fetch_yandex_offer_mapping(token, scope, ids, timeout_seconds=timeout)
                    indexed = {row['offer']['offerId']: row for row in rows}
                elif kind == 'offers':
                    rows = api.fetch_yandex_campaign_offer(token, scope, offer_ids=ids, timeout_seconds=timeout)
                    indexed = {row['offerId']: row for row in rows}
                else:
                    rows = api.fetch_yandex_price_quarantine(token,
                        business_id=scope if kind == 'quarantine:business' else '',
                        campaign_id=scope if kind == 'quarantine:campaign' else '', offer_ids=ids, timeout_seconds=timeout)
                    indexed = {row['offerId']: row for row in rows}
                for index in batch:
                    oid = loaded[index][1].offer_id
                    facts[index][kind.split(':')[0]] = [indexed[oid]] if oid in indexed else []
            except Exception as exc:
                for index in batch:
                    values[index] = {**values[index], 'check_error': str(exc)}
    # 仅对在售且卡片报错的成员补查详情，按 Business 合批，禁止逐 SKU 请求。
    card_groups = defaultdict(list)
    for index, (_, checkpoint, _) in enumerate(loaded):
        fact = facts[index]
        if set(fact) != {'mappings', 'offers', 'quarantine'}:
            continue
        mapping = next(iter(fact['mappings']), {}).get('offer', {})
        offer = next(iter(fact['offers']), {})
        if not fact['quarantine'] and mapping.get('cardStatus') == 'HAS_CARD_CAN_UPDATE_ERRORS' and offer.get('status') == 'PUBLISHED':
            card_groups[checkpoint.business_id].append(index)
    for business, indices in card_groups.items():
        for start in range(0, len(indices), READ_LIMITS['cards']):
            batch = indices[start:start + READ_LIMITS['cards']]
            ids = list(dict.fromkeys(loaded[index][1].offer_id for index in batch))
            try:
                rows = api.fetch_yandex_offer_cards(loaded[batch[0]][2], business, ids, timeout_seconds=timeout)
                indexed = {row['offerId']: row for row in rows}
                for index in batch:
                    oid = loaded[index][1].offer_id
                    facts[index]['cards'] = [indexed[oid]] if oid in indexed else []
            except Exception as exc:
                for index in batch:
                    values[index] = {**values[index], 'check_error': str(exc)}
                    facts[index] = {}
    for index, (plan, checkpoint, token) in enumerate(loaded):
        if leaf._next_pending_step(checkpoint) is not None and 'offers' in facts[index]:
            # 已在售也不能证明本次批准的价格、库存已提交。仅记录平台观察值。
            offer = next(iter(facts[index]['offers']), {})
            if offer and not offer.get('status'):
                values[index]['check_error'] = '平台未返回店铺商品状态，请稍后查询。'
                continue
            inner = values[index].get('result') or values[index]
            observation = {
                'status': offer.get('status') or 'NO_RECORD', 'at': leaf.collect_time_iso(),
            }
            if campaign_errors := leaf._feedback_items(offer.get('errors')):
                observation['campaign_errors'] = campaign_errors
            if campaign_warnings := leaf._feedback_items(offer.get('warnings')):
                observation['campaign_warnings'] = campaign_warnings
            inner['checkpoint']['evidence']['campaign_observation'] = observation
            values[index].pop('check_error', None)
        elif {'mappings', 'offers', 'quarantine'} <= set(facts[index]):
            try:
                values[index] = leaf._confirmation_readback(plan, checkpoint, token, config, facts=facts[index])
            except Exception as exc:
                values[index] = {**values[index], 'check_error': str(exc)}
    return values
