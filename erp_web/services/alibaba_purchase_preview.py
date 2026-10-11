"""1688 多商品预览校验；分组取平台回执，商品映射取冻结的 offer/SKU/spec。"""
import time
from collections import defaultdict

from erp_web.services.alibaba_api_client import AlibabaApiError
from erp_web.services.alibaba_purchase_assignment import quantity


def item_key(item):
    return item['candidate']['offer_id'], item['candidate']['sku_id'], item['spec_id']


def cargo_params(items):
    quantities = defaultdict(int)
    for item in items:
        quantities[item_key(item)] += item['quantity']
    if len({(offer, spec) for offer, _sku, spec in quantities}) != len(quantities):
        raise AlibabaApiError('同一下单规格对应多个 SKU 编号，请重新核对采购来源')
    return [{'offerId': int(offer), 'specId': spec, 'quantity': quantity}
            for (offer, _sku, spec), quantity in sorted(quantities.items())]


def preview_groups(payload, items, address):
    rows = payload.get('orderPreviewResuslt')
    if not isinstance(rows, list) or not rows:
        raise AlibabaApiError('未返回有效订单预览，请核对起订量、库存及收货地址')
    expected = defaultdict(list)
    for item in items:
        expected[item_key(item)].append(item)
    seen, result = set(), []
    for row in rows:
        if row.get('status') not in (True, 'true'):
            raise AlibabaApiError('部分商品预览失败，请核对起订量、库存及收货地址后重新预览')
        cargo = row.get('cargoList')
        if not isinstance(cargo, list) or not cargo:
            raise AlibabaApiError('预览缺少商品明细')
        group = []
        for value in cargo:
            key = str(value.get('offerId')), str(value.get('skuId')), str(value.get('specId'))
            if key not in expected or key in seen:
                raise AlibabaApiError('1688 预览的商品、SKU 或规格与请求不一致')
            matching = expected[key]
            if 'quantity' in value and value['quantity'] != sum(i['quantity'] for i in matching):
                raise AlibabaApiError('1688 预览的商品数量与请求不一致')
            seen.add(key)
            group.extend(matching)
        group.sort(key=lambda item: item['binding']['line_key'])
        if 'assureTrade' not in row.get('tradeModeNameList', []):
            raise AlibabaApiError('该商品未提供已接入的担保交易流程')
        flow = str(row.get('flowFlag') or '')
        if not flow:
            raise AlibabaApiError('预览未返回交易流程')
        amounts = [row.get(k) for k in ('sumPaymentNoCarriage', 'sumCarriage', 'sumPayment')]
        if any(type(a) is not int or a < 0 for a in amounts) or amounts[2] <= 0 or amounts[0] + amounts[1] != amounts[2]:
            raise AlibabaApiError('1688 返回的预览金额无效，不能提交')
        channels = sorted({p['name'] for p in row.get('payChannelInfos', []) if p.get('name') in {'alipay', 'shegou'}})
        if not channels:
            raise AlibabaApiError('预览未提供已接入的支付渠道')
        view = {'candidate': group[0]['candidate'], 'quantity': sum(i['quantity'] for i in group),
                'items': [{'line_key': i['binding']['line_key'], 'title': i['title'], 'supplier': i['supplier'],
                           'candidate': i['candidate'], 'quantity': i['quantity']} for i in group],
                'supplier': ' / '.join(dict.fromkeys(i['supplier'] for i in group if i['supplier'])) or '1688 供应商',
                'recipient': address['fullName'], 'phone': address['mobile'] or address['phone'],
                'address': ' '.join(address[k] for k in ('provinceText', 'cityText', 'areaText', 'townText', 'address') if address[k]),
                'goods_fen': amounts[0], 'shipping_fen': amounts[1], 'total_fen': amounts[2],
                'flow': flow, 'pay_channels': channels, 'expires_at': time.time() + 600}
        result.append((group, view))
    if seen != set(expected):
        raise AlibabaApiError('1688 预览缺少部分商品，已停止下单')
    return result


def verify_group_order(payload, items):
    """核验未知创建回执，要求完整 SKU 集合和总数量吻合。"""
    expected, actual = defaultdict(int), defaultdict(int)
    for item in items:
        expected[item_key(item)] += item['quantity']
    rows = payload.get('result', {}).get('productItems', [])
    if not isinstance(rows, list):
        raise AlibabaApiError('返回订单缺少商品明细，不能关联')
    for row in rows:
        key = str(row.get('productID')), str(row.get('skuID')), str(row.get('specId'))
        count = quantity(row.get('quantity'))
        if not count:
            raise AlibabaApiError('返回订单缺少商品数量，不能关联')
        actual[key] += count
    if actual != expected:
        raise AlibabaApiError('返回订单的商品、规格或数量与批量采购不一致')
