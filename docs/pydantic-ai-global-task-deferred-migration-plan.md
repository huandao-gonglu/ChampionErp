# Pydantic AI Deferred Tools 与 Global Task 恢复链路增量重构计划

> 状态：基于已实施 Capability 基线的待实施增量重构（2026-08-20）。
>
> `docs/global-ai-capability-migration-plan.md` 描述的单主 Agent、Capability Catalog、
> 类型化 Request/Result、ERP Tool Runtime 和领域执行边界已经实施，本计划直接复用
> 这些现行能力，不重新迁移业务 Capability，也不重做上一轮架构。
>
> 本轮只增量修复 Global Task 的 Agent 等待、Deferred 关联、后台完成后的主对话回传、
> 断线恢复和前端只读展示。本文是该生命周期链路的唯一实施依据；旧计划中与这些主题
> 冲突的历史步骤不再执行。

## 1. 背景与问题

当前 `global.chat` 可以调用 `global_task_start` 创建任务，后台 recovery worker 也能继续
推进任务，但 Agent run 与任务执行没有形成完整的暂停/恢复链路：

```text
Agent 调用 global_task_start
  → Controller 创建并执行部分步骤
  → 工具返回 running
  → 当前 Agent run 结束
  → 后台任务继续执行
  → 没有新的 Agent run 消费任务终态
  → 主对话收不到最终 Assistant 回复
```

同时，前端任务卡在挂载和点击“刷新”时会调用可推进任务的写入口。这使前端不再只是
展示层，并造成后台 worker 与 UI 同时拥有任务推进权。

该问题不应继续通过自研 Agent 等待状态机、任务事件协议或模型轮询修补。项目当前锁定的
`pydantic-ai-slim[openai]==2.22.0` 已提供 `CallDeferred`、`DeferredToolRequests`、
`DeferredToolResults`、`ApprovalRequired`、`run_stream_events()` 和官方 UI event stream。
本次重构必须使用这些原生能力完成 Agent 生命周期。

## 2. 架构决策

### 2.1 职责边界

| 边界 | 唯一 owner | 负责内容 | 明确禁止 |
|---|---|---|---|
| Agent 生命周期 | Pydantic AI | Agent run、顶层 tool call、external deferred 暂停/恢复语义、message history、事件编码 | 项目自研第二套 Agent loop、等待/恢复消息协议或事件编码 |
| ERP 任务 | 项目业务代码 | 任务状态、步骤执行、持久化、幂等、租约、业务输入和终态 | 从前端刷新或聊天事件推断执行状态 |
| 权限与审批安全 | 项目业务代码 | actor/tenant、权限校验、审批快照、digest、审计和最终授权 | 把 Pydantic 的 approval 提示当成授权边界 |
| 协议适配 | 薄 HTTP/SSE/WebSocket/Store adapter | 关联 `conversation_id`、保存必要的 Deferred 关联、传输/重放官方事件 | 重新定义 Agent 状态机或并行事件编码 |
| 前端 | Vue/Vercel AI UI | 订阅、读取、展示和提交明确的用户命令 | 通过刷新、挂载或轮询推进业务任务 |

本轮 `global_task_start` 使用 Pydantic `CallDeferred` 表示“顶层外部工具尚未返回”。
Global Task 内部步骤后来进入 `pending_approval` 时，顶层 Agent 已经暂停，不存在另一个
正在运行的 Pydantic tool call；这类审批继续完全由 ERP 任务状态、服务端权限、审批快照、
digest 和审计记录负责。Pydantic `ApprovalRequired` 只适用于未来直接由 Agent 发起并由
Pydantic 暂停的审批工具，不替代本轮 ERP 内部审批。

### 2.2 目标流程

```mermaid
sequenceDiagram
    participant UI as 前端
    participant CHAT as global.chat
    participant PY as Pydantic AI
    participant BRIDGE as AI Tool Bridge
    participant LINK as Deferred Link Store
    participant TASK as GlobalTaskController
    participant WORKER as Recovery Worker
    participant CONT as Continuation Service
    participant STORE as Pydantic Message Store
    participant OUTBOX as Official Event Outbox

    UI->>CHAT: 用户消息
    CHAT->>PY: run_stream_events(message_history)
    PY->>BRIDGE: global_task_start(RunContext)
    BRIDGE->>TASK: 类型化创建请求
    TASK->>LINK: 同事务创建 task + unresolved link（尚未 ready）
    TASK-->>BRIDGE: 类型化 task acceptance
    BRIDGE--xPY: raise CallDeferred(metadata: task_id)
    PY-->>CHAT: DeferredToolRequests
    CHAT->>STORE: 保存 result.all_messages()
    CHAT->>LINK: 与 history 提交一起标记 ready
    CHAT->>OUTBOX: 同事务写官方编码事件
    OUTBOX-->>UI: 提交后发布官方事件
    UI->>CHAT: GET conversation unresolved task
    CHAT-->>UI: task_id + 最小公开状态

    WORKER->>LINK: 读取 ready 屏障
    WORKER->>TASK: 用既有 Task execution lease 独占推进
    TASK-->>WORKER: completed / failed / cancelled
    WORKER->>CONT: 触发一次 continuation claim
    CONT->>PY: DeferredToolResults(tool_call_id → task result)
    PY-->>CONT: 同一 conversation、新 run_id 的结果/事件
    CONT->>STORE: CAS 保存 result.all_messages()
    CONT->>LINK: 与 history 同事务标记 resolved
    CONT->>OUTBOX: 同事务写官方编码事件
    OUTBOX-->>UI: 提交后发布/失败重放
```

同一 conversation 同一时刻只允许一个未解决的 Deferred Tool call。任务处于
`running`、`in_progress`、`needs_input` 或 `pending_approval` 时，不接受该 conversation
的新普通用户回合；用户仍可以通过受信 UI 提交补充资料、批准、拒绝或取消，也可以创建另一条
conversation。这个约束必须由服务端持久化唯一约束和请求前检查强制执行，前端禁用发送仅用于
用户体验。Pydantic 允许一次模型响应产生多个 Deferred call，因此还必须拒绝同一 run 中第二次
`global_task_start`，不能只防下一条 HTTP 消息。该控制 Tool 必须使用 Pydantic `Tool` 的
`sequential=True` 顺序执行设置，并由 conversation unresolved 唯一约束兜底；顺序设置负责避免
并行创建窗口，数据库约束负责最终一致性，二者不能互相替代。

该约束保证历史简单可靠，但意味着长任务期间不能在同一 conversation 继续普通聊天。本轮需求是
“任务终结后自动回传原主对话”，不是“任务运行期间同一会话并行聊天”；若以后需要后者，应另行
设计 detached task/notification 模式，不能让未闭合 tool call 与新回合任意交错。

### 2.3 本轮为何不引入 Pydantic Durable Execution Runtime

Pydantic AI 2.22 另有 Temporal、DBOS、Prefect、Restate 等 durable execution 集成；它们与
Deferred Tools 是不同层次的能力。项目当前只安装 `pydantic-ai-slim[openai]`，并已用 SQLite
Task、CAS、execution lease、Job Status Reader 和 recovery worker 持久化 ERP 业务任务。

本轮不引入新的 durable runtime，理由是：

- 当前是单机 Demo，新增 workflow runtime、依赖、部署单元和序列化约束的成本高于本轮问题；
- ERP Task 的步骤、审批、幂等和外部 Job 已有领域持久化，迁入另一 workflow engine 会扩大范围；
- durable runtime 仍不会替代 task/tool-call 业务关联、服务端授权或前端通知；
- 本轮只需用 Pydantic 原生 Deferred/history/run/event 语义闭合 Agent 生命周期，项目保留最小
  correlation、claim、事务/outbox 适配即可。

当项目进入多进程/多实例生产部署、需要跨机器 workflow 恢复，或无法再用当前 SQLite CAS 明确
保证提交边界时，必须重新评估官方 durable execution 集成；届时不能在现有 coordinator 旁边
再保留一条并行 Agent 生命周期。

## 3. 保留、替换与删除范围

### 3.1 保留

- `GlobalTaskController` 的 ERP 状态机、步骤顺序、恢复策略、CAS、执行 lease 和终态判断；
- `GlobalTaskStore` 及任务快照；
- `AiToolCatalog`、`AiToolCompiler`、`AiToolRuntime` 的类型、权限、预算和业务执行能力；
- 高风险步骤的服务端审批快照、digest、approver 和审计记录；
- Pydantic `ModelMessage` 作为唯一对话历史；
- 现有 `VercelAIAdapter` / `VercelAIEventStream` 协议边界。

### 3.2 替换

- `global_task_start -> GlobalTaskResponse(status=running)` 替换为 Bridge 在成功创建
  task/link 后 `raise CallDeferred(...)`；`CallDeferred` 是 Pydantic 异常，不是返回值；
- 模型调用 `global_task_get` 等待任务，替换为后台构造 `DeferredToolResults`；
- 聊天 deadline 内只推进一个步骤，替换为后台 worker 独占推进全部可执行步骤；
- 前端调用写刷新接口推进任务，替换为纯 GET 状态读取；
- 读取历史时补造 tool return，替换为官方 Deferred 结果显式闭合 tool call；
- 后台任务完成后无消费者，替换为同一 conversation 的 Pydantic 后续 run。

### 3.3 删除

- `GlobalTaskController.refresh_task()` 的执行语义及对应写端点；
- 前端 `refreshGlobalTask()` 写调用和挂载即执行逻辑；
- prompt 中要求模型持续查询/等待任务的说明；
- 为合法 Deferred tool call 合成 interrupted `ToolReturnPart` 的读取路径；
- 只验证旧刷新推进、旧等待和旧消息修复行为的测试及 mock；
- 任何为保留旧流程而增加的 feature flag、fallback、双写或双事件协议。

## 4. 分阶段实施计划

### 阶段 0：先建立产品契约测试

在修改生产代码前增加失败测试，覆盖完整生命周期，而不是只验证 Task 是否写入数据库。

后端必须覆盖：

1. Agent 调用 `global_task_start` 后输出 `DeferredToolRequests`；
2. `CallDeferred` 由 Bridge 明确抛出，不作为普通 Tool 返回值，不进入 ERP JSON
   result 序列化，也不被 `AiToolRuntime` 转换为 `TOOL_EXECUTION_FAILED`；
3. Deferred 请求、`conversation_id`、`request_run_id`、`tool_call_id` 与 `task_id`
   可持久化关联；
4. Task/link 创建后，首次 run 即使已经产出 `DeferredToolRequests`，在包含原始
   `ToolCallPart` 的官方 history 提交前，worker 仍不得执行任务；
5. 首次历史与 link `ready_at` 原子提交后，worker 才能领取任务；
6. 后台任务完成后使用 `DeferredToolResults` 启动同一 conversation、新 `run_id`、
   无新增用户 prompt 的后续 run；
7. 后续 run 生成最终 Assistant 消息，并把官方 `result.all_messages()` 与 link
   `resolved_at` 通过同一事务/CAS 提交；
8. 失败、取消也必须显式闭合 tool call，并由 Agent 生成可解释回复；
9. `needs_input`、`pending_approval` 期间不得提前恢复 Agent；
10. 补资料、批准、拒绝或取消只改变业务状态，由后台 worker 继续或收尾；
11. 两个 worker 竞争时，ERP Task execution lease 只能允许一个执行 Task step，Deferred link
    lease 只能允许一个 continuation；两类 lease 不交叉代替；
12. 同一 conversation 存在未解决 Deferred 时，服务端拒绝新的普通用户回合；
13. `global_task_start` 配置为 Pydantic 顺序执行；同一模型响应两次调用时，最多创建一个
    task/link，另一个调用以稳定错误闭合，不能产生第二个未解决 Deferred；
14. 初始 SSE 或后台订阅断线后，服务端仍完整消费 native events 到
    `AgentRunResultEvent` 并保存历史；
15. 进程重启后未完成任务继续执行，已完成但未提交最终历史的 link 继续恢复；
16. 对“首次 Deferred history 提交前崩溃”“模型返回后最终事务前崩溃”以及
    “最终 history 已提交但 ledger 状态待对账”做故障注入；
17. GET、页面挂载和状态轮询不得改变 task revision、step status 或执行记录。

前端必须覆盖：

1. 首次 Deferred run 不再返回普通 `GlobalTaskResponse` 后，仍能通过
   conversation 的纯读关联查询取得 `task_id` 并展示任务卡；
2. 任务卡由 conversation-level 容器挂载，不依赖 `AiMessagePart` 中存在普通 ToolReturn；
3. 任务卡挂载和刷新只读任务状态；
4. 刷新按钮只调用 GET；
5. approve/reject 保持明确写命令，当前缺失的 submit input/cancel UI 与 API 必须新增；
6. 后台后续 Agent run 的消息进入当前主对话；
7. 页面断线或重载后能从 `/ui-messages` 恢复最终消息，并能重新查询当前未解决任务；
8. snapshot 与 subscribe 之间提交事件时，`after_history_version` 重放不会漏消息；cursor
   过期时触发 resync；
9. conversation 存在未解决 Deferred call 时禁用普通发送并给出明确说明；该前端行为
   只做体验提示，安全与一致性仍由后端校验。

### 阶段 1：扩展统一 Pydantic Agent 入口

修改 `erp_web/services/global_agent_chat_service.py`：

- `GLOBAL_CHAT_PROFILE.output_type` 支持 `str | DeferredToolRequests`；
- instructions 明确：提交任务后由 Deferred 机制恢复，不得主动忙轮询；
- 提供由后台 continuation 调用的同一 `global.chat` 运行入口，不创建第二个 Agent。

修改 `erp_web/services/ai_agent_factory.py`：

- `AiAgentStreamSession` 接受可选 `DeferredToolResults`；
- 唯一地调用官方
  `Agent.run_stream_events(..., deferred_tool_results=...)`；
- 明确区分最终字符串 output 与 `DeferredToolRequests` output；
- 两类结果都使用官方 `result.all_messages()` 原子保存；
- continuation 必须复用同一 `conversation_id`、生成新的 `run_id`，且不合成新的
  `UserPromptPart`；
- 初始 run 与 continuation 都必须由服务端完整消费 native event iterator；客户端
  SSE/WebSocket 断线只停止写客户端，不取消已经接受的 Agent run；
- 继续复用统一 model factory、dependencies、usage limits、instrumentation 和 ToolSet；
- 不在 Factory 内创建第二套 deferred 状态机。

修改 `erp_web/services/ai_tool_bridge.py`：

- `global_task_start` 继续复用现有类型化 request、权限和幂等边界，但其 Agent
  Deferred 握手由标记为 Deferred 的通用控制 Tool Bridge 处理，不让领域 Capability
  感知 Pydantic 类型；
- Bridge 从可信 `RunContext` 取得 `conversation_id`、`run_id` 和 `tool_call_id`，交给
  focused task/link 创建事务；只有 task/link 创建成功后才
  `raise CallDeferred(metadata={"task_id": ...})`；
- `global_task_start` 的 Pydantic Tool definition 固定 `sequential=True`，并增加架构测试防止
  后续编译或注册时丢失该属性；
- `CallDeferred` 必须抛出，不能作为函数返回值；
- `CallDeferred` 不进入 ERP JSON result 序列化，也不被转换成普通字典；
- Direct/Task 领域 Capability 仍经 `AiToolRuntime` 执行；
- bridge 只做 Pydantic 原生类型与 ERP 控制入口的薄转换，不识别任何具体领域步骤；
- deferred 标记只能用于少量 Agent 控制 Tool，不能扩散为第二套 Capability execution
  mode 或让 `AiToolRuntime` import Pydantic Agent 生命周期异常。

### 阶段 2：修正消息历史语义

当前 `erp_web/stores/pydantic_message_store.py::repair_orphaned_tool_returns()` 会在读取时
为缺少 return 的 tool call 合成 interrupted `ToolReturnPart`。对于 Deferred Tool，这种历史
是合法且必须保留的；自动补造会让真正的 `DeferredToolResults` 无法对应原始调用。

实施要求：

- 删除“读取即补造 tool return”的默认行为；
- 保存和读取均只使用 `ModelMessagesTypeAdapter`；
- Deferred 开口保持官方消息原貌；Pydantic message history 保存原始
  `ToolCallPart`，但不保存 `DeferredToolRequests` output 或 `CallDeferred.metadata`；
- `conversation_id`、`tool_call_id`、`task_id`、`request_run_id` 和必要 Deferred
  metadata 必须由 link ledger 持久化，不能假设可从 message history 反推；
- 真正取消、失败或主动放弃时，通过明确 `DeferredToolResults` 闭合；
- 非 Deferred run 的真实中断交给 Pydantic 官方历史清理语义；项目不通过读时修改历史
  掩盖中断，中断状态只记录在运行 claim/技术 trace 中；
- 删除 `INTERRUPTED_TOOL_RETURN_CONTENT`、合成 metadata 和对应旧测试。

### 阶段 3：Global Task 改为后台唯一执行

修改 `erp_web/runtime_units/global_ai_control_tools.py` 和
`erp_web/services/global_task_controller.py`：

1. `global_task_start` 的 typed request 先按现有 Catalog/Compiler 契约校验；
2. focused 创建事务使用可信 `conversation_id`、`request_run_id`、`tool_call_id`，原子创建
   持久化 Task 与尚未 ready 的 Deferred link；
3. 创建事务返回类型化 acceptance，Bridge 随后抛出
   `CallDeferred(metadata={"task_id": ...})`；Controller 本身不返回或 import
   `CallDeferred`；
4. 首次 Agent run 产生 `DeferredToolRequests` 后，通过同一 SQLite 事务保存
   `result.all_messages()` 并设置 link `ready_at`；
5. Controller 不再根据外层聊天 deadline 推进一个步骤，`global_task_start` 路径不执行
   任何 Task step；
6. `ready` link 只是一道“首次 Agent history 已安全提交”的执行屏障；Task 的实际步骤仍由
   recovery worker 通过 `GlobalTaskController` 既有的 Task execution lease 领取并执行，
   不使用 Deferred link lease 领取 Task step；
7. `get_task()` 保持严格纯读；
8. approve/reject/submit_input/cancel 只改变对应业务状态，不直接执行后续步骤，由 worker
   领取后继续；
9. Task 达到 `completed`、`failed` 或 `cancelled` 后产生一次可领取的 continuation 条件；
10. Controller 不调用 Agent，也不 import UI event stream，避免反向依赖。

`global_task_get` 可以保留为用户主动询问既有任务时的只读能力，但不得作为同一 Agent run 的
等待循环或后台完成通知机制。

### 阶段 4：增加最小 Deferred 关联与恢复协调器

增加一个 focused store 保存必要的 Pydantic/Task 关联，不复制完整 Pydantic 请求或消息历史。
建议表名为 `pydantic_deferred_task_links`，最小字段为：

| 字段 | 用途 |
|---|---|
| `link_id` | 项目内部主键，不复用 Provider tool call ID |
| `conversation_id` | 原始主对话 |
| `request_run_id` | 产生 Deferred request 的原始 Agent run |
| `tool_call_id` | 与 Pydantic `ToolCallPart` / `DeferredToolResults` 对应的调用 ID |
| `task_id` | ERP Task 唯一关联，唯一约束 |
| `link_status` | `awaiting_history`、`ready`、`resolved` 或 `abandoned`；唯一的业务生命周期状态 |
| `history_version` | 首次 Deferred history 提交时冻结的 CAS 版本 |
| `created_at` | 创建时间 |
| `ready_at` | 首次 Deferred history 已提交；为空时 worker 禁止执行任务 |
| `lease_id` / `lease_expires_at` | 只用于 Agent continuation claim（及必要的 provisional 清理），不得领取 ERP Task step |
| `continuation_run_id` | 成功提交的后续 Agent run ID |
| `resolved_at` | continuation 完整 history 已成功提交的时间 |
| `abandoned_at` | 首次 history 无法形成且已安全释放 conversation 的时间 |
| `last_error_code` | 恢复失败诊断，不保存敏感 Provider 原文 |

约束必须包括：

- `UNIQUE(task_id)`，Task 与 Deferred link 一对一；
- `UNIQUE(conversation_id, tool_call_id)`，不假设 Provider 的 `tool_call_id` 跨 conversation
  全局唯一；
- `link_status` 使用数据库 `CHECK` 限制合法取值，并由 Store 的条件更新/CAS 限制迁移路径：
  `awaiting_history -> ready -> resolved`，只有无法形成首次 history 且 Task 尚未执行时才允许
  `awaiting_history -> abandoned`；lease 只是临时 claim，不是业务状态；
- 每个 conversation 最多一个 `link_status IN ('awaiting_history', 'ready')` 的 active link；
  SQLite 使用 partial unique index 或等价事务约束；
- worker 只查询 `link_status = 'ready' AND ready_at IS NOT NULL AND resolved_at IS NULL` 的记录。

两类 lease 必须严格分工：`GlobalTaskController` / `GlobalTaskStore` 现有 execution lease 是 ERP
Task step 的唯一执行锁；Deferred link lease 只防两个 continuation service 重复调用模型或重复
提交 history。worker 可以先读取 link 的 ready 屏障再申请 Task execution lease，但不能同时用
两套 lease 表示“Task 正在执行”。outbox publisher 如需 claim，使用独立投递 claim，不复用前两者。

Pydantic message history 保存原始 `ToolCallPart`，不保存 `DeferredToolRequests` output 和
`CallDeferred.metadata`；link ledger 是 task/call 关联与必要 Deferred metadata 的唯一持久化
事实源。它只记录关联、提交屏障、claim 和结果提交状态，不复制 Agent graph、工具请求正文、
完整消息历史或事件，因此不是第二套 Agent 状态机。

初次提交协议必须是：

1. 使用同一 SQLite 事务创建 Task 与 provisional link，`link_status = 'awaiting_history'` 且
   `ready_at` 为空；
2. Bridge 抛出 `CallDeferred`，Pydantic 产出 `DeferredToolRequests`；
3. 服务端完整消费初始 run，校验 output 中恰好存在与 link 匹配的 `tool_call_id`；
4. 在同一事务中保存官方 `result.all_messages()`、递增 conversation history version、设置
   link `ready_at` / `history_version`、把 `link_status` 改为 `ready`，并写入首次 Deferred
   握手的官方事件 outbox；
5. 只有第 4 步提交后，worker 才能领取并执行 Task。

如果进程在 provisional link 创建后、history ready 前退出，Task 不得执行。重启协调器应把
超过明确 TTL 且无法与已保存 Deferred history 对上的 provisional link 标记为 abandoned，
原子写入 `link_status = 'abandoned'` / `abandoned_at`，将尚未执行的 Task 明确取消并释放
conversation；不得伪造 tool call、tool result 或旧流程 fallback。已经存在匹配 Deferred history
的 link 不允许 abandoned，必须修复为 `ready` 或继续标准恢复链路。

新增 focused continuation service，职责限定为：

1. 查找“link 已 ready、Task 已终结且 Deferred link 未解决”的记录；
2. 原子领取 link；
3. 读取并校验同一 conversation 的 Pydantic history：必须存在与
   `(conversation_id, tool_call_id)` 匹配且尚未闭合的 `global_task_start` 调用，history version
   必须与 link 一致；
4. 由 Task 规范 Result 构造大小受限、无敏感字段的类型化结果，再构造
   `DeferredToolResults(calls={tool_call_id: result})`；
5. 通过 `GlobalAgentChatService` / `AiAgentFactory` 的唯一入口启动后续 run；传入相同
   `conversation_id`、新的 `run_id`、原始 history 和 Deferred result，不传新的用户 prompt；
6. 独立完整消费 native events 到 `AgentRunResultEvent`，不依赖任何订阅者在线；
7. 以 link 冻结的 history version 做 CAS，在同一 SQLite 事务中保存官方完整 history、记录
   `continuation_run_id`、设置 `link_status = 'resolved'` / `resolved_at`，并写入待发布 outbox；
8. CAS 失败时重新读取：若 link 已是 `resolved` 且对应 history 已提交，则只确保既有 outbox
   继续投递，不得再次调用模型；否则释放 continuation claim 或等待 lease 超时后重试，不能盲目
   追加消息。

outbox 只保存由官方 `VercelAIEventStream` 编码的有界事件批次以及
`conversation_id`、`history_version`、`run_id` 等投递键，不定义项目自有 Assistant 消息或
Agent event shape。发布器只能读取已经与 history/link 同事务提交的 outbox 记录；投递失败或
进程在提交后退出时重放同一记录，前端按投递键去重并以 `/ui-messages` 的已提交历史为最终事实源。
一次 server broadcast 不等于所有客户端确认，不得据此立即全局删除记录；outbox 至少保留到明确
retention，并支持按 `after_history_version`/cursor 重放。cursor 早于保留窗口时返回
`resync_required`，由客户端读取历史快照。这样可以覆盖“历史已提交、通知尚未发出”以及
“snapshot 后、subscribe 前提交”的窗口，同时不把 transport outbox 扩展成第二套 Agent 生命周期。

Pydantic continuation 需要保留原 `global_task_start` Tool 定义以闭合调用，但本轮没有新的用户
prompt；若模型在 continuation 中再次尝试创建 Task，服务端 unresolved-conversation 约束必须
稳定拒绝，不能产生第二个副作用。普通 Direct 只读能力可以按现有 allowlist 使用。

恢复使用稳定 `continuation_key` 和 history CAS。这里能保证的是：同一
`(conversation_id, tool_call_id)` 最多成功提交并展示一次 continuation history。模型 API 已经
返回、最终 SQLite 事务尚未提交时发生进程崩溃，恢复后可能再次发起模型请求，因此不得声称外部
模型调用具有物理 exactly-once；重复请求也不得重复执行 ERP Task 或提交第二份最终消息。

### 阶段 5：后台事件传输与前端只读化

初始 Agent run 和后续 continuation 都不依赖原始 POST/SSE 请求持续连接。协议层必须：

- 使用现有 `VercelAIEventStream.transform_stream()` / `encode_stream()` 编码 Pydantic
  native events；
- Agent run 是独立 server-side producer，必须完整消费到结果并持久化；对于 Deferred 首次
  握手和后台 continuation，官方编码事件先进入有界发布批次，只有 history/link/outbox 原子提交
  后才能广播，不能向前端发布最终未提交的 Assistant 结果；消息/步骤信封的例外见下方
  「Deferred 首次握手的发布边界」；
- SSE/WebSocket 只是 observer，浏览器断开只移除订阅或停止 socket 写入，不得 `aclose()`
  native iterator、取消 producer 或跳过 history/link/outbox 提交；
- 为活动 conversation 提供后台事件订阅通道；
- 订阅端不存在或断开时，Agent run 仍继续并保存消息历史，已提交的 outbox 等待投递或由重连
  对账；
- 重连后通过现有 `/api/v1/ai-work/conversations/<conversation_id>/ui-messages`
  读取官方 Adapter 派生消息；
- 不创建项目自有 Assistant message shape 或另一套 event codec。

#### Deferred 首次握手的发布边界

计划修订记录（2026-08-21 验收发现 R-01；2026-08-22 二次验收 A-01 收紧；
2026-08-22 三次验收 A-01/A-13 再次收紧并统一有界缓冲口径）：「官方编码事件先提交
后广播」的契约在这里明确一个唯一例外——整条 run 的初始 `start` 与首个 `start-step`。

例外的依据：

- run 是否进入 Deferred 只有在工具执行期（`CallDeferred` 抛出时刻）才能确定，而初始信封
  在任何内容 part 之前编码；确定时刻晚于信封到达时刻，信封不可能被追溯性扣留；
- 信封不携带工具参数、工具状态或 finish reason，是无内容的惰性流信号；官方 error 闭合流
  （提交失败路径）同样会编码同类信封，因此信封本身不构成任何未提交事实的泄露；
- 若把初始信封推迟到首个内容事件或提交之后再发布，会损坏前端思考指示，并让慢模型回合
  长时间零字节输出（代理保活风险）；
- 例外只覆盖初始 `start` 与首个 `start-step`：多模型轮 run 中后续模型轮的 `start-step`
  若实时发布会越过仍在缓冲的前一轮内容，系统性重排官方 SSE，因此后续信封与内容一起缓冲。

例外的边界与失败语义（最终口径）：

- 缓冲边界是「run 开始」。run 是否最终进入 Deferred 只有到 `CallDeferred` 抛出
  才能确定；不仅先行 Direct 工具的事件，连首个工具出现之前的文本也可能属于最终
  未提交的 Deferred 回合。因此除初始 `start` 与首个 `start-step` 外，从 run 开始
  的**全部**官方事件（工具前文本、后续模型轮信封、工具事件、交织文本、终态事件）
  都先缓冲；
- **有界缓冲与事件完整性（统一口径）**：缓冲段与请求侧投递队列均有明确条数/字节
  上限。超限时丢弃的是**中间内容 chunk**（文本/推理 delta、tool-input-delta），
  并保持已缓冲/已投递顺序；**结构性事件**（tool-input-start、tool-input-available、
  tool-output-available、finish-step、finish、error）与请求结束哨兵**保证送达**。
  即：本契约不承诺中间内容 delta 一条不丢，但承诺官方流永远完整闭合（必有
  finish），且前端在回合结束（onFinish）按 /ui-messages 重读对账完整内容。两句话
  不再并存「静默丢弃」与「无事件丢失」的矛盾表述；
- 若 run 最终进入 Deferred：缓冲段由组合事务提交成功后才发布；提交失败时丢弃；
- 若 run 最终未进入 Deferred（含控制工具调用被运行时拒绝）：缓冲段在回合收尾按
  encoder 原顺序补发给客户端；
- 提交失败时客户端观察到的闭合序列是：初始信封 + 官方 error/finish（`finishReason=error`）；
  绝不出现任何未提交的文本、`tool-input-*`、`tool-output-*`、工具调用 id、工具参数
  或成功终态；
- continuation 的官方事件维持原契约：全部提交后广播，不引入信封例外。

复验标准（固化测试）：

- `tests/test_ai_chat_routes.py::test_handshake_publishes_no_tool_state_before_commit_transaction`：
  在组合事务入口打点，此前客户端收到的 chunk 类型恰为 `[start, start-step]`，不含任何
  tool-input-*；提交成功后控制工具段与终态按官方顺序送达；
- `tests/test_ai_chat_routes.py::test_handshake_commit_failure_withholds_uncommitted_terminal_events`：
  事务失败时闭合流不含任何 `tool-input-start/delta/available/error`，不含未提交调用的
  `tool_call_id`，`finishReason` 为 `error`；
- `tests/test_ai_chat_routes.py::test_multi_tool_direct_first_handshake_failure_leaks_no_tool_events`
  / `..._success_publishes_after_commit`（A-01 第一轮）：Direct → `global_task_start` 两面；
- `tests/test_ai_chat_routes.py::test_text_first_handshake_failure_leaks_no_content`
  / `..._success_publishes_content_after_commit`（A-01 第二轮）：text → Direct →
  `global_task_start` 两面——失败时工具前文本也不可见，成功时按序发布；
- `tests/test_ai_chat_routes.py::test_multi_turn_direct_then_deferred_keeps_encoder_order`
  / `test_multi_turn_direct_then_text_keeps_encoder_order`（A-01 第三轮）：第一轮
  Direct → 第二轮 Deferred/文本的多模型轮完整序列与 encoder 原顺序一致，后续
  `start-step` 不再提前；
- `tests/test_ai_chat_routes.py::test_long_stream_and_slow_observer_stay_bounded_and_ordered`
  （A-13，读真实 SDK v7 `delta` 字段）：3000 段长流 + 慢 observer，工具骨架、
  finish-step/finish 不丢，finish 是最后一个数据 chunk，delta 序号严格递增；
- `tests/test_ai_chat_routes.py::test_oversize_plain_run_still_closes_with_finish`
  （A-13 探针二）：超过缓冲上限的普通 run 仍以 finish 完整闭合，不产生无闭合截断流。

Task 中间状态属于 ERP 业务状态，可以由前端只读轮询 GET 展示，不必伪装成 Agent 事件。
Pydantic 2.22 的 Vercel encoder 不会把 external `CallDeferred.metadata.task_id` 编码给前端，
因此新增 conversation-scoped 纯读接口，按 `conversation_id` 返回当前 unresolved link 的
`task_id` 和最小公开状态；该公开接口只返回 `link_status = 'ready'` 的 link，
`awaiting_history` provisional link 仅供服务端恢复/清理，不向前端宣告任务已受理。任务详情再通过
规范 Task GET 读取。前端不得依赖旧
`global_task_start` 普通 ToolReturn 中的 `GlobalTaskResponse` 恢复任务卡。

修改 `front/src/components/ai-work/GlobalTaskApprovalCard.vue`：

- 不再接收旧 ToolReturn `response` 作为挂载条件，改为接收 conversation 级查询得到的 `task_id`
  或规范 Task response；
- 挂载后只通过纯 GET 读取 Task 状态；
- 刷新统一调用纯 GET；
- 删除 `running/in_progress` 时改调写刷新接口的分支；
- 允许定时只读刷新任务状态；
- 保留现有 approve/reject 显式用户动作；
- 新增当前前端尚不存在的 submit input/cancel API wrapper、控件和错误处理；
- 任务完成后不在前端拼 Assistant 回复，等待后台 Pydantic run 的官方消息。

修改 `front/src/components/ai-work/AiChatPanel.vue` 与 `AiMessagePart.vue`：

- `AiChatPanel` 作为活动 conversation 的任务卡挂载入口，接收当前 `conversation_id`，在 conversation
  切换、首次进入和 history version 变化时调用 conversation-scoped 纯读接口；存在 active link 时，
  在消息区独立挂载一张 `GlobalTaskApprovalCard`；
- 任务卡的存在不依赖 `AiMessagePart`、`global_task_start` ToolReturn 或
  `CallDeferred.metadata`；即使没有任何普通 tool output，创建成功后也必须出现；
- `AiMessagePart` 删除根据旧 `GlobalTaskResponse` 自动挂载交互式任务卡的分支，历史 tool part 仅按
  官方 UI message 结构普通展示，避免一项任务出现两张可操作卡片；
- `AiWorkView.vue` 和浮层入口把当前 `conversation_id` 传给 `AiChatPanel`，相关测试覆盖两个入口。

修改 `front/src/stores/aiChat.ts` 及 transport：

- 订阅当前 conversation 的后台官方事件流；
- 收到完成事件后更新当前 `Chat.messages` 和 history version；
- 只按单调递增的 history version 应用 outbox 事件；重复或旧版本只做去重/对账，发现版本跳跃
  时重新读取 `/ui-messages`，避免 continuation 通知与后续普通回合乱序覆盖；
- 断线、切换 conversation 或页面重载时关闭旧订阅；
- 冷启动或重连先读取服务端消息历史及其 history version，再用
  `after_history_version=<version>` 建立“历史重放后无缝转 live”的订阅；服务端必须从 outbox 重放
  该版本之后的保留事件，因此 snapshot 与 subscribe 之间提交的事件不会丢失；
- 如果请求的 cursor 已早于 outbox 保留窗口，订阅端返回明确 `resync_required`，前端重新读取
  `/ui-messages` 后再订阅，不能静默从当前 live 位置开始；
- 未解决 Deferred call 存在时禁用普通发送，但不阻止审批/输入/取消操作。

修改 `erp_web/services/vercel_ai_ui_service.py` 的请求前校验：

- 接受普通用户回合前，在服务端原子检查 conversation 不存在 unresolved link，不能只依赖
  前端按钮状态；
- 普通用户 run 与 continuation 使用同一 conversation run claim/lease 串行化；
- 已经接受的 run 即使客户端断线也继续 drain，明确的用户 Task cancel 命令才取消 ERP Task。

### 阶段 6：删除旧路径并更新架构文档

完成切换后必须全库检索并删除：

- mutating `refresh_task` 及 HTTP/frontend 调用；
- `outer_remaining_seconds` 导致的聊天内单步推进分支；
- 模型等待/轮询 Global Task 的 prompt；
- 合成 orphan tool return 的符号与测试；
- 旧 deferred/event/message fallback、feature flag 和 mock。

同步更新：

- `docs/ai-context-map.md`：描述实际落地后的唯一 Deferred 入口和依赖方向；
- `tests/test_ai_context_architecture.py`：禁止第二 Agent loop、自研 deferred codec 和前端任务推进；
- `tests/test_ai_capability_architecture.py`：继续保证类型化 Task Capability union 与 allowlist；
- Global Task、Agent stream、message store、HTTP route 与前端组件的现行行为测试。

## 5. 数据迁移策略

新增关联表必须使用正式 SQLite schema migration，不允许运行时猜测列或双写旧表。

已有终态 Task 可以继续作为历史只读记录。已有非终态 Task 没有真实 Pydantic
`tool_call_id`，不得伪造 Deferred call：

1. 实施前先查询是否存在 `running`、`in_progress`、`needs_input` 或
   `pending_approval` 的旧任务；
2. 如果不存在，直接迁移；
3. 如果存在，保留快照用于诊断，并用一次性迁移将其明确标记为不可恢复/已取消，提示用户重新
   提交；
4. 不为旧任务创建虚假 tool call，不保留旧执行链 fallback。

由于项目仍处于 Demo 阶段，新写入数据只使用新格式。旧终态记录的读取能力属于真实持久化数据
迁移，不意味着保留旧运行流程。

## 6. 主要影响文件

### Backend

- `erp_web/services/global_agent_chat_service.py`
- `erp_web/services/ai_agent_factory.py`
- `erp_web/services/ai_tool_bridge.py`
- `erp_web/stores/pydantic_message_store.py`
- `erp_web/stores/pydantic_deferred_task_link_store.py`（新增）
- `erp_web/stores/pydantic_ai_event_outbox_store.py`（新增，保存官方编码事件）
- `erp_web/runtime_units/global_ai_control_tools.py`
- `erp_web/services/global_task_controller.py`
- `erp_web/stores/global_task_store.py`
- `erp_web/server.py`
- `erp_web/services/vercel_ai_ui_service.py`
- `erp_web/http_route_units/` 下对应 AI chat / Global Task route
- `erp_web/db.py` 与对应 schema migration

### Frontend

- `front/src/components/ai-work/GlobalTaskApprovalCard.vue`
- `front/src/components/ai-work/AiChatPanel.vue`
- `front/src/components/ai-work/AiMessagePart.vue`
- `front/src/views/AiWorkView.vue`
- `front/src/components/common/AiWorkFloatingButton.vue`
- `front/src/stores/aiChat.ts`
- `front/src/api/` 下 Global Task 与 AI chat transport
- 对应 Vitest 测试

### Tests and docs

- `tests/test_ai_agent_stream_session.py`
- `tests/test_global_task_controller.py`
- `tests/test_global_agent_vertical_integration.py`
- `tests/test_pydantic_message_store.py`
- Deferred link store、conversation lock、continuation CAS 与断线 drain 专项测试
- 官方编码事件 outbox 的提交后发布、重放与前端去重测试
- `tests/test_ai_context_architecture.py`
- Global Task route、worker recovery 和 DB migration 测试
- `docs/ai-context-map.md`

## 7. 验收标准

全部条件同时满足才算完成：

1. 用户不需要发送第二条消息，后台任务终结后主对话自动出现 Agent 最终回复；
2. continuation 使用相同 `conversation_id`、新的 `run_id`、原始 Pydantic history 和
   `DeferredToolResults`，不增加伪造的用户消息；
3. 初始 run 或 continuation 期间前端关闭、断线、切换页面均不取消 server-side producer，
   不影响 Task 执行、history 提交和 Deferred 恢复；
4. 页面重开后可以从 Pydantic message history 读取最终消息，并通过 conversation 纯读接口
   恢复尚未终结的任务卡；
5. GET、页面挂载和状态轮询不会执行任务或改变 task revision；
6. provisional link 的 `ready_at` 设置前 Task 永不执行；无法对上已保存 Deferred history 的
   过期 provisional link 会被明确 abandoned/cancelled 并释放 conversation，随后该 conversation
   可以正常接受新的用户回合；
7. 同一 `(conversation_id, tool_call_id)` 最多成功提交并展示一次 continuation history；
   不承诺崩溃窗口中的外部模型请求物理 exactly-once；
8. history CAS 冲突、两个 worker、普通 user run 与 continuation 竞争时只有一个提交者成功；
9. 一个 conversation 最多一个 unresolved link；同一模型响应或两个并发 POST 不能创建第二个
   Global Task Deferred 或 orphan Task；
10. 重启后未完成任务继续执行，已完成但未提交最终 history 的 link 继续恢复；已经原子提交
    `resolved` history/outbox、但通知尚未投递的记录只重放 outbox，不再次调用模型或追加消息；
11. `needs_input` / `pending_approval` 不会提前产生 `DeferredToolResults`，input/approve 后由
    worker 继续；reject/cancel 到达终态后由 Agent 生成解释；
12. 审批的 actor、权限、digest、task revision 和审计记录仍由服务端验证，未授权请求失败；
13. 前端 approve/reject/input/cancel 都可用；普通发送被锁定时仍可执行这些明确命令；
14. Agent/Tool/Deferred/Message/Event 编码均使用 Pydantic AI 原生机制；项目只实现关联、事务、
    claim、transport 和公开 Task 状态，不创建第二套 Agent loop/message/event codec；
15. 初次 history 提交前、Task 终态后 claim 前、模型返回后最终事务前、最终事务后通知前等
    故障注入均不会造成 Task 重复副作用、永久锁死或重复展示；最终事务后通知失败由已提交
    outbox 重放，且只能发布官方编码事件；
16. 不存在旧刷新推进、模型忙轮询、读时合成 tool return、第二事件协议或双轨 fallback；
17. 后端全量测试与前端测试、类型检查、lint 和构建通过：

```bash
.venv/bin/python -m pytest tests -q
cd front
pnpm test:run
pnpm typecheck
pnpm lint:check
pnpm build
```

## 8. 推荐提交顺序

为避免长期双轨运行，按以下顺序在同一迁移分支完成，最终一次切换：

1. 契约测试、并发测试、故障注入与架构守卫；
2. 正式 schema migration、Deferred link ready/resolved 状态与唯一约束、conversation run
   claim；
3. Pydantic Factory / Tool Bridge / message history 原生 Deferred 支持，以及与客户端连接解耦的
   server-side event drain；
4. `global_task_start` 抛出 Deferred、首次 history/ready 原子提交、worker ready barrier，
   一次性切换 Task 执行 owner；
5. continuation history CAS、history + resolved + 官方事件 outbox 原子提交和提交后通知；
6. conversation → unresolved task 纯读 API、后台订阅、前端只读任务卡与 input/cancel；
7. 旧刷新/轮询/repair/fallback 删除、旧数据迁移、架构文档更新与全量回归。

每一步允许形成独立可审查提交，但主分支最终状态不得同时保留旧等待链和新 Deferred 链。
