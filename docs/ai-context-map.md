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
- `erp_web/services/ai_tool_provider_adapters.py`：native/JSON tool-turn contract
  fake；当前不接真实业务 Provider。

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
Planner、Memory、Policy Engine 和真实类目 ToolSet 不属于 PR 1。

## 类目候选召回层

- `erp_web/schemas/category.py`：类目召回请求、规范化候选、语料身份、覆盖率和
  Provider preflight shape。
- `erp_web/runtime_units/category_retrieval.py`：`CategoryCandidateRetriever` 公共
  入口；负责受控 query variants、Ozon 完整树本地评分、Mercado Libre 多查询
  合并、稳定排序、去重和结构化 Provider 故障。该模块不做 AI rerank、业务阈值、
  人工确认或发布决策。
- `erp_web/runtime_units/category_providers.py`：Provider 注册、preflight 与平台
  适配。Mercado Libre 的 `discover()` 只返回轻量候选，召回层合并 ID 后才通过
  `detail()` 补全路径。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 官方完整类目树的 15 分钟缓存、
  可发布商品类型展平和可复盘 corpus identity。
- `tests/fixtures/category_retrieval_golden.json`：200 条、双平台、L0-L5 分层的首版
  离线 golden set。
- `tests/test_category_retrieval.py`：纯召回、故障分类和字段规范测试。
- `tests/test_category_retrieval_golden.py`：Recall@5/20、零召回率和分层基线。

旧 `erp_web/runtime_units/category_store.py::search_categories_live` 及其 HTTP
契约保持不变。新召回层只返回 `category_id/path_segments`，不传播旧边界中的
`id/path/category_path/raw` 重复或原始字段。

## Product Research

- `erp_web/http_route_units/product_research_routes.py`：调研 HTTP 入口。
- `erp_web/product_research_config.py`：调研配置兼容入口。
- `erp_web/services/product_research_service.py`：调研编排与运行服务。
- `erp_web/schemas/product_research.py`：调研数据形状。

## 架构守卫

- `tests/test_ai_context_architecture.py`：静态依赖与公共入口守卫。
- `tests/test_ai_tools.py`：工具 schema、ToolSet 和 Runtime。
- `tests/test_ai_task_runner.py`：Runner、provider parity 与单 conversation 边界。
- `tests/test_backend_api.py` 与 `tests/test_http_request_security.py`：HTTP contract
  与本机请求安全边界。
- `tests/architecture/`：长期模块边界、持久化与平台契约。
