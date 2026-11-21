"""订单中心的本地跨领域检索；在分页前应用筛选，不执行外部请求。"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import batched
from zoneinfo import ZoneInfo

from erp_web.schemas.orders import account_pairs, account_in_scope
from erp_web.schemas.order_center import validate_order_center_filters
from erp_web.schemas.order_procurement import order_line_key
from erp_web.schemas.receiving_warehouse import delivery_carrier
from erp_web.services.order_source_resolution import resolve_source_selection
from erp_web.stores.order_procurement_store import line_signature


def _contains(term, values):
    return any(term in str(value).casefold() for value in values if value is not None)


def _source_values(source):
    if not source:
        return []
    return [source.supplier, source.source_sku_id, source.source_offer_id,
            source.source_spec_id, source.specification, source.product_url]


def _declaration_status(value):
    if value.get('fulfillment_status') == 'CANCELLED':
        return 'cancelled'
    if value.get('busy') and value.get('operation') == 'create':
        return 'submitting'
    if value.get('create_unknown'):
        return 'unknown'
    if value.get('crossborderbus_order_id'):
        return 'created'
    if value.get('error_message') or value.get('fulfillment_status') == 'EXCEPTION':
        return 'error'
    return 'unsubmitted'


def _deadline_matches(order, filters, now):
    if not any(filters.get(key) for key in ('deadline_from', 'deadline_to', 'deadline_status')):
        return True
    try:
        raw = order.shipment_deadline
        moment = None
        if len(raw) == 10:
            day = date.fromisoformat(raw)
        else:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            moment = parsed.astimezone(now.tzinfo) if parsed.tzinfo else None
            day = (moment or parsed).date()
    except (ValueError, TypeError):
        return False
    if filters.get('deadline_from') and day.isoformat() < filters['deadline_from']:
        return False
    if filters.get('deadline_to') and day.isoformat() > filters['deadline_to']:
        return False
    status = filters.get('deadline_status')
    if status:
        if order.state not in {'pending_shipment', 'processing'}:
            return False
        if status == 'today':
            return day == now.date()
        if status == 'tomorrow':
            return day == now.date() + timedelta(days=1)
        # 仅日期或无时区的平台时间不计算小时级超时，整日过去后才认定超时。
        return moment.timestamp() < now.timestamp() if moment else day < now.date()
    return True


def _logistics(records, value, tracking):
    active = [record for record in records if record.status == 'purchased']
    # 复用采购 Store 对相同运单的去重和最近成功快照选择。
    parcels = [parcel for parcel in (tracking or {}).get('packages', [])
               if parcel.get('tracking_number')]
    registered = [parcel for parcel in value.get('parcels', []) if parcel.get('included', True) and parcel.get('tracking_number')]
    known = {parcel['tracking_number'].strip().upper(): parcel.get('status', '') for parcel in parcels}
    numbers = set(known) | {parcel['tracking_number'].strip().upper() for parcel in registered}
    statuses = {known.get(number, '') for number in numbers}
    states = set()
    if 'WAITACCEPT' in statuses or (not numbers and any(
        (((record.progress or {}).get('data') or {}).get('order') or {}).get('status') == 'waitsellersend'
        for record in active
    )):
        states.add('waiting')
    if statuses & {'ACCEPT', 'TRANSPORT', 'DELIVERING'}:
        states.add('in_transit')
    if numbers and statuses == {'SIGN'}:
        states.add('signed')
    if 'UNSIGN' in statuses:
        states.add('unsigned')
    if numbers and statuses - {'WAITACCEPT', 'ACCEPT', 'TRANSPORT', 'DELIVERING', 'SIGN', 'UNSIGN'}:
        states.add('unknown')
    return active, parcels, registered, numbers, states


def _exception_types(records, value):
    result = set()
    if any((record.progress or {}).get('error')
           or (record.progress or {}).get('state') == 'error'
           or ((record.progress or {}).get('data') or {}).get('logistics_warning') for record in records):
        result.add('purchase')
    if value.get('label_error'):
        result.add('label')
    if value.get('create_unknown'):
        result.add('declaration')
    if value.get('error_message') or value.get('fulfillment_status') == 'EXCEPTION' or value.get('cancel_rejected'):
        result.add('warehouse' if value.get('crossborderbus_order_id') else 'declaration')
    return result


def _source_search_values(order, saved, bindings):
    values = []
    counts = Counter(order_line_key(line) for line in order.items)
    for line in order.items:
        key = order_line_key(line)
        if not key or counts[key] != 1:
            continue
        selection = resolve_source_selection(line, key, line_signature(line),
            saved.get((order.id, key)), bindings.get((order.platform, order.account_id, line.sku), []))
        if selection.status == 'confirmed':
            values.extend(_source_values(selection.source))
        for candidate in selection.candidates:
            values.extend(_source_values(candidate.source))
    return values


class OrderCenterQueryService:
    def __init__(self, procurement, notifications, fulfillment_store):
        self.procurement = procurement
        self.notifications = notifications
        self.fulfillment = fulfillment_store

    def read(self, *, platform='', state='', q='', limit=50, offset=0, now=None, **conditions):
        filters = validate_order_center_filters(conditions)
        now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(filters.get('time_zone') or 'UTC'))
        accounts = self.notifications.accounts()
        scoped = {key: value for key, value in accounts.items() if not platform or key == platform}
        shop = filters.pop('shop', '')
        if shop:
            shop_platform, separator, account = shop.partition(':')
            if not separator or not account_in_scope(accounts, shop_platform, account):
                raise ValueError('所选店铺不属于当前授权账号，请刷新后重新选择')
            scoped = {shop_platform: account} if not platform or platform == shop_platform else {}
        # 无需跨领域关联时直接在数据库分页，常规轮询不扫描整库采购和报单。
        if not q and not any(value for key, value in filters.items() if key != 'time_zone'):
            result = self.notifications.store.read(scoped, platform=platform, state=state, limit=limit, offset=offset)
            if scoped != accounts:
                # 店铺仅筛选订单；通知抽屉及已读游标仍覆盖完整授权范围。
                notices = self.notifications.store.read(accounts, limit=0)
                for key in ('notifications', 'alerts', 'unread', 'latest_alert_id'):
                    result[key] = notices[key]
            return result
        identities = {
            (p, account): (
                self.procurement.identities_for(p, account)
                if hasattr(self.procurement, 'identities_for')
                else self.procurement.identity_provider(p, account)
            )
            for p, account in account_pairs(scoped)
        }
        # 通知和提醒沿用原有读取入口；订单本身由领域事实分批筛选，最后分页。
        result = self.notifications.store.read(accounts, limit=0)
        counts, items, total = Counter(), [], 0
        facts = self.procurement.store.iter_query_facts(scoped, identities)
        for batch in batched(facts, 100):
            orders = [entry[0] for entry in batch]
            records = self.procurement.store.records_many(orders)
            tracking = self.procurement.store.tracking_summaries(orders)
            fulfillments = self.fulfillment.query_records(orders)
            receiving = self.procurement.store.receiving.get_many(orders)
            for order, saved, _, bindings in batch:
                order = order.model_copy(update={'purchase_tracking': tracking.get(order.id)})
                if not self._matches(order, records[order.id], fulfillments.get(order.id, {}),
                                     receiving.get(order.id, {}), saved, bindings, filters, q.casefold(), now):
                    continue
                counts[order.state] += 1
                if state and state != order.state:
                    continue
                if offset <= total < offset + limit:
                    items.append(order.model_dump(mode='json'))
                total += 1
        result.update(items=items, total=total, counts=dict(counts))
        return result

    def _matches(self, order, records, value, receiving, saved, bindings, filters, term, now):
        if filters.get('procurement_status') and self.procurement.store.progress_for_records(order, records) != filters['procurement_status']:
            return False
        if filters.get('declaration_status') and _declaration_status(value) != filters['declaration_status']:
            return False
        if filters.get('warehouse_status'):
            if not value.get('crossborderbus_order_id') or value.get('fulfillment_status') != filters['warehouse_status']:
                return False
        if not _deadline_matches(order, filters, now):
            return False
        active, parcels, registered, numbers, statuses = _logistics(records, value, order.purchase_tracking)
        if filters.get('has_waybill') and bool(numbers) != (filters['has_waybill'] == 'yes'):
            return False
        if filters.get('domestic_status') and filters['domestic_status'] not in statuses:
            return False
        carriers = [parcel.get('company', '') for parcel in parcels] + [parcel.get('carrier', '') for parcel in registered]
        if filters.get('domestic_carrier') and not _contains(filters['domestic_carrier'].casefold(), carriers):
            return False
        warehouse = value.get('receiving_warehouse_snapshot') or receiving
        warehouse_names = [warehouse.get('warehouse_name', ''), warehouse.get('section_name', ''),
                           value.get('warehouse_name', ''), value.get('section_name', '')]
        if filters.get('receiving_warehouse') and not _contains(filters['receiving_warehouse'].casefold(), warehouse_names):
            return False
        platform_carriers = [order.delivery.carrier, delivery_carrier(order)[0], warehouse.get('carrier', '')]
        if filters.get('platform_carrier') and not _contains(filters['platform_carrier'].casefold(), platform_carriers):
            return False
        if error_type := filters.get('exception_type'):
            errors = _exception_types(active, value)
            if not errors or (error_type != 'any' and error_type not in errors):
                return False
        if not term:
            return True
        searchable = [order.id, order.order_id, order.title, order.delivery.tracking_number,
                      order.delivery.shipment_id, value.get('order_number', ''), value.get('crossborderbus_order_id', ''),
                      value.get('platform_tracking_number', ''), value.get('remark', ''), *numbers]
        for line in order.items:
            searchable.extend([line.sku, line.remote_id, line.variant_id, line.title])
        for record in records:
            searchable.extend([record.purchase_order_number, record.note])
            for item in record.items:
                searchable.extend([item.sku, item.title, *_source_values(item.source)])
            data = (record.progress or {}).get('data') or {}
            searchable.extend(parcel.get('tracking_number', '') for parcel in data.get('logistics') or [])
            for product in data.get('products') or []:
                searchable.extend(product.get(key, '') for key in ('offer_id', 'sku_id', 'spec_id', 'specification', 'name'))
        for previous in value.get('attempt_history', []):
            searchable.extend([previous.get('order_number', ''), previous.get('crossborderbus_order_id', ''),
                               previous.get('remark', ''), previous.get('platform_tracking_number', '')])
            searchable.extend(parcel.get('tracking_number', '') for parcel in previous.get('parcels', []))
        return _contains(term, searchable) or _contains(term, _source_search_values(order, saved, bindings))
