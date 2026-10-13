"""补单只读取用户已购买的 1688 订单，确认规格后登记并沿用采购同步。"""

import hashlib
import json
from copy import deepcopy

from erp_web.schemas.alibaba_orders import SupplementLookupRequest, SupplementRecordRequest
from erp_web.schemas.order_procurement import ProcurementSource, RecordPurchaseRequest
from erp_web.services.alibaba_api_client import AlibabaApiClient, ORDER_DETAIL, validate_order_number
from erp_web.services.alibaba_purchase_assignment import quantity
from erp_web.services.alibaba_purchase_query_service import normalize_order


class OrderSupplementService:
    def __init__(self, procurement, config_provider, *, client_factory=AlibabaApiClient):
        self.procurement = procurement
        self.config_provider = config_provider
        self.client_factory = client_factory

    def _line(self, request):
        detail = self.procurement.detail(request.order_id)
        lines = [line for line in detail['lines'] if line['selection']['line_key'] == request.line_key]
        if detail['order']['state'] != 'pending_shipment' or len(lines) != 1:
            raise ValueError('请选择待发货订单中的唯一 SKU')
        line = lines[0]
        if line['selection']['revision'] != request.revision or line['selection']['status'] != 'confirmed':
            raise ValueError('采购来源已变化，请刷新后重新补单')
        if not any(r['status'] == 'purchased' for r in line['records']):
            raise ValueError('尚无有效采购，请先登记首次采购')
        return line

    def lookup(self, body):
        request = SupplementLookupRequest.model_validate(body)
        line = self._line(request)
        config = deepcopy(self.config_provider())
        number = validate_order_number(request.purchase_order_number)
        payload = self.client_factory(config).query(ORDER_DETAIL, number)
        status = normalize_order(payload, number)
        if status['status'] not in {'waitsellersend', 'waitbuyerreceive', 'waitbuyerreceives', 'confirm_goods', 'success'}:
            raise ValueError('请使用已付款且未取消的 1688 订单补单')
        if config != self.config_provider():
            raise ValueError('1688 授权已变化，请重新读取补单')
        self._line(request)
        items = payload.get('result', {}).get('productItems')
        if not isinstance(items, list):
            raise ValueError('1688 未返回采购商品明细')
        candidates = []
        source = line['selection']['source']
        for item in items:
            if not isinstance(item, dict):
                continue
            offer, sku = str(item.get('productID') or ''), str(item.get('skuID') or '')
            count = quantity(item.get('quantity'))
            if not (offer.isascii() and offer.isdigit() and sku.isascii() and sku.isdigit() and count):
                continue
            specification = '；'.join(f"{row.get('name') or ''}：{row.get('value') or ''}"
                                     for row in (item.get('skuInfos') or []) if isinstance(row, dict))
            candidate = {'title': str(item.get('name') or ''), 'specification': specification,
                         'offer_id': offer, 'sku_id': sku, 'spec_id': str(item.get('specId') or ''), 'quantity': count}
            # 指纹绑定确认时看到的完整商品和数量，保存时重新读取，不能静默接受远端变化。
            key = hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            candidates.append({**candidate, 'key': key, 'matched': source['source_sku_id'] == sku
                               and source['product_url'].split('?')[0] == f'https://detail.1688.com/offer/{offer}.html'})
        if not candidates:
            raise ValueError('采购明细缺少商品、SKU 或数量，暂不能自动登记补单')
        return {'ok': True, 'order_number': number, 'order_status': status['status_label'], 'candidates': candidates}

    def record(self, body):
        request = SupplementRecordRequest.model_validate(body)
        lookup = self.lookup(request.model_dump(include=set(SupplementLookupRequest.model_fields)))
        matches = [c for c in lookup['candidates'] if c['key'] == request.candidate_key]
        if len(matches) != 1:
            raise ValueError('采购商品或数量已变化，或规格不唯一，请重新读取后选择')
        candidate = matches[0]
        source = ProcurementSource(source_platform='1688',
            product_url=f"https://detail.1688.com/offer/{candidate['offer_id']}.html",
            source_offer_id=candidate['offer_id'], source_sku_id=candidate['sku_id'], source_spec_id=candidate['spec_id'],
            specification=candidate['specification'] or candidate['title'] or f"SKU {candidate['sku_id']}")
        record_request = RecordPurchaseRequest(order_id=request.order_id, line_key=request.line_key,
            revision=request.revision, request_id=request.request_id, quantity=candidate['quantity'],
            purchase_order_number=lookup['order_number'], receiving_revision=request.receiving_revision, receiving_confirmed=True)
        self.procurement.store.purchase(record_request, self.procurement.accounts_provider(), supplemental_source=source)
        return self.procurement.detail(request.order_id)
