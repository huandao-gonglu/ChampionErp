# 全局 Agent 已实施方案（单用户本地版）

> 状态：已实施（2026-08-13）
>
> 适用场景：单用户、本地运行，只操作当前应用中已经配置的自己的店铺。
>
> 前置条件：现有 AiAgentFactory、AiToolRuntime、显式 ToolSet、focused Capability 和
> PublishingBus 保持为唯一生产路径。
>
> 实施说明：本文保留目标设计基线，当前真实入口见
> [AI Context Map](./ai-context-map.md)。实施时发现的设计缺口、架构更正、替代方案与影响记录在
> [nextStateDoingChange.md](../nextStateDoingChange.md)，不得通过改写本文掩盖设计与实现的差异。

## 1. 结论

本阶段采用轻量顺序任务流：

~~~text
主 Agent 理解目标并制定顺序计划
Controller 保存计划并逐步推进
Controller 直接调用普通业务 Capability
缺资料时暂停并向用户提问
发布前执行确定性校验并请求一次人工确认
确认后幂等提交 PublishingBus
根据平台真实终态判断任务成功或失败
~~~

系统仍然只有一个主 Agent。大部分功能是普通 Python Capability，不创建独立 Agent。只有类目匹配、
属性枚举搜索等确实需要多轮工具推理的局部能力，才复用现有 focused Agent。

本阶段不建设面向云端、多用户或分布式 worker 的通用任务平台。

## 2. 场景假设

当前产品按以下事实设计：

- 只有一个本地用户；
- 用户只能操作应用中自己配置的店铺；
- 后端以单进程为主，任务按顺序推进；
- 同一任务同一时间只执行一个步骤；
- 不存在多个租户互相隔离的问题；
- 不存在多个 worker 同时抢占同一任务的问题；
- 发布是主要高风险动作，发布前只需要一次明确人工确认；
- 进程重启后需要能看到未完成任务，并继续读取 PublishingBus 的真实状态。

现有 AiExecutionContext 仍可使用固定 tenant_id=local、actor_id=local-user 和固定的本地权限集合，
以兼容 AiToolRuntime，但不围绕这些字段建设新的租户、角色或 Policy 系统。

## 3. 明确推迟的能力

以下能力本阶段不实现：

- 多租户权限模型和角色授权中心；
- 动态 Policy Resolver；
- CAS、resource revision 和资源冲突自动合并；
- lease、多 worker claim 和抢占恢复；
- 完整业务审批记录与审批中心；
- outbox 和独立事件投影 worker；
- 通用 Postcondition Registry；
- 通用父子 Agent 预算分配协议；
- 通用 operation/job reconcile 框架；
- 运行中动态检索和挂载任意 Capability；
- 为未来云端部署预留 feature flag、双路径或兼容层。

如果产品以后变为多人协作或远程服务，再根据真实需求单独设计这些能力，不在本阶段提前实现。

## 4. 分层架构

~~~text
页面 / API（/aiWork 全局 Agent 对话）
  → GlobalAgentFacade
      → GlobalTaskController
          ├─ LocalGlobalTaskStore
          ├─ GlobalAgentService
          │   → AiAgentFactory
          │       → Pydantic Agent
          │           → Tool Bridge
          │               → AiToolRuntime
          │                   → 主 Agent 的有限只读 AiToolSet
          ├─ 普通业务 Capability
          │   ├─ 确定性读取 / 修改 / 校验
          │   ├─ 发布提交
          │   └─ 必要时复用 focused Agent
          └─ PublishingBus
              → 平台 adapter / workflow
              → 持久化发布终态
~~~

依赖方向必须保持：

- HTTP route 只依赖 facade/service，不直接 import runtime unit；
- GlobalAgentFacade 负责 HTTP 输入输出映射；
- GlobalTaskController 是顺序任务状态 owner；
- GlobalAgentService 负责主 Agent 的 prompt、Execution Profile、有限只读 ToolSet 和输出校验；
- AiAgentFactory 继续是唯一 Pydantic Agent 装配、运行和恢复入口；
- 所有 Agent Tool 调用继续经过 Tool Bridge 和 AiToolRuntime；
- Controller 直接调用稳定业务 Capability，不调用 Tool executor；
- Capability 的真实业务行为位于 focused service、facade 或 runtime unit；
- PublishingBus 继续拥有发布 job 和平台终态，Global Task 只保存 job_id 引用。

不得新增第二套 Agent loop、第二套 Tool Runtime 或新的 runtime 聚合模块。

## 5. 各组件职责

### 5.1 主 Agent

主 Agent 负责：

- 理解用户目标；
- 生成有限的顺序计划；
- 在目标或用户表述模糊时使用有限只读 Tool 获取必要上下文；
- 计划无法继续、用户改变目标或 Capability 返回无法机械处理的歧义时重新规划；
- 生成清晰的澄清问题和面向用户的计划说明。

主 Agent 不负责：

- 直接读写 Store 或数据库；
- 自己判断是否已经真实发布成功；
- 绕过 AiToolRuntime 调用 executor；
- 执行确定性业务步骤；
- 自己批准发布；
- 创建任意 Tool 名称或动态扩展 ToolSet；
- 管理多 worker、lease、revision 或分布式恢复。

### 5.2 GlobalTaskController

Controller 负责：

- 创建任务并保存用户目标；
- 保存主 Agent 提出的顺序计划；
- 维护当前步骤下标；
- 按计划直接调用普通业务 Capability；
- 根据 CapabilityResult 更新步骤状态；
- 在 needs_input 时暂停；
- 输入明确时直接继续对应 Capability，输入仍然模糊时再调用主 Agent 澄清或重新规划；
- 在发布校验通过后进入 waiting_publish_confirmation；
- 用户确认后直接调用发布 Capability；
- 为发布提交生成稳定 idempotency_key；
- 保存 PublishingBus job_id；
- 读取 PublishingBus 的真实终态并结束任务。

Controller 不实现 model → tool → model 循环，也不直接执行 Tool executor。它调用的是公开业务
Capability，与页面或其他业务工作流调用 service/facade 的方式相同。

### 5.3 普通 Capability

大部分功能实现为普通 Capability：

- drafts.query；
- draft.prepare_for_market；
- product.read；
- product.attributes.update；
- product.images.prepare；
- product.publish.validate；
- product.publish.request；
- 其他确定性读取、转换和草稿修改。

Capability 可以被页面、Controller 或 AI Tool adapter 复用。业务逻辑只有一个 owner，AI adapter
只负责类型转换、可信上下文注入和结果序列化。Controller 的直接调用不经过 AiToolRuntime，因为
它不是 Agent Tool 调用；但 Capability 自身仍必须执行领域校验，并且不得暴露 Store 原语。

### 5.4 focused Agent

只有确实需要多轮模型推理和工具调用时才复用 focused Agent，例如：

- category.match；
- product.attributes.fill 中的平台枚举搜索。

Controller 仍只调用对应业务 Capability，由 Capability 内部决定是否复用 focused Agent。
focused Agent 继续通过现有 AiAgentFactory 和自己的固定 Execution Profile 运行，其 Tool 调用
继续经过 Tool Bridge 和 AiToolRuntime。它不能反向调用主 Agent，也不建立通用父子预算协议。
每个 focused Agent 只使用自己的固定 deadline、model limit 和 Tool limit。

### 5.5 /aiWork 对话入口

/aiWork 当前主要用于查看 AI 执行记录。第一版在同一页面增加“新建对话”按钮和全局 Agent 对话窗体，
不另建页面，也不删除现有请求、结果、事件和原始 JSONL 查看能力。

左侧一级列表默认只展示没有父会话的用户对话。planning 与 focused Agent 的独立执行 conversation
仍完整保留，但通过持久化 parent 引用收纳到所属主对话的“执行详情”，默认折叠；开发者可以显式开启
“显示内部执行会话”。子会话仍支持 ID 直达、请求/结果/事件和原始 JSONL 查看，不参与默认一级列表排序。

对话窗体负责：

- 展示用户消息、主 Agent 答复、计划摘要和步骤状态；
- 展示 needs_input 所需字段，并提交补充资料；
- 在发布前展示确定性摘要和明确的“确认发布”按钮；
- 展示 PublishingBus 提交状态和平台真实终态；
- 在同一对话内继续提交下一条目标，并自动携带最近草稿查询快照。

AI Work journal 只保存有界的对话展示和执行链接，不是任务业务状态 owner。LocalGlobalTaskStore 仍是
任务、暂停、确认和发布引用的唯一 owner；AI Work 投影写入失败不能把已成功的业务步骤改为失败。

## 6. 顺序执行流程

### 6.1 先规划，再执行

Planning run 只暴露少量只读 Tool，例如商品读取和当前草稿状态读取。主 Agent 输出顺序计划：

~~~python
class GlobalTaskStepProposal(BaseModel):
    local_key: str
    capability: str
    objective: str


class GlobalTaskPlanProposal(BaseModel):
    steps: list[GlobalTaskStepProposal] = Field(max_length=12)


class GlobalPlanningDecision(BaseModel):
    action: Literal["plan", "answer", "ask_user"]
    plan: GlobalTaskPlanProposal | None = None
    query_snapshot_id: str = ""
    answer: str = ""
    question: str = ""
    explanation: str = ""
~~~

action=plan 时必须有 plan；action=ask_user 时必须有具体 question；action=answer 只允许用于已注册的
只读查询结果，并必须引用 query_snapshot_id。最终数量、顺序和草稿 ID 从结构化查询快照读取，
不得相信模型在 answer 中自行填写的数字。重新规划继续使用同一个输出类型，不新增 Planner
Runtime。

Controller 必须校验：

- capability 在当前静态 allowlist 中；
- 步骤数量不超过上限；
- 发布校验位于发布请求之前；
- 发布请求最多一个；
- 不包含任意函数路径、HTTP 地址或模型临时创造的 Tool 名称。

校验通过后，Controller 为步骤生成正式 step_id 并保存计划。

### 6.2 Controller 顺序执行 Capability

计划保存后，Controller 从当前 pending 步骤开始顺序执行：

1. 从静态 capability map 找到公开业务 Capability；
2. 构造当前商品、平台、店铺配置和用户已提交资料；
3. 直接调用 Capability；
4. 根据 CapabilityResult 保存 completed、needs_input、in_progress 或 failed；
5. completed 时推进下一步，needs_input 时立即暂停；
6. 只有目标变化、计划不可执行或输入仍有歧义时，才再次调用主 Agent。

确定性读取、字段覆盖、图片准备、发布校验和发布提交都不启动额外主 Agent run。Controller 不按
字符串动态 import，也不直接取得 AiToolBinding.executor；静态 capability map 绑定的是公开
service/facade 函数。

### 6.3 缺资料时暂停

Capability 使用统一结果返回缺失字段：

~~~python
class RequiredInput(BaseModel):
    key: str
    label: str
    reason: str
    options: list[str] = Field(default_factory=list)


class CapabilityError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class CapabilityResult(BaseModel, Generic[TResult]):
    status: Literal["completed", "needs_input", "in_progress", "failed"]
    summary: str
    result: TResult | None = None
    required_inputs: list[RequiredInput] = Field(default_factory=list)
    job_id: str | None = None
    error: CapabilityError | None = None
~~~

约束：

- completed 必须有可验证 result；
- needs_input 必须有明确字段，不能只返回“资料不足”；
- in_progress 目前只用于已经取得 PublishingBus job_id 的发布步骤；
- failed 必须有稳定错误码；
- 非当前状态对应的字段必须为空；
- 原子只读 Tool 可以继续返回自己的紧凑类型。

Controller 收到 needs_input 后保存问题并停止推进。用户提交字段化输入后，同一步骤重新变为
pending：输入明确时直接继续 Capability，输入仍有歧义时再调用主 Agent 澄清或重新规划。

## 7. 简单持久化任务状态

本阶段只需要一个简单的本地任务状态：

~~~python
class LocalTaskStep(BaseModel):
    step_id: str
    capability: str
    objective: str
    status: Literal["pending", "running", "needs_input", "completed", "failed"]
    result_summary: str = ""
    result_ref: str = ""
    error_code: str = ""


class PublishConfirmation(BaseModel):
    status: Literal["none", "pending", "confirmed"] = "none"
    validation_digest: str = ""
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    confirmed_at: datetime | None = None


class LocalGlobalTaskState(BaseModel):
    schema_version: Literal[1] = 1
    task_id: str
    task_kind: str
    goal: str
    product_id: str
    platform: str
    status: Literal[
        "planning",
        "running",
        "needs_input",
        "waiting_publish_confirmation",
        "waiting_publish_result",
        "completed",
        "failed",
        "cancelled",
    ]
    steps: list[LocalTaskStep]
    current_step_index: int
    pending_inputs: list[RequiredInput] = Field(default_factory=list)
    publish_confirmation: PublishConfirmation = Field(default_factory=PublishConfirmation)
    publish_idempotency_key: str = ""
    publish_job_id: str = ""
    draft_query_snapshot_id: str = ""
    ai_work_conversation_id: str = ""
    agent_execution_conversation_ids: list[str] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    created_at: datetime
    updated_at: datetime
~~~

LocalGlobalTaskStore 是该状态的唯一 owner。它可以使用项目现有 SQLite 基础设施，以 task_id 为主键
原子保存整条状态；同时保存草稿查询的轻量 DraftQuerySnapshot，供后续“第一个”“第二个”等指代
稳定解析。任务只记录最近一次 snapshot_id，不复制完整草稿数据。

ai_work_conversation_id 表示用户在 /aiWork 中看到的稳定对话；每次主 Agent 或 focused Agent run 仍由
AiAgentFactory 创建自己的执行记录，并把 ID 追加到 agent_execution_conversation_ids。不得为了让页面
看起来像一条对话而修改 AiAgentFactory，使多个 Agent run 共用同一个 Provider conversation_id。

本阶段不实现：

- revision/CAS；
- lease；
- claim；
- 多 worker 并发推进；
- 通用 operation 表；
- outbox。

单进程内由 Controller 的任务级锁避免同一 task_id 被同时推进。进程重启后读取未完成任务即可，
不自动重放已经保存为 completed 的步骤。

AI Work 继续保存会话和有界事件，写入失败不改变 LocalGlobalTaskStore 的业务状态，不增加 outbox。

## 8. 有限 ToolSet

有限 ToolSet 只用于真正的 Agent run，不用于 Controller 调用普通 Capability。

~~~text
global.task.plan
    - drafts_query
    - product_read
    - product_publish_validation_read

category.product_match
    → category.search
        - search_categories 或 browse_categories

category.attribute_fill
    → category.attribute_values
        - category_attribute_values_search
~~~

规则：

- 主 Agent 的 Planning ToolSet 只有少量只读和 discovery 能力；
- focused Agent 继续使用各自已有的固定 ToolSet；
- 未进入本次 ToolSet 的能力对模型不可见，也不能按名称执行；
- 不向模型暴露 Store、凭据、任意 HTTP、SQL、shell 或文件系统；
- 店铺、平台、商品和草稿从本地配置与可信 Binding Scope 注入，不由模型提交；
- 所有 Agent Tool 调用继续由 AiToolRuntime 执行 schema、固定本地权限、预算和审计检查；
- product.attributes.update、product.images.prepare、product.publish.validate 和
  product.publish.request 是 Controller 调用的普通 Capability，不为了形式统一注册进主 Agent
  ToolSet。

## 9. 写操作幂等

普通字段更新、草稿覆盖、图片选择结果保存等操作优先设计为天然可重复执行：

- 使用“把字段设置为目标值”，而不是“在旧值上追加一次”；
- 使用稳定资源 ID 覆盖相同记录；
- 重复执行得到相同业务状态；
- Controller 已记录为 completed 的步骤不主动重跑。

普通字段和草稿写入不保存额外幂等记录，也不新增通用幂等记录表。

只有以下操作需要持久化幂等记录：

- 发布或其他外部平台提交；
- 创建型操作；
- 产生费用的操作；
- 具有不可逆外部副作用且无法天然重复执行的操作。

本方案第一版只明确要求发布提交使用稳定键：

~~~text
global_task_id + publish_step_id
~~~

PublishingJobStore 持久化 idempotency_key → job_id。重试同一发布步骤时返回原 job，不重复提交；
相同键对应不同商品、平台或目标时返回稳定冲突错误。

## 10. 确定性发布校验

product.publish.validate 复用现有 publish_helpers.py、publish_validation.py 和平台 payload builder，
至少检查：

- 店铺凭据和目标站点可用；
- 类目与平台类目 ID 有效；
- 必填属性完整，枚举 ID 真实；
- 标题、描述、库存、价格和币种有效；
- 图片数量和图片交付地址有效；
- 当前草稿能生成确定性发布 payload；
- Ozon 等异步平台需要的目标字段完整。

校验结果必须包含：

- 面向用户的发布摘要；
- 结构化 validation errors；
- 规范化发布 payload 的 validation_digest。

模型不能覆盖校验结果。存在任何硬错误时不得进入发布确认。

## 11. 发布前一次人工确认

人工确认由 LocalGlobalTaskState 持久化，不保留一个等待用户的 Agent run：

1. product.publish.validate 通过后，Controller 保存发布摘要和 validation_digest；
2. Controller 将任务设为 waiting_publish_confirmation；
3. 页面展示平台、商品、店铺、价格、库存和发布摘要；
4. 用户点击“确认发布”或取消整个任务；
5. 用户取消任务时进入 cancelled，不执行发布；
6. 用户确认时 Controller 保存 confirmed、validation_digest 和 confirmed_at；
7. Controller 直接调用 product.publish.request Capability，并传入已保存的确认上下文和稳定
   idempotency_key；
8. 发布 Capability 在 enqueue 前重新运行确定性校验并比较 digest；
9. digest 一致才提交 PublishingBus。

~~~python
class PublishConfirmationContext(BaseModel):
    task_id: str
    step_id: str
    validation_digest: str
    confirmed_at: datetime
~~~

PublishConfirmationContext 由 Controller 从 LocalGlobalTaskState 构造，不进入模型输入，也不作为
AI Tool 参数。product.publish.request 是普通业务 Capability；缺少 confirmed 状态、digest 或稳定
idempotency_key 时必须拒绝提交。

人工确认是本地任务状态的一次明确转换，不需要额外 Agent 调用或审批系统。

确认只对当前 validation_digest 有效。如果商品或发布 payload 已改变，返回
PUBLISH_CONFIRMATION_STALE，任务重新进入校验和确认，不能沿用旧确认。

## 12. PublishingBus 终态跟踪

PublishingBus 继续负责：

- 持久化发布 job；
- 进程启动时恢复 pending job；
- 调用平台 adapter/workflow；
- 保存每个平台的执行状态和错误；
- 对 Ozon 等异步接口等待真实平台终态；
- 只有获得可验证远端成功证据后才记录成功。

Global Task 只保存 publish_job_id，不复制完整 PublishingBus 状态。

### 12.1 必须补充的最小幂等能力

当前 PublishingBus.enqueue 每次调用都会生成新 job。接入主 Agent 前需要增加可信
idempotency_key：

~~~python
PublishingBus.enqueue(
    product,
    platforms,
    targets=targets,
    idempotency_key=idempotency_key,
)
~~~

PublishingJobStore 对 idempotency_key 建立持久化唯一约束：

- 第一次调用创建 job；
- 相同键重复调用返回原 job_id；
- 不重新 submit；
- key 与商品、平台或目标不一致时返回稳定冲突错误。

这只解决发布重复提交，不建立通用 operation 框架。

### 12.2 任务如何等待发布

发布 Capability 成功 enqueue 后返回：

~~~python
CapabilityResult(
    status="in_progress",
    job_id=job_id,
    summary="发布任务已提交，正在等待平台结果。",
)
~~~

Controller 保存 job_id 并进入 waiting_publish_result。之后：

- 页面以有限频率调用 /api/global-task-state；
- Controller 处理状态请求时调用 PublishingBus.get_status(job_id) 刷新；
- 进程重启后，PublishingBus 先恢复 pending job，Controller 再读取未完成任务引用的 job_id；
- 不让模型轮询 job；
- 不实现通用 reconcile worker。

只有所有目标平台都持久化为真实成功终态时，发布步骤和整体任务才 completed。任一平台终态失败，
任务进入 failed，并展示 PublishingBus 已保存的安全错误。

## 13. 典型流程

用户在 /aiWork 点击“新建对话”，输入：“把这个商品资料补全并发布到 Ozon。”

~~~text
1. facade 创建 AI Work 对话投影和 Global Task
2. 主 Agent 读取商品并生成顺序计划
3. Controller 保存计划
4. category.match
   → completed
5. product.attributes.fill
   → needs_input(battery_type)
6. Controller 暂停并在对话窗体询问用户
7. 用户填写 battery_type
8. Controller 继续同一步骤
   → completed
9. product.images.prepare
   → completed
10. product.publish.validate
   → completed + validation_digest
11. Controller 暂停并在对话窗体展示发布摘要
12. 用户点击确认发布一次
13. Controller 直接调用发布 Capability，重新校验 digest
14. PublishingBus 以稳定幂等键 enqueue
    → waiting_publish_result
15. PublishingBus 获得 Ozon 真实 imported 终态
16. Controller 读取终态并投影到对话窗体
    → task completed
~~~

拿到 Ozon task_id 或 PublishingBus job_id 只表示已经提交，不表示发布成功。

## 14. HTTP 边界

首版提供简单端点：

| 行为 | 建议端点 | 主要字段 |
|---|---|---|
| 创建任务或同一对话的新目标 | POST /api/global-task-start | goal、可选 ai_work_conversation_id、task_kind、product_id、platform、可选 draft_query_snapshot_id |
| 读取任务 | POST /api/global-task-state | task_id |
| 提交缺失资料 | POST /api/global-task-input | task_id、message、可选字段化输入 |
| 确认发布 | POST /api/global-task-publish-confirm | task_id |
| 取消任务 | POST /api/global-task-cancel | task_id |

页面需要跟踪执行进度时，以有限频率调用 /api/global-task-state。
页面提交同一会话的后续目标时，可自动携带最近 draft_query_snapshot_id；用户不需要感知该字段。

首次发送消息时不传 ai_work_conversation_id，facade 创建对话并返回 ai_work_conversation_id 和 task_id。
当前任务终态后，用户继续发送新目标时复用同一个 ai_work_conversation_id，并创建新的 task_id。一个
对话同一时间只允许一个非终态任务；用户可以等待或取消，不能并行启动第二个任务。

不传 tenant、scope、expected revision 或 approval record。

仍必须遵守现有 HTTP 架构：

- route unit 位于 erp_web/http_route_units/；
- 定义 HANDLED_PATHS 和显式 handler map；
- route 只依赖 facade/service；
- 所有 read_body() 通过 validate_request_payload(..., endpoint=handler.path)；
- 路由键与 erp_web/schemas/requests.py::REQUEST_CONTRACTS 同步；
- 请求和响应 shape 位于 erp_web/schemas/。

### 14.1 /aiWork 页面交互

左侧栏顶部增加“新建对话”。点击后只在前端打开空白对话，用户发送第一条消息时才调用
/api/global-task-start，避免产生空记录。

全局 Agent 对话使用以下简单交互：

~~~text
用户消息
→ POST /api/global-task-start 或 /api/global-task-input
→ 页面立即显示提交中状态
→ 有活动任务时有限频率查询 /api/global-task-state
→ 根据状态追加 Agent 答复、计划、缺失资料卡片或发布确认卡片
→ 任务终态后恢复输入框，可在同一对话开始下一个目标
~~~

页面规则：

- 普通新目标调用 global-task-start；只有当前任务 needs_input 时调用 global-task-input；
- 明确字段可以直接提交，纯文本仍有歧义时才由主 Agent 处理；
- waiting_publish_confirmation 必须显示独立确认按钮，输入“确认”或“发布”不能代替接口确认；
- waiting_publish_result 只展示状态并查询，不允许重复提交发布；
- 页面刷新后从 AI Work 对话事件恢复最近 task_id，再读取 LocalGlobalTaskState；
- 左侧默认只列根会话；主对话的执行详情汇总并列出关联子 Agent，内部记录仍可只读打开；
- 只有 use_case_id=global.agent.chat 的记录显示输入框；
- 任务业务状态只查询 /api/global-task-state，不新增 /api/global-task-wait；
- 现有 AI Work events 接口只用于展示对话投影和 Agent 执行链接，不判断发布成功。

对话投影使用少量稳定事件，例如 global.user_message、global.assistant_message、
global.task_state 和 global.agent_execution_link。事件只保存展示所需摘要、task_id 和引用，不复制完整
商品、草稿、Tool 输出或发布 payload。

## 15. 目标文件边界

- erp_web/schemas/global_tasks.py：轻量任务、步骤、输入、确认、草稿查询快照和 CapabilityResult；
- erp_web/stores/global_task_store.py：本地任务、步骤状态和轻量草稿查询快照；
- erp_web/services/global_task_controller.py：顺序推进、暂停、恢复和 PublishingBus 状态刷新；
- erp_web/services/global_agent_service.py：主 Agent planning profile、prompt、有限只读 ToolSet 和
  output validator；
- erp_web/facades/global_agent_facade.py：HTTP 门面和静态业务 Capability composition root；
- erp_web/http_route_units/global_agent_routes.py：薄路由；
- erp_web/services/ai_work_service.py：复用现有 journal 保存有界全局对话投影，不拥有任务状态；
- front/src/views/AiWorkView.vue：增加“新建对话”入口并组合全局 Agent 对话组件；
- front/src/components/ai-work/GlobalAgentChatPanel.vue：消息、输入、缺资料和发布确认交互；
- front/src/api/globalTasks.ts、front/src/types/globalTasks.ts：任务端点和前端类型；
- focused capability/service/runtime unit：真实领域行为；
- erp_web/services/ai_agent_factory.py：唯一 Agent 装配和运行入口；
- erp_web/services/ai_tool_runtime.py：唯一 Tool 安全执行边界；
- erp_web/runtime_units/publishing_bus_core.py：发布 job 和平台终态。

不得创建 erp_web/runtime.py、全局 runtime 聚合器、第二个 ProductStore owner 或第二个
PublishingBus 状态库。

## 16. 现有业务能力接入与串联

本阶段不是只建设任务框架。LocalGlobalTaskStore、Controller 和主 Agent 完成后，必须把现有业务
能力真正接入静态 capability map，并跑通完整商品发布流程，才算本方案落地。

### 16.1 草稿完整纵向试点（优先完成）

第一版先把草稿做成完整纵向试点，而不是只验证任务框架。试点从自然语言查询开始，覆盖稳定指代、
目标市场准备、缺资料暂停，以及用户明确要求时继续进入发布终态。它围绕草稿建立两个稳定领域能力：

~~~text
drafts.query                 # 通用只读查询；同时暴露为 @ai_tool drafts_query
draft.prepare_for_market     # 高层业务 Capability；仅由 Controller 调用
~~~

drafts.query 不是“查询草稿数量”的一次性函数，而是受限、类型化的草稿查询能力：

~~~python
class DraftQueryRequest(BaseModel):
    scope: Literal["active", "published", "all"] = "active"
    platform: str = ""
    status: str = ""
    keyword: str = ""
    view: Literal["summary", "workflow", "publish_readiness", "detail"] = "summary"
    sort: Literal["created_desc", "created_asc", "title_asc"] = "created_desc"
    limit: int = Field(default=50, ge=1, le=100)
    snapshot_id: str = ""
    positions: list[int] = Field(default_factory=list, max_length=10)


class DraftQueryResult(BaseModel):
    total: int
    items: list[DraftSummary]
    count_by_platform: dict[str, int]
    count_by_status: dict[str, int]
    snapshot_id: str
    selected_items: list[DraftSummary] = Field(default_factory=list)
~~~

它必须支持草稿总数、过滤、稳定排序、工作流/发布准备摘要，以及用查询快照解析“第一个”“第二个”。
LocalGlobalTaskStore 保存轻量 DraftQuerySnapshot，只包含 snapshot_id、有序 draft_id 列表、查询条件
和时间，不复制完整草稿。保存查询快照不修改商品或草稿业务数据。

drafts.query 的类型化领域函数可以被 Controller 直接调用；同时用 @ai_tool 声明只读工具
drafts_query，进入主 Agent 的 planning ToolSet。Catalog 仍使用显式函数清单和固定 allowlist，
不扫描包，也不直接注解 ProductStore.load_drafts_index。

纯读取问题采用以下路径：

~~~text
用户：“现在有多少个草稿？”
→ /aiWork 创建或复用 global.agent.chat 对话
→ 主 Agent 调用 drafts_query(scope=active)
→ AiToolRuntime 返回 DraftQueryResult
→ GlobalPlanningDecision.action=answer + snapshot_id
→ facade 从快照确定性渲染“当前有 N 个活动草稿”
→ 任务 completed
~~~

draft.prepare_for_market 表达“把一个草稿准备到目标市场可继续发布”的高层业务结果：

~~~python
class DraftPrepareForMarketRequest(BaseModel):
    draft_id: str
    target_platform: str


class DraftPrepareForMarketResult(BaseModel):
    draft_id: str
    target_platform: str
    completed_parts: list[str]
    readiness: DraftPublishReadiness
~~~

Controller 调用后，Capability 按需编排：

~~~text
读取稳定 draft_id
→ 创建或更新目标平台草稿
→ 本地化文案
→ 准备图片
→ 类目匹配（需要时复用现有 focused Agent）
→ 属性填写（复杂枚举时复用现有 focused Agent）
→ 确定性核价
→ 返回 completed 或明确 required_inputs
~~~

它不直接发布。发布仍必须经过确定性校验、validation_digest、一次人工确认和 PublishingBus。
draft.prepare_for_market 不作为主 Agent 写 Tool；主 Agent 只规划该 Capability，Controller 从静态
map 直接调用。

带序号的后续请求采用查询快照：

~~~text
用户：“把第二个草稿目标市场改成 Ozon，然后重新本地化文案”
→ /aiWork 复用对话并创建新的 Global Task
→ 主 Agent 使用最近 snapshot_id 调用 drafts_query(positions=[2])
→ 得到稳定 draft_id
→ 生成 draft.prepare_for_market 计划
→ Controller 直接执行 Capability
→ 保存 Ozon 草稿和本地化文案
→ 返回准备结果
~~~

如果没有可用快照、序号越界或目标市场语义不明确，主 Agent 必须 ask_user，不能根据数据库当前顺序
猜测。前端在后续请求中自动携带最近 snapshot_id，用户不需要手工输入。

当用户说“把第一个草稿发布到 Ozon”时，主 Agent 生成 draft.prepare_for_market、
product.publish.validate、product.publish.request 的顺序计划。准备步骤可以调用文案、图片、类目、属性
和核价能力；发布步骤仍在 validation_digest 对应的一次人工确认后执行。只有 PublishingBus 记录平台
真实成功终态，整个纵向试点才算完成。

试点验收必须同时证明：

- 只读 Tool 调用经过 AiAgentFactory → Tool Bridge → AiToolRuntime；
- 主 Agent 可以正确回答草稿数量并返回稳定快照；
- “第一个/第二个”解析为快照中的稳定 draft_id；
- Controller 直接调用 draft.prepare_for_market，不调用 Tool executor；
- 文案本地化使用现有 Direct Model/Copy Service，不创建新 Agent；
- 类目和复杂属性才复用现有 focused Agent；
- 缺资料时可暂停、补充并继续；
- 如果用户继续要求发布，可以进入现有校验、确认、PublishingBus 和真实终态流程。

### 16.2 首轮接入清单

| Capability | 当前业务 owner | Controller 接入方式 | 完成条件 |
|---|---|---|---|
| drafts.query | ProductStore.load_drafts_index | 建立类型化查询 Capability，并以只读 @ai_tool 加入 planning ToolSet | 返回统计、有序摘要和 snapshot_id |
| draft.prepare_for_market | copy_service、图片/类目/属性/核价 owner | 建立高层普通 Capability；内部只在必要时复用 focused Agent | 返回目标平台草稿及 publish readiness |
| product.read | ProductStore 及现有商品 facade | 增加紧凑只读 Capability，不暴露 Store 原语 | 返回当前商品和目标草稿事实 |
| category.match | category_match_facade.py 与现有 focused Agent | Controller 调用公开 Capability；内部继续复用 focused Agent | 返回通过现有候选账本和终检的类目 |
| product.attributes.fill | category_attribute_ai_fill.py 与现有 fill Agent service | 提炼稳定 Capability；复杂枚举搜索继续由 focused Agent 完成 | 返回已填写属性或明确 required_inputs |
| product.attributes.update | ProductStore 的草稿 mutation owner | 建立天然可重复执行的字段设置 Capability | 重复提交相同字段得到相同草稿状态 |
| product.images.prepare | image_pool.py 与现有 focused 图片 service | 先收敛一个可验证的普通 Capability，不包装成新 Agent | 草稿拥有满足发布要求的确定图片集合 |
| product.publish.validate | publish_helpers.py、publish_validation.py 和平台 payload builder | Controller 直接调用确定性 Capability | 无硬错误并返回发布摘要和 validation_digest |
| product.publish.request | publish_workflows.py、平台 adapter 与 PublishingBus | 确认后由 Controller 直接调用，并传入稳定发布幂等键 | 返回唯一 PublishingBus job_id |

如果某项现有代码尚未形成稳定公开 Capability，应先增加薄的 focused service/facade adapter。adapter
只组合现有 owner，不复制 prompt、平台请求、Store 持久化或领域校验。

### 16.3 静态 capability map

GlobalAgentFacade 作为 composition root 组装并注入显式函数引用；Controller 只接收类型化 map，
不反向 import HTTP route、领域 facade 或 AI Tool executor：

~~~python
GLOBAL_TASK_CAPABILITIES = {
    "drafts.query": query_drafts,
    "draft.prepare_for_market": prepare_draft_for_market,
    "product.read": product_read,
    "category.match": match_category,
    "product.attributes.fill": fill_product_attributes,
    "product.attributes.update": update_product_attributes,
    "product.images.prepare": prepare_product_images,
    "product.publish.validate": validate_product_publish,
    "product.publish.request": request_product_publish,
}
~~~

这里的函数名表示目标公开 Capability，实施时以各领域最终 owner 为准。每个函数必须使用类型化输入
并返回 CapabilityResult。

### 16.4 步骤之间的数据传递

- Controller 从 ProductStore 和 LocalGlobalTaskState 读取当前事实，不把上一轮模型文字当输入；
- completed Capability 把结果保存到真实领域 owner，并只在任务步骤中保存摘要和资源引用；
- 后续步骤重新读取当前商品或草稿状态；
- needs_input 保存 RequiredInput，用户提交后合并到当前步骤输入；
- 用户输入仍有歧义时才交给主 Agent；明确字段直接进入对应 Capability；
- 发布校验摘要和 validation_digest 进入 PublishConfirmation；
- 发布提交只保存 PublishingBus job_id，最终状态继续从 PublishingBus 读取。

### 16.5 端到端业务验收

至少覆盖以下真实串联路径：

~~~text
创建任务
→ 主 Agent 生成计划
→ Controller 保存计划
→ 类目匹配
→ 属性填写
→ 必要时暂停并接收用户输入
→ 图片准备
→ 确定性发布校验
→ 用户确认一次
→ PublishingBus 幂等提交
→ 读取平台真实终态
→ 任务 completed 或 failed
~~~

测试使用真实 Capability 和任务状态流，只 mock 外部模型与平台网络边界。还必须覆盖：

- 缺资料后暂停并继续；
- 发布校验失败时不能进入确认；
- 重复确认或进程重试只得到同一个发布 job；
- PublishingBus 终态失败时任务不能标记 completed。

只有任务框架通过、但上述业务链路尚未串通，不算本方案完成。

## 17. 实施阶段

### 阶段 0：Capability 与幂等准备

- 确认首轮 Capability 名称、输入、输出和真实 owner；
- 保留现有 category.match focused Agent；
- 复杂属性填写继续作为可复用 focused Agent Capability；
- 把确定性字段修改、图片准备、发布校验和发布请求整理为普通 Capability；
- 普通字段修改和草稿覆盖设计为天然可重复执行；
- 为 PublishingBus.enqueue 增加 idempotency_key 和唯一约束；
- 为首轮能力建立类型化输入、CapabilityResult 和薄 adapter；
- 不创建尚无可靠 owner 的空 Capability。

### 阶段 1：简单任务状态与顺序 Controller

- 建立 schemas/global_tasks.py；
- 建立 LocalGlobalTaskStore；
- 建立 GlobalTaskController；
- 先用无模型测试覆盖计划保存、顺序推进、needs_input、失败、取消和重启读取；
- 同步更新 docs/ai-context-map.md 和架构测试。

### 阶段 2：主 Agent 与有限 ToolSet

- 建立 GlobalAgentService 和唯一主 Agent Execution Profile；
- 实现只读 Planning run；
- Controller 按计划通过静态 capability map 直接调用普通 Capability；
- 只有需要多轮推理的 Capability 才进入现有 focused Agent；
- 主 Agent 和 focused Agent 的所有 Tool 调用继续经过
  AiAgentFactory → Tool Bridge → AiToolRuntime；
- 验证主 Agent 不能调用未进入 planning ToolSet 的能力；
- 不增加 Runtime hook、动态 Policy Resolver 或第二套 Agent loop。

### 阶段 3：/aiWork 对话入口

- 在 AiWorkView 左侧栏增加“新建对话”；执行记录通过主对话执行详情和开发者开关保留查看能力；
- 增加 GlobalAgentChatPanel、globalTasks API 和前端类型；
- 首次消息创建 global.agent.chat 投影和 Global Task，后续目标复用对话并创建新 task_id；
- 一个对话只允许一个非终态任务，页面刷新后可以恢复最近活动任务；
- 展示普通消息、计划、步骤、RequiredInput、发布确认和发布终态；
- 发布必须点击确认按钮，文本消息不能绕过确认接口；
- 任务状态只有限频率调用 /api/global-task-state，不新增 global-task-wait；
- 增加前端组件和 API 交互测试。

### 阶段 4：草稿完整纵向试点

- 实现类型化 drafts.query，并以只读 @ai_tool drafts_query 显式加入 planning ToolSet；
- 实现 DraftQuerySnapshot 的保存、最近快照传递和稳定序号解析；
- 实现 action=answer 的确定性结果渲染，模型不能自行决定数量、顺序或草稿 ID；
- 实现高层普通 Capability draft.prepare_for_market；
- 串联目标平台草稿、本地化文案、图片、类目、属性和确定性核价的现有 owner；
- 类目和复杂属性按需复用 focused Agent，其余步骤不增加模型调用；
- 覆盖“有多少草稿”“第二个草稿改为 Ozon 并重新本地化”和缺资料暂停恢复；
- 用户明确要求发布时，继续进入校验、确认、PublishingBus 和真实平台终态。

### 阶段 5：其余现有业务能力串联

- 在 GlobalAgentFacade composition root 建立并向 Controller 注入静态
  GLOBAL_TASK_CAPABILITIES；
- 依次接入商品读取、类目匹配、属性填写、字段更新和图片准备；
- Controller 直接调用普通 Capability，复杂类目和属性能力内部复用现有 focused Agent；
- 统一 completed、needs_input 和 failed 结果；
- 打通 RequiredInput 保存、用户输入提交和同一步骤继续；
- 验证步骤之间从真实 ProductStore/草稿状态传递数据，不依赖模型文字。

### 阶段 6：发布确认与终态

- 接入确定性发布校验和 validation_digest；
- 实现 LocalGlobalTaskState 中的一次人工确认；
- 确认后由 Controller 直接调用发布 Capability，并传入 PublishConfirmationContext；
- 接入 PublishingBus job_id；
- 通过 /api/global-task-state 有限频率刷新 PublishingBus 状态；
- 验证相同幂等键只创建一个发布 job；
- 验证远端终态前任务始终是 waiting_publish_result。

### 阶段 7：端到端验收与收口

- 跑通第 16.1 节草稿纵向试点和第 16.5 节完整发布路径；
- 增加缺资料、校验失败、重复确认和平台失败测试；
- 更新 docs/ai-context-map.md；
- 更新 tests/test_ai_context_architecture.py；
- 运行 /aiWork 对话组件和 API 测试；
- 删除被新 Capability 替换的旧名称、旧入口和旧测试；
- 不保留 alias、feature flag、shadow path 或 fallback；
- 运行完整后端测试。

## 18. 与当前架构的符合性

该轻量方案符合当前架构，原因如下：

- 继续由 AiAgentFactory 创建唯一 Pydantic Agent；
- 主 Agent 只在规划、重新规划和处理歧义时使用现有 run_sync；
- 确定性步骤由 Controller 直接调用公开业务 Capability，不产生额外模型调用；
- 所有模型 Tool 调用继续经过 PydanticToolBridge 和 AiToolRuntime；
- 主 Agent 和 focused Agent 的 ToolSet 仍是显式、不可变、run-scoped 的有限集合；
- Controller 不调用 Tool executor；其直接 Capability 调用不属于 Agent Tool 边界；
- 固定本地权限和 tenant_id=local 不改变 Runtime 安全边界；
- route → facade/service → focused runtime 的依赖方向保持不变；
- /aiWork 的写操作只调用 GlobalAgentFacade，不给现有只读 ai_work_routes.py 增加领域编排；
- AI Work journal 只做对话展示投影，LocalGlobalTaskStore 仍是任务状态 owner；
- 用户对话 ID 与 AiAgentFactory 每次 run 创建的执行 conversation_id 明确分离并通过引用关联；
- ProductStore 仍是商品和草稿唯一持久化 owner；
- PublishingBus 仍是发布 job 与平台终态唯一 owner；
- Ozon 等异步发布只有平台确认成功后才算完成；
- 大部分业务能力保持普通代码，不会扩散出大量 Agent。

当前需要补充但不改变架构的地方：

- 新增简单 LocalGlobalTaskStore；
- 新增顺序 GlobalTaskController；
- 为 PublishingBus.enqueue 增加 idempotency_key；
- 增加主 Agent 的静态只读 Planning ToolSet；
- 增加类型化草稿查询、轻量查询快照和 draft.prepare_for_market；
- 在 /aiWork 增加全局 Agent 对话入口、任务交互和发布确认卡片。

## 19. 验收标准

- 系统只有一个主 Agent，所有 Agent 都由 AiAgentFactory 创建；
- 普通功能不包装成 Agent；
- Planning run 只能读取，不能写；
- drafts.query 是通用类型化查询，不是只返回草稿数量的一次性函数；
- 草稿数量、排序和序号指代必须来自 DraftQuerySnapshot，不采用模型自行生成的数字或 ID；
- draft.prepare_for_market 由 Controller 直接调用，并复用现有业务 owner；
- /aiWork 左侧栏可以新建全局 Agent 对话，首次发送后返回稳定 ai_work_conversation_id；
- 同一对话可以连续创建多个顺序任务，但同一时间只能有一个非终态任务；
- 页面刷新后能恢复消息、最近 task_id、待补资料状态和待发布确认状态；
- 现有普通 AI 执行记录保持只读，只有 global.agent.chat 对话显示输入框；
- AI Work 投影不代替 LocalGlobalTaskState，也不决定任务或发布是否成功；
- Controller 保存计划并严格顺序执行；
- 确定性读取、修改、校验和发布提交不启动主 Agent run；
- Controller 只调用公开业务 Capability，不调用 Tool executor；
- 只有确需多轮推理的 Capability 才复用 focused Agent；
- 主 Agent 和 focused Agent 当前 ToolSet 外的 Tool 不可见且不可执行；
- 缺资料时任务持久化为 needs_input，用户补充后可继续；
- 普通字段和草稿更新天然可重复执行，不要求统一幂等记录；
- 发布提交使用持久化幂等键，重启或重试不会重复创建发布 job；
- 发布前确定性校验必须通过；
- 发布前只进行一次与当前 validation_digest 绑定的人工确认；
- 发布确认由 Controller 持久化，确认前不执行发布 Capability；
- PublishingBus job_id 不等于发布成功；
- 只有平台真实成功终态才能把任务标记为 completed；
- 页面只通过 /api/global-task-state 有限频率查询任务和发布状态，不新增 /api/global-task-wait；
- 用户输入“确认”或“发布”不能绕过明确的发布确认按钮和确认端点；
- HTTP 路由、facade/service、runtime、Store 和 schema 边界符合项目架构测试；
- 第 16.1 节的草稿纵向试点已进入静态 map，查询、指代、市场准备、暂停恢复和可选发布端到端通过；
- 只完成任务框架但未串通现有业务能力，不得视为完成；
- 不出现多租户 Policy、CAS、lease、outbox、通用 reconcile 或动态 Tool 挂载。

## 20. 最终原则

~~~text
主 Agent 决定顺序计划和当前步骤如何表达
/aiWork 对话窗体承载用户输入、暂停补资料和发布确认
Controller 保存计划并直接、顺序调用 Capability
Capability 完成具体业务工作
类型化只读 Tool 和查询快照提供可验证事实与稳定指代
AiToolRuntime 决定 Agent Tool 调用能否执行
确定性 validator 决定是否允许发布
用户在发布前确认一次
PublishingBus 决定发布 job 的真实终态
~~~
