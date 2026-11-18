"""1688 多商品预览校验；分组取平台回执，商品映射取冻结的 offer/SKU/spec。"""
import time
from collections import defaultdict

from erp_web.services.alibaba_api_errors import AlibabaApiError


def item_key(item):
    return item['candidate']['offer_id'], item['candidate']['sku_id'], item['spec_id']


def group_cargo(items):
    quantities = defaultdict(int)
    for item in items:
        quantities[item_key(item)] += item['quantity']
    if len({(offer, spec) for offer, _sku, spec in quantities}) != len(quantities):
        raise AlibabaApiError('同一下单规格对应多个 SKU 编号，请重新核对采购来源')
    return [{'offer_id': offer, 'spec_id': spec, 'quantity': quantity}
            for (offer, _sku, spec), quantity in sorted(quantities.items())]


def preview_groups(previews, items, address):
    expected = defaultdict(list)
    for item in items:
        expected[item_key(item)].append(item)
    seen, result = set(), []
    for preview in previews:
        group = []
        for item in preview.items:
            key = item.offer_id, item.sku_id, item.spec_id
            if key not in expected or key in seen:
                raise AlibabaApiError('1688 预览的商品、SKU 或规格与请求不一致')
            matching = expected[key]
            if item.quantity_reported and item.quantity != sum(i['quantity'] for i in matching):
                raise AlibabaApiError('1688 预览的商品数量与请求不一致')
            seen.add(key)
            group.extend(matching)
        group.sort(key=lambda item: item['binding']['line_key'])
        view = {'candidate': group[0]['candidate'], 'quantity': sum(i['quantity'] for i in group),
                'items': [{'line_key': i['binding']['line_key'], 'title': i['title'], 'supplier': i['supplier'],
                           'candidate': i['candidate'], 'quantity': i['quantity']} for i in group],
                'supplier': ' / '.join(dict.fromkeys(i['supplier'] for i in group if i['supplier'])) or '1688 供应商',
                'recipient': address['fullName'], 'phone': address['mobile'] or address['phone'],
                'address': ' '.join(address[k] for k in ('provinceText', 'cityText', 'areaText', 'townText', 'address') if address[k]),
                'goods_fen': preview.goods_fen, 'shipping_fen': preview.shipping_fen, 'total_fen': preview.total_fen,
                'flow': preview.flow, 'pay_channels': sorted(set(preview.pay_channels)), 'expires_at': time.time() + 600}
        result.append((group, view))
    if seen != set(expected):
        raise AlibabaApiError('1688 预览缺少部分商品，已停止下单')
    return result


def verify_group_order(order, items):
    """核验未知创建回执，要求完整 SKU 集合和总数量吻合。"""
    if not order.products_complete:
        raise AlibabaApiError('返回订单缺少商品明细，不能关联')
    expected, actual = defaultdict(int), defaultdict(int)
    for item in items:
        expected[item_key(item)] += item['quantity']
    for product in order.products:
        key = product['offer_id'], product['sku_id'], product['spec_id']
        count = product['quantity']
        if not count:
            raise AlibabaApiError('返回订单缺少商品数量，不能关联')
        actual[key] += count
    if actual != expected:
        raise AlibabaApiError('返回订单的商品、规格或数量与批量采购不一致')
