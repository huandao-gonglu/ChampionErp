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
  versioned capability profile owner；探测结果归一化进 capability profile；
  未接入能力不得静默跳过，临时网络错误不得记为不支持。
- `erp_web/services/ai_model_probe_service.py`：API 模型能力探测 adapter；chat、JSON、联网、
  Function Call、图片生成和图片编辑全部使用独立 probe binding。探测不读取待测 capability
  声明，Function Call 必须完成 tool call → tool result → final response 的完整往返。
- `erp_web/services/ai_model_config.py`：保存带 `configuration_fingerprint` 的 v2 能力证明；Provider、
  Base URL、模型名、API style、transport 配置或受控 `extra` 改变后，规范化阶段会移除失效证明及
  对应 capability，旧版无指纹配置仍可读取并通过重新探测升级。
- `erp_web/services/ai_model_discovery.py`：与推理解耦的远端模型目录发现；按 Catalog 的可选发现
  策略复用 Pydantic Provider 持有的 client，目录不可用不改变推理能力判定。
- `erp_web/services/ai_model_errors.py`：Provider/Pydantic Model 错误的最薄脱敏透传边界；保留
  HTTP 状态、Provider code/message/request ID，不得改写为其他业务含义。
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
- `front/src/views/AiWorkView.vue`：AI Work 页面。左侧 conversation 列表按 `updated_at` 倒序；
  右侧按优先级选择数据源——前台 presentation（observe Chat 实时消息）、活动 `global.chat`
  （共享 `Chat.messages`）或服务端 `/ui-messages` 只读派生历史；“原始消息”辅助标签提供
  规范 Pydantic JSON 树、Raw JSON 与下载；支持 `conversation_id` / `presentation_id` query 定位。

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
- `erp_web/services/ai_tool_declaration.py`：dependency-light `@ai_tool`、`Injected` 与
  不可变契约元数据；装饰时不注册、不读取配置，也不执行领域逻辑。
- `erp_web/services/ai_tool_compiler.py`：受限同步函数签名、Pydantic `TypeAdapter`、本地
  `$defs/$ref` 展开、Schema 支持子集和机械 executor adapter 的唯一编译 owner。
- `erp_web/services/ai_tool_catalog.py`：调用方显式函数清单、场景 allowlist、Execution Profile
  权限与独立可信 Binding Scope 的唯一 Catalog 抽象；不扫描包或依赖 import side effect。
- `erp_web/services/ai_tool_registry.py`：不可变 run-scoped ToolSet 与
  definition/executor 映射；旧 `AiToolRegistry` 容器已删除。同步 executor 必须通过
  `deadline_aware_tool_executor` 显式声明 cooperative deadline 契约，并把
  `AiExecutionContext.bounded_timeout_seconds()` 用于每个阻塞 I/O；Runtime 不用
  无法安全中止的后台线程伪装 hard cancellation。
- `erp_web/services/ai_tool_runtime.py`：工具查找、校验、权限、去重、预算、执行和
  最小业务审计；写工具在 executor 前检查可信幂等键，恢复写工具在真正调用 executor 前先
  持久化执行检查点。
- `erp_web/services/ai_agent_dependencies.py`：请求级 Agent dependencies，绑定唯一
  execution context、recorder、Tool Runtime、tenant、business scope、审批、幂等和
  use-case state。
- `erp_web/services/ai_tool_bridge.py`：把显式 ERP ToolSet 转换为 Pydantic
  `FunctionToolset`；Pydantic tool 只调用 `AiToolRuntime.execute(...)`，不直接调用
  领域 executor，并强制串行执行以保护 Runtime 的预算和去重状态。
- `erp_web/services/ai_agent_factory.py`：由 Pydantic Agent 独占 model → tool → model
  循环、类型化 output、重试、usage limit 和公开 deferred 协议；`open_stream_run(...)` 是协议
  无关的流式运行入口，装配语义与 `run_sync` 一致，成功时用官方 `result.all_messages()` 原子
  替换 conversation 历史。
- `erp_web/stores/pydantic_message_store.py`：Pydantic 官方 `ModelMessage` 历史的唯一持久化
  边界；`messages_json` 用 `ModelMessagesTypeAdapter` 校验与序列化，是消息的唯一事实来源。

依赖方向：

```text
facade / focused Agent service
  → AiAgentFactory
      → Pydantic Agent → centralized Model Factory
      → Pydantic Tool Bridge
          → AiToolRuntime
              → run-scoped AiToolSet
                  ← explicit AiToolCatalog + scene allowlist + Binding Scope
                      ← @ai_tool + AiToolCompiler ← typed domain capability
```

`AiToolRuntime` 不得 import 类目、平台、发布或其他具体领域模块。全局 Agent 的 planning
service 属于上层用例编排，不进入通用 Tool Runtime；Memory 和 Policy Engine 仍不属于当前切片。
真实领域 ToolSet 在所属 runtime unit 中显式构造，不注册到动态全局表。

旧自定义 runner、JSON Tool Protocol 和 Agent tool-turn provider adapter 已物理删除。当前
不存在 feature flag、shadow run、fallback、旧 API HTTP/SDK 请求栈或第二条 Agent 生产路径。

## 全局 Agent 顺序任务流

全局 Agent 已按单用户、本地运行的轻量方案落地。任务与发布终态由 Controller 和 PublishingBus
持久化，不从对话事件推断；全局任务规划/执行与 `global.chat` 气泡对话是两条互不耦合的链。

```text
/api/global-task-*
  → erp_web/http_route_units/global_agent_routes.py
      → erp_web/facades/global_agent_facade.py
          → erp_web/services/global_task_controller.py
              ├─ erp_web/stores/global_task_store.py
              ├─ erp_web/services/global_agent_service.py
              │   → AiAgentFactory
              │   → erp_web/runtime_units/global_task_tools.py
              ├─ 静态九项业务 Capability
              └─ PublishingBus
```

### HTTP 与持久化入口

- `erp_web/http_route_units/global_agent_routes.py`：五个显式 POST 入口：
  `/api/global-task-start`、`/api/global-task-state`、`/api/global-task-input`、
  `/api/global-task-publish-confirm`、`/api/global-task-cancel`。不存在
  `/api/global-task-wait`；页面只有限频率读取 state。
- `erp_web/facades/global_agent_facade.py`：唯一 composition root 和 HTTP shape 映射；装配
  Controller、主 Agent planning service、只读 ToolSet、九项 Capability 与 PublishingBus 状态读取。
  route 不直接依赖 runtime unit。
- `erp_web/services/global_task_controller.py`：计划保存、严格顺序推进、暂停/补资料、发布确认、
  发布终态刷新和重启后继续的 owner；直接调用类型化 Capability，不调用 Tool executor。
- `erp_web/stores/global_task_store.py`：`LocalGlobalTaskState` 的唯一 Store；为现有任务控制器保留的
  草稿快照方法委托给 `erp_web/stores/draft_query_snapshot_store.py`。
- `erp_web/stores/draft_query_snapshot_store.py`：`DraftQuerySnapshot` 的唯一持久化 owner，供全局任务与
  `global.chat` 共享，但不依赖任务状态或任务 schema。
- `erp_web/schemas/global_tasks.py`、`erp_web/schemas/draft_capabilities.py`：任务、步骤、
  `pending_input_owner`、带 `input_type/input_owner` 的类型化 `RequiredInput`、受控计划参数、发布确认和
  草稿快照 shape。补充值由 Controller 按 owner 合并到 step、属性或核价输入，不靠 facade 字段白名单。

`DraftQuerySnapshot.total` 和聚合统计覆盖完整匹配集合；为限制模型上下文和本地状态大小，
`draft_ids/items` 只保存按 `limit` 截取的当前有序页。`draft_position` 是该页内的一基序号，Controller
再把它解析为稳定 `draft_id` 并读取当前 ProductStore 事实；模型输出的数字或 ID 不直接进入写操作。
`view=summary/workflow/publish_readiness/detail` 采用同一稳定 `DraftSummary` schema 的分级字段投影，
快照重放保持创建时 view，不把 view 当作无效果展示提示。

### Planning ToolSet 与静态 Capability map

`erp_web/services/global_agent_service.py` 是主 Agent 唯一 planning profile。planning run 只绑定
`erp_web/runtime_units/global_task_tools.py::drafts_query`：工具声明
`side_effect="none"`、只拥有 `global.task.read` 权限，ProductStore、草稿快照 repository 和最近 snapshot ID
通过可信 Scope 注入。九项业务能力只出现在 Controller 的静态 map，不会作为主 Agent 写 Tool：

- `drafts.query`
- `draft.prepare_for_market`
- `product.read`
- `category.match`
- `product.attributes.fill`
- `product.attributes.update`
- `product.images.prepare`
- `product.publish.validate`
- `product.publish.request`

`action=answer` 只允许引用真实 `query_snapshot_id`；当前数量答复由 Controller 根据 snapshot 的
`total` 确定性渲染，不采信模型自行组织的数字。`draft_position`、`target_platform` 和受控
`GlobalTaskPlanParameters` 进入类型化计划；商品、草稿、店铺和资产等稳定身份仍由任务上下文、
查询快照或领域 owner 注入并复核。补资料使用类型化 `RequiredInput`：带候选项的类目/枚举是单选，
草稿序号和图片资产是多值输入；facade 统一归一化 `target_platform` / `platform`。发布上下文遇到多个
候选目标且请求未明确平台/站点时返回 `DRAFT_TARGET_AMBIGUOUS`；平台下仍有多个站点时返回
`DRAFT_TARGET_SITE_AMBIGUOUS`，不会静默选择首项或默认站点。

### 目标市场 Capability 拆分

- `erp_web/runtime_units/market_capability_support.py`：草稿定位、目标选择、类目详情与持久化共享支撑。
- `erp_web/runtime_units/category_capabilities.py`：`category.match` 的稳定草稿 adapter；focused
  类目匹配函数由 facade 注入，runtime 不反向 import facade。
- `erp_web/runtime_units/attribute_fill_capabilities.py`：规则填充与 focused 属性 Agent adapter；
  未解决的真实必填属性返回类型化 `RequiredInput`。
- `erp_web/runtime_units/market_pricing_capability.py`：确定性核价和草稿持久化。
- `erp_web/runtime_units/market_prepare_capabilities.py`：`draft.prepare_for_market` 的高层顺序编排；
  复用现有目标草稿、文案、图片、类目、属性和核价 owner，不复制领域实现；文案重生成以稳定
  task/step operation key 与文案同次持久化，重启后不会重复消费同一次 `regenerate_copy`。
- `erp_web/runtime_units/product_capabilities.py`、`erp_web/runtime_units/publish_capabilities.py`：
  商品读取/幂等字段更新/图片准备，以及确定性发布校验/确认后队列提交的 focused adapter。

focused 类目和属性执行在完成、暂停或后处理失败时都返回自己的 AI Work `conversation_id`；高层市场
准备聚合为 `agent_execution_conversation_ids`，Controller 先持久化这些 ID 再投影链接，不复制
transcript 或 Tool 输出。

### AiWork Pydantic 消息历史

旧的 AiWork 事件投影、JSONL 消息记录、`ai_sessions`、parent/child conversation、long-poll 与
Global Task 聊天耦合已全部删除。当前 AiWork 只围绕 Pydantic 官方 `ModelMessage` 历史：

- `erp_web/http_route_units/ai_work_routes.py`：只读检查入口。
  `GET /api/v1/ai-work/conversations` 列出历史索引；
  `GET /api/v1/ai-work/conversations/{id}` 返回规范 `ModelMessage` JSON；
  `GET /api/v1/ai-work/conversations/{id}/ui-messages` 用官方 Adapter 派生只读 `UIMessage[]`。
- `erp_web/stores/pydantic_message_store.py`：`ModelMessage` 历史唯一持久化边界。
- 已退役的 `/events`、`/raw`、`/children`、wait/after_seq 参数继续返回 404。

### 全局对话（global.chat）与实时消息流

`global.chat` 是独立对话 profile：输出自然语言文本，业务入口
`erp_web/services/global_agent_chat_service.py`，只选择服务端 prompt、只读 ToolSet、Execution
Profile 与权限，再调用 `AiAgentFactory.open_stream_run(...)`。它与 `GlobalAgentService.plan()`、
`/api/global-task-*` 的独立规划/执行职责并行，但不接入气泡消息链，也不写 task 与 conversation 关联。

- `erp_web/http_route_units/ai_chat_routes.py`：`POST /api/v1/ai-chat/runs` 薄路由，预流校验返回
  标准 JSON，开始输出后只发送官方 Vercel SSE chunk。
- `erp_web/services/vercel_ai_ui_service.py`：唯一 Vercel 协议入口，负责解析请求、领取锁与 claim、
  运行/编码 SSE 与历史 `dump_messages()`；是新增 Pydantic UI import 的唯一 owner。
- `erp_web/facades/ai_chat_facade.py`：composition root，从 `AppContext` 装配 focused services。
- `erp_web/services/ai_chat_run_registry.py`：进程内按 conversation ID 的活动 run 互斥屏障，由
  `AppContext` 单例持有。
- `erp_web/stores/ai_chat_turn_claim_store.py` + `ai_chat_turn_claims` 表：`client_message_id`
  幂等领取与 profile/owner 归属，只存运行控制元数据，不存消息正文。
- `erp_web/stores/draft_query_snapshot_store.py`：草稿查询快照的独立持久化 owner，不依赖 Global Task。
- `erp_web/services/global_chat_tools.py`：global.chat 独立的只读 `drafts_query` ToolSet，使用
  `global.chat.read` 权限与独立快照 store，不复用 Global Task scope/store。

实时展示链是 `AgentStreamEvent → VercelAIAdapter/VercelAIEventStream → SSE → @ai-sdk/vue Chat`；
历史展示链是 `PydanticMessageStore → ModelMessage[] → VercelAIAdapter.dump_messages() → UIMessage[]`。
消息事实唯一来源是 `pydantic_message_histories.messages_json`，不存在第二张 UI 消息表或消息双写。

### AI Presentation 通用可观测层

用户直接触发的 AI 能力保持业务 HTTP 接口自有 start 与类型化结果，实时展示由通用
presentation 层统一提供：前端触发前预留 presentation，业务请求在 HTTP 公共边界用
`X-AI-Presentation-ID` header claim 该预留，此后请求范围内任何经 `AiAgentFactory`
运行的 Pydantic Agent 都会把 native event 流自动发布为官方 Vercel chunk；前端用
AI SDK 官方 reconnect 约定只读观察展示流。Agent 不感知前端协议，业务类型化结果仍由
focused service/store 拥有，前端不从消息解析业务结果；展示断连或失败不改变业务结果
裁定。

- `erp_web/services/ai_presentation_context.py`：dependency-light presentation 运行上下文
  与 observer 协议。`AiPresentationContext` 不可变描述一次 Agent 运行在请求调用范围中的
  位置（root/child）；`bind_presentation_context()` / `current_presentation_context()` 是
  contextvar 唯一绑定/读取点。HTTP 边界在 claim 后建立 root scope；factory 内第一个 Agent
  派生 presentation root run，运行内部再次进入 factory 派生 child，继承 presentation 与
  observer。`AiRunObserver` / `AiNativeEventPublisher` 是窄展示观察协议；无 presentation
  scope 时 observer no-op，业务执行语义不因是否有浏览器观察而改变。
- `erp_web/services/ai_presentation_registry.py`：`AppContext` 单例持有。进程内短期
  presentation 状态机（`reserved/bound/running/finalizing/completed/failed/expired`）与
  有界官方编码 chunk 缓冲；提供唯一 presentation stream lease；`claim_root_run` 原子领取
  唯一 root run 槽位（首个顺序 Agent 成功，后续返回已领取的 root），保证一次前台交互最多
  一个根流；`finish_request` 由边界统一收尾请求生命周期（区分 `request_failed`）；TTL 清理
  过期预留与终态缓冲。规范消息仍由 `PydanticMessageStore` 持久化，chunk 缓冲只是短期展示
  副本，不是第二份消息事实源。
- `erp_web/services/ai_presentation_service.py`：`reserve_presentation` 服务端生成
  presentation/conversation ID 并预留（短 TTL；不执行 Agent、不读取业务数据）；
  `claim_presentation_scope` 在 HTTP 公共边界原子 claim 并构造携带
  `RegistryAiPresentationObserver` 的 root scope，非法/过期/重复 claim 返回 None，由边界
  映射稳定 409（`AI_PRESENTATION_CLAIM_INVALID`），不静默创建第二个展示；
  `RegistryAiPresentationObserver` 透传 Agent native events，同时经
  `vercel_ai_ui_service.new_event_stream()` 的官方 `transform_stream()` / `encode_stream()`
  编码为 chunk 发布到 registry，发布失败（含缓冲溢出）只停止发布，不改写 Agent 执行语义；
  装配期失败经 `publish_error_chunks` 也只发布官方 error/finish chunk。
- `erp_web/http_routes.py`：POST 公共边界。浏览器边界校验后读取
  `X-AI-Presentation-ID` 并 claim，拒绝返回 409；成功后 contextvar scope 覆盖整个
  dispatch，handler 正常返回或抛错都由 finally 的 `finish_request` 收尾 presentation
  请求生命周期；请求成败按实际 HTTP 响应状态裁定（正常返回的 4xx/5xx 标记 failed，
  200 + ok=false 的业务判断型结果仍 completed）。业务 route 不读取该 header。
- `erp_web/http_route_units/ai_presentation_routes.py`：`POST /api/v1/ai-presentations`
  预留（display title 仅清洗后用于 UI）；`GET /api/v1/ai-presentations/{id}` 只读展示
  元数据状态（含脱敏展示错误，不返回业务结果）；
  `GET /api/v1/ai-presentations/{id}/stream` 官方 Vercel UI SSE observe 流——领取唯一
  lease，从游标 0 重放已缓冲 chunk 并实时转发；未知、过期或 lease 已占用返回 204
  （Vercel reconnect 约定的“无可用流”）；浏览器断连只释放 lease，不取消业务请求与 Agent。
  不存在通用 result endpoint，业务结果始终来自原业务接口。
- `erp_web/services/ai_agent_factory.py` 执行内核：从当前 contextvar scope 派生本次运行
  的 presentation 上下文，经 observer 原子领取 registry 唯一 root run 槽位决定 root/child
  （一次前台交互最多一个根流，后续顺序 Agent 一律 child）；root run 的规范历史用
  `scope.conversation_id` 持久化，实时流与 `PydanticMessageStore` 历史同一 ID；
  `resume_sync()` 复用同一 native-event 内核（经 `deferred_tool_results` 注入审批结果），
  恢复运行同样发布 presentation 事件。存在 observer 时 `session.events()` 返回包装流，消费
  它同时驱动官方转换/发布，事件原样透传；生命周期通知（run_started/running/finalizing/
  completed/failed）与子运行紧凑状态卡全部故障隔离，展示失败只降级展示。无
  presentation scope 的后台 Agent 不产生 SSE；child Agent 不产生第二条 SSE。
- 前端：`front/src/api/aiPresentations.ts`（reserve/status transport 与 observe Chat；
  `DefaultChatTransport` 的 reconnect URL 即
  `GET /api/v1/ai-presentations/{id}/stream`）、
  `front/src/services/withAiForeground.ts`（通用前台 wrapper：原子占用 → reserve →
  attach observe Chat → 展示流与业务请求并发 → 业务 response 唯一裁定成败 → 有界流收尾
  → finish 与终态提示）、`front/src/stores/aiWorkDisplay.ts`（通用 presentation 展示协调
  store；`AiChatStore` 仍是全局聊天唯一 owner，接管只改变展示选择）。
  `front/src/components/common/AiWorkFloatingButton.vue` 按 displayMode 渲染
  global-chat / presentation 分支；`front/src/views/AiWorkView.vue` 按 presentation 实时、
  global.chat、历史三档选择数据源，支持 `conversation_id` / `presentation_id` query 定位。
- 已迁移：类目匹配（`matchCategory`）与类目属性填充（`fillAttributesByAi`）使用同一
  wrapper；不存在业务专用展示 start/result 协议。新增第三个前台能力时后端 Agent service
  无需改动，前端只增加 wrapper 声明与业务提示映射。完整契约见 `docs/aiworkpage.md`。

## 类目平台搜索层

- `erp_web/marketplaces/category_provider.py`：定义绑定式 `CategorySearcher` 与
  `CategoryNavigator`；前者按关键词发现，后者读取顶层节点并按 parent IDs 展开，
  两者的方法都不接收 platform/site。
- `erp_web/runtime_units/category_searchers.py`：任务入口根据当前平台实例化具体
  检索对象。Mercado Libre 调用 `domain_discovery/search`；Ozon 同一绑定对象同时
  保留人工关键词搜索能力并为自动匹配实现树导航；Yandex 只在本地缓存类目树上做
  规范化关键词匹配（source `yandex_cache`），并把限流/认证/缺凭据错误统一分类。
  平台选择只发生在对象创建处。
- `erp_web/runtime_units/category_providers.py`：平台 API 与类目详情适配。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 类目语料的刷新、人工搜索和树导航入口；
  24 小时内复用缓存，远端瞬时网络错误时最多使用 7 天旧语料，认证错误不允许
  stale fallback。完整树不会进入 AI 上下文。
- `erp_web/runtime_units/ozon_category_cache.py`：Ozon 展平类目语料的版本化压缩 JSON
  持久化 owner；使用原子替换写入，文件只含 Client ID 单向摘要，不保存凭据。
- `erp_web/runtime_units/yandex_category_api.py`：Yandex 类目树与类目参数语料的刷新
  与缓存 owner；类目树按语言与凭据作用域缓存（内存 + gzip 持久化 JSON），6 小时
  新鲜窗口用于避开 Yandex 每小时限额，远端瞬时错误最多使用 7 天旧语料；类目参数
  按类目 ID 短 TTL 内存缓存。缓存文件只含凭据作用域单向摘要，不保存凭据。
  平台 shape → 通用 CategoryProvider shape 的机械转换发生在 `category_providers.py`
  平台边界。
- `erp_web/schemas/category.py`：规范化候选、搜索结果、匹配结果 shape，以及
  Agent Service 与领域工具共同使用的请求级 `CategoryCandidateLedger`；
  `normalize_category_attribute_definition` 为带单位属性暴露 `unit_options/default_unit`。
- `tests/test_category_searchers.py`、`tests/test_ozon_category_api.py`：平台对象选择、
  远端/缓存搜索、错误分类和 Ozon ID 配对测试；含 Yandex 关键词搜索与
  HTTP 420 限流（可重试）/401 认证失败（终态）分类。

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
  带单位的平台属性（Yandex）通过共享的 `unit_options/default_unit` 暴露可选单位，
  属性值以 `{value, unit}` 提交；`dictionary_value_id` 按字符串传输与校验，
  不做数值化（Yandex 字典 ID 超出安全整数范围）。
- `POST /api/category-attribute-values` 是平台枚举值的唯一公开读取入口；
  `erp_web/runtime_units/category_store.py` 通过 `CategoryProvider.attribute_values` 分派，
  Ozon 由 `erp_web/runtime_units/ozon_category_api.py` 调用独立的
  `description-category/attribute/values` 接口并跨页搜索、短时缓存。
- `erp_web/product_model/category_model.py`：类目属性有效性和未解决必填项的唯一确定性判断；
  `strict_enum/open_enum/free_text` 三种值模式同时供规则填充、Agent target 和发布预检使用。
- `erp_web/runtime_units/category_attribute_ai_fill.py`：类目属性填充编排入口；先执行规则填充，
  只把规则处理后仍未解决的必填属性交给 Agent，合并后重新计算最终阻塞项。重复执行时
  已有效的开放枚举或文本值不会再次进入 Agent。
- `erp_web/runtime_units/category_attribute_tools.py`：类型化
  `search_category_attribute_values(...)` 能力通过 `@ai_tool` 声明唯一工具
  `category_attribute_values_search`；该工具只接受 `value_mode=strict_enum` 的平台强制枚举。
  显式 Catalog 与场景 allowlist 绑定平台、站点、类目和 request-scoped Ledger，AI 只能
  批量提交当前属性 ID 与搜索词。Definition、Schema 与机械
  executor adapter 均由 Compiler 生成，不存在旧手写工具名或闭包 executor。
  `erp_web/services/category_attribute_fill_agent_service.py` 负责类型化输出和候选账本校验：
  平台强制枚举只能选择本次工具返回的 `dictionary_value_id + value`；开放枚举优先使用
  schema options，没有匹配选项时允许填写有商品依据的自定义文本且不得提交枚举 ID。
- `config/prompts/category_attribute_fill.json`：`category.attribute_fill` Agent prompt；
  明确区分发布必填、平台强制枚举、建议枚举和普通自定义属性，并要求技术参数、链接、
  编码、证件与文件不得编造。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`（ID 原样按字符串存取，不做数值化），搜索输入不进入草稿；
  带 `unit_options` 的属性通过数值输入 + 单位下拉生成 `{value, unit}`；
  发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories(keyword)`。工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/facades/category_match_facade.py`：`category.match` 共享业务阶段；
  首轮发送裁剪后的双语商品事实；Ozon 同时发送真实顶层节点并允许最多四次树导航，
  Mercado Libre 最多三次换词发现。最终选择必须经过叶子候选账本、站点、可发布状态、
  详情、Ozon ID 配对和属性读取校验；达到资源上限时返回 unresolved，不静默改选。
  `prepare_category_match_input / setup_category_match_search / finalize_category_match`
  被 Global Task capability 与同步 focused HTTP 入口共用，行为一致。
- `erp_web/services/category_match_agent_service.py`：`category.product_match` 的 focused
  Execution Profile、prompt 渲染、类型化 `CategoryMatchAgentOutput` 与 Ledger output
  validator；只通过统一 `AiAgentFactory` 的流式 `open_stream_run` 运行（同步执行路径已删除）。
- `erp_web/http_route_units/category_routes.py::handle_category_match`：
  `POST /api/v1/category-match` 同步 focused 入口。类型化业务结果由本接口独占，始终
  返回 200 与类型化 `CategoryMatchResult`（`ok=false` 属于业务判断型结果；subject
  错误仍映射其 4xx）；实时展示关联由 HTTP 公共边界的 `X-AI-Presentation-ID` claim
  完成，route 不读取 presentation header，不导入 registry/SSE。
  `category_facade.py::load_category_match_subject` 只做草稿上下文加载；其完整路径为
  `erp_web/facades/category_facade.py`。
- `config/prompts/category_product_match.json`：`category.product_match`
  Execution Profile 的可配置 prompt。
- `front/src/api/workflow/publishing.ts::matchCategory`：经通用 `withAiForeground`
  wrapper 调用同步 `POST /api/v1/category-match`；presentation ID 通过 axios config
  `aiPresentationId` 注入并由拦截器转换为 `X-AI-Presentation-ID` header，不进入 JSON
  body；显式 timeout 大于后端 deadline 并留余量；类型化结果适配到现有人工候选 shape。
- `front/src/stores/workflow/actions/publishing.ts::autoSuggestCategoriesForDraft`：
  自动匹配唯一入口，逐目标站点调用 `matchCategory`；不包含运行时开关或第二条
  自动匹配分支。属性填充分支 `fillAttributesByAi` 使用同一 wrapper 触发
  `POST /api/category-ai-fill`（见“类目匹配 Capability”属性填充段）。
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

- `erp_web/marketplace_registry.py`：维护站点的 `market_currency`（市场展示）与
  站点锁定的 `listing_currency`，并声明平台能力与店铺绑定字段。Ozon 的刊登币种为空，
  禁止用俄罗斯市场的 RUB 作为发布默认值。Yandex 声明
  `store_binding_fields=("business_id", "campaign_id")`：发布确认的店铺身份要求两个字段
  同时存在，可变的 `shop_name`、脱敏 token 或单独 `business_id` 都不能作为绑定身份。
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

- `erp_web/product_model/platform_sku.py`：卖家 SKU 的唯一生成规则。SKU 稳定绑定
  `draft_id`：同一草稿编辑重发保持不变，同一商品重新推出的新草稿获得新 SKU；
  未发布草稿中的“其他”等占位值会被替换，已绑定远端刊登的历史 SKU 不得静默变化。
- `erp_web/http_route_units/publish_routes.py`：发布预检、payload 预览、同步发布与
  发布队列 HTTP 入口。
- `erp_web/http_route_units/get_routes.py`：发布任务列表与指定 Job 详情的只读查询入口。
- `erp_web/facades/publish_facade.py`：HTTP 层唯一发布 facade；业务编排进入
  `erp_web/runtime_units/publish_workflows.py`。
- `erp_web/runtime_units/publish_adapter.py`：发布平台适配器注册表。只有这里注册且
  在 `marketplace_registry.py` 声明 `CAP_PUBLISH` 的平台才允许进入真实发布流程。
- `erp_web/runtime_units/publish_mercadolibre.py`：Mercado Libre 专属发布与错误处理；
  首次发布创建 item，后续同一草稿按已持久化的 `item_id` 更新。
- `erp_web/runtime_units/publish_ozon.py`：Ozon `/v3/product/import` payload、
  草稿目标站点中的 `type_id/category_id + description_category_id` 配对、异步导入
  终态确认及错误字段映射；不得从商品级 `local_platform_categories` 回捞发布类目。
- `erp_web/marketplaces/yandex_http.py`：Yandex Market Partner API 的唯一 HTTP 边界
  （Api-Key 认证、HTTP 420 限流识别、`errors[]/warnings[]` 解析），覆盖 token 信息、
  campaign、商品映射、价格、库存与类目请求；HTTP/网络错误分类为类型化
  `PublishAdapterError(retryable)`，不包含发布编排。
  `GET /v2/campaigns/{campaignId}` 响应按顶层 `campaign` 解析（无 result 包装）；
  HTTPError 响应体中的平台 `errors[]/warnings[]` 同样解析并逐字段脱敏后保留。
  403/404 按请求上下文分类，不得一律判定为“API-Key 权限不足”：Campaign 端点的
  403/404 提示 Campaign ID 不属于当前 API-Key 所在柜台（确认填写的不是 Business ID），
  token/仓库/价格端点的 403 分别提示对应方法权限（如 INVENTORY_AND_ORDER_PROCESSING、PRICING）。
- `erp_web/runtime_units/publish_yandex.py`：Yandex 发布 payload 构造（按
  “目录商品 / 上架条件 / 价格 / 库存”分组）、`validate_yandex_draft()` 平台校验、
  checkpoint 状态机与错误映射。`publish_yandex_payload()` 只执行第一个尚未完成的
  远端 mutation；`poll_yandex_publish_status()` 只依据已持久化 checkpoint 推进，
  重启恢复不重复执行已完成写步骤。价格写入按已验证 `only_default_price` 分流
  Business/Campaign 级接口，价格进入隔离区时阻断自动确认。
- `erp_web/schemas/yandex.py`：Yandex wire 与发布状态机 shape（token/campaign/
  checkpoint/result 等）的唯一 owner。
- `erp_web/stores/config_store.py`：店铺授权摘要与 `_auth_status_label`。Yandex 在线
  派生的动态授权元数据（`business_id`、scopes、价格/库存能力、仓库等）只持久化到
  SQLite `store_auth` auth detail，不进入静态 JSON；真实 token 或 Campaign ID 变化时，
  同一次保存原子清除旧派生能力与成功态，状态回到“已保存，未测试”。
- `erp_web/facades/product_facade.py`：保存 Ozon 草稿时，若只提供 `type_id/category_id`，
  通过当前 Ozon 类目缓存自动解析并持久化隐藏的 `description_category_id`。
- `erp_web/runtime_units/runtime_api.py::publish_product`：平台无关的预检、artifact、
  日志与商品发布状态持久化；成功结果中的 `item_id/product_id/offer_id` 会写入草稿
  `last_publish_task`，作为后续更新同一远端刊登的身份依据。
- `erp_web/runtime_units/publishing_bus_core.py`：SQLite 发布任务和并发执行；适配器
  必须返回可验证的远端成功证据。所有 enqueue 都必须提供可信 `idempotency_key`；SQLite 原子占用
  该键，并把 `product_id + platforms + draft_id/site/product_id targets + confirmation digests` 保存为
  不可变事实。同键同事实
  返回原 job 且不重复提交，同键不同事实返回 `PUBLISH_IDEMPOTENCY_CONFLICT`。人工页面队列入口使用
  服务端生成的 `manual:<uuid>`，不复用全局任务的稳定键。`GET /api/publish-bus/jobs` 返回按时间倒序的轻量任务摘要，
  支持 cursor、状态、平台和商品筛选；`GET /api/publish-bus/status` 只返回指定 Job 的完整详情。
  两个读取接口都不返回 worker 恢复专用的完整商品快照、approved payload、digest、店铺 identity 或
  幂等事实。
- `erp_web/runtime_units/publish_capabilities.py`：发布摘要包含当前已授权店铺的脱敏稳定
  `store_identity`；validation digest 同时绑定商品、草稿、平台、站点、店铺身份和最终 payload。
  确认后提交会重新执行确定性校验并常量时间比较 digest，随后把已批准 payload/digest/identity 写入
  PublishingBus job。worker 现取凭据，但外发前复核店铺身份与完整 digest，并直接发送冻结 payload；
  不会重新构造已确认内容。Capability 还会在重校验与队列准入前按完整确认事实恢复既有 job，封闭
  “job 已落库、GlobalTask 尚未保存 job_id”的崩溃窗口。店铺切换、payload 篡改或事实冲突都会在网络
  调用前安全失败。
- 发布错误类型化重试契约：PublishingBus 只在适配器抛出
  `PublishAdapterError(retryable=True)` 且未耗尽重试次数时重试；店铺绑定校验失败
  （`PublishApprovalBindingError`）与未分类异常一律立即终态失败。
  `erp_web/marketplaces/config_http.py` 按状态码把 HTTP 失败分类为平台类型化错误
  （401/403 认证失败、404 资源不存在、408/420/423/425/429 与 5xx 可重试），
  网络/超时错误同样类型化；既有消息格式保持不变，字符串解析方不受影响。
- `erp_web/services/image_delivery_service.py`：发布图片 HTTPS delivery 唯一边界。
  图片保存 provider-neutral 的 `storage_key`，公网 URL 只是根据当前 provider 与
  `ERP_IMAGE_HTTPS_BASE_URL` 重新计算的缓存；平台发布模块不得读取隧道、磁盘根目录
  或对象存储配置。`existing_url` 只接受已有公网 URL，`local_static` 把本地文件按内容
  hash 复制到独立公开目录，可由 Quick Tunnel、Named Tunnel 或普通静态服务器暴露。
- `scripts/dev.sh` 默认以 `ERP_IMAGE_HTTPS_TUNNEL=auto` 管理 Quick Tunnel 生命周期：
  检测到 `cloudflared` 后先取得随机 HTTPS 地址并注入后端环境，开发服务退出时一并停止；
  `required` 在 Tunnel 不可用时阻断启动，`off` 禁用自动 Tunnel。固定域名环境直接设置
  `ERP_IMAGE_HTTPS_BASE_URL`，不会创建 Quick Tunnel。
- Ozon 与 Yandex 适配器在草稿校验和 payload 构造前调用图片 delivery（Yandex 以公网
  URL 列表投递 `pictures`）；Mercado Libre 保持平台图片上传接口与 `ml-id:*` 流程，
  不经过通用 HTTPS 图片服务。

Ozon 创建/更新商品是异步操作。提交 `/v3/product/import` 获得 `task_id` 后，必须
轮询 `/v1/product/import/info`；只有每个商品返回 `status=imported` 且没有逐项错误，
才写入 `real_publish_success`。拿到 `task_id` 本身不算发布成功。

Yandex 上架确认同样是异步操作。`offer-mappings/update` 提交后必须由 poll 回读
Business 商品映射与 Campaign 商品状态确认终态；确认轮询超过上限仍未终态时任务失败，
pending 状态不得记为发布成功。确认时 cardStatus（官方 OfferCardStatusType，无
PUBLISHED 值）先于 Campaign 状态裁决：`HAS_CARD_CAN_UPDATE_ERRORS`/`NO_CARD_ERRORS`
表示本次变更未被接受（即使 Campaign 仍为 PUBLISHED），审核中状态继续有界轮询；
只有卡片接受态配合 Campaign `PUBLISHED` 才判定成功。Business 级库存（无仓库组）
写入单一选定发布仓库，避免单一库存数复制到多个仓库造成放大。

## Product Research

- `erp_web/http_route_units/product_research_routes.py`：调研 HTTP 入口。
- `erp_web/product_research_config.py`：调研配置入口。
- `erp_web/services/product_research_service.py`：调研编排与运行服务。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_tools.py`：工具 schema、ToolSet 和 Runtime。
- `tests/test_ai_tool_catalog.py`：注解元数据、TypeAdapter Compiler、Schema 规范化、可信 Scope、
  allowlist、幂等策略与类目试点契约指纹快照。
- `tests/test_category_match_agent_service.py`：真实 `FunctionModel + Agent` 的类目工具循环、
  类型化 output、validator 契约，以及绑定 presentation scope 下发布官方展示 chunk 的
  端到端测试。
- `tests/test_ai_presentation_registry.py`、`tests/test_ai_presentation_routes.py`、
  `tests/test_ai_presentation_context.py`：presentation 预留/claim/lease/TTL 与 chunk
  缓冲边界，reserve/status/stream HTTP 契约（204 无流约定、单 lease、晚 attach 重放、
  断连不取消业务），contextvar scope 派生与 observer 协议。
- `tests/test_ai_agent_factory_presentation.py`：factory 执行内核的 presentation 集成——
  绑定 scope 自动发布官方 chunk、无 scope 运行不产生 SSE、发布失败只降级展示。
- `tests/test_ai_agent_instrumentation.py`：技术 spans、usage、trace 关联、脱敏和故障隔离。
- `tests/test_ai_agent_state_store.py`、`tests/test_ai_agent_deferred_runtime.py`：公开消息
  serialization、版本迁移、审批/拒绝、跨进程恢复、权限/scope/deadline 与幂等 claim。
- `tests/test_global_agent_service.py`、`tests/test_global_task_controller.py`、
  `tests/test_global_task_store.py`：只读 planning profile、静态九能力、顺序状态机、暂停恢复、
  发布确认与本地持久化。
- `tests/test_draft_query_service.py`、`tests/test_market_prepare_capabilities.py`、
  `tests/test_product_capability_service.py`、`tests/test_publish_capability_service.py`：查询快照、
  目标市场纵向能力、商品 mutation、店铺身份 digest 和幂等发布 adapter。
- `tests/test_yandex_publish.py`：Yandex 适配器确定性 payload（目录商品/上架条件/价格/库存
  分组）、checkpoint 状态机、草稿校验与错误映射、首次 mutation 才调用发布接口。
- `tests/test_publish_retry_contract.py`：发布错误类型化重试契约——仅
  `PublishAdapterError(retryable=True)` 重试，绑定错误与未分类异常终态失败，
  `config_http` 状态码分类与既有消息格式保持。
- `tests/test_yandex_publish_workflows.py`：Yandex 预览 digest、确认入队与状态回读的
  HTTP 契约，含 400 需确认 / 409 确认过期路径。
- `tests/test_global_agent_routes.py`、前端 `GlobalAgentChatPanel` 测试：五个 HTTP 入口、稳定对话恢复、
  RequiredInput 与独立发布确认交互。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。
