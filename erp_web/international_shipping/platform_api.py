"""物流只读 API；凭据由调用方传入，不读取数据库、不刷新或保存令牌。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_CEILING

from .models import Candidate, Package, PriceForShipping, QuoteResult, money, number


class PlatformClient:
    def __init__(self, platform: str, credentials: dict):
        self.platform = platform
        if platform == 'ozon':
            if not credentials.get('client_id') or not credentials.get('api_key'):
                raise ValueError('Ozon 缺少店铺凭据，请先在店铺授权页配置')
            self.base = 'https://api-seller.ozon.ru'
            self.headers = {'Client-Id': str(credentials['client_id']), 'Api-Key': credentials['api_key']}
        elif platform == 'mercadolibre':
            if not credentials.get('access_token'):
                raise ValueError('Mercado 缺少访问令牌，请先在店铺授权页配置')
            self.base = 'https://api.mercadolibre.com'
            self.headers = {'Authorization': 'Bearer ' + credentials['access_token']}
        else:
            raise ValueError('该平台不需要物流 API 客户端')

    def call(self, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
        allowed = (
            self.platform == 'ozon' and path in ('/v2/warehouse/list', '/v2/delivery-method/list') and body is not None
        ) or (
            self.platform == 'mercadolibre' and re.fullmatch(r'/users/\d+/shipping_options/free', path) and body is None
        )
        if not allowed:
            raise ValueError('国际物流模块只允许仓库、渠道查询和运费试算')
        url = self.base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers={**self.headers, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            hint = '，请在店铺授权页检查或刷新授权' if exc.code in (401, 403) else ''
            raise ValueError(f'{self.platform} 物流接口返回 HTTP {exc.code}{hint}') from None
        except (OSError, ValueError):
            raise ValueError(f'{self.platform} 物流接口不可用或响应无效') from None
        if not isinstance(result, dict):
            raise ValueError(f'{self.platform} 物流接口返回了无效结构')
        return result


def discover_ozon(client: PlatformClient) -> list[dict]:
    warehouses, cursor = [], ''
    for _ in range(100):
        body = client.call('/v2/warehouse/list', body={'cursor': cursor, 'limit': 100})
        warehouses.extend(body.get('warehouses', []))
        if not body.get('has_next'):
            break
        next_cursor = body.get('cursor')
        if not next_cursor or next_cursor == cursor:
            raise ValueError('Ozon 仓库列表分页不完整')
        cursor = next_cursor
    else:
        raise ValueError('Ozon 仓库列表超过分页上限')
    ids = {str(item['warehouse_id']) for item in warehouses if item.get('is_rfbs') and item.get('status') == 'created'}
    methods, offset = [], 0
    for _ in range(100):
        body = client.call('/v2/delivery-method/list', body={'limit': 50, 'offset': offset})
        page = body.get('delivery_methods', [])
        methods.extend(item for item in page if str(item.get('warehouse_id')) in ids and item.get('status') == 'ACTIVE')
        if body.get('has_next') is False or ('has_next' not in body and len(page) < 50):
            break
        if not page:
            raise ValueError('Ozon 配送方式分页不完整')
        offset += len(page)
    else:
        raise ValueError('Ozon 配送方式超过分页上限')
    return methods


def quote_mercadolibre(
    client: PlatformClient,
    package: Package,
    binding: dict,
    price_for_shipping: PriceForShipping,
    *,
    cny_per_usd: Decimal,
    category_id: str = '',
    listing_type_id: str = 'gold_pro',
    free_shipping: bool = True,
) -> QuoteResult:
    """按原实验迭代报价与售价，返回原币金额；不把远端业务类型当作试算物流类型。"""
    user_id = str(binding.get('user_id') or '')
    if not user_id.isdigit():
        raise ValueError('Mercado 销售市场缺少授权子账号 ID，请重新检查店铺授权')
    cny_per_usd = number(cny_per_usd, 'USD 换算汇率', positive=True)
    endpoint = f'/users/{user_id}/shipping_options/free'
    dims = [str(value.to_integral_value(rounding=ROUND_CEILING)) for value in (package.height_cm, package.width_cm, package.length_cm)]
    grams = package.weight_g.to_integral_value(rounding=ROUND_CEILING)
    price = money(price_for_shipping(Decimal(0), 'USD', Decimal(0)) / cny_per_usd)
    if price <= 0:
        raise ValueError('Mercado 运费试算需要有效候选售价')
    for iteration in range(8):
        # 延续原实验已验证的试算请求口径；不是从 remote 猜测出的配送类型。
        params = {
            'dimensions': 'x'.join(dims) + ',' + str(grams), 'item_price': str(price),
            'currency_id': 'USD', 'listing_type_id': listing_type_id, 'condition': 'new',
            'mode': 'me2', 'logistic_type': 'drop_off', 'verbose': 'true', 'free_shipping': str(free_shipping).lower(),
        }
        if category_id:
            params['category_id'] = category_id
        body = client.call(endpoint, params=params)
        coverage = body.get('coverage')
        quote = coverage.get('all_country') if isinstance(coverage, dict) else None
        if not isinstance(quote, dict):
            raise ValueError('Mercado 响应缺少 all_country 运费报价')
        amount = number(quote.get('list_cost'), 'Mercado 运费', positive=True)
        currency = str(quote.get('currency_id') or '').strip().upper()
        if not currency:
            raise ValueError('Mercado 运费响应缺少币种')
        wanted = money(price_for_shipping(amount, currency, Decimal(0)) / cny_per_usd)
        if wanted == price:
            billable = number(quote.get('billable_weight', grams), 'Mercado 计费重', positive=True)
            candidate = Candidate(
                f'{binding.get("site_id", "")}:{binding.get("logistic_type", "")}',
                f'Mercado {binding.get("site_id", "")} all_country', amount, currency, billable,
                evidence={
                    'source': 'platform_api', 'endpoint': '/users/{user_id}/shipping_options/free',
                    'site_id': binding.get('site_id'), 'logistic_type': binding.get('logistic_type'),
                    'request': params, 'iterations': iteration + 1,
                    'limitation': 'API all_country 运费试算；非最终结算账单，不重复扣减 discount',
                },
            )
            return QuoteResult([candidate])
        price = wanted
    raise ValueError('Mercado 运费与售价在 8 轮内未收敛，请调整输入后重新核价')
