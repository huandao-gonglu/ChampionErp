"""普通业务查询组合根：只声明一次，自动生成页面路由及 AI 可发现的数据目录。"""
from erp_web.schemas.business_data import DraftDataParameters, DraftIndexParameters, NoDataParameters, PricingDataParameters, ProductDataParameters
from erp_web.schemas.managed_settings import TariffRulesQuery
from erp_web.services.business_queries import BusinessQueries, BusinessQuery
from erp_web.services.pricing_data import draft_pricing_data
from erp_web.services import config_queries
from erp_web.services.operation_availability import operation_availability
from erp_web.schemas.platform_query_capabilities import PublishJobsQueryRequest, PublishJobStatusQueryRequest, PublishLogsQueryRequest


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
    BusinessQuery('settings.system', '系统设置：订单自动同步间隔、图片水印、微信转发类型；水印图片内容可展开，默认仅读取文字和样式。', NoDataParameters,
                  lambda context, _: context.config.system_settings(), '',
                  ('orders_auto_sync_interval_hours', 'watermark.text', 'watermark.opacity', 'watermark.size_percent', 'watermark.density', 'watermark.color', 'wechat_forward_types'),
                  http_path='/api/system-settings'),
    BusinessQuery('settings.forbidden_categories', '共享 Yandex 禁售类目名单及导入来源；按 categories 分页，未列出不等于平台允许发布。', NoDataParameters,
                  lambda context, _: context.config.forbidden_categories(), 'categories', ('category_id', 'name'), http_path='/api/system-settings/forbidden-categories'),
    BusinessQuery('settings.image_hosting', '图片托管配置、默认目标、公开域名与最近保存的测试结果。密钥只提供是否配置，历史测试不代表当前实时连通。', NoDataParameters,
                  lambda context, _: {'ok': True, 'image_hosting': context.config.public_image_hosting()}, 'image_hosting',
                  ('default_profile_id', 'profiles'), http_path='/api/image-hosting'),
    BusinessQuery('settings.models', '已配置 AI 模型名称、启用状态、模型类型及功能绑定；不读取密钥、命令、浏览器配置或本机文件。', NoDataParameters,
                  config_queries.model_settings, 'models', ('id', 'name', 'enabled', 'model', 'connection_type', 'capabilities')),
    BusinessQuery('settings.model_bindings', 'AI 功能与模型的绑定及生成参数；未显式绑定的功能不代表没有可用模型，运行时按模型能力选择。', NoDataParameters,
                  lambda context, request: config_queries.model_settings(context, request)['bindings']),
    BusinessQuery('settings.product_research', '选品搜索市场、数据源、启用状态、搜索策略、限额与缓存配置；不执行搜索或提供数据源密钥。', NoDataParameters,
                  config_queries.research_settings, '', ('search_defaults', 'provider_runtime', 'target_markets')),
    BusinessQuery('settings.store', '店铺名称、平台/站点、业务仓标识、刊登默认值及本地授权状态。实际发布币种以各店铺 currency_configuration 为准，listing 只是全局默认值；不刷新授权，不提供凭据。', NoDataParameters,
                  config_queries.store_settings, 'stores'),
    BusinessQuery('settings.collection', '1688 采集自动识别偏好、Cookie 是否保存及 API 超时；配置状态不代表授权实时有效。', NoDataParameters,
                  config_queries.collection_settings),
    BusinessQuery('settings.pricing_defaults', '核价默认费用、利润率、汇率接口超时和缓存时长；不返回可能含凭据的接口地址。', NoDataParameters,
                  config_queries.pricing_defaults, permission='pricing.read'),
    BusinessQuery('settings.approval', '业务工具审批模式：ask 为询问审批，full 为完全授权；只读当前保存偏好，不代表任何额外业务授权。', NoDataParameters,
                  lambda context, _: {'mode': context.config.ai_tool_approval_mode()}),
    BusinessQuery('operations.availability', '当前请求的模型与桌面能力限制；仅描述本次连接，不代表其他设备都不可用。', NoDataParameters,
                  lambda context, _: {'ok': True, **operation_availability(context.config.load_app_config())}, http_path='/api/operation-availability'),
    BusinessQuery('products.index', '当前空间商品索引，按需分页读取；按列表位置选择商品仍使用带快照的商品查询能力。', NoDataParameters,
                  lambda context, _: {'ok': True, 'items': context.products.load_products_index()}, 'items',
                  ('id', 'product_id', 'name', 'title', 'updated_at'), permission='product.read', http_path='/api/products-index'),
    BusinessQuery('drafts.index', '草稿索引；scope 指定活动、归档或全部，按位置引用仍使用带快照的草稿查询能力。', DraftIndexParameters,
                  lambda context, request: {'ok': True, 'items': context.products.load_drafts_index(request.scope)}, 'items',
                  ('draft_id', 'product_id', 'platform', 'title', 'updated_at'), permission='draft.read', http_path='/api/drafts-index'),
    BusinessQuery('publish.logs', '本地发布日志；只读历史，日志不代表平台实时状态。', PublishLogsQueryRequest,
                  lambda context, request: {'ok': True, 'items': context.db.list_publish_logs(limit=request.limit)}, 'items',
                  permission='publish.read', http_path='/api/publish-logs'),
    BusinessQuery('publish.jobs', '发布任务分页列表，状态与平台过滤；读取不重试、不发布、不轮询。', PublishJobsQueryRequest,
                  lambda context, request: {'ok': True, **context.publishing_bus.list_jobs(**request.model_dump())}, 'items',
                  ('job_id', 'product_name', 'status', 'updated_at'),
                  permission='publish.read', http_path='/api/publish-bus/jobs'),
    BusinessQuery('publish.job', '指定发布任务的公开状态；读取不推进或重试任务。', PublishJobStatusQueryRequest,
                  lambda context, request: {'ok': True, 'job': context.publishing_bus.get_public_status(request.job_id)},
                  permission='publish.read', http_path='/api/publish-bus/status', not_found_status=404),
))


def build_business_data_catalog(context):
    return BUSINESS_QUERIES.bind(context)


__all__ = ['BUSINESS_QUERIES', 'build_business_data_catalog']
