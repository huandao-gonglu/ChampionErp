# AI Context Map

本文件列出后端主要公共入口、依赖方向和测试边界。它面向后续维护者与 AI，
不替代模块内契约。

## 运行时边界

- `erp_web/runtime.py` 与原 `runtime_units` 兼容转发模块已经删除，不得重建聚合入口。
- 直接 import 具体 facade、service、store 或 schema owner。
- 新 HTTP 行为从 `erp_web/http_route_units/` 的显式 handler map 进入；路由把编排交给
  `erp_web/facades/` 或职责单一的 service。

## AI Provider 与 AI Work

- `erp_web/services/ai_provider_contracts.py`：Provider 能力协议，包括并列的
  `AiChatProvider` 与 `AiToolTurnProvider`。
- `erp_web/services/ai_gateway.py`：现有 AI gateway 稳定门面。
- `erp_web/services/ai_gateway_providers.py`：Provider 注册表、业务客户端和
  `AiProviderClient`，不承载具体协议实现。
- `erp_web/services/ai_gateway_http_providers.py`：HTTP Provider 实现。
- `erp_web/services/ai_gateway_cli_provider.py`：CLI Provider 实现。
- `erp_web/services/ai_gateway_browser_provider.py`：浏览器 Provider 实现。
- `erp_web/services/ai_gateway_provider_types.py`：Provider 共享请求 shape。
- `erp_web/services/ai_image_provider.py`：图片 Provider。
- `erp_web/services/ai_work_service.py`：AI Work conversation journal。
- `erp_web/http_route_units/ai_work_routes.py`：AI Work 读取与等待 HTTP 入口。
- `front/src/views/AiWorkView.vue`：AI Work 监视界面。

一次新执行链必须先通过 `AiProviderClient.start_invocation()` 创建唯一
`AiInvocation` 与 recorder；Provider adapter 只消费 invocation 中的 context 和
recorder，不创建 conversation。

## AI Tool Task 执行层

- `erp_web/schemas/ai_tools.py`：`AiToolDefinition`、`AiToolCall`、
  `AiToolResult`、`AiToolTurn` 和边界校验。
- `erp_web/schemas/ai_trace.py`：执行 ID、deadline、权限和预算上下文。
- `erp_web/services/ai_invocation.py`：解析后的 model/provider、execution context
  与 recorder 单次创建边界。
- `erp_web/services/ai_tool_registry.py`：不可变 ToolSet 与显式
  definition/executor 映射；PR 1 不注册业务工具。同步 executor 必须通过
  `deadline_aware_tool_executor` 显式声明 cooperative deadline 契约，并把
  `AiExecutionContext.bounded_timeout_seconds()` 用于每个阻塞 I/O；Runtime 不用
  无法安全中止的后台线程伪装 hard cancellation。
- `erp_web/services/ai_tool_runtime.py`：工具查找、校验、权限、去重、预算、执行和
  trace。
- `erp_web/services/ai_task_runner.py`：共享的 model → tool → model 循环与停止条件。
- `erp_web/services/ai_tool_provider_adapters.py`：统一 tool-turn adapter；生产
  `JsonToolTurnProviderAdapter` 复用现有 `AiChatProvider.chat_json` 与同一个
  invocation recorder，native/JSON fake 继续用于协议 contract test。

依赖方向：

```text
facade / future execution profile
  → AiProviderClient.start_invocation
  → AiTaskRunner
      → AiToolTurnProvider
      → AiToolRuntime
          → explicit AiToolSet
```

`AiToolRuntime` 不得 import 类目、平台、发布或其他具体领域模块。Main Agent、
Planner、Memory 和 Policy Engine 仍不属于当前切片；真实领域 ToolSet 在所属
runtime unit 中显式构造，不注册到动态全局表。

## 类目平台搜索层

- `erp_web/marketplaces/category_provider.py`：定义绑定式 `CategorySearcher`；唯一
  方法是 `search_categories(keyword)`，不接收 platform/site。
- `erp_web/runtime_units/category_searchers.py`：任务入口根据当前平台实例化具体
  搜索器。Mercado Libre 调用 `domain_discovery/search`；Ozon 搜索服务端缓存类目
  树。平台选择只发生在对象创建处，后续通过同一接口多态调用。
- `erp_web/runtime_units/category_providers.py`：平台 API 与类目详情适配。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 类目树与可搜索展平记录分别做
  15 分钟缓存；多轮关键词调用不重复展平或计算 corpus hash，完整树不会进入 AI
  上下文。
- `erp_web/schemas/category.py`：规范化候选、搜索结果与匹配结果 shape。
- `tests/test_category_searchers.py`、`tests/test_ozon_category_api.py`：平台对象选择、
  远端/缓存搜索、错误分类和 Ozon ID 配对测试。

`erp_web/runtime_units/category_store.py::search_categories_live` 与自动匹配内部工具
共用 `CategorySearcher`，但 `POST /api/category-search` 仍只服务人工关键词搜索。

## 类目匹配 Capability

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet；只暴露
  `search_categories(keyword)`。ToolSet 绑定一个已经实例化的 `CategorySearcher`，
  工具 schema 与执行器均没有 platform/site 分支。
- `erp_web/facades/category_match_facade.py`：`category.match` 公开编排入口；
  首轮只发送原语言/目标语言标题与描述及少量有效商品事实；模型必须调用搜索，
  可换词重试，最多 3 轮/3 次。最终选择必须经过候选账本、站点、可发布状态、
  详情、Ozon ID 配对和属性读取校验。
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
  多轮换词、强制搜索、未知 ID、deadline、凭据和工具去重测试。

endpoint 内部只使用 `category_id/path_segments`。前端只在 API 边界转换成人工
选择组件需要的 `id/path`，且不会自动写入模型首选；用户仍需点击候选确认。HTTP
结果只返回最后搜索词与去重后的轻量候选；多轮搜索历史留在 AI Work trace，避免
在业务 JSON 中重复候选。

## Product Research

- `erp_web/http_route_units/product_research_routes.py`：调研 HTTP 入口。
- `erp_web/product_research_config.py`：调研配置入口。
- `erp_web/services/product_research_service.py`：调研编排与运行服务。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_tools.py`：工具 schema、ToolSet 和 Runtime。
- `tests/test_ai_task_runner.py`：Runner、provider parity 与单 conversation 边界。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。
