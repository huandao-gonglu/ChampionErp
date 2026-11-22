# 普通读取覆盖清单

普通查询由 `business_query_composition.py` 定义，以下清单依据实际 HTTP 覆盖记录和
`business_query_inventory.py` 生成。新增普通 GET 或应用配置段未登记 owner 时，架构回归失败；
普通查询自动进入目录，不需要维护第二份 AI 资源表。

## 应用配置段

| 配置段 | 公开业务查询 |
|---|---|
| `system_settings` | `settings.system` |
| `forbidden_categories` | `settings.forbidden_categories` |
| `image_hosting` | `settings.image_hosting` |
| `ai_tool_approval_mode` | `settings.approval` |
| `auto_ai_recognition` | `settings.collection` |
| `alibaba_cookie` | `settings.collection` |
| `1688_api` | `settings.collection` |
| `ai_models` | `settings.models` |
| `ai_use_case_bindings` | `settings.model_bindings` |
| `pricing_defaults` | `settings.pricing_defaults` |
| `pricing_templates` | `pricing.templates` |
| `yandex_warehouses` | `yandex.warehouses` |
| `product_research` | `settings.product_research` |

含凭据的段只公开业务字段与是否配置；完整维护页保留其受信协议。模型连接元数据不调用
桌面执行器，选品数据源不返回 API Key，汇率地址不返回带访问参数的原值。

## GET 入口

| 路径 | 分类 | 统一查询 / 保留原因 |
|---|---|---|
| `/api/account/newapi` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/account/registration-config` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/account/session` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/admin/settings` | 凭据视图 | 企业凭据维护与可能携带访问参数的汇率地址；业务默认配置见 settings.pricing_defaults/settings.collection。 |
| `/api/admin/users` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/ai-config` | 本机连接协议 | 完整模型设置带本机连接桥接；模型和绑定业务元数据见 settings.models/settings.model_bindings。 |
| `/api/alibaba/authorization` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/alibaba/crossborder/authorization` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/business-queries/draft.content` | 统一查询 | draft.content |
| `/api/business-queries/draft.pricing` | 统一查询 | draft.pricing |
| `/api/business-queries/product.skus` | 统一查询 | product.skus |
| `/api/business-queries/settings.approval` | 统一查询 | settings.approval |
| `/api/business-queries/settings.collection` | 统一查询 | settings.collection |
| `/api/business-queries/settings.model_bindings` | 统一查询 | settings.model_bindings |
| `/api/business-queries/settings.models` | 统一查询 | settings.models |
| `/api/business-queries/settings.pricing_defaults` | 统一查询 | settings.pricing_defaults |
| `/api/business-queries/settings.product_research` | 统一查询 | settings.product_research |
| `/api/business-queries/settings.store` | 统一查询 | settings.store |
| `/api/crossborderbus/services` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/crossborderbus/settings` | 统一查询 | fulfillment.settings |
| `/api/drafts-index` | 统一查询 | drafts.index |
| `/api/image-hosting` | 统一查询 | settings.image_hosting |
| `/api/logistics/settings` | 统一查询 | logistics.settings |
| `/api/online-products` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/online-products/attributes` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/online-products/source-images` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/online-products/yandex-shop-names` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/operation-availability` | 统一查询 | operations.availability |
| `/api/orders` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/address-note` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/alibaba-purchase` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/alibaba-purchase/addresses` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/alibaba-purchase/batch` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/detail` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/fulfillment` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/integrations` | 凭据视图 | 包含带回调凭据的地址；普通店铺及本地授权状态见 settings.store。 |
| `/api/orders/receiving-warehouse` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/orders/summary` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/pricing/exchange-rates` | 显式刷新 | refresh 参数可访问远端并更新缓存，保留独立刷新入口；缓存配置见 settings.pricing_defaults。 |
| `/api/pricing/templates` | 统一查询 | pricing.templates |
| `/api/products-index` | 统一查询 | products.index |
| `/api/publish-bus/jobs` | 统一查询 | publish.jobs |
| `/api/publish-bus/status` | 统一查询 | publish.job |
| `/api/publish-logs` | 统一查询 | publish.logs |
| `/api/shipping/tariffs` | 统一查询 | shipping.tariff_versions |
| `/api/shipping/tariffs/rules` | 统一查询 | shipping.tariff_rules |
| `/api/state` | 受信组合视图 | 包含审批凭据、目录和媒体传输引用；普通配置及索引从其统一查询读取。 |
| `/api/system-settings` | 统一查询 | settings.system |
| `/api/system-settings/forbidden-categories` | 统一查询 | settings.forbidden_categories |
| `/api/system-settings/user-agents` | 用户规则 | 用户独有规则由原生指令装配读取，仅受信设置界面可编辑，不提供自主改写入口。 |
| `/api/tasks` | 领域读取 | 已有领域查询包含对象归属、快照、分页或远端只读操作，继续复用其领域能力。 |
| `/api/v1/ai-work/conversations` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/ai-work/conversations/` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/ai-work/conversations/<conversation_id>/events` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/ai-work/conversations/<conversation_id>/image-batch` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/ai-work/conversations/<conversation_id>/title` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/ai-work/conversations/<conversation_id>/ui-messages` | 原生对话协议 | 原生历史、事件订阅及对话投影，保留专门协议。 |
| `/api/v1/product-research/hot-products/runs` | 领域任务 | 选品任务的当前/最近/指定快照，已有 research_run_status_query 覆盖其领域读取。 |
| `/api/v1/product-research/source-registry` | 凭据视图 | 完整数据源编辑配置带凭据占位；普通配置见 settings.product_research。 |
| `/api/wechat/status` | 身份协议 | 登录、用户管理、配对或授权协议，不是普通业务配置读取。 |
| `/api/yandex/warehouses` | 统一查询 | yandex.warehouses |

## 验收入口

`.venv/bin/python scripts/business_query_acceptance.py` 生成机器可读覆盖清单，不调用模型。
显式添加 `--live --output /tmp/独立验收目录` 才访问发布环境并执行真实问答；可用
`--case` 选择失败场景复验。会话凭据仅在内存中使用，结束后退出并撤销。

事实期望从普通页面 JSON 生成，不把期望答案提供给模型，也不使用另一个模型修正答案。
六类结构化业务问答覆盖货代配置、仓库分工、系统样式、托管目标、模型绑定及选品默认值；
`tests/test_business_answer_regression.py` 保留历史错误和漏项作为不能通过的反例。
结构化事实核对与自然语言抽查分别记录，不能把接口成功或格式正确等同于回答准确。
大文本只校验首段及总长度，单独记录，不宣称逐字完整验收。

添加 `--natural` 可执行与上一版相同的三道自然语言题；只记录完成状态与体量，准确性保持待人工复核，不自动判为通过。

线上同源核对覆盖普通配置；发布日志、索引和任务使用有界样本，不递归遍历历史业务数据。
