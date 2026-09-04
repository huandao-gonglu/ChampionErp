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

## SQLite 数据库版本边界

- `erp_web/db.py` 是 SQLite schema 与版本门禁的唯一 owner，当前
  `SCHEMA_VERSION=14`。数据库文件不存在或 `user_version=0` 且没有任何用户 schema
  object 时，才会在单事务内创建当前结构。
- 现有数据库只在版本为 14 且全部 table、column、constraint、index、view、trigger 与当前
  建库 SQL 的完整结构签名一致时打开。非空 v0、v1–v13、未来版本和结构残缺/额外的 v14
  都在写入前失败；运行时不升级、修复、删除或重建数据库。
- 旧库切换是显式运维流程：先导出需保留的配置与授权，再停止应用、删除主库及
  `-wal`/`-shm`，创建全新 v14 后导回配置。`upc_pool.json` 是已购买 UPC 的显式资产导入，
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
- `erp_web/services/ai_agent_factory.py`：Pydantic Agent 的唯一装配与同步/流式运行入口；
  创建请求级 dependencies、usage limits 和 instrumentation，不包含领域终检。需审批
  写工具只能交给 `GlobalTaskController`，Factory 不维护第二套 deferred 状态机。
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
  活动 conversation 把 `conversation_id` 传给 `AiChatPanel`，由 conversation 级 `task-link`
  纯读接口驱动挂载唯一 `GlobalTaskApprovalCard`；存在未解决任务时普通发送被锁定，
  审批/补资料/取消等明确命令不受影响。`front/src/stores/aiChat.ts` 订阅后台官方事件 SSE
  （按单调递增 `history_version` 应用，`resync_required` 时重读 `/ui-messages` 再重连），
  后台 continuation 提交的最终回复经该通道只读进入对话。

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

- 缺失能力：锁定的 `pydantic-ai-slim[openai]==2.22.0` 支持 Responses 原生图片工具，但没有
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
- `erp_web/services/ai_tool_bridge.py`：把显式 ERP ToolSet 转换为 Pydantic
  `FunctionToolset`；Pydantic tool 只调用 `AiToolRuntime.execute(...)`，不直接调用
  领域 executor，并强制串行执行以保护 Runtime 的预算和去重状态。
- `erp_web/services/ai_agent_factory.py`：由 Pydantic Agent 独占 model → tool → model
  循环、类型化 output、重试和 usage limit；`run_sync(...)` 与 `open_stream_run(...)`
  是统一同步/流式入口，成功时用官方 `result.all_messages()` 原子替换 conversation
  历史。需审批与长任务恢复只由 `GlobalTaskController` 承担，Agent Factory 不保存第二套
  deferred state。
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

## 单主 Agent（global.chat）与全局任务 Capability 化

全局只有 `global.chat` 一个主 Agent / 对话入口 / 全局模型绑定。不存在独立 Planner，
也不存在第二次计划模型调用：主 Agent 在对话回合内直接选择类型化任务步骤，经
`global_task_start` 提交。`global_task_start` 受理成功后 Bridge 抛出 Pydantic 官方
`CallDeferred`，当前 run 以官方 Deferred 语义挂起；任务执行只由后台 recovery
worker 完成，任务终结后 continuation run 用官方 `DeferredToolResults` 恢复同一
conversation 并生成最终 Assistant 回复。任务与发布终态由 Controller 和
PublishingBus 持久化，不从对话事件推断。

```text
global.chat（唯一主 Agent）
  → erp_web/facades/global_task_facade.py::build_global_chat_toolset
      ├─ Direct 只读 Capability（GLOBAL_CHAT_DIRECT_CAPABILITIES）
      └─ 任务控制 ToolSet（global_ai_control_tools.py）
          → global_task_start（类型化 step union，agent_deferred）
              → erp_web/services/global_task_controller.py::accept_deferred_task
                  ├─ APPLICATION_CAPABILITY_CATALOG（唯一业务 Catalog）
                  ├─ erp_web/stores/global_task_store.py
                  ├─ erp_web/stores/pydantic_deferred_task_link_store.py
                  │   （conversation → task 关联 + history ready 屏障）
                  └─ PublishingBus（发布终态）

后台推进（不依赖原始请求连接）：
  server.py::start_global_task_recovery_worker
    → GlobalTaskController.recover_unfinished_tasks（worker 执行步骤）
    → GlobalTaskContinuationService.recover_pending
        （任务终结 → DeferredToolResults 续跑 → 最终回复原子提交）

受信任务 UI（只读 + 明确用户命令）：
  GET /api/v1/global-tasks/<task_id>（纯读任务状态 + 计算型执行进度视图）
  GET /api/v1/ai-work/conversations/<id>/task-link（conversation → 未解决任务）
  GET /api/v1/ai-work/conversations/<id>/events（官方事件订阅 SSE）
  POST /api/global-task-{input,approve,reject,cancel}
    → erp_web/http_route_units/global_agent_routes.py
        → erp_web/facades/global_task_facade.py（受信任务 UI HTTP 门面）
```

`app_config.task_approval_mode` 是唯一审批等级设置：`ask`（询问审批，默认）和
`full`（完全授权）。`ask` 模式下，高风险步骤进入 `pending_approval`，只能由携带
进程级 `X-Approval-Token` 的受信 UI 调用正式 approve/reject 入口；`full` 模式下，
Controller 在副作用前为当前步骤生成冻结快照、digest 和审批审计记录，再通过同一个
`AiToolRuntime` 执行。完全授权不扩大 ToolSet、Binding Scope 或权限集合，也不绕过
输入校验、Capability version、operation key 与执行侧 digest 重核。

审批等级通过现有 `/api/save-settings` 保存；修改该字段同样必须携带
`X-Approval-Token`。主 Agent ToolSet 不包含设置、approve 或 reject 工具，因此模型
无法读取、修改审批等级或自行批准任务。不存在测试专用授权端点、命令或旁路。

### 唯一 Capability 组合根

- `erp_web/ai_capability_composition.py`：唯一业务 Capability 组合根。全部领域能力
  tuple 在此显式汇总，由 `AiToolCatalog.compile` 编译为唯一
  `APPLICATION_CAPABILITY_CATALOG`；不扫描包、不动态发现、不存在第二个 Schema
  compiler 或 Task Spec 层。`GLOBAL_CHAT_DIRECT_CAPABILITIES`（只读、主 Agent 可
  直接调用）、`GLOBAL_TASK_CAPABILITIES`（可作为任务步骤）、
  `INTERNAL_ONLY_CAPABILITIES`（当前为空，预留 focused Agent 内部用途）是三个互斥
  exposure 集合；`validate_capability_exposure()` 校验每个 Catalog Capability 至少
  进入一个 exposure 集合、Internal 与 Direct/Task 互斥、Direct allowlist 不含写能力。
- `erp_web/facades/global_task_facade.py`：唯一应用装配入口。
  `build_capability_binding_scope` 按 Scope 类型构造可信 Binding Scope 并注入领域
  依赖（采集 cookie/密钥只从已保存配置解析，模型输入永远不提供凭据）；
  `build_global_task_controller` 装配 Controller 与 Task ToolSet；
  `build_global_chat_toolset` 把 Direct 只读能力与四个任务控制工具合并为
  `global.chat` ToolSet。四个 `/api/global-task-*` POST 门面只是受信任务 UI 的
  补资料、审批确认/拒绝与取消入口；任务状态读取是纯 GET
  （`/api/v1/global-tasks/<task_id>` 与 conversation 级 `task-link`），不存在任何
  可推进任务的写刷新。审批确认/拒绝必须携带
  服务端下发给受信 UI 的 token，主 Agent 不具备对应工具。任务创建只经由
  `global_task_start` 工具，不存在 `/api/global-task-start` 或发布确认专用入口。
- `erp_web/runtime_units/global_ai_control_tools.py`：四个任务控制工具
  （`global_task_start/get/submit_input/cancel`）。
  `global_task_start` 的 steps 由 `project_task_step_union` 从每个 Task Capability 的
  Pydantic Request 机械投影为 discriminated union：每步携带该 Capability 的真实
  参数 Schema；不存在逐 Capability 手写 step model、任意字典参数或 Controller 内的
  Capability 名称分支。
- `erp_web/services/global_task_controller.py`：任务状态机、严格顺序推进、暂停/补
  资料与审批门的 owner；`accept_deferred_task` 只受理任务与创建 deferred link，
  不执行任何步骤。任务推进只由后台 recovery worker 完成，HTTP 门面与前端读取
  都不能推进任务；执行步骤只依赖 Catalog 的类型化
  request/executor。`submit_input` 会随步骤保存用户实际提交过的顶层字段名，执行时
  只通过可信 `business_scope` 传递该来源标记；模型在初始计划中主动生成同名值不能
  冒充用户选择。Controller 与 `erp_web/schemas/global_tasks.py` 不含 Capability
  名称分支、Planner 或旧 `global.task.plan` 引用（架构测试守卫）。
- `erp_web/stores/global_task_store.py`：`LocalGlobalTaskState` 的唯一 Store；草稿
  快照方法委托给 `erp_web/stores/draft_query_snapshot_store.py`。后者是
  `DraftQuerySnapshot` 的唯一持久化 owner，不依赖任务状态或任务 schema。
- `erp_web/stores/pydantic_deferred_task_link_store.py`：conversation → 未解决
  Deferred 任务的唯一关联表（当前 schema v14）。`awaiting_history` provisional link 只
  供服务端恢复/清理；`ready` link 是前端任务卡与发送锁定的唯一依据；任务终结并
  continuation 提交后 link 变 `resolved`。
- `erp_web/stores/pydantic_ai_event_outbox_store.py`：官方编码事件 outbox。事件
  批次只在 history/link/outbox 原子提交成功后发布；订阅端按单调递增
  `history_version` 重放，游标超出保留窗口时明确 `resync_required`。
- `erp_web/services/global_task_continuation_service.py`：任务终结后用官方
  `DeferredToolResults` 续跑同一 conversation，最终 Assistant 回复与
  history/link/outbox 原子提交；不合成项目自有 Assistant message shape。
- `erp_web/services/global_task_progress_service.py`：GlobalTask 执行进度投影
  服务。把当前任务与领域 Job 已持久化状态即时投影为计算型只读
  `GlobalTaskExecutionProgress`：不写回任务状态、不推进任务、不触发 CAS/revision
  递增，进度读取失败只降级为通用运行信息。领域专用状态由按 `job_type` 注册的
  `JobStatusReader` 类型化为 `JobStateSnapshot`（生命周期字段 status/error 供
  Controller 消费，展示字段供投影服务消费），Reader 缺失/异常或 Job 缺失均安全降级。
- `erp_web/services/vercel_ai_ui_service.py`：`/api/v1/ai-chat/runs` 的服务端
  drain；接受普通回合前原子拒绝存在未解决 link 的 conversation
  （`AI_CHAT_CONVERSATION_TASK_PENDING`），客户端断线不取消已接受的 run。
- `erp_web/schemas/global_tasks.py`：任务、步骤、`pending_input_owner`、带
  `input_type/input_owner` 的类型化 `RequiredInput`、审批与拒绝 shape。补充值由
  Controller 按 owner 合并到 step、属性或核价输入，不靠 facade 字段白名单。另含
  只读执行进度契约：`JobStateSnapshot`（Reader 类型化快照）、
  `GlobalTaskExecutionProgress` / `GlobalTaskViewResponse`（HTTP/UI 进度读模型）；
  进度字段均为白名单且限长，不透传凭据、完整 payload 或原始平台对象。

声明 `approval_required` 的 Capability 只能进入 `GLOBAL_TASK_CAPABILITIES`，主
Agent 不得直接触发破坏性写入；审批 payload 携带确定性 digest，Capability 执行时
重算并比较，目标或事实被篡改时以 `*_APPROVAL_STALE` 稳定码安全失败。

`DraftQuerySnapshot.total` 和聚合统计覆盖完整匹配集合；为限制模型上下文和本地状态大小，
`draft_ids/items` 只保存按 `limit` 截取的当前有序页。`draft_position` 是该页内的一基序号，Controller
再把它解析为稳定 `draft_id` 并读取当前 ProductStore 事实；模型输出的数字或 ID 不直接进入写操作。
`view=summary/workflow/publish_readiness/detail` 采用同一稳定 `DraftSummary` schema 的分级字段投影，
快照重放保持创建时 view，不把 view 当作无效果展示提示。

发布上下文遇到多个候选目标且请求未明确平台/站点时返回 `DRAFT_TARGET_AMBIGUOUS`；
平台下仍有多个站点时返回 `DRAFT_TARGET_SITE_AMBIGUOUS`，不会静默选择首项或默认站点。

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
  `GET /api/v1/ai-work/conversations/{id}/ui-messages` 用官方 Adapter 派生只读
  `UIMessage[]`（含 `history_version` 订阅游标）；
  `GET /api/v1/ai-work/conversations/{id}/task-link` 返回 conversation → 未解决
  Deferred 任务的纯读关联（只含 `ready` link）；
  `GET /api/v1/ai-work/conversations/{id}/events` 官方编码事件订阅 SSE：先从
  outbox 重放 `after_history_version` 之后的保留批次再转 live，游标超出保留窗口
  时返回 `resync_required`；
  `GET /api/v1/global-tasks/{task_id}` 纯读任务状态，并附带计算型只读
  `execution_progress`（当前步骤、活跃 Job 阶段/重试/下次检查、内部活动与耗时）；
  GET 不推进任务、不递增 revision，连续读取不产生任何写入。
- `erp_web/stores/pydantic_message_store.py`：`ModelMessage` 历史唯一持久化边界；
  每次提交递增 `history_version`，不合成 orphan tool return。
- 已退役的 `/raw`、`/children`、wait/after_seq 参数继续返回 404。

### 全局对话（global.chat）与实时消息流

`global.chat` 是唯一主 Agent 对话入口：输出自然语言文本，业务入口
`erp_web/services/global_agent_chat_service.py`，只选择服务端 prompt、`global.chat`
ToolSet、Execution Profile 与权限，再调用 `AiAgentFactory.open_stream_run(...)`。
Execution Profile 的 output type 是 `str | DeferredToolRequests`：
`global_task_start` 受理成功时 Bridge 抛出官方 `CallDeferred`，run 以 Deferred 语义
挂起；任务终结后 `open_continuation_run(...)` 用官方 `DeferredToolResults` 恢复同一
conversation 并生成最终回复。
ToolSet 由 `erp_web/facades/global_task_facade.py::build_global_chat_toolset` 装配：
Direct 只读能力加四个任务控制工具；写与审批能力只经类型化任务步骤执行，主 Agent
没有直接写工具，也不存在并行的第二条规划链。

- `erp_web/http_route_units/ai_chat_routes.py`：`POST /api/v1/ai-chat/runs` 薄路由，预流校验返回
  标准 JSON，开始输出后只发送官方 Vercel SSE chunk。
- `erp_web/services/vercel_ai_ui_service.py`：唯一 Vercel 协议入口，负责解析请求、领取锁与 claim、
  运行/编码 SSE 与历史 `dump_messages()`；是新增 Pydantic UI import 的唯一 owner。
- `erp_web/facades/ai_chat_facade.py`：composition root，从 `AppContext` 装配 focused services。
- `erp_web/services/ai_chat_run_registry.py`：进程内按 conversation ID 的活动 run 互斥屏障，由
  `AppContext` 单例持有。
- `erp_web/stores/ai_chat_turn_claim_store.py` + `ai_chat_turn_claims` 表：`client_message_id`
  幂等领取与 profile/owner 归属；只存运行控制元数据及安全的 error code、trace ID、
  最后工具名，不存消息正文、工具参数或工具结果。
- `erp_web/stores/draft_query_snapshot_store.py`：草稿查询快照的独立持久化 owner，不依赖 Global Task。
- `erp_web/facades/global_task_facade.py`：装配 `global.chat` ToolSet 的 Direct 只读
  能力绑定；`drafts_query` 与任务步骤共用同一 Capability，快照经独立 snapshot store
  持久化，不依赖任务状态。

实时展示链是 `AgentStreamEvent → VercelAIAdapter/VercelAIEventStream → SSE → @ai-sdk/vue Chat`；
历史展示链是 `PydanticMessageStore → ModelMessage[] → VercelAIAdapter.dump_messages() → UIMessage[]`。
消息事实唯一来源是 `pydantic_message_histories.messages_json`，不存在第二张 UI 消息表或消息双写。

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
- `erp_web/schemas/category_brand.py` 与
  `erp_web/runtime_units/category_brand_values.py`：平台作用域内的品牌属性识别、无品牌事实
  等价关系及官方 strict-enum 候选解析；Ozon 属性 85 不得扩散为其他平台的品牌身份。
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
  schema options，没有匹配选项时允许填写有商品依据的自定义文本且不得提交枚举 ID。非品牌
  `strict_enum` 的候选真实性以 request-scoped Ledger 为边界，候选适用性仍需商品事实；类目
  “类型”枚举可由已确认的类目 ID/路径提供跨语言证据。品牌只接受明确无品牌语义或与唯一、
  一致的商品品牌字段精确匹配的候选，禁止相似品牌替换。自由文本仍需商品事实证据，包装重量
  等结构化事实只允许通过确定性单位换算放行。
- `config/prompts/category_attribute_fill.json`：`category.attribute_fill` Agent prompt；
  明确区分发布必填、平台强制枚举、建议枚举和普通自定义属性，并要求技术参数、链接、
  编码、证件与文件不得编造。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`（ID 原样按字符串存取，不做数值化），搜索输入不进入草稿；
  实时候选按 `next_cursor/has_more` 追加并按 ID 去重，大品牌字典通过“加载更多”继续读取；
  带 `unit_options` 的属性通过数值输入 + 单位下拉生成 `{value, unit}`；
  发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories(keyword)`。工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/facades/category_match_facade.py`：`category_match` 共享业务阶段；
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
- `erp_web/facades/product_facade.py::duplicate_draft_payload`：草稿复制请求的唯一 HTTP
  编排入口；`erp_web/stores/product_store.py::ProductStore` 仍是草稿规范化、复制、
  持久化和索引更新的唯一 owner。

## 商品发布

- `erp_web/product_model/platform_sku.py`：卖家 SKU 的唯一生成规则。SKU 稳定绑定
  `draft_id`：同一草稿编辑重发保持不变，同一商品重新推出的新草稿获得新 SKU；
  未发布草稿中的“其他”等占位值会被替换，已绑定远端刊登的历史 SKU 不得静默变化。
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
- `erp_web/services/product_research_service.py`：调研编排与运行服务。手动 AI focused HTTP 调研在当前
  presentation scope 内同步完成，以便 Direct Model 事件持续输出到同一 SSE；未绑定 presentation 的普通调研与
  Global Task 仍调用独立的 `create_hot_product_run_async()`，不被前台 presentation 生命周期限制。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_capability_architecture.py`：单主 Agent 与全业务 Capability 化守卫——
  exposure 覆盖规则、审批能力只能进 Task allowlist、写能力幂等/恢复元数据与只读能力
  不得声明幂等、`global_task_start` step union 与 Task allowlist 同源机械投影、
  Controller/Task schema 无 Capability 名称分支与 Planner 残留、业务 Catalog 只在
  组合根编译一次。
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
- `tests/test_global_task_controller.py`、`tests/test_global_task_store.py`、
  `tests/test_global_agent_vertical_integration.py`：capability-name-agnostic Controller
  状态机、类型化步骤执行、审批门、暂停恢复与本地持久化。
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
- `tests/test_global_agent_routes.py`、前端 `AiChatPanel` / `GlobalTaskApprovalCard`
  测试：四个受信任务 POST 门面与纯读 GET、conversation 级任务卡挂载、发送锁定、
  RequiredInput 与审批确认/拒绝交互、后台事件订阅重连。
- `tests/test_ai_context_architecture.py`：禁止第二 Agent loop、自研 deferred codec
  与前端任务推进的架构守卫。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。
