# Pydantic AI 原生 Agent 重构计划

> 状态：原生代码替换已完成；真实模型验收及未完成项见 [交付记录](pydantic-ai-native-agent-refactor-delivery.md)。\
> 日期：2026-09-09。\
> 当前方向：Agent 生命周期继续由 Pydantic AI core 负责；2026-09-24 增加官方 Harness 的 CodeMode 和输出限制，用于通用 Python 工具编排，见 [Python 执行说明](agent-python-execution.md)。\
> 本轮实施依据：本文取代旧计划中“固定步骤交给 GlobalTaskController、仅终态回到 Agent、任务期间封锁会话”的设计。已有领域能力、数据保护和安全要求继续有效。

## 1. 要交付的行为

用户说“处理这些草稿”后，AI 能读取相关资料，选择业务工具，根据执行结果决定下一步，完成当前授权范围内能完成的工作。草稿准备不隐含发布授权。

- 草稿箱提供对所选草稿的 AI 批量准备入口，将稳定 `draft_id` 集合和用户目标交给同一主 Agent；全局对话也能查询并确定同样的目标范围，不另建批量专用流水线。
- 多条独立草稿可以并发准备；一条缺资料，不应使其他可处理草稿停下来。
- 已在商品、草稿、店铺配置或相关对话中提供的资料，应先查找并复用。只有确实缺失、冲突或需要用户决定的事实才提问。
- 工具返回缺字段或可修复错误时，AI 有机会读取资料、修改参数、换用适合的工具；不能由 Controller 直接把整个目标冻结为 `needs_input`。
- 用户可以在原对话补充资料、纠正要求、请求取消；提交后必须明确显示何时生效，不能因为存在后台任务就禁止发送。
- 结果按草稿说明已完成内容、剩余问题和证据。部分完成、发布仍在远端处理中、执行结果不确定，都不能被汇报成全部成功。

这不是承诺模型一定能解决所有问题。是否有效，要由上述行为的测试和实际草稿验收证明，不能以“已经 import Pydantic AI”代替验收。

## 2. 已确认的基线与根因

本地依赖已核对：`pydantic-ai-slim[openai]==2.22.0`、`pydantic==2.13.4`、`pydantic-graph==2.22.0`；未安装 Harness。实施先以当前版本验证，不把依赖升级与业务改造捆绑。

| 当前位置 | 已确认的行为 | 本轮处理 |
| --- | --- | --- |
| `erp_web/ai_capability_composition.py`、`erp_web/facades/global_task_facade.py` | 主对话主要使用只读工具，写操作经任务控制工具提交 | 主 Agent 按权限直接获得 focused 业务工具 |
| `erp_web/services/global_task_controller.py` | 按预先提交的 steps 顺序执行；缺资料由 Controller 暂停 | 删除固定 Agent 任务计划的执行权，让工具结果回到模型 |
| `erp_web/services/global_task_continuation_service.py` | 整个任务到终态才回到模型 | 改为原生工具级 Deferred 结果衔接 |
| `erp_web/services/ai_tool_runtime.py` | continuation 中禁止再次创建 deferred 任务 | 删除“恢复后只能收尾”的策略，允许继续原生工具循环 |
| `erp_web/services/ai_tool_bridge.py` | 所有工具 `sequential=True` | 按实际依赖约束并发，不统一串行 |
| `erp_web/services/ai_model_factory.py` | 全部模型强制 `parallel_tool_calls=False` | 移除无条件关闭，遵循 Provider 能力与原生限制 |
| `erp_web/server.py` | 单个 recovery 线程同步推进各任务及 continuation | 回收线程只领取、对账和投递；耗时执行不占住扫描入口 |
| `erp_web/services/vercel_ai_ui_service.py`、`front/src/stores/aiChat.ts` | unresolved task 导致服务端拒绝普通回合、前端禁止发送 | 替换为受控接收与原生边界消费，保留历史单写者约束 |
| `erp_web/services/capability_input_provenance.py` 及调用方 | 部分用户选择只认可当前任务步骤的提交标记 | 支持有实体范围和来源依据的既有用户事实 |

当前内部 model → tool → model 循环已经由 Pydantic AI 提供。主要问题是外部设计把业务动作放进固定步骤执行器，使主 Agent 很少有机会参与后续决策。上一轮计划还明确要求保留这种约束；因此本轮必须同时替换代码和旧的实施依据。

并发也不是一个开关的问题：模型设置、工具设置、后台执行方式和共享对象写入都要处理。既有发布队列本身已有并发能力，不需要重建。

## 3. 原生能力与项目职责

| 需求 | 优先使用的原生能力 | 项目只保留的职责 |
| --- | --- | --- |
| 连续决策与工具调用 | `Agent`、`Tool`、`FunctionToolset` | 工具目录、ERP 参数及可信依赖绑定 |
| 参数纠错与失败反馈 | Pydantic 校验、`ModelRetry`、`ToolFailed` 或类型化业务结果 | 区分业务缺口、服务故障及副作用是否已经发生 |
| 并发与用量约束 | 原生并发工具调度、`sequential`、`UsageLimits`、Agent 并发限制 | Provider 配额与实体写入互斥；不另建 Agent 调度器 |
| 人工审批 | `requires_approval` / `ApprovalRequired`、`DeferredToolResults.approvals` | 身份、权限、授权范围、业务快照、审计 |
| 外部长任务 | `ExternalToolset` / `CallDeferred`、`DeferredToolRequests`、`DeferredToolResults` | 领域 Job、工具调用关联、结果持久化及领取去重 |
| 对话与上下文投影 | 原生 `ModelMessage`、序列化及 `Hooks(model_request=...)` | 按会话保存完整历史，仅向模型发送旧工具结果摘要；`ProcessHistory` 会替换 run 历史，故不用于审计历史投影 |
| 流式展示与恢复 | Pydantic 官方 UI adapter / event encoder | HTTP、提交版本、传输、断线后读取已提交历史 |

原生工具并发、错误反馈见 [Advanced Tool Features](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/)；外部结果与审批见 [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)；历史管理见 [Messages and chat history](https://pydantic.dev/docs/ai/core-concepts/message-history/)。官方文档为滚动版本，具体接法还须通过本地 2.22.0 的契约测试。

保留一段项目适配代码前，评审必须回答：原生接口是什么、为什么仍需这段代码、它拥有的是否仅是 ERP 或存储职责、何时可以删除。不得仅以“需要灵活性”或“以后方便扩展”为理由保留第二套 Agent 基础设施。

### 3.1 保留并收窄

- `ai_agent_factory.py` 继续是唯一 Agent 装配和运行入口；`ai_model_factory.py` 保持模型创建边界。
- Catalog、声明与编译器保留业务能力清单、类型及权限元数据；优先直接使用类型化函数生成原生 Tool。若可信依赖注入仍需 `Tool.from_schema`，只保留机械适配和必要参数校验，并记录原因。
- `AiToolRuntime` 收窄为业务执行边界。原生已经负责的调用预算、参数重试及生命周期控制应移除；领域幂等不能被原生 usage 计数替代。
- focused 领域服务、平台适配、发布校验、图片处理和已有持久 Job 继续使用。`prepare_draft_for_market` 可以作为可选复合工具，但不能成为主 Agent 唯一的写入通道，也不能私自接管全局等待与恢复。
- 批次状态只描述目标草稿及业务完成情况，不保存决定下一步工具的 `current_step_index`。进度展示是投影，不拥有执行权。

### 3.2 必须删除或替换

- 删除 `GlobalTaskController` 作为 AI 固定 steps 执行器的实现，以及仅服务该实现的 step union、input owner、推进规则和测试；独立有用的幂等、审计、Job 查询先迁回领域 owner。
- 退役 `global_task_start/get/submit_input/cancel` 这组固定任务控制工具，撤掉主对话必须提交 steps 的 prompt。
- 删除只允许一个未解决任务的会话约束，以及禁止 continuation 再调用工具的特判。不能删除同一会话历史的 CAS 和单写者保护。
- 替换 `pydantic_deferred_task_link_store.py` 的“一个会话关联一个整任务”模型，按原生 `tool_call_id` 集合保存请求与领域关联；不要复制一个新的 Agent 状态机。
- 审批 UI 与补资料 UI 不再绑定旧步骤推进。同步清理 `GlobalTaskApprovalCard.vue`、`front/src/api/globalTasks.ts` 及相关状态依赖。
- 退役仅服务旧流程的 `/api/global-task-{input,approve,reject,cancel}`、`/api/v1/global-tasks/<task_id>` 和 conversation `/task-link` 契约。新入口使用原生 UI 协议和必要的领域查询，路由与 `REQUEST_CONTRACTS` 同步修改；不能把旧端点换名后继续同一状态机。
- 旧 `agent_deferred` 特判链、事件文本反向解析、provisional 修补是否仍有必要，以原生 Deferred 接法验证结果为准；优先从产生窗口的源头删除，而非再加一层协调服务。

## 4. 上下文与补资料规则

“曾经填过”不等于“所有内容永远放进模型窗口”。目标是相关事实可查到、来源可信、真正被使用。

1. 开始处理时加载目标草稿、关联商品、适用店铺及平台配置的有界摘要；缺细节时通过 focused read 按字段查询。
2. 新的明确用户要求优先于旧要求；已确认业务值优先于未经确认的提取或推测。平台事实、来源文本与用户决定要能区分。
3. 同一商品的重量等共用事实按商品复用；平台类目、站点售价、币种及授权按各自范围处理。相似商品和不同站点不能因为名称接近就共用选择。
4. 对话提供的稳定业务事实，在确定含义及实体范围后写入既有业务字段，并保存必要来源引用。当前会话加载原生历史；其他会话的相关信息通过有权限、可定位来源的查询工具取得，不导入所有历史。
5. 工具参数中的“用户说过”不构成可信证明。服务端校验引用是否来自真实用户消息、已保存的用户字段或有效授权，不能让模型自行设置 `user_supplied=true`。
6. 普通缺字段先作为类型化业务结果回到 Agent，包含实体、字段、约束及已有值来源。Agent 查询可用事实，继续独立工作后再询问剩余问题；不能把普通资料缺口一律升级成阻塞整个 Agent 的 Deferred。
7. 一个答案可应用于范围明确的多个草稿，并分别持久化；用户在其他页面更新资料后，下次执行重新读取当前业务版本，不沿用过期缺口快照。
8. 模型输入投影不能覆盖完整持久历史。补齐现有上下文修复计划尚未完成的 canonical history 保留要求，使用原生消息与 `new_messages()` 验证追加边界。

不新增通用 Memory 引擎、向量库或自研 Conversation 协议。若现有业务字段或来源信息确实不足，新增最小领域结构，并先写清读写 owner。

## 5. 并发、审批、长任务与用户纠正

### 5.1 并发边界

- 同一次模型响应中的独立工具由 Pydantic 调度。同步工具使用框架支持的线程执行方式；不能在 async 包装里直接执行阻塞业务代码。
- 不同草稿可并发；同一草稿及共享商品聚合上的冲突写入按实际资源保护。跨资源操作必须有固定加锁顺序，锁不跨模型思考或人工等待。
- 先检查 Runtime 的可变去重缓存、数据库连接线程归属、上下文注入和 `ProductStore` 更新语义。不能只改两处并发设置就宣称完成。
- 依赖前一步结果的操作保持依赖顺序；发布等外部副作用继续使用领域幂等与远端对账。调用超时不表示远端没有执行。
- 重新核定当前 12 次工具调用等小任务预算，以 15 条草稿场景验证可完成性；保留有界预算，并在耗尽时明确已完成与未完成内容，不能无限重开 run 绕过限制。

### 5.2 原生 Deferred 接法

长任务按业务工具关联原生 Deferred，不再把“未来所有步骤”包装成一个等待对象。优先验证 `ExternalToolset`：在取得原生请求后，原子保存请求、history 与 Job 投递记录，再执行领域任务；若具体工具需 `CallDeferred`，记录不适用 ExternalToolset 的原因。

同进程立即可解决的 Deferred 可使用原生 `HandleDeferredToolCalls`；跨请求、人工或持久 Job 等待使用原生请求/结果恢复。一次可以有多个 tool call，适配层必须保持各自 ID、参数、权限与结果对应关系。

需要特别验证：部分结果返回时框架是否要求其余结果齐备，以及用户新消息与 Deferred 结果的合法组合。**Deferred 暂停期间不能宣称同一 Agent 仍在思考。**普通草稿缺资料使用前述业务结果反馈，已启动的独立 Job 继续执行；需要人工的发布审批应在可独立完成的准备工作之后收集。若原生暂停粒度仍无法满足实际独立工作场景，先记录具体失败用例并评估原生任务组合方式，不能悄悄恢复固定步骤执行器。

### 5.3 用户输入与审批

普通资料补充通过正常用户回合进入 Agent。运行期间到达的消息由传输层有序接收，在可用的原生 run/tool 边界消费，界面显示“已收到，等待当前操作结束后应用”；暂停 run 的恢复使用受支持的 `user_prompt` 与 `DeferredToolResults` 组合。不能并发写同一条历史，也不能把用户输入伪造成工具成功结果。

审批交给原生 approval 表达和恢复；服务端继续核验原始调用、操作者、批准范围及业务快照。参数被修改、快照失效时重新验证授权，模型无权自行批准。取消后不再开始新副作用，已经提交的平台 Job 按真实取消或对账能力反馈，不能把“停止等待”汇报成“撤销发布”。

### 5.4 持久化与故障边界

保留必要的事务、CAS、领域 Job 领取及幂等记录：history 未持久化时不执行对应的外部投递；结果与 history 提交成功后才确认恢复完成；重复投递可对账。

本轮保证已持久化消息和 Deferred/领域 Job 边界的恢复。Pydantic core 不自动保存任意进程内执行位置；普通工具发生副作用后崩溃，要靠业务回执与幂等对账，不能盲目重放。若必须保证更细粒度的崩溃恢复，先评估官方 [Durable Execution 集成](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)，不得自研 checkpoint Agent loop；本计划不默认新增运行平台依赖。

实时流使用官方编码，恢复以已提交的原生历史为事实源。先核对前端实际消费：如果后台只需要版本变化通知，删除无人消费的事件 chunk 持久化；不能继续维护两套恢复事实源。旧验收报告中的缓冲溢出、终止帧与任务切换问题要用当前代码复验，不沿用旧报告的通过或失败结论。

## 6. 实施顺序与每阶段出口

| 阶段 | 工作 | 进入下一阶段前必须得到的证据 |
| --- | --- | --- |
| 0：锁定边界 | 当前依赖核验、调用方/持久数据清单、退役清单、验收用例 | 每项自研保留都有理由；辨明哪些存量数据与外部契约真实存在 |
| 1：原生最小闭环 | 用 `FunctionModel` 和隔离测试工具验证动态下一步、并发、多个 Deferred、原生审批、用户追加与历史追加 | 运行中的原生 API 证明上述契约；不调用真实发布或收费模型 |
| 2：替换主链路 | 主 Agent 直接绑定领域工具；接入错误反馈、来源读取、原生 approval/Job adapter；同步修改会话 UI 和草稿箱批量入口 | 两个用户入口复用同一链路；真实领域服务配合可控模型完成草稿准备，工具结果确实回到主 Agent；旧执行器退出生产入口 |
| 3：并发与可靠性 | 资源写入保护、持久化提交边界、后台投递、用户纠正、断线/重启 | 多草稿并发有实际时间区间重叠；无丢字段、重复副作用或历史错序 |
| 4：删除与交付 | 删除旧路径和过期测试，更新文档、契约和架构守卫，完成全量及产品验收 | 只有一条生产路径；全部验收条目有记录，未完成项不能标记完成 |

各阶段是开发顺序，不是上线双轨。使用同一重构分支逐段迁移，最终不保留 feature flag、shadow、备用 Controller 或运行时旧流程 fallback。

实施前已清点工作库：6 个商品、15 条草稿、194 份原生历史、22 个旧任务和 22 个旧关联记录。用户明确授权“数据都可以删除，不需要兼容”，本次停止旧服务后直接重建工作库为 schema 15，不保留旧迁移或兼容执行路径。新链路仍不得伪造成功或盲目重发未知平台操作；实际处置和验证见交付记录。

## 7. 验收清单

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 工具报告缺字段，资料在商品或历史中 | 模型先读取，再带可信来源继续；没有重复向用户索取 |
| A2 | 首次选择失败，但有可用替代工具 | 后续工具由模型根据结果选择，不依赖预写 steps；有界重试后仍可解释失败 |
| A3 | 15 条草稿、至少两条独立可执行、一条确实缺资料 | 草稿箱选择与全局对话均能发起；不处理未选草稿；独立工具执行时间重叠；其余可处理草稿完成；只汇总真实剩余缺口 |
| A4 | 共用商品事实与不同站点设置混合 | 共用事实正确复用，站点售价/币种/销售目标不串用，已保存选择不被无依据覆盖 |
| A5 | 用户通过对话补充，或在页面更新后要求继续 | 使用最新值继续原目标，不要求回到旧任务卡再填一次 |
| A6 | 单 run 多个 Deferred 和混合批准/拒绝 | ID 与结果一一对应，拒绝操作不执行，无重复 Job；限制与等待语义如实展示 |
| A7 | Deferred 完成后还需要其他工具 | 原生 Agent 能继续处理，不被强迫立即生成最终回复 |
| A8 | 运行期间追加纠正或取消 | 消息可提交并按序生效，后续动作遵守新范围；进行中的不可撤销操作如实反馈 |
| A9 | 并发操作同草稿或同商品 | 无丢失更新、空默认值覆盖或死锁；不同对象不被全局锁串行化 |
| A10 | 模型/网络异常、写后编码错误、远端结果未知 | 不误报缺用户资料，不把已经写入视为未执行，不盲目重发副作用 |
| A11 | 首次提交前、工具副作用后、结果提交前后崩溃 | 无孤立授权和重复发布；可从持久边界恢复或明确标记待对账 |
| A12 | 多轮流、慢客户端、断线重连、历史投影 | 原生协议可消费，终止语义正确，已提交消息可恢复；完整历史不被裁剪结果覆盖 |
| A13 | 权限与预算 | 客户端不能伪造历史、授权或资料来源；配额耗尽有明确结果且不循环重开 run |
| A14 | 删除验收 | 固定步骤执行器、退役入口/配置/prompt/测试不再存在于生产路径，无旧流程 fallback |

先用可控模型测试工具闭环和故障边界，再用真实模型在隔离草稿上验证选择工具与提问质量。可控模型测试不能证明真实模型足够聪明；真实模型演示也不能替代并发和幂等测试。发布验证使用平台 mock 或测试环境，不为验收向真实店铺发布商品。

代码改动后执行：

```bash
.venv/bin/python -m compileall -q erp_web
.venv/bin/python -m pytest tests/test_ai_context_architecture.py -q
.venv/bin/python -m pytest tests -q
pnpm --dir front test:run
pnpm --dir front typecheck
pnpm --dir front types:check
pnpm --dir front build
```

架构守卫验证唯一 Agent 入口、领域边界和退役实现缺席；保留部分补丁、写回执、权限及幂等测试。删除只要求严格 steps 顺序、第二个 Deferred 必须拒绝、任务期间必须封锁会话的旧断言。

## 8. 文档与交付要求

实现时同步更新 `docs/ai-context-map.md`、`AGENTS.md` 的实际 owner、请求/响应 Schema、工具覆盖清单及前端类型。本文待实施期间不把当前架构地图提前写成目标架构。

下列文件中已被本文替代的方案，在实现完成后删除过期设计正文，历史过程由版本控制保留；仍有效的领域和可靠性要求迁入当前文档：

- `docs/global-ai-capability-migration-plan.md` 中固定 Global Task 步骤设计。
- `docs/pydantic-ai-global-task-deferred-migration-plan.md` 中整任务 Deferred、单调用限制和会话封锁设计。
- `docs/pydantic-ai-global-task-deferred-architecture-simplification-review.md` 中以保留固定 Controller 为前提的候选方案。
- `docs/ai-tool-context-boundary-repair-plan.md`、`docs/global-task-execution-progress-visibility-plan.md` 中旧步骤输入和进度投影绑定。

交付报告必须列出原生能力接法、删除项、仍保留的适配代码及理由、数据处置、A1–A14 证据和实际失败项。改动规模按覆盖边界和验收结果判断，不以减少行数或测试总数宣称完成。
