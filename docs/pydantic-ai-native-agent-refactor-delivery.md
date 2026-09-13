# Pydantic AI 原生 Agent 重构交付记录

实施日期：2026-09-09。对应 [重构计划](pydantic-ai-native-agent-refactor-plan.md) 和 [当前架构地图](ai-context-map.md)。

2026-09-09 完成代码替换和可控验收；当时配置缺少 API Key，未验收真实模型质量。2026-09-11 已使用已配置模型开展真实对话，原始审查记录与运行证据保留在本地，不随源码提交。可控测试不代表真实模型任务已经通过。截至该次验收记录，真实验收仍未完成：翻译服务返回 HTTP 402（余额不足），且有待重填字段及待补真实资料。

## 原生能力及保留适配的理由

已核对安装版本：`pydantic-ai-slim 2.22.0`、`pydantic 2.13.4`、`pydantic-graph 2.22.0`。依赖版本未改变。依据官方 [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)、[动态工具与工具校验](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/) 和 [消息历史](https://pydantic.dev/docs/ai/core-concepts/message-history/) 文档，测试直接运行当前安装版本的 `Agent`、`FunctionModel` 和原生消息类型。

- `AiAgentFactory` 继续是唯一 Agent 装配/运行入口。主 Agent 直接取得 focused 领域读写工具；`Tool.from_schema` 的同步工具由 Pydantic 并发调度。原生 `UsageLimits` 是唯一工具额度执行边界，没有额外的项目预算重试槽。
- 工具参数校验使用原生 `args_validator` / `ModelRetry`；业务缺字段和可修复错误作为工具结果返回主 Agent。没有固定步骤执行器。
- 审批使用 `ApprovalRequired`、官方 UI approval parts 和 `DeferredToolResults`。服务端校验调用 ID、工具名、冻结参数、审批身份及业务快照 digest。部分批准/拒绝先持久化，当前原生批次全部结果齐备后恢复；恢复后允许继续调用工具。
- 持久领域工具使用 `CallDeferred`。选择该原生方式是因为 ERP 工具在产生外部请求前需要参数校验和条件审批快照；`ExternalToolset` 可以表示纯外部调用，但不能替代这些已经存在的业务校验。未实现项目自己的等待/恢复协议。
- `AgentRunStorage` 只把原生 hook 连接到输入收件箱、消息 CAS 和写入检查点。收件箱存储官方消息对象；已应用消息进入 canonical history。原生 `new_messages()` 追加到完整历史，模型输入裁剪不覆盖历史。
- `AgentCallStore` 用同一 SQLite 事务提交原生历史、Deferred 请求和待投递记录。领域写回执记录已开始、已完成、等待 Job 或结果未知，防止丢失回执后盲目重发。它不保存下一步工具、步骤计划或 Agent 状态。
- `AgentJobService` 只领取领域工具和查询 Job 真实终态。扫描线程不调用模型，独立 Job 使用线程池；结果齐备后调用同一个原生 Agent 入口。
- HTTP 断线不取消后台运行。实时消息使用官方 `VercelAIEventStream` 编码；后台订阅只通知历史版本变化，前端重读官方派生历史。慢客户端队列溢出会收到原生 error/finish，不能把不完整 delta 当成完整回复。
- `ProductStore` 在局部读改写期间对商品聚合互斥，旧草稿快照冲突返回 409。时间版本精度提高到微秒；文案生成结束后合入最新草稿。锁不跨模型调用或人工等待。

为什么保留这些项目代码：Pydantic 不负责 ERP 草稿范围、平台发布凭据/业务幂等、SQLite 事务、收件箱 HTTP 202 回执和业务快照授权。这些适配只处理应用数据和安全边界；Agent 的循环、工具调用、暂停、批准、恢复、消息和事件编码均使用原生能力。

## 用户入口和删除项

全局对话与草稿箱“AI 准备所选”共用 `POST /api/v1/ai-chat/runs`。批量入口传递稳定 `target_draft_ids`；服务端检查写入目标。运行中仍可提交补充、纠正和取消，界面显示接收状态，在下一个原生边界应用。取消不会声称撤销已经发出的平台操作。

已删除固定 Controller、步骤 union、四个任务控制工具、独立任务状态 Store、Deferred task-link、持久化事件 outbox、旧 continuation/progress service、全局任务 HTTP 路由、旧任务卡以及 `task_approval_mode`。对应旧 prompt、只验证旧流程的测试和 mock 已移除或改写。旧设计正文由版本控制保存，当前文档只指向有效方案。

仍保留：发布/研究等独立领域 Job、自身幂等/对账状态、focused 类目和属性 Agent、独立 AI Presentation。这些能力不充当旧任务执行器的 fallback。

## 数据处置

实施前工作库有 6 个商品、15 条草稿、194 份原生历史、22 个旧任务和 22 个旧关联记录。用户明确授权“数据都可以删除，不需要兼容”。

已停止原有本地服务，删除工作目录的 schema 14 `erp.sqlite3` 和空的 `data/erp.sqlite3`，创建 schema 15 工作库。没有保留旧格式读取、迁移、灰度或双轨执行器；没有重发旧平台任务。配置文件中已移除退役审批模式键。

重新启动后开发服务使用原来的 `127.0.0.1:5050`；`/api/state` 返回 200。工作库商品、草稿、原生历史、Deferred、收件箱和工具回执均为 0。旧任务和 task-link GET 端点返回 404。

## A1–A14 验收证据

下表是可控模型、本地域服务和平台 mock 的验证范围；不代表真实模型质量已经通过。

| 编号 | 结果及证据 |
| --- | --- |
| A1 | 原生模型收到缺属性结果后调用真实 `product_read`，复用商品品牌，再调用准备工具完成；没有第二次用户输入。`test_native_domain_workflow.py::test_missing_attribute_is_recovered_from_product_facts_without_another_user_prompt`。 |
| A2 | 业务错误进入模型上下文后可改用读取工具并重新选择调用，原生参数错误和预算保持有界。见上述资料恢复用例、`test_ai_tool_bridge.py`、`test_ai_agent_budget.py`。 |
| A3 | 16 条隔离草稿中选择 15 条；14 条真实准备成功，1 条返回真实缺口，未选草稿未变。前两条执行区间实际重叠。草稿箱提交所选 ID 的前端测试使用真实 SDK。 |
| A4 | 商品事实、CBT 多销售目标、目标币种/售价与已保存选择的领域回归通过。见 `test_market_prepare_capabilities.py`、`test_domain_write_capabilities.py` 和 SKU/平台相关回归。 |
| A5 | 原生收件箱有序接收用户更新，字段来源校验拒绝伪造消息/实体/值；页面部分补丁可写入最新草稿，旧完整快照被拒绝。见 `test_native_agent_integration.py`、`test_native_reliability.py` 与草稿持久化回归。 |
| A6 | 同一 run 多个 Deferred 调用及混合批准/拒绝；分批审批可重复提交相同结果，未决定的审批继续等待，被拒绝调用不执行。见 `test_pydantic_native_contracts.py`、`test_native_agent_integration.py`。 |
| A7 | 外部结果齐备后继续调用读取工具；真实发布领域 Job 的成功与失败均回到主 Agent。见 `test_native_agent_integration.py`、`test_native_domain_workflow.py`。 |
| A8 | 运行中取消返回 202 并在后续写入前生效；等待审批时取消关闭原生审批并恢复。前端输入框和发送按钮运行中可用。 |
| A9 | 同商品不同字段并发部分补丁不丢失；旧草稿快照返回冲突；不同草稿准备有执行重叠。见 `test_native_reliability.py` 和 15 条草稿验收。 |
| A10 | 模型异常产生官方 error/finish 且不从收件箱无限重开；写后输出错误保留副作用诊断；未知操作不能用新 call ID 直接重发相同调用。平台未知结果、权限及输出回执回归通过。 |
| A11 | 模拟 Deferred 提交前失败，无待投递记录和外部副作用；模拟投递后缺回执及重启，结果标为未知且不重发；CAS 拒绝旧写者；用户输入转换失败不留下孤立领取。 |
| A12 | 实时 socket 增量流、多轮 canonical 历史、断线、慢客户端 error/finish 与完整历史恢复通过。历史版本和请求代次防止前端旧响应覆盖新会话。 |
| A13 | 拒绝客户端历史、伪造审批身份/参数和来源；原生额度严格限制整个并行批次，预算失败不无限重启。见 HTTP 安全、native integration、budget、provenance 回归。 |
| A14 | 架构测试检查退役文件及唯一入口；HTTP coverage 无遗漏；生产代码不再包含旧控制工具/任务端点/审批模式。旧端点本地实际返回 404。 |

## 2026-09-09 检查记录

- `.venv/bin/python -m compileall -q erp_web`：通过。
- `.venv/bin/python -m pytest tests/test_ai_context_architecture.py -q`：通过。
- `.venv/bin/python -m pytest tests -q`：**1,677 passed，60 subtests passed**。
- `pnpm --dir front test:run`：**56 个文件，442 项测试通过**。
- `pnpm --dir front typecheck`、`types:check`、`build`：通过。
- Python 变更文件格式与未使用导入检查通过；`git diff --check` 通过。
- 前端 lint 无错误，仍有未修改的 `DraftSkuPanel.vue` 的 6 条格式警告；构建保留已有大 chunk 提示。

当时未完成项：模型配置缺少 API Key，真实模型在隔离草稿上的行为验收尚未执行。2026-09-11 已使用已配置模型开展真实对话，该次真实验收尚未完成，不能用本节可控测试结果替代真实模型验收结论。没有向真实店铺发布商品。
