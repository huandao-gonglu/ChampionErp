"""普通业务查询组合根：只声明一次，自动生成页面路由及 AI 可发现的数据目录。"""
from erp_web.schemas.business_data import DraftDataParameters, NoDataParameters, PricingDataParameters, ProductDataParameters
from erp_web.schemas.managed_settings import TariffRulesQuery
from erp_web.services.business_queries import BusinessQueries, BusinessQuery
from erp_web.services.pricing_data import draft_pricing_data


def draft_content(context, request):
    content, error, _ = context.products.load_draft_content(request.draft_id)
    if error:
        raise ValueError('草稿不存在')
    return content['draft']


def product_skus(context, request):
    product, error, _ = context.products.load_required_product_from_body({'product_id': request.product_id})
    if error:
        raise ValueError('商品不存在')
    return {'product_id': request.product_id, 'rows': product.get('sku_items', [])}


BUSINESS_QUERIES = BusinessQueries((
    BusinessQuery('shipping.tariff_rules', '国际物流运费表：轻小件 Extra Small、普通件 Other 的尺寸重量售价限制、电池准入和费率；按 platform 查询。', TariffRulesQuery,
                  lambda context, request: context.tariffs.rules(request), 'rules',
                  ('id', 'carrier', 'service', 'group', 'max_g', 'max_edge_cm', 'max_sum_cm', 'price_max',
                   'price_currency', 'fixed', 'per_g', 'currency', 'step_g', 'battery', 'liquid'), permission='pricing.read', http_path='/api/shipping/tariffs/rules'),
    BusinessQuery('shipping.tariff_versions', '当前国际物流运费表来源、版本与更新状态。', NoDataParameters,
                  lambda context, _: context.tariffs.detail(), 'items', permission='pricing.read', http_path='/api/shipping/tariffs'),
    BusinessQuery('yandex.warehouses', 'Yandex 核价/刊登使用的远端发布仓、运输模式与比价物流方案。核价候选来自此处，不由本地报单收货地址决定；只读缓存，不刷新远端。', NoDataParameters,
                  lambda context, _: context.yandex_logistics.detail(), 'pricing_options', permission='pricing.read', http_path='/api/yandex/warehouses'),
    BusinessQuery('pricing.templates', '已有核价模板、固定费用及定价配置。', NoDataParameters,
                  lambda context, _: {'ok': True, **context.config.load_app_config()['pricing_templates']}, permission='pricing.read', http_path='/api/pricing/templates'),
    BusinessQuery('draft.content', '草稿完整业务资料，可展开 pricing 参数、sku_items 及已保存核价明细；不重新核价。', DraftDataParameters,
                  draft_content, '', ('draft_id', 'product_id', 'platform', 'title', 'updated_at'), permission='draft.read'),
    BusinessQuery('draft.pricing', '逐 SKU 核价明细。shipping_summary 给出候选与决策的相对字段路径，用 fields 读取 sku_id 和这些路径即可核对物流，无需载入整份佣金回执或重复读取摘要。preview_id 读取未应用预览；历史未记录的筛选原因不能猜测。', PricingDataParameters,
                  lambda context, request: draft_pricing_data(context.products, context.pricing_cache, request), 'rows',
                  ('sku_id', 'target_key', 'applied_price', 'current_package', 'shipping_summary'), permission='pricing.read'),
    BusinessQuery('product.skus', '商品全部规格事实、成本及包装尺寸重量；读取不修改资料。', ProductDataParameters,
                  product_skus, 'rows', ('id', 'package_dimensions'), permission='product.read'),
    BusinessQuery('logistics.settings', '报单货代、采购收货仓及地址配置；包含启用/默认状态和已确认物流商承接规则；用于采购/报单，不参与核价选渠道。缺少本地收货点不能据此排除核价渠道，核价发布仓见 yandex.warehouses。warehouses 为合作仓目录，carriers 为可配置枚举，不代表已经配置。只读本地缓存，不刷新远端。', NoDataParameters,
                  lambda context, _: context.fulfillment.receiving.logistics.detail(), 'addresses',
                  ('id', 'name', 'warehouse_id', 'warehouse_name', 'enabled', 'is_default', 'rules', 'remark'),
                  permission='order.read', http_path='/api/logistics/settings'),
    BusinessQuery('fulfillment.settings', '跨境巴士报单授权状态、合作渠道/仓库目录和默认报单规则；rules 是已保存规则，delivery_choices 只是订单配送来源。Yandex 另需读取 logistics.settings 的收货地址和承接规则，不能因默认规则为空认定未配置。只读本地资料，不自动报单。', NoDataParameters,
                  lambda context, _: context.fulfillment.settings(), '',
                  ('authorized', 'secret_configured', 'user_name', 'expires_at', 'rules'),
                  permission='order.read', http_path='/api/crossborderbus/settings'),
))


def build_business_data_catalog(context):
    return BUSINESS_QUERIES.bind(context)


__all__ = ['BUSINESS_QUERIES', 'build_business_data_catalog']
