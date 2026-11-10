"""同一草稿的所选 SKU 共用最低合计运费的可用发布仓库。"""
from copy import deepcopy
from decimal import Decimal
from erp_web.services.pricing_service import pricing_calculation_fingerprint


def common_warehouse(quotes):
    options = []
    for quote in quotes:
        costs = {}
        for candidate in quote.get('shipping_candidates', []):
            plan = candidate.get('fulfillment_plan') or {}
            warehouse = plan.get('publish_warehouse_id')
            if warehouse is None:
                continue
            amount = Decimal(candidate['amount_cny'])
            costs[warehouse] = min(costs.get(warehouse, amount), amount)
        options.append(costs)
    common = set.intersection(*(set(costs) for costs in options)) if options else set()
    return min(common, key=lambda warehouse: (sum(costs[warehouse] for costs in options), warehouse)) if common else None


def unify_yandex_warehouse(session, items, results):
    entries = []
    for index, item in enumerate(items):
        targets = [target for target in item['input']['targets'] if target.get('platform') == 'yandex']
        if targets:
            quotes = [quote for quote in results[index].get('results', []) if quote.get('platform') == 'yandex']
            entries.append((index, targets, quotes))
    if not entries:
        return results
    if any(not quotes or any(not quote.get('ok') for quote in quotes) for _, _, quotes in entries):
        return results
    warehouse = common_warehouse([quote for _, _, quotes in entries for quote in quotes])
    if warehouse is None:
        message = '所选 SKU 没有共同可用的发布仓库，不能拆仓核价'
        for index, _, quotes in entries:
            for quote in quotes:
                quote.update(ok=False, errors=[*quote.get('errors', []), {'field': 'shipping', 'message': message}])
            results[index].update(ok=False, error=message, error_code='YANDEX_NO_COMMON_WAREHOUSE')
        return results
    originals = {(index, quote.get('target_key') or quote.get('site', '')): deepcopy(quote)
                 for index, _, quotes in entries for quote in quotes}
    costs_by_quote = []
    for _, _, quotes in entries:
        for quote in quotes:
            costs = {}
            for candidate in quote.get('shipping_candidates', []):
                key = candidate.get('fulfillment_plan', {}).get('publish_warehouse_id')
                if key is not None:
                    amount = Decimal(candidate['amount_cny'])
                    costs[key] = min(costs.get(key, amount), amount)
            costs_by_quote.append(costs)
    warehouse_selection = {
        'basis': 'all_selected_skus_one_each', 'warehouse_id': warehouse, 'quote_count': len(costs_by_quote),
        'warehouses': [{'warehouse_id': key, 'eligible_quote_count': sum(key in costs for costs in costs_by_quote),
                        'total_cny': str(sum(costs[key] for costs in costs_by_quote)) if all(key in costs for costs in costs_by_quote) else None}
                       for key in sorted({key for costs in costs_by_quote for key in costs})],
    }
    session.shipping.yandex_plans = [plan for plan in session.shipping.yandex_plans if plan['publish_warehouse_id'] == warehouse]
    changed = [(index, targets) for index, targets, quotes in entries
               if any(quote.get('calculation_basis', {}).get('shipping_evidence', {}).get('fulfillment_plan', {}).get('publish_warehouse_id') != warehouse for quote in quotes)]
    recalculated = session.calculate_many([{**items[index]['input'], 'targets': targets} for index, targets in changed])
    for (index, _), replacement in zip(changed, recalculated, strict=True):
        result = results[index]
        replacements = iter(replacement.get('results', []))
        result['results'] = [next(replacements) if quote.get('platform') == 'yandex' else quote
                             for quote in result.get('results', [])]
        result['targets'] = result['results']
        if result['results'][0].get('platform') == 'yandex':
            result.update({key: value for key, value in replacement.items()
                           if key not in ('ok', 'results', 'targets', 'input', 'errors', 'precheck_errors', 'exchange_rates')})
        result['ok'] = replacement.get('ok', False) and all(quote.get('ok') for quote in result['results'])
        result['errors'] = [error for quote in result['results'] for error in quote.get('errors', [])]
        result['precheck_errors'] = result['errors']
        if not replacement.get('ok'):
            result['error'] = replacement.get('error') or '统一仓库核价未完成'
    for index, _, _ in entries:
        for quote in results[index].get('results', []):
            if quote.get('platform') != 'yandex':
                continue
            original = originals[(index, quote.get('target_key') or quote.get('site', ''))]
            evidence = quote.get('calculation_basis', {}).get('shipping_evidence', {})
            decision = evidence.setdefault('decision', {})
            original_decision = original.get('calculation_basis', {}).get('shipping_evidence', {}).get('decision', {})
            decision.update(deepcopy(original_decision), warehouse_selection=warehouse_selection)
            quote['shipping_candidates'] = original.get('shipping_candidates', [])
            quote['calculation_fingerprint'] = pricing_calculation_fingerprint(quote['calculation_basis'])
            for destination in quote.get('destination_results', []):
                destination['calculation_fingerprint'] = quote['calculation_fingerprint']
        first = results[index].get('results', [{}])[0]
        if first.get('platform') == 'yandex':
            results[index].update({key: first[key] for key in ('calculation_basis', 'calculation_fingerprint', 'shipping_candidates')})
    return results
