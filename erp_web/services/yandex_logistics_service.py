"""远端 Yandex 发布仓指定物流商与运输模式；本地报单仓不参与。"""
from copy import deepcopy
import hashlib
import json

from erp_web.international_shipping.tariff_store import load_active
from erp_web.marketplaces.yandex_http import fetch_yandex_partner_warehouses, YandexApiError
from erp_web.schemas.logistics import CARRIERS
from erp_web.schemas.yandex_warehouses import SaveYandexWarehouses, account_key
from erp_web.schemas.orders import utc_iso


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def carrier_key(name):
    normalized = str(name).replace(' ', '').upper()
    return next((key for key, label in CARRIERS.items() if label.replace(' ', '').upper() == normalized), '')


def shipping_mode_name(route):
    """轻小件等独立服务保留为单独模式，不自动套用其优惠费率。"""
    group = route['group'].strip()
    return route['service'].strip() + (f' {group}' if group and group != 'Other' else '')


class YandexLogisticsService:
    def __init__(self, config_store, rules_dir, *, snapshot_loader=None):
        self.config_store = config_store
        self.rules_dir = rules_dir
        self.snapshot_loader = snapshot_loader or (lambda platform: load_active(rules_dir, platform))

    def account(self):
        store = self.config_store.load_store_config().get('yandex', {})
        return {key: str(store.get(key) or '') for key in ('business_id', 'campaign_id', 'placement_type', 'stock_update_mode')}

    def identity(self):
        store = self.config_store.load_store_config().get('yandex', {})
        return fingerprint([self.account(), store.get('api_token', '')])

    def configuration(self):
        settings = self.config_store.load_app_config()['yandex_warehouses']
        return settings['accounts'].get(account_key(self.account()), {'revision': 0, 'bindings': []})

    def warehouse_catalog(self, refresh=False):
        identity = self.identity()
        account = self.account()
        cached = self.config_store.load_app_config()['yandex_warehouses']['catalog']
        if refresh:
            self.config_store.save_yandex_warehouse_catalog({})
            if account['placement_type'] != 'FBS' or account['stock_update_mode'] != 'business':
                raise ValueError('按物流选择发布仓库目前要求 Yandex FBS 独立仓库；仓库组不能指定组内单仓')
            store = self.config_store.load_store_config().get('yandex', {})
            if not account['business_id'] or not account['campaign_id'] or not store.get('api_token'):
                raise ValueError('请先完成 Yandex 店铺授权')
            rows = fetch_yandex_partner_warehouses(store['api_token'], account['business_id'])
            items = []
            for row in rows:
                models = [m for m in row.get('models', []) if isinstance(m, dict)
                          and m.get('placementType') == 'FBS' and m.get('apiAvailability') == 'AVAILABLE']
                campaigns = {str(m['campaignId']) for m in models if m.get('campaignId')}
                if not str(row.get('id', '')).isdigit() or not models:
                    continue
                if len(campaigns) != 1:
                    raise ValueError(f"仓库 {row.get('name') or row['id']} 未提供唯一的 FBS 店铺关联")
                items.append({'id': int(row['id']), 'name': str(row.get('name') or row['id']),
                              'campaign_id': campaigns.pop()})
            if identity != self.identity():
                raise ValueError('Yandex 账号已变化，请重新同步发布仓库')
            cached = {'identity': identity, 'items': items, 'checked_at': utc_iso()}
            self.config_store.save_yandex_warehouse_catalog(cached)
        return cached if cached.get('identity') == identity else {'items': [], 'checked_at': ''}

    def routes(self):
        snapshot = self.snapshot_loader('yandex')
        return [{'id': row['id'], 'name': row['name'], 'carrier': carrier_key(row['carrier']),
                 'transport': row['transport'], 'service': row['service'], 'group': row['group']}
                for row in snapshot['content']['rules'] if carrier_key(row['carrier'])]

    @staticmethod
    def mode_bindings(routes, configured):
        """保留旧配置读取能力；跨模式或失效的旧关联必须重新选择。"""
        by_id = {r['id']: r for r in routes}
        bindings, warnings = [], []
        for stored in configured['bindings']:
            binding = {k: stored[k] for k in ('warehouse_id', 'carrier', 'enabled')}
            mode = stored.get('shipping_mode')
            if mode is None:
                selected = [by_id.get(key) for key in stored['route_ids']]
                modes = {shipping_mode_name(r) for r in selected if r and r['carrier'] == stored['carrier']}
                valid = selected and all(r and r['carrier'] == stored['carrier'] for r in selected)
                mode = next(iter(modes)) if valid and len(modes) == 1 else ''
                if not mode:
                    warnings.append({'warehouse_id': stored['warehouse_id'],
                                     'message': '原关联无法确定唯一运输模式，请按 Yandex 后台设置重新选择。'})
            bindings.append({**binding, 'shipping_mode': mode})
        return bindings, warnings

    @staticmethod
    def modes(routes):
        modes = {}
        for route in routes:
            key = (route['carrier'], shipping_mode_name(route))
            mode = modes.setdefault(key, {'carrier': key[0], 'value': key[1], 'transports': set()})
            mode['transports'].add(route['transport'])
        return [{'carrier': m['carrier'], 'value': m['value'], 'transport': ' / '.join(sorted(m['transports']))}
                for _, m in sorted(modes.items())]

    def detail(self, refresh=False):
        errors = []
        try:
            catalog = self.warehouse_catalog(refresh)
        except (ValueError, RuntimeError, YandexApiError) as exc:
            catalog = {'items': [], 'checked_at': ''}
            errors.append(str(exc))
        try:
            routes = self.routes()
        except (ValueError, FileNotFoundError) as exc:
            routes = []
            errors.append(str(exc))
        configured = self.configuration()
        bindings, warnings = self.mode_bindings(routes, configured)
        return {'ok': True, 'account': self.account(), 'revision': configured['revision'],
                'bindings': bindings, 'binding_warnings': warnings, 'modes': self.modes(routes), 'warehouses': catalog['items'],
                'checked_at': catalog.get('checked_at', ''), 'routes': routes, 'error': '；'.join(errors),
                'pricing_options': self.plans(),
                'carriers': [{'value': k, 'label': v} for k, v in CARRIERS.items()]}

    def save(self, body):
        request = SaveYandexWarehouses.model_validate(body)
        identity = self.identity()
        options = self.detail(refresh=any(b.enabled for b in request.bindings))
        if any(getattr(request, k) != options['account'][k] for k in ('business_id', 'campaign_id')):
            raise ValueError('发布仓库映射不属于当前 Yandex 店铺，请重新选择')
        for binding in request.bindings:
            if not binding.enabled:
                continue
            if not any(w['id'] == binding.warehouse_id for w in options['warehouses']):
                raise ValueError(options['error'] or '所选 Yandex 发布仓库已不可用，请重新同步')
            if not any(m['carrier'] == binding.carrier and m['value'] == binding.shipping_mode for m in options['modes']):
                raise ValueError('运输模式已失效或不属于所选物流商，请重新选择')
        if identity != self.identity():
            raise ValueError('Yandex 店铺已变化，请刷新后重新配置')
        self.config_store.save_yandex_warehouse_bindings(request.model_dump())
        return self.detail()

    def plans(self):
        account = self.account()
        if account['placement_type'] != 'FBS' or account['stock_update_mode'] != 'business':
            return []
        warehouses = {w['id']: w for w in self.warehouse_catalog()['items']}
        try:
            routes = {r['id']: r for r in self.routes()}
        except (ValueError, FileNotFoundError):
            return []
        configured = self.configuration()
        plans = []
        bindings, _ = self.mode_bindings(list(routes.values()), configured)
        for binding in bindings:
            warehouse = warehouses.get(binding['warehouse_id'])
            if not binding['enabled'] or not warehouse or not warehouse.get('campaign_id'):
                continue
            for route in routes.values():
                if route['carrier'] != binding['carrier'] or shipping_mode_name(route) != binding['shipping_mode']:
                    continue
                plan = {**account, 'authorization_campaign_id': account['campaign_id'],
                        'campaign_id': warehouse['campaign_id'],
                        'route_id': route['id'], 'route_name': route['name'], 'carrier': route['carrier'],
                        'shipping_mode': binding['shipping_mode'],
                        'publish_warehouse_id': warehouse['id'], 'publish_warehouse_name': warehouse['name'],
                        'configuration_revision': configured['revision']}
                plan['fingerprint'] = fingerprint(plan)
                plans.append(plan)
        return sorted(plans, key=lambda p: (p['route_id'], p['publish_warehouse_id']))

    def pricing_plans(self):
        try:
            bindings, _ = self.mode_bindings(self.routes(), self.configuration())
        except (ValueError, FileNotFoundError):
            return []
        if not any(b['enabled'] and b['shipping_mode'] for b in bindings):
            return []
        try:
            self.warehouse_catalog(refresh=True)
        except (RuntimeError, YandexApiError) as exc:
            raise ValueError(f'Yandex 发布仓库同步失败，无法核验可用物流方案：{exc}') from exc
        return self.plans()

    def configuration_fingerprint(self):
        return fingerprint([self.identity(), self.configuration()])

    def validate(self, plan):
        if not isinstance(plan, dict) or not plan.get('fingerprint'):
            raise ValueError('缺少已核价的远端仓库物流方案，请在系统设置中指定 Yandex 发布仓库运输模式后重新核价')
        current = next((p for p in self.plans() if p == plan), None)
        if current is None:
            raise ValueError('核价使用的 Yandex 发布仓库或运输模式已变化，请重新核价')
        return deepcopy(current)


def draft_fulfillment_plan(draft):
    key = f"yandex:{draft.get('site') or 'global'}".lower()
    quote = (draft.get('pricing') or {}).get('targets', {}).get(key, {})
    return deepcopy(quote.get('calculation_basis', {}).get('shipping_evidence', {}).get('fulfillment_plan', {}))
