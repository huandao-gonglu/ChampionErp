"""普通读取覆盖账本：新配置和新增 GET 必须落入统一查询或明确的领域/协议边界。"""

# 值是公开业务视图；含凭据的字段仅提供配置状态，不能读取原值。
APP_CONFIG_READS = {
    'system_settings': 'settings.system', 'forbidden_categories': 'settings.forbidden_categories',
    'image_hosting': 'settings.image_hosting', 'ai_tool_approval_mode': 'settings.approval',
    'auto_ai_recognition': 'settings.collection', 'alibaba_cookie': 'settings.collection',
    '1688_api': 'settings.collection', 'ai_models': 'settings.models',
    'ai_use_case_bindings': 'settings.model_bindings', 'pricing_defaults': 'settings.pricing_defaults',
    'pricing_templates': 'pricing.templates', 'yandex_warehouses': 'yandex.warehouses',
    'product_research': 'settings.product_research',
}

RETAINED_GET_READS = {
    **{path: ('身份协议', '登录、用户管理、配对或授权协议，不是普通业务配置读取。') for path in (
        '/api/account/registration-config', '/api/account/session', '/api/admin/users', '/api/account/newapi',
        '/api/alibaba/crossborder/authorization', '/api/alibaba/authorization', '/api/wechat/status',
    )},
    **{path: ('原生对话协议', '原生历史、事件订阅及对话投影，保留专门协议。') for path in (
        '/api/v1/ai-work/conversations', '/api/v1/ai-work/conversations/',
        '/api/v1/ai-work/conversations/<conversation_id>/title',
        '/api/v1/ai-work/conversations/<conversation_id>/ui-messages',
        '/api/v1/ai-work/conversations/<conversation_id>/events',
        '/api/v1/ai-work/conversations/<conversation_id>/image-batch',
    )},
    **{path: ('领域读取', '已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。') for path in (
        '/api/tasks', '/api/orders', '/api/orders/summary', '/api/orders/detail', '/api/orders/address-note',
        '/api/orders/receiving-warehouse', '/api/orders/fulfillment', '/api/orders/alibaba-purchase',
        '/api/orders/alibaba-purchase/batch', '/api/orders/alibaba-purchase/addresses', '/api/crossborderbus/services',
        '/api/online-products', '/api/online-products/yandex-shop-names', '/api/online-products/source-images',
        '/api/online-products/attributes',
    )},
    **{path: ('个人万邦直购', '独立于 ERP 业务环节的 1688 个人万邦直购状态、独立记录及远端 1688 地址读取，不进入 ERP 业务数据目录。') for path in (
        '/api/alibaba/direct-purchase/state',
        '/api/alibaba/direct-purchase/addresses',
    )},
    '/api/admin/settings': ('凭据视图', '企业凭据、1688 接入方案选择、共享 Gemini Canvas 项目地址和可能携带访问参数的汇率地址。买家凭据通过个人授权保存，业务默认配置见 settings.pricing_defaults/settings.collection。'),
    '/api/orders/integrations': ('凭据视图', '包含带回调凭据的地址；普通店铺及本地授权状态见 settings.store。'),
    '/api/ai-config': ('本机连接协议', '完整模型设置带本机连接桥接；模型和绑定业务元数据见 settings.models/settings.model_bindings。'),
    '/api/state': ('受信组合视图', '包含审批凭据、目录和媒体传输引用；普通配置及索引从其统一查询读取。'),
    '/api/system-settings/user-agents': ('用户规则', '设置界面读取可编辑文档；当前账号的偏好正文由原生 Agent 指令加载。'),
    '/api/pricing/exchange-rates': ('显式刷新', 'refresh 参数可访问远端并更新缓存，保留独立刷新入口；缓存配置见 settings.pricing_defaults。'),
    '/api/v1/product-research/hot-products/runs': ('领域任务', '选品任务的当前/最近/指定快照，已有 research_run_status_query 覆盖其领域读取。'),
    '/api/v1/product-research/source-registry': ('凭据视图', '完整数据源编辑配置带凭据占位；普通配置见 settings.product_research。'),
    '/api/exports/download': ('文件下载', '导出任务生成的短期 ZIP 二进制流式下载入口（支持 Range 断点续传），非普通业务只读查询。'),
}


def audit_query_coverage(paths, registry):
    """返回实际入口清单；未登记的新 GET 直接失败，不能静默漏掉。"""
    paths = set(paths) - {'/api/business-data/catalog'}
    shared = registry.http_queries
    missing = paths - set(shared) - set(RETAINED_GET_READS)
    if missing:
        raise ValueError('普通 GET 尚未定义共用查询或说明保留边界：' + ', '.join(sorted(missing)))
    return [{'path': path, 'query': shared.get(path, ''),
             'classification': '统一查询' if path in shared else RETAINED_GET_READS[path][0],
             'reason': '' if path in shared else RETAINED_GET_READS[path][1]} for path in sorted(paths)]


__all__ = ['APP_CONFIG_READS', 'RETAINED_GET_READS', 'audit_query_coverage']
