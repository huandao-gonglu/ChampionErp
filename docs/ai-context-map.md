# AI Context Map

本文件列出后端主要公共入口、依赖方向和测试边界。它面向后续维护者与 AI，
不替代模块内契约。

## 运行时边界

- `erp_web/runtime.py` 与原 `runtime_units` 兼容转发模块已经删除，不得重建聚合入口。
- 直接 import 具体 facade、service、store 或 schema owner。
- 新 HTTP 行为从 `erp_web/http_route_units/` 的显式 handler map 进入；路由把编排交给
  `erp_web/facades/` 或职责单一的 service。

## AI Provider 与 AI Work

### 当前统一边界

最终目标不是只统一 Agent loop，而是统一全部 `connection_type=api` 的 AI 推理请求：需要工具
循环的用例使用 Pydantic Agent，不需要 Agent 的普通 chat/JSON/stream 使用 Pydantic Direct
Model Requests，图片等能力使用锁定版本提供的 Pydantic capability/native tool。两类调用都
必须复用 `erp_web/services/ai_model_factory.py` 创建的 Model/Provider。

阶段 6 已完成：普通 chat/JSON/stream 统一由 Pydantic Direct Model Requests 发送；图片请求
也先进入 Pydantic Direct Model，再由集中 Factory 创建的 focused Images Model 执行。CLI 与
浏览器 AI 不属于 API Provider，继续使用独立适配器。

- `erp_web/services/ai_provider_contracts.py`：CLI/Browser 等独立连接的最小产品能力协议；不拥有
  API 厂商 wire protocol。
- `erp_web/services/ai_gateway.py`：稳定 AI gateway 门面，不包含协议解析或 SDK client。
- `erp_web/services/ai_gateway_providers.py`：业务调用编排和 `AiProviderClient`；API 分支直接进入
  `ai_direct_request_service`，非 API 注册表只包含 CLI/Browser。
- `erp_web/services/ai_provider_catalog.py`：产品已正式接入的 Provider Catalog、旧配置迁移和
  Pydantic Provider 公共构造器的唯一 owner。前端只消费 Catalog，不扫描 Pydantic 包，也不
  根据 Base URL 或模型名猜测厂商。
- `erp_web/services/ai_direct_request_service.py`：普通 API chat/JSON/stream/image 的唯一
  Pydantic Direct Model 执行入口，负责公开 Pydantic message/event 转换和项目结果归一化。
- `erp_web/services/ai_gateway_probe.py`：API/CLI/Browser 共用的能力探测编排、四态结果
  （`supported` / `unsupported` / `unavailable` / `inconclusive`）、确定性探测素材、连接指纹与
  versioned capability profile owner；每项真实探测创建独立 AI Work conversation，结果返回
  `conversation_id`；未接入能力不得静默跳过，临时网络错误不得记为不支持。
- `erp_web/services/ai_model_probe_service.py`：API 模型能力探测 adapter；chat、JSON、联网、
  Function Call、图片生成和图片编辑全部使用独立 probe binding。探测不读取待测 capability
  声明，Function Call 必须完成 tool call → tool result → final response 的完整往返。
- `erp_web/services/ai_model_config.py`：保存带 `configuration_fingerprint` 的 v2 能力证明；Provider、
  Base URL、模型名、API style、transport 配置或受控 `extra` 改变后，规范化阶段会移除失效证明及
  对应 capability，旧版无指纹配置仍可读取并通过重新探测升级。
- `erp_web/services/ai_model_discovery.py`：与推理解耦的远端模型目录发现；按 Catalog 的可选发现
  策略复用 Pydantic Provider 持有的 client，目录不可用不改变推理能力判定。
- `erp_web/services/ai_model_errors.py`：Pydantic Model 错误到项目稳定错误的脱敏转换。
- `erp_web/services/ai_gateway_cli_provider.py`：CLI Provider 实现。
- `erp_web/services/ai_gateway_browser_provider.py`：浏览器 Provider 实现。
- `erp_web/services/ai_gateway_provider_types.py`：Provider 共享请求 shape。
- `erp_web/services/ai_generation_settings.py`：功能绑定统一生成配置的归一化、
  能力描述与 Pydantic `ModelSettings`/受控 `extra_body` 映射；业务层不得直接拼接
  `reasoning_effort`、`enable_thinking` 等厂商字段。
- `erp_web/services/ai_model_factory.py`：Pydantic AI Model/Provider 的唯一创建入口；正式业务
  使用 `create_pydantic_model_binding` 并校验已启用能力，能力发现使用
  `create_pydantic_probe_binding`，只根据待测操作选择 Chat/Responses/Images Model，不允许用
  尚未产生的 capability 声明阻断探测。两条入口共享私有构造器和同一套 API style、认证、
  timeout、模型类型与密钥脱敏规则。
- `erp_web/services/ai_agent_factory.py`：Pydantic Agent 的唯一主要装配、运行、暂停与
  恢复入口；创建请求级 dependencies、usage limits、instrumentation 和版本化 deferred
  状态，不包含领域终检。
- `erp_web/services/ai_agent_instrumentation.py`：独立 OpenTelemetry 技术 trace owner；
  关闭 prompt/tool 内容采集并在 JSONL exporter 再次脱敏。观测写失败不影响业务结果。
- `erp_web/services/ai_agent_observability.py`：Agent 的 AI Work 内容投影 owner；保存有界且
  脱敏的初始输入、逐轮 Pydantic model request/response 与工具往返，失败运行同样保留模型
  被拒绝的输出和 retry feedback。
- `erp_web/services/ai_agent_state_store.py`：公开 Pydantic 消息与
  `DeferredToolRequests` 的版本化 envelope、有限 lease 原子 claim、写工具执行前检查点、
  durable `ready` 结果、`in_doubt` 防重放状态、审批记录和恢复安全校验。
- `erp_web/services/ai_pydantic_image_model.py`：登记过的 focused 例外，见下文；它是仅支持
  Images API 的 Pydantic `Model`，只能由 `ai_model_factory` 创建，并且只能经 Pydantic Direct
  Request 调用。
- `erp_web/services/ai_work_service.py`：AI Work conversation journal。能力探测使用专用
  `capability_probe.*` 事件保存实际探测消息、模型文本、工具往返和脱敏后的图片引用；普通业务
  `provider.request` 仍只保存摘要；Agent 内容通过独立 `agent.request` / `agent.transcript`
  投影写入，不把认证字段或无限大小内容带入 journal。
- `erp_web/http_route_units/ai_work_routes.py`：AI Work 读取与等待 HTTP 入口。
- `front/src/views/AiWorkView.vue`：AI Work 监视界面；支持通过 `conversation_id` query 定位会话，
  按正常对话流展示探测或 Agent 输入、逐轮模型消息和工具事件；旧 Agent 记录以现存运行摘要与
  终态回退，不显示空白页签。

```text
API use case
  → centralized Pydantic Model Factory
      ├─ Agent use case → AiAgentFactory → Pydantic Agent
      ├─ plain chat/json/stream → Pydantic Direct Model Requests
      └─ image/model capability → Pydantic capability 或登记过的 focused 例外

CLI / Browser use case
  → focused CLI / Browser Adapter
```

不得为了让普通调用复用 Pydantic 而把它们包装成虚假 Agent；也不得因为普通调用不需要 Agent，
就继续维护 Pydantic 之外的通用 API Provider 请求栈。

### Focused 例外登记：专用 Images API

- 缺失能力：锁定的 `pydantic-ai-slim[openai]==2.22.0` 支持 Responses 原生图片工具，但没有
  能绑定 `gpt-image-*` 专用模型并表达 `images.generate` / `images.edit` 的公开 Model。
- 限定范围：只有 `erp_web/services/ai_pydantic_image_model.py::OpenAIImagesModel`；不支持
  chat、JSON、stream 或 function tool，不能加入非 API Provider 注册表。
- 强制路径：`ai_model_factory` 创建 → `ai_direct_request_service` 调用
  `pydantic_ai.direct.model_request` → focused Model 使用同一 Pydantic Provider client。
- 行为约束：图片编辑失败直接报错，不允许 edit→generate fallback。
- 移除条件：锁定的 Pydantic AI 版本提供覆盖专用 Images generate/edit 的公开 Model/capability
  后，以原生实现直接替换并删除 focused Model。
- 守卫：`tests/test_ai_context_architecture.py` 禁止旧 HTTP/Image Provider、原始 `urllib`
  推理和 Direct Model 第二 owner。

统一生成配置、覆盖顺序与当前 Provider 映射见
`docs/ai-provider-generation-settings.md`。

## AI Tool Task 执行层

- `erp_web/schemas/ai_tools.py`：ERP `AiToolDefinition`、轻量内部
  `AiToolCommand`、`AiToolResult` 和 JSON schema 边界校验；不承担 Provider wire protocol。
- `erp_web/schemas/ai_trace.py`：执行 ID、deadline、权限和预算上下文。
- `erp_web/services/ai_invocation.py`：解析后的 model/provider、execution context
  与 recorder 单次创建边界。
- `erp_web/services/ai_tool_registry.py`：不可变 ToolSet 与显式
  definition/executor 映射；不通过动态全局表注册业务工具。同步 executor 必须通过
  `deadline_aware_tool_executor` 显式声明 cooperative deadline 契约，并把
  `AiExecutionContext.bounded_timeout_seconds()` 用于每个阻塞 I/O；Runtime 不用
  无法安全中止的后台线程伪装 hard cancellation。
- `erp_web/services/ai_tool_runtime.py`：工具查找、校验、权限、去重、预算、执行和
  最小业务审计；恢复写工具在真正调用 executor 前先持久化执行检查点。
- `erp_web/services/ai_agent_dependencies.py`：请求级 Agent dependencies，绑定唯一
  execution context、recorder、Tool Runtime、tenant、business scope、审批、幂等和
  use-case state。
- `erp_web/services/ai_tool_bridge.py`：把显式 ERP ToolSet 转换为 Pydantic
  `FunctionToolset`；Pydantic tool 只调用 `AiToolRuntime.execute(...)`，不直接调用
  领域 executor，并强制串行执行以保护 Runtime 的预算和去重状态。
- `erp_web/services/ai_agent_factory.py`：由 Pydantic Agent 独占 model → tool → model
  循环、类型化 output、重试、usage limit 和公开 deferred 协议。

依赖方向：

```text
facade / focused Agent service
  → AiAgentFactory
      → Pydantic Agent → centralized Model Factory
      → Pydantic Tool Bridge
          → AiToolRuntime
              → explicit AiToolSet → domain executor
```

`AiToolRuntime` 不得 import 类目、平台、发布或其他具体领域模块。Main Agent、
Planner、Memory 和 Policy Engine 仍不属于当前切片；真实领域 ToolSet 在所属
runtime unit 中显式构造，不注册到动态全局表。

旧自定义 runner、JSON Tool Protocol 和 Agent tool-turn provider adapter 已物理删除。当前
不存在 feature flag、shadow run、fallback、旧 API HTTP/SDK 请求栈或第二条 Agent 生产路径。

## 类目平台搜索层

- `erp_web/marketplaces/category_provider.py`：定义绑定式 `CategorySearcher` 与
  `CategoryNavigator`；前者按关键词发现，后者读取顶层节点并按 parent IDs 展开，
  两者的方法都不接收 platform/site。
- `erp_web/runtime_units/category_searchers.py`：任务入口根据当前平台实例化具体
  检索对象。Mercado Libre 调用 `domain_discovery/search`；Ozon 同一绑定对象同时
  保留人工关键词搜索能力并为自动匹配实现树导航。平台选择只发生在对象创建处。
- `erp_web/runtime_units/category_providers.py`：平台 API 与类目详情适配。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 类目语料的刷新、人工搜索和树导航入口；
  24 小时内复用缓存，远端瞬时网络错误时最多使用 7 天旧语料，认证错误不允许
  stale fallback。完整树不会进入 AI 上下文。
- `erp_web/runtime_units/ozon_category_cache.py`：Ozon 展平类目语料的版本化压缩 JSON
  持久化 owner；使用原子替换写入，文件只含 Client ID 单向摘要，不保存凭据。
- `erp_web/schemas/category.py`：规范化候选、搜索结果、匹配结果 shape，以及
  Agent Service 与领域工具共同使用的请求级 `CategoryCandidateLedger`。
- `tests/test_category_searchers.py`、`tests/test_ozon_category_api.py`：平台对象选择、
  远端/缓存搜索、错误分类和 Ozon ID 配对测试。

`erp_web/runtime_units/category_store.py::search_categories_live` 继续服务人工关键词搜索；
自动匹配按绑定对象能力选择 `CategoryNavigator` 或 `CategorySearcher`，两者不互相 fallback。

## 通用文本翻译

- `erp_web/http_route_units/translation_routes.py`：唯一公开入口 `POST /api/text-translate`；
  只接受 `target_language` 与扁平 `content` 键值对象。
- `erp_web/facades/translation_facade.py`：HTTP 状态映射，不包含类目或属性领域分支。
- `erp_web/runtime_units/text_translation.py`：唯一 `text.translate` AI 用例调用方；校验请求与
  模型响应拥有完全相同的 key 集合，并统一返回扁平 `{key: value}`。
- `config/prompts/text_translate.json`：唯一“翻译” Prompt。调用方负责注入目标语言和具体文本；
  类目候选与平台属性在前端各自独立组装内容并触发，不共享领域 payload。

已退役的类目结果翻译、类目属性翻译端点、用例、Prompt 和 runtime unit 不提供兼容路径。

## 类目匹配 Capability

- `POST /api/category-attrs` 返回平台类目属性定义；Ozon 字典字段保留
  `dictionary_id/is_dictionary/is_collection/max_value_count/category_dependent`，不把大字典内联到类目响应。
- `POST /api/category-attribute-values` 是平台枚举值的唯一公开读取入口；
  `erp_web/runtime_units/category_store.py` 通过 `CategoryProvider.attribute_values` 分派，
  Ozon 由 `erp_web/runtime_units/ozon_category_api.py` 调用独立的
  `description-category/attribute/values` 接口并跨页搜索、短时缓存。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`，搜索输入不进入草稿；发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories(keyword)`。工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/facades/category_match_facade.py`：`category.match` 公开编排入口；
  首轮发送裁剪后的双语商品事实；Ozon 同时发送真实顶层节点并允许最多四次树导航，
  Mercado Libre 最多三次换词发现。最终选择必须经过叶子候选账本、站点、可发布状态、
  详情、Ozon ID 配对和属性读取校验；达到资源上限时返回 unresolved，不静默改选。
- `erp_web/services/category_match_agent_service.py`：`category.product_match` 的 focused
  Execution Profile、prompt 渲染、类型化 `CategoryMatchAgentOutput` 与 Ledger output
  validator；只通过统一 `AiAgentFactory` 运行。
- `erp_web/http_route_units/category_routes.py::handle_category_match`：
  `POST /api/category-match` 薄路由入口。`category_facade.py` 只做草稿上下文与
  HTTP status 映射；其完整路径为 `erp_web/facades/category_facade.py`。
- `config/prompts/category_product_match.json`：`category.product_match`
  Execution Profile 的可配置 prompt。
- `front/src/api/workflow/publishing.ts::matchCategory`：HTTP 契约到现有人工
  候选 shape 的边界适配。
- `front/src/stores/workflow/actions/publishing.ts::autoSuggestCategoriesForDraft`：
  自动匹配唯一入口，逐目标站点调用 `matchCategory`；不包含运行时开关或第二条
  自动匹配分支。
- `tests/test_category_match_facade.py`、`tests/test_category_tools.py`：首次上下文裁剪、
  Ozon 逐层导航与有限回退、Mercado Libre 多轮换词、未知 ID、deadline、凭据和工具去重测试。

endpoint 内部只使用 `category_id/path_segments`。前端只在 API 边界转换成人工
选择组件需要的 `id/path`，且不会自动写入模型首选；用户仍需点击候选确认。HTTP
结果只返回最后检索位置与去重后的轻量叶子候选。完整技术 spans 和 usage 进入 instrumentation；
AI Work 保存脱敏且有界的 Agent 输入、每轮模型消息、工具参数/结果、trace 关联和最终业务摘要，
用于区分模型选错、validator 拒绝、工具失败与资源上限。

Ozon 自动类目召回不再要求模型猜中平台类目关键词。后端从
`erp_web/runtime_units/ozon_category_api.py` 的现有扁平商品类型语料恢复真实父子关系，
首次输入提供顶层节点，`erp_web/runtime_units/category_tools.py::browse_categories`
每次最多展开两个真实分支。标准流程逐层到达 `product_type`，必要时在最多四次导航内
回退到尚未展开的备选分支；只有工具真实返回的叶子 `category_id` 可以进入详情终检。
Mercado Libre 仍使用其独立的远端 domain discovery 关键字能力，不作为 Ozon fallback。

## 发布币种与核价

- `erp_web/marketplace_registry.py`：只维护站点的 `market_currency`（市场展示）与
  站点锁定的 `listing_currency`。Ozon 的刊登币种为空，禁止用俄罗斯市场的 RUB
  作为发布默认值。
- `erp_web/services/listing_currency_service.py`：发布币种解析唯一边界。Mercado Libre
  按站点锁定，Yandex 按 campaign 规则锁定，Ozon 按店铺合同锁定；解析失败必须阻断
  核价和发布，不允许回退到市场国家币种。
- `erp_web/marketplaces/category_services.py::fetch_ozon_seller_info` 与
  `erp_web/runtime_units/store_credentials.py::refresh_ozon_currency_capability`：通过
  Ozon `/v1/seller/info` 发现并持久化店铺合同币种。授权测试会刷新；核价在能力缺失时
  只补取一次。
- `erp_web/services/pricing_service.py`：所有发布售价使用 `{amount, currency}` Money；
  商品成本、物流与利润先统一在 CNY 核算，再按已解析的 `listing_currency` 换算。
  每个目标分别保存 `calculation_basis` 与 SHA-256 指纹。
- `erp_web/runtime_units/draft_publish_context.py`：从 `pricing.targets[platform:site]`
  投影当前目标的发布价格。持久化草稿没有含义不明的顶层 `price/currency`。
- `erp_web/runtime_units/publish_validation.py`：发布前核对目标币种、Money 币种、核价
  指纹、商品成本及包装尺寸；任何变化都会把旧核价判为 stale 并要求重新核价。
- 商品 schema v2 会读取 v1 数据，但旧数字售价和无指纹核价只标记失效，不自动猜币种。

## 商品发布

- `erp_web/http_route_units/publish_routes.py`：发布预检、payload 预览、同步发布与
  发布队列 HTTP 入口。
- `erp_web/http_route_units/get_routes.py`：发布任务列表与指定 Job 详情的只读查询入口。
- `erp_web/facades/publish_facade.py`：HTTP 层唯一发布 facade；业务编排进入
  `erp_web/runtime_units/publish_workflows.py`。
- `erp_web/runtime_units/publish_adapter.py`：发布平台适配器注册表。只有这里注册且
  在 `marketplace_registry.py` 声明 `CAP_PUBLISH` 的平台才允许进入真实发布流程。
- `erp_web/runtime_units/publish_mercadolibre.py`：Mercado Libre 专属发布与错误处理。
- `erp_web/runtime_units/publish_ozon.py`：Ozon `/v3/product/import` payload、
  草稿目标站点中的 `type_id/category_id + description_category_id` 配对、异步导入
  终态确认及错误字段映射；不得从商品级 `local_platform_categories` 回捞发布类目。
- `erp_web/facades/product_facade.py`：保存 Ozon 草稿时，若只提供 `type_id/category_id`，
  通过当前 Ozon 类目缓存自动解析并持久化隐藏的 `description_category_id`。
- `erp_web/runtime_units/runtime_api.py::publish_product`：平台无关的预检、artifact、
  日志与商品发布状态持久化。
- `erp_web/runtime_units/publishing_bus_core.py`：SQLite 发布任务和并发执行；适配器
  必须返回可验证的远端成功证据。`GET /api/publish-bus/jobs` 返回按时间倒序的轻量任务摘要，
  支持 cursor、状态、平台和商品筛选；`GET /api/publish-bus/status` 只返回指定 Job 的完整详情。
  两个读取接口都不返回 worker 恢复专用的完整商品快照。
- `erp_web/services/image_delivery_service.py`：发布图片 HTTPS delivery 唯一边界。
  图片保存 provider-neutral 的 `storage_key`，公网 URL 只是根据当前 provider 与
  `ERP_IMAGE_HTTPS_BASE_URL` 重新计算的缓存；平台发布模块不得读取隧道、磁盘根目录
  或对象存储配置。`existing_url` 只接受已有公网 URL，`local_static` 把本地文件按内容
  hash 复制到独立公开目录，可由 Quick Tunnel、Named Tunnel 或普通静态服务器暴露。
- `scripts/dev.sh` 默认以 `ERP_IMAGE_HTTPS_TUNNEL=auto` 管理 Quick Tunnel 生命周期：
  检测到 `cloudflared` 后先取得随机 HTTPS 地址并注入后端环境，开发服务退出时一并停止；
  `required` 在 Tunnel 不可用时阻断启动，`off` 禁用自动 Tunnel。固定域名环境直接设置
  `ERP_IMAGE_HTTPS_BASE_URL`，不会创建 Quick Tunnel。
- Ozon 适配器在草稿校验和 payload 构造前调用图片 delivery；Mercado Libre 保持平台
  图片上传接口与 `ml-id:*` 流程，不经过通用 HTTPS 图片服务。

Ozon 创建/更新商品是异步操作。提交 `/v3/product/import` 获得 `task_id` 后，必须
轮询 `/v1/product/import/info`；只有每个商品返回 `status=imported` 且没有逐项错误，
才写入 `real_publish_success`。拿到 `task_id` 本身不算发布成功。

## Product Research

- `erp_web/http_route_units/product_research_routes.py`：调研 HTTP 入口。
- `erp_web/product_research_config.py`：调研配置入口。
- `erp_web/services/product_research_service.py`：调研编排与运行服务。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_tools.py`：工具 schema、ToolSet 和 Runtime。
- `tests/test_category_match_agent_service.py`：真实 `FunctionModel + Agent` 的类目工具循环、
  类型化 output 与 validator 契约。
- `tests/test_ai_agent_instrumentation.py`：技术 spans、usage、trace 关联、脱敏和故障隔离。
- `tests/test_ai_agent_state_store.py`、`tests/test_ai_agent_deferred_runtime.py`：公开消息
  serialization、版本迁移、审批/拒绝、跨进程恢复、权限/scope/deadline 与幂等 claim。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。
