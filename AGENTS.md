# 项目开发约定

## 说明的作用域

- 本文件记录长期开发边界；具体入口见 [AI 上下文地图](docs/ai-context-map.md)，前端约定见 [前端规范](front/前端规范.md)，按任务读取相关部分。
- 历史方案和验收记录描述当时的决策与证据，不构成新的任务授权；其中的清库许可、阶段顺序和临时限制不延续到后续任务。
- **Developer instructions are not product copy.** 开发约束、架构说明、任务解释和验收标准默认只指导实现；只有任务明确要求或真实 UX 需要时，才转为用户可见文案。
- `config/agents.md` 和 `config/prompts/` 是 ERP 产品运行时配置，不是 Codex 开发规范；修改它们会改变产品 Agent 行为。

## 产品阶段与兼容策略

- 本项目处于 Demo / 初始开发阶段。采用当前方案，替换废弃设计时同步清理旧实现、配置、说明及仅验证旧行为的测试。
- 兼容应有真实依据：持久化用户数据、生产部署、公开 API、第三方协议、外部调用方或用户明确要求。代码或测试已存在本身不构成兼容契约；真实契约保留读取或迁移能力，无法确认时保留并说明疑点。
- 不为保险、回滚或迁就旧测试增加 feature flag、双写、shadow 或 legacy fallback；回滚依靠版本控制。有独立产品价值的能力可以并存，但不作为新流程的隐式失败回退。

## 后端边界

- HTTP 路由位于 `erp_web/http_route_units/`，通过 `HANDLED_PATHS` 和显式 handler map 分派。路由不直接依赖业务 runtime unit；编排交给 facade 或 focused service。
- 请求体通过 `validate_request_payload(..., endpoint=handler.path)` 校验，路由键与 `erp_web/schemas/requests.py::REQUEST_CONTRACTS` 同步。共享请求、响应和业务形状由 `erp_web/schemas/` 定义。
- 使用显式依赖，不增加 Python 通配导入、运行时命名空间注入或兼容聚合入口；公开重导出声明 `__all__`。避免循环依赖，共享纯逻辑与网络、持久化等副作用分开。
- 商品和草稿持久化归 `ProductStore`，应用配置归 `app_config`；调用真实 owner，不重建已退役的 `erp_web/runtime.py` 或 Store 委托层。
- 商品聚合写入使用短锁与版本校验；锁不得跨模型调用、网络等待或人工审批。

## Pydantic AI 与 ERP 的职责

- Pydantic AI 独占 Agent run、工具循环、重试、Deferred/审批、消息历史及事件编码。HTTP、SSE、持久化层只做适配，不另建 Agent 执行、等待或恢复协议。
- ERP 负责业务状态、权限、幂等和领域 Job。长任务保留领域持久化，通过原生 Deferred Tools 与 Agent 衔接；后台 Job 扫描不执行模型。
- Agent 统一经 `AiAgentFactory` 装配和运行；API 模型统一经 `ai_model_factory` 创建，非 Agent API 请求经 `ai_direct_request_service`。CLI/Browser 是独立连接方式，不作为 API 推理旁路。
- 通用 Tool Runtime、声明、编译器和 Catalog 不依赖平台或领域模块。能力清单、场景 allowlist 和可信 Binding Scope 显式装配；装饰器不执行注册或业务逻辑，不依赖包扫描和 import 副作用。
- 新增或调整 AI 基础设施前，核对安装版本与官方原生能力。确需项目适配时，在设计或评审中说明原生能力的缺口、适配范围和移除条件；被替代的旧路径按上述兼容策略清理。
- 原生消息是唯一历史事实源；模型输入摘要不覆盖完整持久历史。适配层保存必要的 Deferred、收件箱和实际工具回执，不保存下一步工具计划。

## 弹窗与抽屉

- 优先复用 `front/src/components/shared/WorkspaceDialog.vue`；自定义遮罩复用 `useBackdropDismiss` 的完整按下、松开和取消处理。
- 仅同一主指针左键在遮罩空白处按下并松开时关闭。从内容区拖出、文本选择、取消手势不得关闭，提交中的禁止关闭约束继续有效。不要用 `click.self` 或单个指针事件 `.self` 代替手势判定。
- 相关交互修改覆盖“内部按下、外部松开不关闭”和“正常遮罩点击关闭”两种回归场景。

## 验证与文档

- 后端公共入口或职责边界变化时更新 `docs/ai-context-map.md`；地图记录导航和不明显的约束，函数签名、字段全集和依赖版本以代码、Schema 及配置为准。
- 架构规则由 `tests/test_ai_context_architecture.py` 和 `tests/architecture/` 等守卫保护；新增或调整边界时更新对应守卫。
- 后端重构完成后运行 `.venv/bin/python -m pytest tests -q`；涉及导入或模块边界时至少完成编译检查与架构测试。其他改动按影响范围选择验证，文档修改不要求运行无关功能测试。

## 临时产物

调试截图、响应转储和一次性分析写入仓库外的独立临时目录，用完清理。正式交付物、测试夹具和回归基准按项目约定保存；业务数据、数据库备份、配置与凭据不能因未被 Git 跟踪就当作垃圾删除。

<!-- CODEGRAPH_START -->
## CodeGraph

本项目已配置 `codegraph_*` 工具。结构、调用关系和影响范围查询优先使用 CodeGraph；字面文本使用 `rg`。索引提示过期或与工作区不一致时读取相关源码；工具不可用时可使用原生搜索。未初始化时先确认是否运行 `codegraph init -i`。
<!-- CODEGRAPH_END -->
