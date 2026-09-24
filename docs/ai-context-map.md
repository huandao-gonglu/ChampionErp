# AI Context Map

本文件列出后端主要公共入口、依赖方向和测试边界。它面向后续维护者与 AI，
不替代模块内契约。

## 运行时边界

- `erp_web/runtime.py` 与原 `runtime_units` 兼容转发模块已经删除，不得重建聚合入口。
- 直接 import 具体 facade、service、store 或 schema owner。
- 新 HTTP 行为从 `erp_web/http_route_units/` 的显式 handler map 进入；路由把编排交给
  `erp_web/facades/` 或职责单一的 service。
- `erp_web/runtime_units/json_store.py`：运行时 JSON 文件原子读写的依赖轻量 owner；
  配置、发布产物和其他领域模块不得再从类目 Store 借用通用文件写入能力。

## 草稿操作后的继续保存

- `copy_facade.generate_copy_payload` 通过 `current_draft_id` 绑定用户选中的独立草稿。`copy_generation.save_copy_result` 将该操作上下文交给 `ProductStore.save_draft_copy_result`；Store 必须在商品归一化前取出草稿 ID，不能丢失后改为同平台最新草稿。文案仅合入指定草稿的最新内容，已删除的目标不得被重建。
- `/api/category-precheck` 会持久化目标的预检结论，因此响应包含保存后的 `draft`、`productContext` 和索引。前端 `runCategoryPrecheck` 与类目动作同步这份草稿及其 `updatedAt`，后续保存沿用新版本；预检记录只保留检查结论，不嵌套整个保存响应。
- `/api/save-draft` 继续拒绝真正过期的版本；不得通过省略 `updated_at` 或自动强制重试绕过并发修改保护。`tests/test_draft_save_after_actions.py` 验证同商品同平台双草稿的文案隔离、预检后继续保存以及旧版本仍被拒绝。

## 商品采集

- 商品库推到草稿统一从 `/api/claim-products` → `collect_facade.claim_products_payload` → `collect_helpers.claim_products_to_platforms`，请求必须显式携带 `product_ids` 与 `targets`（平台、销售市场、注册语言）。每个商品按语言创建一份独立草稿，市场只包含该语言下勾选的项目；美客多销售市场按授权物流操作归入 CBT 的 `sites_to_sell`。响应的 `claimed_count` 统计成功商品数，`draft_count` 统计实际创建草稿数。
- 顶部批量与单行操作共用 `claimProductsToDrafts`，只传各自商品 ID 和顶部市场选择，不使用当前平台或整库兜底。AI 认领与市场准备任务仍使用其显式平台参数，复用同一个商品复制、草稿持久化循环。

- `source_sites.py` 的采集质量门槛与核价/发布条件分开：1688 有标题和图片且未被验证拦截即可入库。缺失包装长宽高、重量通过 `collect_helpers.py` 的 `missing_fields` / `next_action` 提示在 SKU 页补齐，不触发采集失败；有规格时逐 SKU 判断包装完整性，不用首项资料代表整组。
- `front/src/views/workflow/CollectView.vue` 组织采集方式；`BrowserCollector.vue` 负责浏览器页面选择，
  `CollectBatchManager.vue` 负责 URL 增删改、状态筛选和失败重试。
- 批量列表状态为 `pending`（未开始）、`running`（正在采集）、`waiting_verification`（等待验证）、`success`（完成）、`failed`（失败）。部分结果保留资料提示并显示失败。
- `front/src/stores/workflow/actions/collection.ts` 逐条提交 `/api/collect-batch`。遇到登录或验证码，后端在解析、下载、商品写入之前返回 `waiting_verification`；其 `verification` 契约由 `schemas/collection.py` 定义，仅含标签 ID、原商品 URL、平台。
- `/api/collect-verification` 由 `collect_routes` → `collect_facade` → `source_collect_verification.inspect_collection_verification` 只读检查原标签。前端每两秒检查一次，不导航、不刷新；验证完成后用原标签 ID 调用 `/api/collect-from-browser-tab`，再次验证页面身份并采集，然后继续后续链接。标签被关闭、离开商品或无法读取时返回可操作提示，不改采集目标。
- 用户可以取消等待，未开始的链接保持原状。`collectQueue.ts` 本地保留等待项的标签 ID 和 URL，刷新应用或取消后点击开始采集可恢复；采集应用关闭后不承诺后台执行。不保存凭据或整份商品。正在实际采集时刷新仍标记结果未确认，避免假报成功。删除列表行不删除入库商品。
- `/api/collect-from-browser-tab` 的显式 URL 或标签 ID 只选择指定目标；`save_only` 只保存 HTML/截图，不解析、不下载商品图片、不修改商品。验证码页不会作为部分商品写入。
- 上述等待是 ERP 浏览器采集的领域交互，不创建 Agent run、审批或消息协议，不涉及 Pydantic AI 生命周期。
- `source_collect_browser.cdp_target_for_url` 复用相同 URL 或同一 1688 offer 路径的页面，后者允许验证后查询参数变化；其他商品仍通过 Chrome DevTools 的 `PUT /json/new` 创建目标页。打开操作直接返回目标句柄，采集不重复查找或强制刷新，保留人工登录和验证后的页面；协议依据：[Chrome DevTools HTTP endpoints](https://chromedevtools.github.io/devtools-protocol/)。

## 草稿描述

- 商品采集和主档只保存标题、属性、SKU、图片等事实资料，不采集或维护描述与独立卖点；采集诊断不再统计描述。新建草稿的描述为空，不从来源继承，也不拼接模板描述。
- `services/copy_service.py` 根据标题、商品/来源属性和 SKU 规格生成平台草稿 `description`，允许在草稿中人工编辑；发布与生图读取草稿文案。缺少事实依据的优势、用途、认证等不得编造。
- `stores/product_description_migration.py` 在数据库读取边界移除历史商品/来源描述及卖点，保留各草稿的描述，历史草稿卖点并入各自描述。保存后仅写入当前契约；不改变 SQLite schema，也不改写发布历史。
- 文案生成继续使用 Pydantic AI 原生 `PromptedOutput` 和输出校验，没有新增 Agent 基础设施。

## SQLite 数据库版本边界

- `erp_web/db.py` 是 SQLite schema 与版本门禁的唯一 owner，当前
  `SCHEMA_VERSION=15`。数据库文件不存在或 `user_version=0` 且没有任何用户 schema
  object 时，才会在单事务内创建当前结构。
- 现有数据库只在版本为 15 且全部 table、column、constraint、index、view、trigger 与当前
  建库 SQL 的完整结构签名一致时打开。非空 v0、v1–v14、未来版本和结构残缺/额外的 v15
  都在写入前失败；运行时不升级、修复、删除或重建数据库。
- 旧库切换是显式运维流程：先导出需保留的配置与授权，再停止应用、删除主库及
  `-wal`/`-shm`，创建全新 v15 后导回配置。`upc_pool.json` 是已购买 UPC 的显式资产导入，
  不是旧 schema 兼容路径。

## AI Provider 与 AI Work

### 当前统一边界

最终目标不是只统一 Agent loop，而是统一全部 `connection_type=api` 的 AI 推理请求：需要工具
循环的用例使用 Pydantic Agent，不需要 Agent 的普通 chat/JSON/stream/typed output 使用
Pydantic Direct Model Requests，图片等能力使用锁定版本提供的 Pydantic capability/native tool。两类调用都
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
- `erp_web/services/ai_direct_request_service.py`：普通 API chat/JSON/stream/typed output/image 的唯一
  Pydantic Direct Model 执行入口，负责公开 Pydantic message/event 转换和项目结果归一化；类型化输出
  使用 `OutputObjectDefinition` 的 prompted output，不在业务 Prompt 重复字段清单。用户前台请求携带
  presentation scope 时，由该边界把 Direct Model 原生 `Part*Event` 交给统一 observer，并用
  `PydanticMessageStore` 保存预留 conversation 的官方 `ModelMessage[]`；无 presentation 时不改变 Provider 契约。
- `erp_web/services/ai_structured_output.py`：非 Agent 类型化输出的 dependency-light Schema 适配边界；
  从同一个 Pydantic 类型生成 JSON Schema 并验证返回值。CLI/Browser 尚无 Pydantic `Model` 适配器，
  因而仅在这两个非 API 边界附加自动生成的 Schema；适配器就绪后删除该提示式分支。
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
- `erp_web/services/ai_agent_factory.py`：唯一 Pydantic Agent 装配与运行入口。`ai_model_errors.py` 统一保留嵌套 Direct Model 错误的 HTTP 状态、可重试性和安全原因，供 Agent 与业务工具边界使用。
- `erp_web/services/copy_service.py`：`copy.generate` 通过同一工厂运行独立文案 Agent。
  使用原生 `PromptedOutput` 校验输出结构，生成后不再额外调用 AI 复核语言、事实或营销措辞。
  字段类型、必填内容及平台标题长度校验通过后直接交给领域层保存。格式错误的原生重试
  最多两次，整个生成过程最多三次模型请求，并受总计 240 秒的 deadline 约束。
  事实摘要保留重量的 kg 单位，不复制主 Agent 的全部消息。已核对安装的 Pydantic AI 2.43.0
  与[原生结构化输出文档](https://ai.pydantic.dev/output/#prompted-output)：直接复用原生格式
  校验及重试，不增加业务 Agent loop。文案生成使用集中 Agent 工厂支持的 API 模型连接。
  原生 `UsageLimits` 限制模型请求和工具调用；`PrepareTools` 在预算耗尽后隐藏业务工具，
  不预留可执行的额外额度。`Hooks.before_node_run` 在 `UserPromptNode` 和
  `ModelRequestNode` 开始前更新页面背景，通过 `RunContext.enqueue` 接收用户更新；
  消息注入和事件编码由原生队列负责，不按用户文本生成或重置工具权限。
  主 Agent 使用 `str | DeferredToolRequests`，focused Agent 保留其类型化输出与独立领域能力。
- `erp_web/services/ai_model_context_projection.py`：原生模型请求 Hook 的纯输入投影；旧大工具结果只在发送副本中形成摘要，完整原生 run 历史与持久化不裁剪。保留工具配对、本轮结果及 Provider 需要的思考信息，不维护第二套历史或恢复协议。
- `erp_web/services/global_agent_chat_service.py`：主对话服务；主模型依据完整对话选择实际提供的工具，并按原生执行回执回答先前操作结果。批量任务逐目标汇报覆盖及缺口，不再由额外模型推断授权或推断强制补做的目标。
- `erp_web/runtime_units/collect_helpers.py::claim_products_to_markets`：AI 认领和商品库市场选择共享按语言分组逻辑；全部市场从真实店铺绑定及平台注册表解析。
- `erp_web/services/ai_tool_bridge.py`：`Tool.from_schema` 的输入校验通过原生
  `args_validator` 在执行前复用现有 JSON Schema 与可选的纯领域参数校验器；参数错误抛出 `ModelRetry`
  让模型纠正，授权、执行及输出错误仍由 Runtime 处理，不重试已产生副作用的操作。
- `erp_web/services/ai_agent_budget.py`：通用的本地额度诊断与动态指令。保留资源类型、限制值、
  实际用量和拒绝阶段；本地额度异常不冒充 Provider 限流或领域无匹配。不修改原生消息、
  不实现第二套 Agent loop；重试次数与逐工具请求/返回的闭合仍由 Pydantic AI 管理。
- `erp_web/services/ai_agent_instrumentation.py`：独立 OpenTelemetry 技术 trace owner；
  关闭 prompt/tool 内容采集并在 JSONL exporter 再次脱敏。观测写失败不影响业务结果。
- `erp_web/services/ai_agent_observability.py`：Agent 的 AI Work 内容投影 owner；保存有界且
  脱敏的初始输入、逐轮 Pydantic model request/response 与工具往返，失败运行同样保留模型
  被拒绝的输出和 retry feedback。
- `erp_web/services/ai_pydantic_image_model.py`：登记过的 focused 例外，见下文；它是仅支持
  Images API 的 Pydantic `Model`，只能由 `ai_model_factory` 创建，并且只能经 Pydantic Direct
  Request 调用。
- `front/src/views/AiWorkView.vue`：AI Work 页面。左侧 conversation 列表按 `updated_at` 倒序；
  右侧按优先级选择数据源——前台 presentation（observe Chat 实时消息）、活动 `global.chat`
  （共享 `Chat.messages`）或服务端 `/ui-messages` 只读派生历史；“原始消息”辅助标签提供
  规范 Pydantic JSON 树、Raw JSON 与下载；支持 `conversation_id` / `presentation_id` query 定位。
  全局对话与草稿箱批量准备共享 `front/src/stores/aiChat.ts`。原生工具卡提交批准/拒绝；
  运行期间仍可提交用户消息，收件箱显示接收状态。后台只发送历史版本变更通知，
  前端用官方 Adapter 派生的历史恢复消息，不在浏览器推进 Agent。
  前台短句 `initialUserMessage` 经 reserve 的 `initial_user_message` 传入 presentation scope，
  由 `AiAgentFactory` 保存到原生输入 metadata 的 `presentation_user_message`；只用于展示，
  不替换模型输入、不作为授权证据。`/ui-messages` 在官方转换后逐条恢复短句（空标记隐藏输入）；
  未标记的用户消息原样保留，不按 conversation ID 过滤。原始消息保留完整输入，
  历史展示不依赖浏览器缓存或仍然存活的 presentation registry。

```text
API use case
  → centralized Pydantic Model Factory
      ├─ Agent use case → AiAgentFactory → Pydantic Agent
      ├─ plain chat/json/stream/typed output → Pydantic Direct Model Requests
      └─ image/model capability → Pydantic capability 或登记过的 focused 例外

CLI / Browser use case
  → focused CLI / Browser Adapter
```

不得为了让普通调用复用 Pydantic 而把它们包装成虚假 Agent；也不得因为普通调用不需要 Agent，
就继续维护 Pydantic 之外的通用 API Provider 请求栈。

### Focused 例外登记：专用 Images API

- 缺失能力：锁定的 `pydantic-ai-slim[openai]==2.43.0` 支持 Responses 原生图片工具，但没有
  能绑定 `gpt-image-*` 专用模型并表达 `images.generate` / `images.edit` 的公开 Model。
- 限定范围：只有 `erp_web/services/ai_pydantic_image_model.py::OpenAIImagesModel`；不支持
  chat、JSON、function tool 或供应商级实时增量，不能加入非 API Provider 注册表。
- 强制路径：`ai_model_factory` 创建 → `ai_direct_request_service` 调用
  `pydantic_ai.direct.model_request` / `model_request_stream` → focused Model 使用同一
  Pydantic Provider client。Images API 始终执行非流式供应商请求；需要 AI Work 展示时，
  `request_stream()` 使用 Pydantic `CompletedStreamedResponse(replay_events=True)` 把完成响应
  转成官方一次性事件流，不自定义第二套事件协议。
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
- `erp_web/services/global_agent_chat_service.py`：主对话服务；主模型依据完整对话选择实际提供的工具，并按原生执行回执回答先前操作结果。批量任务逐目标汇报覆盖及缺口，不再由额外模型推断授权或推断强制补做的目标。
- `erp_web/runtime_units/collect_helpers.py::claim_products_to_markets`：AI 认领和商品库市场选择共享按语言分组逻辑；全部市场从真实店铺绑定及平台注册表解析。
- `erp_web/services/ai_tool_bridge.py`：把显式 ERP ToolSet 转换为 Pydantic
  `FunctionToolset`；Pydantic tool 只调用 `AiToolRuntime.execute(...)`，不直接调用
  领域 executor；原生 Pydantic 调度独立同步工具的并发，Runtime 仅按 call ID 去重。
- `erp_web/services/ai_agent_factory.py`：由 Pydantic Agent 独占 model → tool → model
  循环、类型化 output、重试和 usage limit；`run_sync(...)` 与 `open_stream_run(...)`
  是统一同步/流式入口，保留完整 canonical history，仅追加原生 `new_messages()`；不能把模型上下文裁剪结果写回历史。
  审批与外部工具暂停由原生 Deferred 请求和结果表达。
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

## 单主 Agent（global.chat）与领域能力

主 Agent 直接调用 focused ERP 工具，读取结果后继续决定下一步。草稿箱的“AI 准备所选”
只把目标 `draft_id` 和用户目标提交给同一入口。不存在固定步骤计划或第二个执行 Agent。

```text
POST /api/v1/ai-chat/runs（Vercel SubmitMessage，可带 target_draft_ids）
  → ai_chat_facade → VercelAiUiService → GlobalAgentChatService
    → AiAgentFactory → Pydantic Agent → 原生 Tool → AiToolRuntime → 领域 Capability
      ├─ 普通结果/缺字段 → 原生模型继续决策
      ├─ ApprovalRequired → 原生工具卡 → DeferredToolResults
      └─ CallDeferred → 原生请求和历史同事务落盘
          → AgentJobService 领取领域 Job → 真实终态 → DeferredToolResults → 同一入口恢复
```

当前 owner：

- `erp_web/ai_capability_composition.py`：显式 Catalog 与场景权限；全局工具包含领域读写工具。
- `erp_web/facades/agent_capability_facade.py`：应用能力 Scope、所选草稿范围、可信消息来源、Job Reader 装配。
- `erp_web/services/global_agent_chat_service.py`：主对话服务；主模型依据完整对话选择实际提供的工具，并按原生执行回执回答先前操作结果。批量任务逐目标汇报覆盖及缺口，不再由额外模型推断授权或推断强制补做的目标。
- `erp_web/runtime_units/collect_helpers.py::claim_products_to_markets`：AI 认领和商品库市场选择共享按语言分组逻辑；全部市场从真实店铺绑定及平台注册表解析。
- `erp_web/services/ai_tool_bridge.py`：机械参数校验、原生并发、审批快照与 Deferred 转接；不选择下一步。
- `erp_web/services/tool_approval.py`：业务审批内容 digest，绑定工具名/版本、operation key、原生 call ID 和审批版本；执行前重核。
- `erp_web/services/agent_run_storage.py`：原生 hook 到消息 CAS、输入收件箱与副作用检查点的适配。
- `config/agents.md`、`erp_web/services/agent_memory.py`：ERP 主 Agent 的长期业务记忆与唯一文件读取边界。`GlobalAgentChatService.instructions()` 在每次新运行或 Deferred 恢复时加载当前应用目录下的文件，沿现有 Factory 的原生 `instructions` 注入系统上下文；不增加 Agent loop 或消息协议。它不受页面背景眼睛开关影响，不加载仓库开发用 `AGENTS.md`，不回退到其他实例的记忆。文件为 UTF-8、上限 32 KiB；缺失或空文件表示没有附加记忆，读取失败、非法编码和超限明确报错，不静默截断规则。文件编辑在下次 run 生效；当前仅提供文件读取，未提供 Agent 自主写入记忆工具。接入主 Agent 记忆不表示已有专用属性 Agent 的规则和校验已经完成迁移。
- `erp_web/schemas/ai_page_context.py`：主对话页面背景的有界契约与中文指令渲染，只接受页面枚举和资源 ID。`vercel_ai_ui_service.py` 校验请求的 `page_context` 后写入原生消息 metadata，缺省表示本条消息不携带背景；客户端消息 metadata 仍被丢弃。`AgentRunStorage` 随用户输入更新背景，Factory 使用 Pydantic AI 原生动态 `instructions` 注入模型系统上下文，不创建独立系统消息历史或新 Agent loop；Deferred 恢复复用已保存快照，后续关闭开关会清除当前背景。
- `front/src/stores/aiPageContext.ts`、`useAiPageContext.ts`：可见页面及编辑区域提供定位信息，输入框眼睛开关默认开启并在本机记住选择；普通发送和运行中追加消息均在发送瞬间复制背景。关闭编辑器、切换页面或 KeepAlive 停用时撤销该区域背景，页面背景不作为写权限，也不携带表单未保存值。
- `erp_web/stores/agent_call_store.py`：原生 Deferred 请求/结果序列化、收件箱和领域执行回执；不存步骤计划、Agent 状态或事件副本。
- `erp_web/services/agent_job_service.py`：固定大小线程池领取和对账领域 Job；scanner 不执行模型。等待平台真实结果后恢复原生 Agent，允许再调用工具。
- `erp_web/runtime_units/domain_job_readers.py`：发布与研究 Job 的只读终态和有界活动证据。
- `erp_web/schemas/domain_jobs.py`：Job Reader 的有界生命周期/活动证据，供模型了解真实终态。
- `erp_web/stores/product_mutation.py`：商品聚合的短期互斥；`ProductStore` 的局部读改写保护、草稿旧快照冲突检测。锁不跨模型调用或人工等待。
- `erp_web/runtime_units/conversation_fact_capabilities.py`：按草稿、会话归属查询真实用户消息；引用不能由模型自报可信标志替代。

原生 Deferred 要求当前批次全部调用结果/审批齐备才恢复模型。部分结果先落盘，独立领域 Job 可并发。
新增用户消息始终可接收，在下一个原生模型边界生效。取消不会撤销已经发出的平台操作；未开始的调用先返回用户要求已更新，等待模型重新决定。

缺资料是工具业务结果，包含字段、原因、选项和工具参数路径；主 Agent 可以先读取关联商品、草稿、平台和有权限历史。
商品共用事实与各草稿/站点决定分开保存。`source_message_id`/`source_conversation_id` 只定位实际消息，服务端检查归属、实体范围与值；已保存销售目标可以复用。

`DraftQuerySnapshot.total` 覆盖完整匹配集合，`draft_ids/items` 只保存有界页。主 Agent 使用返回的稳定 `draft_id` 调用写工具。
目标平台/站点不明确时返回 `DRAFT_TARGET_AMBIGUOUS` 或 `DRAFT_TARGET_SITE_AMBIGUOUS`，不能静默选首项。

### Endpoint Coverage Manifest

`erp_web/ai_capability_coverage.py` 为全部已处理 HTTP 端点维护静态声明清单：每个
端点标注 `business_domain` 与处置——`capability` 必须列出能力名，`internal_only` /
`excluded` 必须给出原因。`all_handled_endpoints()` 汇总 GET/POST 路由 owner 的全部
入口；架构测试要求清单零未分类、零遗漏，业务域端点不允许无原因排除。

### 目标市场 Capability 拆分

- `erp_web/runtime_units/market_capability_support.py`：草稿定位、目标选择、类目详情与持久化共享支撑。
- `erp_web/runtime_units/category_capabilities.py`：`category_match` 的稳定草稿 adapter；focused
  类目匹配函数由 facade 注入，runtime 不反向 import facade。
- `erp_web/runtime_units/attribute_fill_capabilities.py`：规则填充与 focused 属性 Agent adapter；
  未解决的真实必填属性返回类型化 `RequiredInput`。
- `erp_web/runtime_units/market_pricing_capability.py`：确定性核价和草稿持久化。
- `erp_web/runtime_units/market_prepare_capabilities.py`：`draft_prepare_for_market` 的高层顺序编排；
  复用现有目标草稿、文案、图片、类目、属性和核价 owner，不复制领域实现；文案重生成以稳定
  conversation/tool_call operation key 与文案同次持久化，重启后不会重复消费同一次 `regenerate_copy`。
- `erp_web/runtime_units/product_capabilities.py`、`erp_web/runtime_units/publish_capabilities.py`：
  商品读取/幂等字段更新/图片准备，以及确定性发布校验/确认后队列提交的 focused adapter。
  `product_read` 按需返回 `ProductFacts.attributes` 的主档补充属性和
  `ProductFacts.source_attributes` 的完整来源属性，不按通用字段
  白名单裁剪；来源原文、范围值与多规格混合值只作为待核实资料，不作为指令或精确参数。
  商品编辑页复用 `ProductAttributesEditor.vue`，分别展示、搜索和编辑“商品补充属性”
  与“来源产品属性”；通过 `/api/save-product` 分别保存至 `attributes` 与 `source.attributes`。
  前端保存未编辑的属性时保持原始 JSON 类型；AI 主档补丁完成后须用 `product_read` 回读
  对应字典的实际值，不能仅凭写回执认定某个具体属性已生效。`ProductStore.save_product_profile` 清理
  被修改或删除的来源属性在主档 `attributes` 中的同值采集副本，保留独立维护的不同值。
  平台属性由主对话根据来源事实及真实平台定义直接判断，通过确定性写工具保存。
- `erp_web/runtime_units/source_inspect_capability.py` 与 `erp_web/schemas/source_inspect.py`：
  `inspect_source_facts` 提供草稿来源属性的分类文本视图、完整原始 JSON 与采集时间；
  复用 `ProductCapabilityScope` 和草稿详情存储接口，显式进入主 Agent 只读能力集合。
  分类只作查看提示，不生成无采集依据的置信度或认证结论；执行继续使用现有 Pydantic AI 原生工具链。

focused 类目和属性执行在完成、暂停或后处理失败时都返回自己的 AI Work `conversation_id`；高层市场
准备聚合为 `agent_execution_conversation_ids`，主 Agent 在原生工具结果中取得这些 ID，不复制
transcript 或 Tool 输出。

### 原生对话 HTTP 与消息历史

- `POST /api/v1/ai-chat/runs`：新用户消息或官方 approval-responded 消息。用户消息 ID 幂等领取与输入同事务保存；仅服务端历史参与模型调用。
- `POST /api/v1/ai-chat/cancel`：接收 `{id, message_id}`，直接取消本轮原生令牌；不排队发送取消 prompt。相同消息的停止幂等，已知旧消息不能取消新回合；取消先于发送到达时，现有 claim 幂等记录阻止迟到消息启动。
- 同会话有活动 run 或未齐备 Deferred 时返回 HTTP 202 接收凭据。批准/拒绝还需 `X-Approval-Token`，客户端不能更改服务端 call ID、工具名或参数。
- `GET /api/v1/ai-work/conversations` 与 `/{id}`：历史索引和 canonical Pydantic JSON。
- `GET /api/v1/ai-work/conversations/{id}/ui-messages`：官方 `VercelAIAdapter.dump_messages()` 输出，加有界待处理调用、输入接收状态与运行错误；`run_active`、`run_status`、`latest_message_id` 分别来自运行互斥表及原有 claim，供停止确认和目标关联。
- `GET /api/v1/ai-work/conversations/{id}/events`：只通知 `history_version` 变更，前端重新读取 `/ui-messages`；不保存、重编码或重放自定义 Agent 事件。
- `ai_chat_run_registry.py`：进程内同会话互斥和 Pydantic `CancellationToken` 关联；历史提交额外使用 SQLite CAS 防止旧写者覆盖。
- `ai_chat_turn_claim_store.py`：用户输入幂等身份/归属/安全错误码，不含消息正文。
- `pydantic_message_store.py`：完整原生消息的读取、校验；`agent_call_store.py` 组合提交同一消息表及 Deferred，二者不各存一份历史。

实时流为 `AgentStreamEvent → VercelAIEventStream → SSE → @ai-sdk/vue Chat`。HTTP 断线不取消后台执行。
慢客户端超过有界队列后收到官方编码的 error/finish，再从已提交历史恢复。最终消息先持久化再下发终态。
未知副作用保留回执，重启不盲目重发；实际平台 Job 自己保留幂等和对账状态。

AI Work 选中普通对话后，直接使用本次 `/ui-messages` 响应绑定共享 Chat 并显示输入框；
无需额外点击继续，也不重复读取历史。打开对话只读取历史并订阅更新，发送消息才提交新的 run。
历史响应必须仍匹配当前选择，旧会话回调不得断开当前订阅；业务 Agent 执行记录仍只读展示。

### 对话停止与框架边界

当前锁定 Pydantic AI 2.43.0，已核对[官方取消文档](https://pydantic.dev/docs/ai/core-concepts/agent/#cancelling-a-run)及安装源码。
直接使用原生 `CancellationToken`、`RunCancelled.all_messages()` 与 Vercel `abort` 编码；
没有自研 Agent 取消状态机或事件编码。`services/ai_run_cancellation.py` 只通过 ContextVar
把同一个官方令牌传给主 Agent、同步工具中的嵌套 Agent 和该范围内领取的后台领域任务。
工具 I/O 的既有 `bounded_timeout_seconds()` 检查点同时检查取消，阻止后续批次；
已阻塞的同步 I/O 和已提交的平台操作无法由 Python 线程取消强制撤销，保留实际回执并继续对账。
Agent 取消后保留框架快照，未闭合工具历史在新用户回合由框架自动修复。

领域侧只撤下尚未执行的 queued 工具、未处理输入和原生 Deferred 恢复请求；
不会为停止再调用模型生成确认，也不会由后台扫描重新启动已取消操作。
新的明确用户输入可建立新的令牌；旧操作的工作线程仍持有旧令牌，不能被新输入重新激活。
前端立即调用 `Chat.stop()` 结束可见输出，同时独立请求后端取消；收到后端确认前显示“正在停止”。
后台 Deferred 等待期间也提供停止按钮。浏览器断线仍不等价于用户明确停止。
对话输入区连续按两次 Esc 复用同一停止入口：第一次把按钮显示为 `Esc`，第二次触发停止；
焦点变化、窗口失焦、切换会话、输入其他按键或开始组合输入时取消等待，长按重复事件不算第二次。
输入法组合期间不拦截 Enter/Tab 执行发送或命令，也不把候选框 Esc 当作停止；
按[浏览器 IME 事件说明](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event#keydown_events_with_ime)
同时检查组合事件状态、`isComposing` 和 `keyCode === 229`，覆盖 compositionend 先于提交按键的顺序。

### AI Presentation 通用可观测层

用户直接触发的 AI 能力保持业务 HTTP 接口自有 start 与类型化结果，实时展示由通用
presentation 层统一提供：前端触发前预留 presentation，业务请求在 HTTP 公共边界用
`X-AI-Presentation-ID` header claim 该预留，此后请求范围内经 `AiAgentFactory`
运行的 Pydantic Agent，以及统一边界内的 Pydantic Direct Model，会把 native event 流自动发布为官方 Vercel chunk；前端用
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
  `RegistryAiPresentationObserver` 透传 Pydantic native events，同时经
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
  `scope.conversation_id` 持久化，实时流与 `PydanticMessageStore` 历史同一 ID。存在
  observer 时 `session.events()` 返回包装流，消费
  它同时驱动官方转换/发布，事件原样透传；生命周期通知（run_started/running/finalizing/
  completed/failed）与子运行紧凑状态卡全部故障隔离，展示失败只降级展示。无
  presentation scope 的后台 Agent 不产生 SSE；child Agent 不产生第二条 SSE。
- `erp_web/services/ai_direct_request_service.py`：在 HTTP presentation root scope
  存在时将文本、JSON、联网搜索和图片请求转为 Pydantic Direct Model stream，原子领取同一个 root run 槽位，并把原生
  `PartStartEvent` / `PartDeltaEvent` / `PartEndEvent` 交给 observer；未绑定展示时仍执行原非流式
  请求。root Direct 请求完成前后把官方 request/response 保存到 presentation conversation；Direct Model 不伪报 `had_agent_run`，同一 presentation 的后续 Direct 请求不创建第二条
  start/finish 流。OpenAI Responses 官方终态包含完整 response，且 `output` 为数组；部分第三方
  网关会在内容增量完整后发送 `response.completed.response.output=null`，Pydantic AI 2.22.0 至
  2.37.0 均会在终态辅助函数中迭代该空值。Direct 边界仅在异常精确来自该 Pydantic 辅助函数、
  API style 为 `openai_responses` 且 `response_stream.get()` 已有有效 parts 时将其恢复为正常 EOF；
  其他异常不吞掉。待 Pydantic AI 原生兼容空 output，或所有已支持网关遵循官方 schema 后删除
  此临时适配。
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
- 已迁移：类目匹配、类目属性填充、单个/批量文案、图片编辑和翻译/重绘、类目/平台属性翻译、
  产品调研 AI 搜索与 Provider 测试、AI 模型能力测试，全部使用同一 wrapper；不存在业务专用展示 start/result 协议。完整契约见
  `docs/aiworkpage.md`。

## 类目平台搜索与规则读取层

- `erp_web/marketplaces/category_provider.py`：定义绑定式 `CategorySearcher` 与
  `CategoryNavigator`，以及类目详情、属性定义和枚举分页的 `CategoryProvider` ABC；
  注册平台必须显式实现核心规则读取契约。
- `erp_web/schemas/category_definition.py`：内部 `CategoryDefinition`、有界公共属性/枚举
  分页 View 与稳定 fingerprint 的唯一 shape owner。内部定义不含平台原始 `raw` 或完整
  枚举全集，公共 View 也不暴露 `platform_binding`。
- `erp_web/runtime_units/category_catalog.py`：业务消费者的统一类目读取入口；负责 Provider
  解析、定义 Loader 注入与有界公共投影。类目匹配、属性填充、预检、payload 编译、前端和
  Agent 工具不得绕过 Catalog/注入 Loader 直接读取平台规则。
- `erp_web/runtime_units/category_providers.py`：Mercado Libre、Ozon、Yandex 的显式
  `CategoryProvider` 实现与注册表；平台 API shape 在这里归一化为当前定义和明确
  `platform_binding`。Mercado Libre CBT 类目预测固定调用
  `/marketplace/domain_discovery/search`，并统一通过
  `store_credentials.get_mercadolibre_access_token()` 取得已校验、必要时已刷新的
  Global Selling Access Token；Provider 不得直接读取凭据。区域站点仍调用
  `/sites/{site}/domain_discovery/search`。
- `erp_web/runtime_units/category_definition_cache.py`：统一属性定义持久缓存 owner；24 小时
  fresh、最多 7 天 transient stale，401/403、凭据缺失、禁用类目和结构错误不得用 stale
  掩盖。缓存不进入商品、草稿、任务或 Agent history。
- `erp_web/schemas/category_grouping.py`：刊登分组字段识别和值派生的唯一纯规则入口。
  Ozon 按类目字段名称识别，Yandex 使用分组属性 200；组合展示使用 `draft.grouping.name`
  或标题，独立刊登省略可选分组值，必填值按 SKU 卖家编码派生。公共属性页的
  `CategoryAttributeSummary.managed_by=listing_grouping` 表示由系统填写，保持平台的
  `required/read_only` 事实不变；该标记在公共投影时计算，不依赖旧缓存是否携带标记。
  普通属性和 SKU 属性 AI 填写都排除此字段；`category_model` 必填检查、SKU 发布投影与
  Ozon wire 构造使用同一规则。旧属性值不能改变分组设置，也不作为第二份可编辑来源。
  仅调整领域输入与确定性派生，不新增或修改 Agent 生命周期、模型循环或重试机制。
- `erp_web/runtime_units/category_searchers.py`：任务入口根据当前平台实例化具体
  检索对象。Mercado Libre 调用 `domain_discovery/search`；Ozon 同一绑定对象同时
  保留人工关键词搜索能力并为自动匹配实现树导航；Yandex 只在本地缓存类目树上做
  规范化关键词匹配（source `yandex_cache`），并把限流/认证/缺凭据错误统一分类。
  平台选择只发生在对象创建处。
- Mercado Libre OAuth 的 `code_verifier` 只在生成授权链接到兑换 code 期间存在；兑换
  成功后必须删除，授权检查清单在 Access Token 与 Refresh Token 已就绪时不再把它显示为
  缺失项。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 类目语料的刷新、人工搜索和树导航入口；
  24 小时内复用缓存，远端瞬时网络错误时最多使用 7 天旧语料，认证错误不允许
  stale fallback。完整树不会进入 AI 上下文。
- `erp_web/runtime_units/ozon_category_cache.py`：Ozon 展平类目语料的版本化压缩 JSON
  持久化 owner；使用原子替换写入，文件只含 Client ID 单向摘要，不保存凭据。
- `erp_web/runtime_units/yandex_category_api.py`：Yandex 类目树与类目参数语料的刷新
  与缓存 owner；类目树按语言与凭据作用域缓存（内存 + gzip 持久化 JSON），6 小时
  新鲜窗口用于避开 Yandex 每小时限额，远端瞬时错误最多使用 7 天旧语料；底层类目参数
  还有短 TTL 内存缓存，归一化后的 `CategoryDefinition` 统一进入上述持久定义缓存。
  缓存文件只含凭据作用域单向摘要，不保存凭据。
- `erp_web/schemas/category.py`：规范化候选、搜索结果、匹配结果 shape，以及
  Agent Service 与领域工具共同使用的请求级 `CategoryCandidateLedger`；
  `normalize_category_attribute_definition` 为带单位属性暴露 `unit_options/default_unit`。
- `tests/test_category_searchers.py`、`tests/test_ozon_category_api.py`：平台对象选择、
  远端/缓存搜索、错误分类和 Ozon ID 配对测试；含 Yandex 关键词搜索与
  HTTP 420 限流（可重试）/401 认证失败（终态）分类。

`erp_web/runtime_units/category_query_capabilities.py::category_search`（v3）为通用 AI 查询入口，
接收 `product_type` 与 `keywords` 列表，合并查询固定语言的通用名和补充词，复用
`category_keyword_search.py` 的并发与合并逻辑；`limit` 限制合并
结果总量，返回逐词错误与命中词，同时保留 Ozon 类目 ID 配对。
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
  `description-category/attribute/values` 接口分页，并把非空检索交给平台的
  `description-category/attribute/values/search` 大字典搜索接口；结果短时缓存。品牌空查询首屏
  会用当前类目的实时搜索结果置顶官方“无品牌”候选；`无品牌/其他/Generic/no brand` 等查询别名
  只转换为平台原文检索词，枚举 ID 不做跨类目硬编码。
- `erp_web/schemas/category_brand.py`：平台品牌身份及无品牌查询词；仅采用平台实际返回的候选。
- `erp_web/product_model/category_model.py`：类目选择、属性有效性与发布必填项判断。
- `erp_web/runtime_units/product_capabilities.py`：属性任务优先使用 `draft_attributes_read(scope=common)`，一次返回 `product` 商品/来源事实、`targets` 全部目标的类目及完整已填公共属性、共用包装尺寸和 SKU 数量，不携带图片或 SKU 明细。指定 `scope=sku` 和明确平台/站点后，才按 `next_offset` 分页读取已选启用 SKU 的有效事实和差异属性。
- `category_attributes_query` 默认 `scope=common`，SKU 任务使用 `scope=sku`，检查全部定义可用 `scope=all`；`write_scope` 标明可写范围，排除字段通过 `excluded_attributes` 的精简记录说明。过滤沿用原始分页游标，空页仍按 `has_more` 继续；不丢弃必填或可选属性。`category_attribute_values_query` 查询真实候选，小字典优先空查询，独立目标/候选并行，同一目标集中写入。主对话负责事实判断、语义匹配、翻译和缺资料时询问用户；不设前 20 个可选属性的限制。
- `product_attributes_update` / `draft_sku_attributes_update` 分别保存公共属性和指定 SKU 的差异属性。`erp_web/runtime_units/category_attribute_updates.py` 只执行确定性校验：服务端重读类目定义，核对作用域、只读字段、值类型/数量/单位以及平台枚举 ID 与原文。网络校验后在商品锁内重读当前目标，再局部合并本次字段，拒绝变化后的类目、停用或未选 SKU。
- `erp_web/runtime_units/category_attribute_access.py` 是查询与写入共用的纯作用域规则：公共、SKU、托管及只读字段一致判定。托管字段在枚举查询前拒绝；Ozon 单字符枚举值用字典分页按 ID 和原文精确核对，不走至少两字符的搜索端点，不跳过枚举真实性校验。本地查询参数错误不可重试，网络故障保留可重试属性。
- 属性填写只有主对话这一条 AI 路径。页面入口、专用属性 Agent/复核模型、局部 HTTP 调用和复合草稿准备里的隐式属性步骤均已删除。`draft_prepare_for_market` 返回的完成步骤只包含目标、文案、图片、类目和定价；主对话随后按需直接填写属性。
- `erp_web/services/global_agent_chat_service.py` 与 `config/agents.md` 规定事实复用、公共/SKU 边界、无品牌优先及缺口汇报。继续使用已安装的 Pydantic AI 2.43.0 原生工具调用、消息历史和指令装配；此次不需要新增 Agent loop 或生命周期。
- `front/src/composables/useAiAttributeResults.ts` 仅把本轮成功写入回执投影到当前草稿同一类目的属性表，逐字段更新；历史回放和失败结果不覆盖表单，不触发业务调用。眼睛开关继续控制发送时的页面背景。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`（ID 原样按字符串存取，不做数值化），搜索输入不进入草稿；
  实时候选按 `next_cursor/has_more` 追加并按 ID 去重，大品牌字典通过“加载更多”继续读取；
  带 `unit_options` 的属性通过数值输入 + 单位下拉生成 `{value, unit}`；
  发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories`（v4）。先在 `product_identity` 摘出原始规格中的实物结构，再填写固定语言的
  `product_type / alternative_names / keywords`，三组词在一次工具调用中合并查询。
  不额外启动规划 Agent；工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/schemas/category_search_language.py`：根据注册平台/站点确定唯一检索语言，独立于商品
  原文和草稿语言。Yandex/Ozon 俄语，MLB 葡语，其余美客多本地站西语，CBT 预测接口固定英语。
  CBT 覆盖定义在 `marketplace_registry.py`，不改变刊登语言。执行前整批拒绝中文或错误文字体系，
  被拒的批次不调用搜索器；拉丁字母短词的英/西/葡语语义仍需要模型遵循契约，不能将文字体系
  检查宣称为完整语言识别。
- `erp_web/runtime_units/category_keyword_search.py`：关键词批量 I/O owner；每个匹配任务内
  去重并复用成功查询，最多 3 个并发查询，沿用入口绑定的 provider 超时和任务 deadline。
  每词最多 8 个候选，交错合并后最多返回 24 个新增候选，保留 `matched_keywords`、逐词错误和
  `truncated`。后续查询不重复展开已见类目的全文，改用 `repeated_candidate_ids` 引用历史；
  已见类目仍可最终选择，新增候选优先使用返回名额。`truncated` 与 `remaining_candidate_count`
  只表示缓存中尚有未展示的新候选；`query_candidate_counts` 是每词排序返回量，不是全库覆盖率。
  仅将实际返回模型的候选登记到账本；缩小关键词组可从缓存取回被裁剪的候选。
  64 个词仅作为异常入参保护，不设置总关键词配额；工具调用次数仍由 Pydantic AI 原生预算管理。
- `erp_web/facades/category_match_facade.py`：`category_match` 共享业务阶段；
  首轮发送裁剪后的双语商品事实；绑定导航器时发送真实顶层节点并允许最多四次树导航，
  绑定搜索器时使用关键词列表批量发现，所有平台复用相同批量契约。最终选择必须经过叶子候选账本、站点、可发布状态、
  详情和 Ozon ID 配对校验；匹配阶段不读取属性定义，属性由用户选择类目后的独立加载步骤读取，
  避免属性接口超时使已完成的匹配失败。达到资源上限时返回 failed 并保留通用额度错误和 details。
  仅模型主动、有效地 abstain 返回 unresolved；检索未确认不等于平台没有类目，不静默改选。
  `prepare_category_match_input / setup_category_match_search / finalize_category_match`
  被 主 Agent 领域工具 与同步 focused HTTP 入口共用，行为一致。
- `erp_web/services/category_match_agent_service.py`：`category.product_match` 的 focused
  Execution Profile、prompt 渲染、类型化 `CategoryMatchAgentOutput` 与 Ledger output
  validator；关键词模式首轮一次规划基于实物的主要相关方向，批量检索后优先提交结果；
  仅有具体缺口才补查，不按最低关键词数量阻止 abstain，也不额外启动规划 Agent。
  最多八次实际工具调用，模型请求上限为十二次，给越界纠正及最终输出的原生校验重试留出余量；
  总 deadline 为 150 秒。保留针对具体缺口的补查能力。输出 `category_match.v2` 携带完整路径、
  实物类型关系与中文结构对照；路径必须来自候选账本，明确结构冲突或不确定时不得选择。
  原始标题/规格优先于翻译和营销扩写；同一用途或材质不能把相邻叶子变成上位类目。
  这些约束不能证明模型的语义判断始终正确，须用真实模型评测而非旧 AI 选择作为正确答案。
  输出校验、重试、调用预算和生命周期均直接使用 Pydantic AI 2.43.0 原生能力；
  已核对官方文档：https://pydantic.dev/docs/ai/core-concepts/output/ 与
  https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/ 。
  只通过统一 `AiAgentFactory` 的流式 `open_stream_run` 运行（同步执行路径已删除）。
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
  body；显式 timeout 为 180 秒，覆盖后端 150 秒 deadline 并留余量；类型化结果适配到现有人工候选 shape。
- `front/src/stores/workflow/actions/publishing.ts::autoSuggestCategoriesForDraft`：
  自动匹配唯一入口，逐目标站点调用 `matchCategory`；不包含运行时开关或第二条
  自动匹配分支。属性填写统一从主对话执行。
- `tests/test_category_match_facade.py`、`tests/test_category_tools.py`：首次上下文裁剪、
  Ozon 逐层导航与有限回退、Mercado Libre 多轮换词、未知 ID、deadline、凭据和工具去重测试。
- `tests/test_ai_agent_budget.py`：使用原生 FunctionModel/Agent 验证单批和跨批第五次调用不执行、
  RetryPrompt 正确闭合工具消息、预算错误保持通用含义。
- `scripts/evaluate_category_match.py`：读取历史商品事实或 JSON，使用当前配置的真实模型和平台
  检索器，记录首批完成率、候选召回、调用数、重试、耗时与结构判断。评测使用隔离数据库，
  不修改原会话、商品或草稿；可临时指定已配置模型比较，不能把旧 AI 结果当作已确认标准。

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

- 国际物流入口：现有 `/api/calculate-price` → `runtime_units/pricing_runtime.py`
  → `services/pricing_shipping.py` → `international_shipping/ShippingModule.quote()`。
  模块负责 Ozon/Yandex 费率版本和三平台物流计算，返回全部候选；ERP 使用既有汇率
  换成物流字段 CNY/USD 后选最低价。模块通过回调使用当前售价算法校验货值，
  不反向导入 ERP。Mercado 的内置运费表已删除，多销售国家分别请求报价并计算售价。
  凭据由项目装配，Mercado token 仍通过 `get_mercadolibre_access_token()` 取得。
  证据进入既有核价依据和指纹；逐 SKU 核价固定模板版本。无新增 tariff HTTP 端点，
  模板导入/预览/启用由模块 CLI 维护，详见 `erp_web/international_shipping/README.md`。
- 店铺授权配置（`store_auth.auth_detail_json`）中的 `listing_currency` 是核价与发布
  的唯一币种事实源。注册表、国家、站点、草稿历史值和前端 option 都不是发布币种
  来源，也不得作为 fallback。
- `erp_web/marketplace_registry.py`：只维护站点身份、标签、语言、平台能力与店铺绑定
  字段；不再携带 `market_currency`/`listing_currency`。Yandex 声明
  `store_binding_fields=("business_id", "campaign_id")`：发布确认的店铺身份要求两个字段
  同时存在，可变的 `shop_name`、脱敏 token 或单独 `business_id` 都不能作为绑定身份。
- `erp_web/services/listing_currency_service.py`：无平台分支的纯状态机服务。负责
  远端发现结果归一化（单值锁定 / 多值待选 / 无能力人工 / 失败 refresh_failed）、
  人工选择校验、ISO 4217 校验与币种指纹计算；不做网络请求、持久化或站点推断。
- `erp_web/runtime_units/store_credentials.py` 与
  `erp_web/runtime_units/mercadolibre_auth.py`：授权 tester 在凭据校验成功后调用平台
  远端能力（Ozon `/v1/seller/info`、Yandex Business settings、Mercado Libre
  `/users/me` + `/marketplace/users/{user_id}` 账号映射；区域账号再读取站点元数据），
  返回统一发现结果，由共享状态机持久化到店铺授权配置。CBT 是 Global Selling
  父账号/全局刊登命名空间，不是普通国家站点：OAuth token 与 `account_user_id`
  只绑定这个 CBT parent；子 marketplace user 只作为 `site_id + seller_id + logistic_type`
  operation 保存，不能拆成多套本地店铺账号。禁止请求 `/sites/CBT`；标准 CBT 按
  官方发布契约把 USD 作为 `locked` 发现结果持久化，来源为
  `global_selling_contract`。账号实际启用的子市场与物流方式持久化在
  `marketplace_bindings`，不得从静态注册表推断。
  授权失败或凭据/身份变化会清除币种 ready 状态；核价层不再有远端币种补取副作用。
  `store_credentials.get_mercadolibre_access_token()` 是业务代码取得 Mercado Libre
  token 的唯一入口：它在凭据锁内重读 SQLite 最新值，经 `/users/me` 校验，并在明确
  401 时以 CAS 语义刷新一次 access/refresh token。类目、订单、图片与发布调用不得
  从配置字典直接提取 token；刷新实现不再属于 `publish_mercadolibre.py`。
- `erp_web/http_route_units/auth_config_routes.py::/api/store-auth/currency`：受控人工
  币种选择/填写接口；`/api/save-settings` 只接受注册表凭据字段与非敏感静态字段，
  币种派生字段一律由后端授权/币种服务写入。
- `erp_web/services/pricing_service.py`：所有发布售价使用 `{amount, currency}` Money；
  商品成本、物流与利润先统一在 CNY 核算，再按店铺 `listing_currency` 换算。
  每个目标分别保存 `calculation_basis`（含 `currency_fingerprint`）与 SHA-256 指纹。
- `erp_web/runtime_units/draft_publish_context.py`：从 `pricing.targets[platform:site]`
  投影当前目标的发布价格，并提供 `build_store_publish_context()`。持久化草稿没有含义
  不明的顶层 `price/currency`。
- `erp_web/runtime_units/publish_validation.py`：发布前重新加载当前店铺配置，核对店铺
  币种 ready 状态、草稿币种快照、币种指纹、Money 币种、核价指纹、商品成本及包装
  尺寸；任何变化都会把旧核价判为 stale（STORE_CURRENCY_CHANGED/PRICING_STALE）并要求
  重新核价。
- Mercado Libre CBT 草稿中的 `site=CBT` 只表示 Global Selling 刊登范围与 CBT 类目
  命名空间；真实销售目的地保存在当前目标的 `sites_to_sell[]`，每项必须精确匹配
  授权同步的 `marketplace_bindings` 中的 `site_id + logistic_type`。不得把 CBT 写入
  `sites_to_sell[].site_id`，也不得自动选择账号的全部子市场。只要任一 child binding
  的 `business_model=CBT CN Fulfillment Managed`，整个 CBT seller 就必须在标准售价
  流程中显式阻断且不提供标准销售目标选项，不能误用 `price/currency_id` 流程。
- CBT 是内部 provider/发布目标，不是前端语言或销售市场。草稿与 CBT target 的
  `language` 表示当前文案语言；前端按该语言展示并勾选 child market，保存时把选择
  映射到同一个 CBT target 的 `sites_to_sell[]`。草稿箱市场选择器是唯一人工选择入口；
  文案编辑区不得再维护第二套市场勾选或按市场拆分的标题状态。
- `erp_web/services/mercadolibre_listing_model.py`：根据 `/users` 返回的 CBT 身份与
  `user_product_seller` tag 派生唯一 `listing_model`。有 tag 时为 `user_products`，
  无 tag 时为 `traditional_global_items`；两者是 Mercado 账号侧互斥合同。缺少 tag
  只会禁止 User Products wire contract，不得阻断传统 `/global/items`，也不得在某个
  endpoint 报错后切换模型。区域账号不映射到任何发布模型，本项目仍不提供区域
  `/items` 直发。
- CBT 草稿缺少或包含未授权目的地时，核价 Capability 返回受限
  `sales_target` 多选项；选项由当前账号 `marketplace_bindings` 机械生成，并按当前
  草稿文案语言对应的 child market 过滤，稳定值为
  `SITE_ID:logistic_type`（例如 `MLM:remote`）。仅任务卡经 `submit_input` 明确提交的
  选择会生效，初始计划中的模型值会被忽略；单独的 `MLM` 因无法区分物流方式而无效。
  选择通过账号目标契约后先规范化保存到 `target_sites[].sites_to_sell`，由 ProductStore
  清除旧核价/预检，再继续当前核价步骤；该字段不属于 `pricing_input`。已有前端选择时
  AI 直接复用；AI 补充的选择也写回同一字段，不能形成任务专属的第二份选择状态。
- `sites_to_sell[]` 同时属于核价指纹和发布审批快照：任一销售国家或物流方式
  及其市场级 `price/net_proceeds/listing_type_id/free_shipping/sale_terms/status` 变化都会清除旧核价、
  预检与发布就绪状态，撤销旧发布预览，但保留已发生的远端商品身份。人工审批摘要
  必须可读地列出每个 `site_id/logistic_type`，目的地、销售条件或
  店铺映射在批准后变化时，旧批准必须判定为 stale。
- 对 `listing_model=user_products`，`marketplace_bindings[].pricing_model` 是 Mercado
  计价模式事实源。同一个 Siteless User Product 只能使用一种模式：普通售价模式发送根级 `price` 与市场级 `price`；
  明确启用 net proceeds 的账号发送根级 `global_net_proceeds` 与市场级
  `net_proceeds`。两组字段互斥，选中市场的账号模式不一致、目标同时携带两种价格、
  或普通价格账号试图发送 `net_proceeds` 时必须在本地失败，不能靠远端报错或静默
  fallback。Fully Managed 虽然也使用 `global_net_proceeds`，但它是独立发布契约，仍由
  上述标准流程阻断。传统 Global Items 账号则按其独立合同发送根级和市场级
  `price`；不得把 User Products binding 的计价校验反向套到已经明确识别的传统模型。
- `erp_web/marketplaces/yandex_currency.py`：Yandex wire 编码边界（内部 RUB ↔ wire
  RUR），只作用于最终 payload 与发现归一化，不是币种来源。
- 商品 schema v2 仍会在输入归一化边界读取 v1 payload，但旧数字售价和无指纹核价只标记
  失效，不自动猜币种；这是商品 payload 兼容，不是 SQLite 旧版本运行时迁移。
- `erp_web/stores/store_currency_migration.py`：发布币种事实源切换的一次性内容迁移
  （幂等）：Ozon 旧合同币种迁移为 locked+ready 并删除 `contract_currency`；Yandex /
  Mercado Libre 的静态推断重置为 unresolved；静态 JSON 剥离派生币种字段并按授权状态
  迁移 `account_site_id`。

## 商品与草稿

- `erp_web/http_route_units/product_routes.py`：商品与草稿 HTTP 入口；路由只负责通过
  `validate_request_payload(..., endpoint=handler.path)` 校验请求并把编排交给
  `erp_web/facades/product_facade.py`。
- `POST /api/duplicate-draft` 的当前唯一语义是：以一个独立草稿为来源，创建具有新
  `draft_id` 的新刊登草稿。它复制来源草稿的可编辑内容及待复核项，但必须重置卖家
  SKU、UPC、预检结果、发布状态及全部远端刊登身份；不得复用来源草稿身份、把操作退化为
  复制 ID/文本，也不得保留旧复制路径作为 fallback。
- 复制草稿保留逐 SKU 已应用核价及其币种快照；报价不绑定草稿/卖家编码。
  预检仍按当前店铺币种、成本、包装、费用及销售目标检查有效性。缺失核价币种快照
  报 `PRICING_STALE`，仅非空快照与当前币种不符时报告币种变化。
- `erp_web/facades/product_facade.py::duplicate_draft_payload`：草稿复制请求的唯一 HTTP
  编排入口；`erp_web/stores/product_store.py::ProductStore` 仍是草稿规范化、复制、
  持久化和索引更新的唯一 owner。
- `runtime_units/draft_edit_capabilities.py` 暴露 `draft_duplicate` 和
  `draft_sku_selection_update` 两个可组合操作，共用 `ProductWriteCapabilityScope`。
  AI 逐次复制多份，再用完整 `selected_sku_ids` 集合设置每份的发布选择；空集合取消
  全选，不删除商品 SKU。`ProductStore.update_draft_sku_selection` 在商品锁内校验
  SKU 归属及启用状态，仅提交勾选变化，复用编辑器保存和预检失效规则。
- `facades/agent_draft_scope.py` 从同会话的可信 `draft_duplicate` 成功回执解析新草稿
  的来源，只允许当前所选草稿及其副本后代进入后续写入范围；不自动开放同商品的兄弟
  草稿，不信任模型提交的来源声明。后续用户消息仍按当前所选范围与操作权限检查。
- 工具参数、身份回执和勾选契约位于 `schemas/product_write_capabilities.py`。
  复制防重继续复用 Pydantic AI 原生 tool call ID 与现有 Tool Bridge 的持久回执：
  同一调用重放返回原回执，新调用可创建另一份副本；结果未知时沿用现有阻断机制。
  本次只增加业务工具和范围校验，不新增 Agent loop、恢复协议或审批状态机。
  已核对本地 `pydantic-ai-slim==2.43.0` 与官方 Function Tools / Toolsets 文档。

## 商品发布

- `erp_web/product_model/sku_model.py`：商品实际 SKU、来源快照、草稿选品与每行卖家编码的唯一契约。商品保存全部实际规格；草稿通过 `sku_id` 引用，卖家编码绑定 `draft_id + sku_id`。已发送的编码和远端关联不能由普通保存覆盖。
- `erp_web/runtime_units/sku_publish_projection.py`：逐 SKU 合并草稿覆盖值、目标属性和独立核价结果，并校验平台组合条件。临时单品投影不写回主档。
- `erp_web/runtime_units/sku_precheck.py`：平台无关的纯预检问题汇总。以错误码、类目差异字段和受影响 SKU 集合关联必填缺失与整组空值检查；其它组合约束独立阻断。以 `erp_web/schemas/publish_capabilities.py` 中的 `PublishIssueSku`、`PublishRelatedIssue` 保留规格身份与关联校验，并按类目定义定位填写入口。
- `erp_web/runtime_units/sku_publish_adapter.py`：注册表唯一发布入口，委托 `sku_precheck.py` 汇总 SKU 预检错误和提醒；编译带每项身份的冻结 SKU 清单；平台叶子适配器保留原生单品 I/O。每项写前落盘、写后保存响应，成功项按内容指纹跳过，未知结果禁止再次创建，异步确认仅推进原任务。
- 前端 `ProductSkuEditor.vue` 维护商品事实，`DraftSkuPanel.vue` 负责草稿选品和覆盖，`actions/pricing.ts` 按 SKU × 目标调用现有核价引擎。包装资料或费用改变后必须重新应用售价。
- `erp_web/product_model/sku_image_model.py`：SKU 图片资产引用与原图地址迁移的纯函数 owner。采集统一下载规格图并按来源去重；内部 `image_asset_id` 是唯一关联，草稿以同名覆盖字段单独选图。旧持久化 `image` 只在读取边界迁移，发布不再按 URL/路径匹配。
- 前端 `SkuImagePicker.vue` 从素材池选图；图片页“关联 SKU”复用商品/草稿保存入口。`replace_selected` 处理结果只替换当前草稿的相应 SKU 引用，换图不使核价失效。无调用方的 `set_sku` 素材标记 action 已删除。
- 商品 schema 当前为 4；保留本地商品及草稿的 SKU 图片地址迁移能力，不恢复旧的按下标选品格式。详见 `docs/sku-workflow.md`。
- `erp_web/http_route_units/publish_routes.py`：发布预检、payload 预览、非 Mercado
  平台同步发布、发布队列、`POST /api/mercadolibre/pause-user-product` 与
  `POST /api/publish-bus/reconcile` HTTP 入口。reconcile 只读取 job 已持久化的远端
  task 终态，绝不重放 publish mutation。
  Mercado Libre 明确拒绝 `/api/publish-product` 直发旁路，只允许预览、人工确认与
  PublishingBus 持久队列。暂停请求只接受
  `siteless_user_product_id`，不得把本地站点 item ID 当作全局商品身份。
- `erp_web/http_route_units/get_routes.py`：发布任务列表、指定 Job 详情与
  `GET /api/mercadolibre/user-products` 的只读查询入口。Mercado User Products 列表
  以本地草稿 `publication` 为主索引；仅显式 `refresh=true` 时，才按已经持久化的
  Siteless ID 调用 `/marketplace/user-products/{id}/mapping` 刷新 Item/Local UP
  身份映射。mapping 必须是官方顶层单元素数组，且 Siteless ID、CBT owner、父 Item/UP
  及站点映射全部与当前账号和本地 publication 闭包一致；空数组、多元素、身份漂移或
  非法 `UP...` 标识只记录 refresh error，不能覆盖已确认身份。mapping 不提供权威状态、售价或刊登类型，因此已有业务事实保持为本地
  snapshot，不能把 mapping 读取称为远端状态刷新。禁止用
  `/users/{id}/items/search` 或本地站点 item 搜索拼出全量 families。
- `erp_web/facades/publish_facade.py`：HTTP 层唯一发布 facade；业务编排进入
  `erp_web/runtime_units/publish_workflows.py`。
- `erp_web/runtime_units/publish_adapter.py`：发布平台适配器注册表。只有这里注册且
  在 `marketplace_registry.py` 声明 `CAP_PUBLISH` 的平台才允许进入真实发布流程。
- `erp_web/services/mercadolibre_target_contract.py` 与
  `erp_web/services/mercadolibre_market_precheck.py`：Mercado Libre 多市场预检的确定性
  契约与展示投影。前者按用户原始 `sites_to_sell[]` 顺序校验每个市场 operation，后者把
  结果分为父级与逐市场 `blocked/passed`；任一确定性错误必须保持顶层 `ok=false`。
  当前店铺授权 operation 为 `CBT CN International Drop Shipping + remote` 时，按官方
  Cainiao 规则检查长≤60cm、宽≤40cm、高≤35cm、三边和≤135cm；墨西哥、智利、
  哥伦比亚、巴西和阿根廷的包装重量≤15kg，乌拉圭≤20kg。市场级规则通过独立的
  scope 元数据投影，字段仍指向真实的 `package_dimensions`，投影后不得把内部 scope
  元数据返回给客户端。平台运行时的 `item.shipping.mode.not_supported`，或远端仅返回
  `can't send the product in this kind of shipment` message 时，只映射为中性的“物流方式
  不支持”，不得仅凭一次类目切换实验诊断为类目不兼容；当前不可运营市场也
  只根据真实远端响应映射，不固化为永久预检规则。新确定性规则必须有当前官方路线文档，
  并严格限定 business model、物流和市场，不能把旧承运商或单次发布结果扩大化。
- `erp_web/runtime_units/publish_mercadolibre.py`：Mercado Libre 专属发布、User
  Products 查询/暂停与错误处理。一个本地 Mercado 草稿只持久化一个
  `publication` 聚合；`publication.model` 明确区分 `user_products` 与
  `traditional_global_items`，前者以 `siteless_user_product_id` 为全局身份，后者以
  `parent_item_id` 为 CBT 全局身份，`publication.markets[]` 统一保存各销售市场的
  item/user-product 投影。
  暂停统一调用 `PUT /global/user-products/{siteless_user_product_id}`，成功后同步持久化
  远端明确确认的市场状态；HTTP 206/部分响应只暂停被确认的市场，未确认市场保留原状态
  并记录 `error/last_operation`。网络、5xx 或不可验证响应统一标记 `outcome_unknown`，
  不得假定全部市场已经暂停，也不得从 User Products 管理动作回退到传统 Item 路径。
- `erp_web/marketplaces/publishing.py`：只按已验证授权写入 payload 的
  `_listing_model` 显式分发，远端错误不会触发 fallback。User Products 首次创建向
  `POST /global/user-products/families` 发送单元素数组，并要求响应 cardinality、
  Siteless ID 与每个市场的 Item/Local UP 映射严格闭包。已有 User Product 新增市场时
  先调用 `POST /global/user-products/{id}` 并确认映射；只有当前 payload 与本地
  `confirmed_payload` 存在可证明的字段差异时才执行共享字段 `PUT`，纯新增市场不发送
  无关更新，缺少可信旧快照的复杂字段不猜测重提。若 PUT
  异步则只轮询 `/user-products-families/tasks/{task_id}`，任务根必须 `finished` 且每个
  User Product 都有明确 succeeded/failed 终态。poll 阶段不得再发新增市场 mutation。
  写响应身份漂移、确认响应畸形、超时或崩溃窗口统一进入 `outcome_unknown`，保留活动
  锁并禁止自动重放。存在 task ID 时，用户可从发布任务页触发只读对账；只有确认
  `applied/partially_applied/not_applied` 后才把 job 收敛到终态并释放同草稿/平台锁，
  初次 unknown 与最终对账结论分别保存审计日志。没有 task ID 的未知
  创建仍必须通过 Mercado 后台或支持渠道人工确认，不能猜测或强制解锁。传统模型首次
  创建使用完整 `POST /global/items` 并保留 `parent-item-info: true`；已有父项只用最小
  `sites_to_sell` 请求调用 `POST /global/items/{parent_item_id}` 添加尚无 `item_id` 的
  operation，已有 Item 绝不重复 POST。标准发布不执行全量 PUT；父根 payload 与已创建
  市场字段由 `confirmed_payload` 锁定，变更时必须创建新的 Global Item。响应只把通过
  operation 闭包校验的 `item_id/site_items` 作为真实成功身份；绝不恢复区域 `/items`。
- `erp_web/runtime_units/platform_query_capabilities.py` 与
  `publish_admin_capabilities.py`：AI 侧对应唯一能力名分别为
  `mercadolibre_user_products_query` 和 `mercadolibre_user_product_pause`。旧的远端 item
  列表、item close 与二次真实发布确认能力已经退役；真实发布统一走当前发布工作流。
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
  支持 cursor、状态、平台和商品筛选；平台摘要从不可变的已批准 payload 白名单投影
  `sites_to_sell[].site_id/logistic_type`，使 CBT 父刊登下的实际销售市场可见，但不返回
  售价、标题、属性或其他 payload 内容。`GET /api/publish-bus/status` 只返回指定 Job 的完整详情。
  `POST /api/publish-bus/reconcile` 仅适用于平台状态为 `outcome_unknown` 且保存了 task ID
  的 job；对账期间继续保留活动锁，对账成功后再次执行终态草稿补偿持久化。
  两个读取接口都不返回 worker 恢复专用的完整商品快照、approved payload、digest、店铺 identity 或
  幂等事实。
- `erp_web/runtime_units/publish_capabilities.py`：发布摘要包含当前已授权店铺的脱敏稳定
  `store_identity`；validation digest 同时绑定商品、草稿、平台、站点、店铺身份和最终 payload。
  `product_publish_validate` 是严格只读边界，不调用平台 `prepare_product`，因此普通上架预检
  不会上传图片或改写商品。受信的 `publish-payload-preview` 工作流才显式调用
  `prepare_and_evaluate_publish_validation`：先完成无副作用草稿预检，通过后准备平台素材
  （Mercado 本地图片上传并写回 picture ID），再以同一份类目定义编译最终 payload 与 digest。
  确认后提交会重新执行确定性校验并常量时间比较 digest，随后把已批准 payload/digest/identity 写入
  PublishingBus job。worker 现取凭据，但外发前复核店铺身份与完整 digest，并直接发送冻结 payload；
  不会重新构造已确认内容。Capability 还会在重校验与队列准入前按完整确认事实恢复既有 job，封闭
  “job 已落库、工具回执尚未保存 job_id”的崩溃窗口。店铺切换、payload 篡改或事实冲突都会在网络
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
- `erp_web/services/product_research_service.py`：调研编排与运行服务。手动 AI focused HTTP 调研在当前
  presentation scope 内同步完成，以便 Direct Model 事件持续输出到同一 SSE；未绑定 presentation 的普通调研与
  主 Agent 的原生 Deferred 工具调用独立的 `create_hot_product_run_async()`，不被前台 presentation 生命周期限制。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_capability_architecture.py`：单主 Agent 与全业务 Capability 化守卫——
  exposure 覆盖规则、审批能力只能进 Task allowlist、写能力幂等/恢复元数据与只读能力
  不得虚构幂等、主 Agent 的目录权限与原生工具 Schema 一致、业务 Catalog 只在组合根编译一次。
- `tests/test_ai_capability_coverage.py`：Endpoint Coverage Manifest 零未分类、零遗漏，
  业务域端点排除必须带原因。
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
- `tests/test_pydantic_native_contracts.py`、`tests/test_native_agent_integration.py`：安装版本的原生并发、Deferred/混合审批、恢复后继续调用工具。
- `tests/test_native_domain_workflow.py`：15 条所选草稿、真实准备服务与平台 mock 的审批/Job 纵向验收。
- `tests/test_native_reliability.py`：商品并发、旧快照拒绝、取消、CAS、未知副作用重启、模型异常与慢客户端恢复。
- `tests/test_domain_write_capabilities.py`、`tests/test_domain_collect_capabilities.py`、
  `tests/test_publish_admin_capabilities.py`：商品/草稿写能力、采集凭据规则与
  发布管理审批 digest 的行为测试。
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
- `tests/test_ai_chat_routes.py` 与前端原生对话测试：增量 SSE、身份边界、收件箱、工具审批、批量入口与历史重连。
- `tests/test_ai_context_architecture.py`：禁止第二 Agent loop、自研 deferred codec
  与前端任务推进的架构守卫。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。

### SKU 平台属性填写

主对话通过 `draft_attributes_read(scope=sku)` 按目标分页读取 SKU 事实，通过 `draft_sku_attributes_update` 明确提交当前 SKU 的差异值；后端不再分批调用其他模型。公共属性写入拒绝变体字段，SKU 写入拒绝公共字段，均不改来源商品事实。`DraftSkuAttributesEditor.vue` 继续复用平台枚举、集合和单位控件进行手工编辑；`DraftSkuPanel.vue` 只负责选品及详情位置。

`sku_custom_attributes.py` 继续负责 Mercado User Products 自定义规格的纯契约，发布编译与组合预检共用。来源规格和历史 `source_option_translations` 数据仍可读取，当前属性填写由主对话直接按事实判断。

SKU 新草稿默认选品由 `sku_model.new_draft_sku_rows` 定义：全部启用规格选中，停用规格不选中。`collect_helpers.py` 的两条新建草稿路径共同调用它，卖家编码待取得真实草稿 ID 后生成；`DraftSkuPanel.vue` 对新增事实采用相同默认值，并以表头父复选框表示全选、半选和全未选。已有显式取消选择不会因刷新重新选中。空白采集数据不生成 `single` 规格，真实来源删除的旧 SKU 仍保留身份并停用。

### 主对话工具权限与执行回执

- 主对话工具由 `ai_capability_composition.py` 的显式 Catalog/allowlist、Execution Profile 权限与 `AiToolRuntime` 的代码校验决定。用户文本、页面背景和历史消息 metadata 均不产生工具授权表；不再调用额外授权模型，也不持久化按回合推断的写权限。所选草稿及其合法副本范围、字段校验、发布/删除审批继续在既有执行边界生效。
- 主模型结合完整对话理解“是、继续”等指代，工具可用不等于应当调用。问“刚才写没写”时先依据真实回执回答；计划、工具名、发送调用或当前字段存在均不能证明写入成功。只有结果未知或需核实当前状态时才查询。
- 已核对安装的 Pydantic AI 2.43.0 与[原生工具准备](https://ai.pydantic.dev/tools-advanced/#agent-wide-dynamic-tools)、[Deferred Tools](https://ai.pydantic.dev/deferred-tools/) 文档。直接使用原生 `PrepareTools` 管理额度/收尾时间、`RunContext.enqueue` 注入用户消息，以及原生 Deferred 审批/恢复；无需另建授权 Agent、消息历史协议或状态机。
- `draft_sku_package_update` 是 SKU 包装资料写入口，声明在 `runtime_units/draft_edit_capabilities.py`，契约在 `schemas/draft_package.py`，写入由 `ProductStore.update_draft_sku_package` 持锁执行。请求明确 `draft_id`、`sku_ids` 和 `package_dimensions` 局部字段；仅更新指定已选启用 SKU 的 `overrides.package_dimensions`，保留其他字段、逐 SKU 重量和全部目标市场，返回实际应用字段、SKU 范围及变更数量。零值、非有限数、未知/停用/未选 SKU 和已发布对象在写入前拒绝。
- `draft_sku_attributes_update` 仅写平台类目属性，不能写包装字段。通用 `draft_save` 仍为 internal，不为包装编辑重新开放整对象保存。

### 属性事实边界

主对话不能将混合 SKU 的汇总描述套给每个规格，不能从图片比例猜测尺寸、重量、品牌或认证。当前读工具提供来源文字与结构化 SKU 事实；资料不足时在主对话询问用户，不启动额外的属性图片填写或复核 Agent。

`draft_read`、按草稿查询的 `product_read` 与 `draft_attributes_read` 均返回草稿共用的 `package_dimensions`（cm/kg）。`draft_attributes_read(scope=sku)` 的 `skus[].package_dimensions` 单独保留来源 SKU 与草稿覆盖后的有效包装尺寸；商品主档尺寸为空或 SKU 尺寸为零，不代表草稿共用尺寸不存在。读取不自动将共用尺寸套用到所有 SKU，实际发货资料仍按逐 SKU 事实校验。
