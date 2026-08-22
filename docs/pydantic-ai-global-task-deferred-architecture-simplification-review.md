# Pydantic AI Deferred 与 Global Task 架构瘦身评审建议

> 状态：待评审  
> 日期：2026-08-22  
> 适用范围：`docs/pydantic-ai-global-task-deferred-migration-plan.md` 对应的当前实现  
> 目标：在不削弱原子提交、崩溃恢复、断线恢复和业务安全边界的前提下，判断当前实现是否还能进一步使用 Pydantic AI 原生能力并减少无效基础设施  

## 1. 背景

当前迁移已经删除旧的 Agent 等待、写刷新推进、读取时伪造 ToolReturn 和普通 ToolReturn 任务卡路径，并使用 Pydantic AI 的 `CallDeferred`、`DeferredToolRequests`、`DeferredToolResults`、`run_stream_events()` 和官方 Vercel event encoder 管理 Agent 暂停与恢复语义。

代码增长的主体不是新的 model/tool/model loop，而是此前缺失的可靠性闭环：

- ERP Task 与 Pydantic `tool_call_id` 的持久关联；
- history version、CAS 与 continuation claim；
- 请求断线后继续运行的 server-side producer；
- history/link/outbox 组合事务；
- 后台通知、重放和前端 conversation 恢复；
- 旧数据库 schema 的升级与 legacy Task 处置。

这些能力不能仅凭“代码变多”判断为重复造轮子。但当前实现仍有若干可以进一步收敛的设计点。本文件只定义五项架构评审，不直接授权实施重写。

## 2. 评审原则

1. Pydantic AI 是 Agent 生命周期、Deferred 语义、ModelMessage 和 Agent event 编码的唯一 owner；
2. 项目只保留 ERP 领域状态、安全校验、必要持久关联、事务、transport 和 UI；
3. 每一个自定义 Deferred 状态、event 字段或后台协调步骤，都必须能对应一个 Pydantic 不提供的项目需求；
4. 不为了减少行数删除 CAS、幂等、授权、原子提交或明确需要的崩溃恢复语义；
5. 没有真实持久数据、公开 API 或外部调用方证据时，不默认保留 Demo 阶段的兼容层；
6. 一个职责只能有一个生产 owner，测试不得冻结没有生产调用方的备用入口；
7. 评审结论应以“可删除的状态、分支、owner 和故障窗口”为依据，而不是只以 LOC 为依据。

## 3. 五项评审概览

| 编号 | 评审项 | 当前建议 | 优先级 | 是否直接实施 |
|---|---|---|---|---|
| R-01 | 用 Pydantic `ExternalToolset` 重新评估 `global_task_start` | 先做最小技术验证，再决定是否替换 `CallDeferred` 特判链 | 高 | 否，先验证 |
| R-02 | 将完整 event-chunk outbox 简化为真实使用的通知契约 | 优先选择最小 durable history-version notification | 高 | 评审确认后实施 |
| R-03 | 集中提交后发布 owner，删除重复与死入口 | 应当收敛 | 中 | 可以独立实施 |
| R-04 | 评估 `DetachedChatRunner` 的保留边界 | 当前服务器架构下保留，设定明确移除条件 | 中 | 当前不重写服务器 |
| R-05 | 依据真实数据决定 v10/v11/v12 migration | 先做数据与部署证据审计；无证据则删除兼容链 | 中 | 证据确认后实施 |

## 4. R-01：评估用 `ExternalToolset` 取代自定义 `agent_deferred` 特判链

### 4.1 当前实现

当前 `global_task_start` 作为普通项目 Tool 先执行 ERP acceptance，再由 Bridge 手动抛出 Pydantic `CallDeferred`：

```text
模型调用 global_task_start
  → Tool declaration/compiler/runtime/bridge
  → Controller 创建 task + provisional link
  → Bridge raise CallDeferred(metadata={task_id})
  → Agent 输出 DeferredToolRequests
  → 协议层提交 history + ready + outbox
```

为支持这条路径，`agent_deferred` 已贯穿多个通用层：

- `erp_web/services/ai_tool_declaration.py`
- `erp_web/schemas/ai_tools.py`
- `erp_web/services/ai_tool_compiler.py`
- `erp_web/services/ai_tool_runtime.py`
- `erp_web/services/ai_tool_bridge.py`
- `erp_web/services/ai_agent_factory.py`
- `erp_web/services/vercel_ai_ui_service.py`

同时产生以下项目状态或特判：

- `deferred_call_started`；
- provisional/`awaiting_history` link；
- `ready_at` 前后的 repair/abandon；
- 对第二个 deferred control call 的 runtime 特判；
- 对官方 UI Adapter 输出的 `approval-requested` 展示修正；
- 从官方已编码 SSE 文本中反向识别 `global_task_start` 并切换缓冲。

当前路径使用的是官方 `CallDeferred`，并不是错误实现；需要评审的是它是否仍是本项目的最小原生接法。

### 4.2 Pydantic 原生候选

Pydantic AI 2.22 已提供 `pydantic_ai.toolsets.ExternalToolset`。External Tool 不在当前 Agent run 内执行，模型调用后由 Pydantic 直接产生 `DeferredToolRequests.calls`，调用方在 Agent run 外执行并通过 `DeferredToolResults` 恢复。

候选流程：

```text
模型调用 external global_task_start
  → Pydantic 输出 DeferredToolRequests
  → focused handshake service 校验唯一 call 与类型化参数
  → 同一事务创建 task + link + 初始 history + notification
  → ready 后由 ERP worker 执行 Task
  → Task 终态后用 DeferredToolResults 恢复同一 Agent
```

该方案理论上可以把“Task 创建”和“初始 Deferred history 提交”合并到一个事务入口，从源头消除 task 已创建但 history 尚未提交的 provisional 窗口。

### 4.3 预期收益

如果验证成立，可能删除或显著简化：

1. `agent_deferred` declaration/schema/compiler/runtime 元数据链；
2. Bridge 中 `CallDeferred` acceptance 特判；
3. `deferred_call_started` 和按编码后 SSE 文本识别工具的逻辑；
4. provisional `awaiting_history`、repair/abandon 和相关 sweeper；
5. `CallDeferred.metadata.task_id` 与 UI Adapter 展示修正；
6. 部分重复 call-id 防御逻辑，改为对 `DeferredToolRequests.calls` 做一次集合校验。

### 4.4 必须验证的问题

技术验证必须回答：

1. 是否能复用现有 Catalog/Compiler 生成的 `global_task_start` JSON Schema，而不建立第二份 Tool 定义；
2. handshake service 能否在模型参数之外注入可信 `conversation_id`、`run_id`、actor 和 tenant；
3. 参数校验或业务 acceptance 失败时，如何用官方 Deferred result 让模型稳定闭合，而不是留下开放 call；
4. 同一模型响应出现两个 external `global_task_start` 时，能否在创建任何 orphan Task 前稳定拒绝；
5. 官方 Vercel Adapter 对 external Deferred part 的展示是否正确，能否删除当前 `_normalize_deferred_approval_parts()`；
6. approval、`needs_input`、reject/cancel 终态是否仍保持 ERP 安全与状态边界；
7. `DeferredToolRequests.build_results()` 能否替代当前部分手工 call-id 结果校验；
8. 初始 history、task、link 和 notification 能否在当前 SQLite owner 中真正一次提交。

### 4.5 决策门槛

只有技术验证同时满足以下条件才替换当前方案：

- 删除的通用特判和状态明显多于新增的 handshake adapter；
- 不减少任何类型校验、权限、幂等和故障恢复能力；
- 能证明一个 external call 对应且只对应一个 task/link；
- 不新增第二个 Tool Catalog、Agent 入口或 event codec；
- 初始事务和恢复测试比当前路径更简单，而不是把复杂度转移到另一个 service。

如果验证失败，应保留当前 `CallDeferred` 方案，并在架构文档中明确“不使用 ExternalToolset”的具体原因。

## 5. R-02：重新定义 outbox 的真实产品契约

### 5.1 当前实现与实际消费不一致

当前后端：

1. 使用官方 Vercel encoder 生成 chunks；
2. 截取有界终态段；
3. 将 chunks 序列化到 `events_json`；
4. outbox/SSE 发送 `{type, history_version, run_id, kind, events}`；
5. 对 chunk 数量和总字节数设置限制并提供 resync-only 降级。

涉及：

- `erp_web/stores/pydantic_deferred_task_link_store.py`
- `erp_web/stores/pydantic_ai_event_outbox_store.py`
- `erp_web/services/ai_conversation_event_stream.py`
- `erp_web/services/global_task_continuation_service.py`
- `erp_web/services/vercel_ai_ui_service.py`
- `erp_web/db.py::ai_conversation_event_outbox.events_json`

但前端 `front/src/stores/aiChat.ts::handleEventPayload()` 完全不消费 `events`。它只读取 `type/history_version`，随后 GET `/ui-messages`，以已经提交的完整 Pydantic history 重新渲染。

因此当前同时维护了两个目标：

- “重放官方 event chunks”；
- “收到 version 通知后重读 history”。

实际产品只使用第二个目标。

### 5.2 必须二选一的契约

#### 方案 A：真正消费官方事件

保留完整 event outbox，前端直接将重放 chunks 交给官方 UI stream 消费，并用 history snapshot 处理 outbox 保留期之外的 resync。

适用于：

- 产品需要离线后重放完整逐 token/逐工具事件；
- 官方事件本身是前端恢复的事实源；
- 团队愿意承担 event schema/version 与大批次存储成本。

如果选择该方案，前端必须真实使用 `events`；不能继续只把它们当作 version 通知。

#### 方案 B：最小 durable history-version notification（推荐）

实时首轮聊天继续使用官方 Vercel stream；后台 continuation 和断线恢复只发送最小提交通知：

```json
{
  "type": "history_committed",
  "conversation_id": "...",
  "history_version": 3,
  "run_id": "...",
  "kind": "continuation"
}
```

前端收到后 GET `/ui-messages`，完整 history 仍是唯一事实源。

最小 durable notification outbox 只需保存：

- `conversation_id`；
- `history_version`；
- `run_id`；
- `kind`；
- `created_at/published_at`。

可以删除：

- `events_json`；
- encoded chunk 终态筛选；
- chunk 数量与字节限制；
- continuation/handshake 对 encoded chunks 的收集和事务参数；
- resync-only 空 chunks 降级；
- SSE payload 中未消费的 `events`。

该通知是 transport metadata，不是第二套 Assistant message 或 Agent event codec。文档必须明确它只表示“某个 Pydantic history version 已提交”，不能携带回答正文、ToolReturn 或 Agent 状态。

### 5.3 更进一步的可选方案

在单机 Demo 中，SSE subscription 也可以定期比较 `PydanticMessageStore.get_version()`：版本变大即通知前端重读，从而不再需要完整 outbox/bus/publisher。

但这个选择会把低延迟推送改为数据库轮询，也会降低“提交成功后立即通知在线页面”的确定性。除非明确接受该产品权衡，否则本轮更推荐保留最小 durable notification outbox，而不是彻底删除通知持久化。

### 5.4 验收条件

1. 只保留一种前端恢复事实源；
2. snapshot→subscribe 窗口内提交的新版本不会丢失；
3. 通知重复、乱序和重连时按 `history_version` 幂等；
4. 通知失败不影响 history/link 已成功提交；
5. resolved history 只能重发通知，不能再次调用模型；
6. outbox 有明确的保留与清理策略；
7. 删除所有没有消费者的 payload 字段和限制逻辑。

## 6. R-03：集中提交后发布 owner，并清理重复入口

### 6.1 当前重复

提交后 `list_after → event_bus.publish → mark_published` 逻辑分布在：

- `erp_web/services/vercel_ai_ui_service.py`：首次 Deferred 握手；
- `erp_web/services/global_task_continuation_service.py`：最终 continuation；
- `erp_web/services/ai_conversation_outbox_publisher.py`：后台补投。

当前还存在以下可疑冗余：

- `PydanticDeferredTaskLinkStore.create_with_task()` 没有生产调用，真实创建入口在 `LocalGlobalTaskStore.create_task_with_deferred_link()`；
- `PydanticAiEventOutboxStore.latest_history_version()` 及对应 DB 查询没有生产调用；
- 协议层通过解析已经编码的官方 SSE 文本识别 `global_task_start`，使通用 Vercel adapter 反向依赖领域 Tool 名和 wire shape。

### 6.2 建议 owner

收敛为一个 focused publisher，例如：

```text
AiConversationOutboxPublisher
  ├─ publish_committed(conversation_id, history_version, kind)
  ├─ publish_pending(limit)
  ├─ mark_published(outbox_id)
  └─ retention cleanup
```

调用约束：

1. handshake/continuation 事务只负责提交 history/link/outbox；
2. 事务成功后只调用 publisher 的单一入口；
3. publisher 失败只能留下 unpublished 记录，不得改变已提交业务结果；
4. 后台 worker 调用同一个 publisher 的 pending 重试入口；
5. registry、claim 和 lease cleanup 必须在 publisher 异常之外完成；
6. event bus 不得由其他业务 service 直接调用。

### 6.3 协议分层修正

如果仍需识别“从 deferred tool-input-start 起延迟发布”，应在 native/transformed event 层使用通用 Deferred signal，而不是：

```text
官方 event
  → 编码成 SSE JSON 文本
  → 项目再次 JSON parse
  → 硬编码判断 global_task_start
```

如果 R-01 采用 ExternalToolset，这段逻辑应优先随之删除；如果保留 `CallDeferred`，也应把 deferred 信号放在通用 Tool metadata 或 native event 边界。

### 6.4 验收条件

1. 生产代码只有一个 publish/mark-published owner；
2. handshake 和 continuation 不再复制 outbox 查询与发布循环；
3. 发布失败不会泄漏 registry、claim 或 lease；
4. 删除没有生产调用方的 store/DB 方法及只为它们存在的测试；
5. 通用 UI 协议适配层不硬编码 ERP Tool 名；
6. retention、重复投递和无订阅者行为有明确测试。

## 7. R-04：保留 `DetachedChatRunner`，但限制其生命周期与扩张

### 7.1 当前存在理由

当前 HTTP 服务基于同步请求处理，每次请求创建并关闭一次 event loop。若 Agent producer 绑定在请求 loop：

- 客户端断线会取消 producer；
- loop 关闭会中断 native event drain；
- history/link/outbox 和 claim 可能无法形成终态。

`erp_web/services/ai_chat_detached_runner.py` 使用进程级 daemon thread 和专用 event loop 托管 producer，使浏览器连接生命周期与 Agent run 生命周期解耦。

这不是 Pydantic Agent loop 的重复实现。它只负责托管 coroutine，真正的 Agent run 仍由 `AiAgentFactory` 和 Pydantic `run_stream_events()` 执行。

### 7.2 当前建议

本轮保留 `DetachedChatRunner`，不为了减少代码同时迁移整个 HTTP server。

但必须限定边界：

1. runner 不保存 Task、link、claim、history 或重试状态；
2. runner 不实现 model/tool/model loop；
3. 业务 commit、claim cleanup 和 registry release 仍由 producer owner 负责；
4. runner 只提供 submit、shutdown 和必要的健康状态；
5. 进程退出必须有明确的 graceful shutdown 行为；
6. daemon thread 中未观察的 Future 异常必须进入日志/监控；
7. 不在 runner 内继续增加 scheduler、outbox 或多 worker 逻辑。

### 7.3 移除条件

满足以下任一条件时应删除该 runner，而不是继续扩建：

- HTTP 服务迁移到具有 application-lifetime event loop 的 ASGI 架构；
- 引入 Pydantic 官方支持的 Temporal、DBOS、Prefect 或 Restate durable execution；
- Agent producer 改由已有的正式后台 Job/worker runtime 托管。

届时应同时删除 daemon thread、跨线程 Future、进程内 event loop 管理及相关测试。

### 7.4 验收条件

1. 请求断线不会取消 producer；
2. producer 正常、失败和取消都能完成 claim/registry cleanup；
3. server shutdown 不接受新 producer，并在有界时间内处理或明确放弃存量 producer；
4. runner 不成为新的业务协调 owner；
5. 多进程部署限制在架构文档中明确记录。

## 8. R-05：以真实数据证据决定旧数据库 migration

### 8.1 当前兼容范围

`erp_web/db.py` 当前支持：

- v10 → v12；
- v11 → v12；
- v12 → v13；
- legacy unfinished Global Task 的明确取消；
- 对缺少真实 Pydantic `tool_call_id` 的旧 Task 不伪造 Deferred link。

迁移本身符合持久化安全原则，但项目处于 Demo/初始开发阶段。是否保留这部分成本，必须由真实数据和部署证据决定，不能仅因“代码已经存在”自动形成兼容契约。

### 8.2 证据审计

评审前应确认：

1. 是否存在需要保留的用户数据库或团队共享数据库；
2. 是否有任何已部署实例仍运行 v10/v11/v12；
3. 旧数据是否包含不可重新生成的 conversation、Task、审批或业务记录；
4. 是否存在外部脚本或发布版本依赖旧 schema；
5. 是否已经有备份、导出或一次性迁移方案；
6. legacy unfinished Task 是应取消、导出还是允许直接丢弃。

### 8.3 决策分支

#### 存在真实不可丢数据

保留 migration，并要求：

- 使用真实 v10/v11/v12 schema fixture 测试逐级升级；
- 重复启动迁移幂等；
- migration 与当前 schema 创建路径最终完全一致；
- legacy Task 的取消有审计记录且不伪造 `tool_call_id`；
- 文档明确支持的最低版本和未来移除策略。

#### 不存在真实兼容契约

按项目 Demo 策略删除：

- v10/v11/v12 upgrade 分支；
- legacy Task 一次性取消逻辑；
- 只验证旧行为的 migration fixture/test；
- 旧 schema 常量、错误说明和文档描述。

当前 schema 直接作为唯一初始化格式；开发数据库通过明确重建或一次性导出/导入处理，不保留 runtime compatibility path。

### 8.4 安全边界

在证据审计完成前不得直接删除旧数据库或破坏现有文件。即使最终决定不保留 runtime migration，也应先明确哪些数据库可重建、哪些需要备份或导出。

## 9. 推荐评审顺序

五项并非完全独立，建议按以下顺序决策：

1. **先验证 R-01 ExternalToolset。** 它会直接影响 provisional link、事件缓冲和 UI Deferred part；
2. **再决定 R-02 事件契约。** 明确前端究竟消费官方 events 还是只消费 history-version notification；
3. **根据前两项结果实施 R-03 owner 收敛。** 避免先重构随后再次推翻 publisher；
4. **独立完成 R-05 数据证据审计。** 该结论不依赖 Agent 流程；
5. **R-04 当前只记录边界与移除条件。** 不在本轮同时迁移 HTTP server。

## 10. 建议形成的正式决策记录

评审完成后，将结果填写为：

| 编号 | 最终决定 | 采用理由 | 放弃方案及理由 | 删除范围 | 新增范围 | 验收测试 | Owner |
|---|---|---|---|---|---|---|---|
| R-01 | 待定 |  |  |  |  |  |  |
| R-02 | 待定 |  |  |  |  |  |  |
| R-03 | 待定 |  |  |  |  |  |  |
| R-04 | 建议保留 | 当前同步 HTTP 生命周期需要独立 producer owner | 本轮迁移 ASGI：范围扩大 | 未来满足移除条件后整体删除 | 仅补边界/停机验证 | 断线、异常、shutdown |  |
| R-05 | 待数据审计 |  |  |  |  | migration fixtures |  |

任何“保留当前实现”的决定也必须写明原生方案为什么不适用，避免未来再次重复讨论。

## 11. 预期结果

完成这五项评审后，目标不是机械追求净删除，而是达到以下状态：

1. Pydantic 原生 Deferred 能力使用到合理的最高层级；
2. 项目只保存真正有消费者的数据；
3. history/link/notification 各自只有一个提交和发布 owner；
4. 同步 HTTP 带来的临时基础设施不会继续扩张为第二套 durable runtime；
5. 数据库兼容成本与真实持久数据价值匹配；
6. 迁移计划、代码、测试和产品实际行为使用同一套契约。

即使最终保留大部分可靠性代码，只要以上职责边界成立，本次迁移仍然是合理的；如果评审确认 ExternalToolset 和最小 version notification 可行，则应在正式合入前完成相应收敛，避免长期维护无消费者的事件与跨层 Deferred 特判。
