"""业务查询组合根：一份资源声明同时服务页面和 Agent，不注册 AI 专用查询。"""
from erp_web.schemas.business_data import DraftDataParameters, NoDataParameters, PricingDataParameters, ProductDataParameters
from erp_web.schemas.managed_settings import TariffRulesQuery
from erp_web.services.business_data import BusinessDataCatalog, BusinessResource
from erp_web.services.pricing_data import draft_pricing_data


def build_business_data_catalog(context):
    def draft_content(request):
        content, error, _ = context.products.load_draft_content(request.draft_id)
        if error:
            raise ValueError('草稿不存在')
        return content['draft']

    def product_skus(request):
        product, error, _ = context.products.load_required_product_from_body({'product_id': request.product_id})
        if error:
            raise ValueError('商品不存在')
        return {'product_id': request.product_id, 'rows': product.get('sku_items', [])}

    return BusinessDataCatalog((
        BusinessResource('shipping.tariff_rules', '国际物流运费表、渠道尺寸重量货值限制、电池准入和费率；按 platform 查询。', TariffRulesQuery,
                         lambda request: context.tariffs.rules(request), 'rules',
                         ('id', 'carrier', 'service', 'group', 'max_g', 'max_edge_cm', 'max_sum_cm', 'price_max',
                          'price_currency', 'fixed', 'per_g', 'currency', 'step_g', 'battery', 'liquid'), permission='pricing.read'),
        BusinessResource('shipping.tariff_versions', '当前国际物流运费表来源、版本与更新状态。', NoDataParameters,
                         lambda _: context.tariffs.detail(), 'items', permission='pricing.read'),
        BusinessResource('yandex.warehouses', 'Yandex 已配置发布仓、运输模式及可比价物流方案；只读本地目录，不刷新远端。', NoDataParameters,
                         lambda _: context.yandex_logistics.detail(), 'pricing_options', permission='pricing.read'),
        BusinessResource('pricing.templates', '已有核价模板、固定费用及定价配置。', NoDataParameters,
                         lambda _: {'ok': True, **context.config.load_app_config()['pricing_templates']}, permission='pricing.read'),
        BusinessResource('draft.content', '草稿完整业务资料，可展开 pricing 参数、sku_items 及已保存核价明细；不重新核价。', DraftDataParameters,
                         draft_content, '', ('draft_id', 'product_id', 'platform', 'title', 'updated_at'), permission='draft.read'),
        BusinessResource('draft.pricing', '逐 SKU 核价明细、包装、所选物流、候选报价、筛选依据和统一选仓理由。preview_id 可读取尚未应用的预览；历史缺失依据不能猜测。', PricingDataParameters,
                         lambda request: draft_pricing_data(context.products, context.pricing_cache, request), 'rows',
                         ('sku_id', 'target_key', 'applied_price', 'current_package', 'shipping_summary'), permission='pricing.read'),
        BusinessResource('product.skus', '商品全部规格事实、成本及包装尺寸重量；读取不修改资料。', ProductDataParameters,
                         product_skus, 'rows', ('id', 'package_dimensions'), permission='product.read'),
    ))


__all__ = ['build_business_data_catalog']
