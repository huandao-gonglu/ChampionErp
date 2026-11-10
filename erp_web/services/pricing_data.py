"""普通业务查询直接读取现有草稿和核价预览，不触发模型或重新核价。"""
from copy import deepcopy


def draft_pricing_data(products, cache, request):
    content, error, _ = products.load_draft_content(request.draft_id)
    if error:
        raise ValueError('草稿不存在')
    draft, product = content['draft'], content['product']
    by_id = {row['id']: row for row in product.get('sku_items', [])}
    pricing = {row['sku_id']: row.get('pricing', {}) for row in draft.get('sku_items', [])}
    state = 'saved'
    if request.preview_id:
        cached = cache.previews.get(request.preview_id)
        if not cached or cached['draft_id'] != request.draft_id:
            raise ValueError('核价预览不存在、已过期或不属于此草稿')
        pricing = {row['sku_id']: {'targets': {q['target_key']: q for q in row['result'].get('results', [])}}
                   for row in cached['batch']['items']}
        state = 'preview'
    rows = []
    for row in draft.get('sku_items', []):
        if not row.get('selected') or request.sku_id and row['sku_id'] != request.sku_id:
            continue
        facts = by_id.get(row['sku_id'], {})
        for target_key, quote in pricing.get(row['sku_id'], {}).get('targets', {}).items():
            if request.target_key and request.target_key != target_key:
                continue
            evidence = quote.get('calculation_basis', {}).get('shipping_evidence', {})
            rows.append({'applied_price': None, 'calculation_basis': {'shipping_evidence': {}},
                         'sku_id': row['sku_id'], 'sku': row.get('sku', ''), 'target_key': target_key,
                         'current_package': deepcopy(facts.get('package_dimensions', {})), **deepcopy(quote),
                         'shipping_summary': {'route': evidence.get('route', ''), 'tariff_version': evidence.get('tariff_version', ''),
                             'warehouse': evidence.get('fulfillment_plan', {}).get('publish_warehouse_name', ''),
                             'decision_available': bool(evidence.get('decision')),
                             'decision_status': '已记录实际筛选过程' if evidence.get('decision') else '历史报价未保存筛选记录，不能据此认定渠道被拒绝',
                             'decision_path': 'calculation_basis.shipping_evidence.decision' if evidence.get('decision') else '',
                             'candidates_path': 'shipping_candidates',
                             'limitation': evidence.get('limitation', '')}})
    return {'state': state, 'updated_at': draft.get('updated_at', ''), 'preview_id': request.preview_id,
            'warnings': ['当前包装可能已修改；历史计算应以 calculation_basis 中的参数与费率版本为准。'], 'rows': rows}


__all__ = ['draft_pricing_data']
