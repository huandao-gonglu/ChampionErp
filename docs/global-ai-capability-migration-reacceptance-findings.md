# Global AI Capability Migration 复验整改清单

## 文档目的

本文记录对 `global-ai-capability-migration-plan.md` 重新实现版本的复验结果，并列出当前阻止最终验收通过的 5 项问题。

当前结论：**主链路已经能够运行，但审批安全、副作用治理、超时控制和异步任务生命周期仍未闭合，暂不具备最终验收条件。**

## 1. P1：模型可以自行批准高风险任务

### 问题

`global_task_start` 与 `global_task_approve` 同时暴露给 Global Agent，且 `global_task_approve` 不要求审批。模型可以创建一个进入 `pending_approval` 状态的任务，随后直接调用批准工具完成审批。

现有的 `call_id` 防伪不能解决该问题，因为模型不需要伪造 `call_id`：批准工具会在服务端为模型生成有效的批准记录。

### 证据

- `erp_web/runtime_units/global_ai_control_tools.py`
- `global_task_approve` 位于 Global Agent 可用的控制能力集合中。
- `global_task_approve` 的能力声明为 `approval_required=False`。
- 批准请求只需要任务和步骤标识，没有模型无法提供的可信人工授权凭据。

### 风险

- 高风险写操作的“人工审批”可以被同一个决策主体自行完成。
- 审批流程只有状态变化，没有形成真正的权限边界。
- 提示词注入或模型误判可能直接触发删除、发布等敏感操作。

### 整改要求

- 从模型 ToolSet 中移除 `global_task_approve` 和 `global_task_reject`。
- 批准和拒绝只能由可信 UI/API 入口调用。
- 服务端必须从已认证用户会话或可信执行上下文中取得审批身份，不能接受模型可自行构造的身份信息。
- 审批记录应包含审批人、审批时间、任务版本和被冻结的步骤参数摘要。

### 验收标准

- Global Agent 的工具清单中不存在批准、拒绝能力。
- 模型连续调用工具无法把任务从 `pending_approval` 推进到执行状态。
- 未携带可信人工授权上下文的批准请求被服务端拒绝。
- 自动化测试覆盖模型自批、重复审批、过期任务版本审批和跨用户审批。

## 2. P1：审批展示内容没有与真实执行内容绑定

### 问题

发布任务的审批界面展示模型提交的 `approval.summary`，但执行阶段只重新计算并比较 `validation_digest`，没有验证该 summary 是否等于服务器根据冻结参数生成的真实摘要。

因此，模型可以提交正确的 digest，同时提供错误或误导性的标题、价格、店铺、目标商品等人类可读信息。用户批准的视觉内容可能与系统最终执行的内容不一致。

### 证据

- `erp_web/runtime_units/publish_capabilities.py`
- 发布执行逻辑校验 `request.approval.validation_digest`。
- 当前没有将 `request.approval.summary` 与服务器重新计算的 `evaluation.result.summary` 做等值校验。
- 通用待审批状态直接展示请求中的 approval payload。

### 风险

- 用户可能在错误信息基础上批准真实发布操作。
- digest 只保护机器字段，未保护用户实际看到并确认的语义内容。
- 审批日志无法证明“所见即所批、所批即所执行”。

### 整改要求

- 人类可读审批摘要必须由服务端根据被冻结的规范化参数生成。
- 模型不能提交最终用于展示的审批标题、价格、目标店铺或商品摘要。
- 审批摘要、冻结参数、步骤 ID、任务版本和 capability version 应共同纳入审批摘要计算。
- 执行前必须重新核对当前任务版本和批准时的冻结快照。

### 验收标准

- 修改模型提交的 summary 不会改变审批页面展示内容。
- 修改任何受审批保护的执行参数都会导致旧审批失效。
- 审批页面展示的数据与实际发送给平台的关键字段一致。
- 自动化测试覆盖“正确 digest + 伪造 summary”“审批后参数变化”和“旧版本审批重放”。

## 3. P1：只读发布验证能力存在持久化副作用

### 问题

`product_publish_validate` 被声明为 `side_effect="none"` 并进入 Direct/read-only 能力集合，但其执行路径会调用 `save_draft_precheck_result`，修改草稿或预检状态。

这使模型能够通过一个名义上的只读能力修改业务状态，绕过 Task Runtime 对写操作提供的幂等、审批、恢复和审计约束。

### 证据

- `erp_web/runtime_units/publish_capabilities.py`
- `product_publish_validate` 的 capability metadata 声明无副作用。
- `evaluate_publish_validation` 会调用 `save_draft_precheck_result` 持久化预检结果。
- 该能力位于 Direct 能力集合，而不是 Task write 集合。

### 风险

- Catalog 中的副作用声明与真实行为不一致。
- 只读权限可能间接获得写入能力。
- 重试、并发执行或重复调用可能产生不可见的状态变化。
- 依赖 capability metadata 的安全策略和架构测试失去可信度。

### 整改要求

选择并落实以下一种边界，不保留隐式双路径：

1. 将验证能力改为纯计算，只返回验证结果和服务端生成的摘要，不写入草稿；或
2. 将持久化预检结果拆成独立的 Task write capability；或
3. 如果持久化本身就是产品契约，则将整个能力归类为写能力，并通过 Task Runtime 执行。

### 验收标准

- 所有 `side_effect="none"` 能力在执行前后都不会修改业务持久化状态。
- 架构测试能够检测 Direct 能力对 store、数据库写接口和外部写操作的调用。
- 发布验证的重复调用结果稳定，不产生额外业务状态变化。
- 如保留预检结果持久化，其写入必须具备 operation key、幂等和审计记录。

## 4. P1：能力超时契约没有约束真实阻塞操作

### 问题

Tool Runtime 当前主要在调用前检查剩余时间，并不能强制终止已经开始的阻塞操作。多项新的网络、浏览器、AI、采集和平台能力接收 `AiExecutionContext` 后直接丢弃，没有将 `bounded_timeout_seconds()` 传给底层 I/O。

此外，外层 Global Agent 的执行上限与内层 Task 的执行上限不一致。内层任务可能运行和产生副作用后，外层请求才因自己的较短 deadline 报错，造成“实际成功、用户看到失败”的结果分裂。

### 证据

- `erp_web/services/ai_tool_registry.py` 中的 deadline wrapper 只在执行前读取 deadline。
- 多个 capability 使用 `del execution`，未将剩余时间传播到底层调用。
- 采集、研究、图片、物流及平台 I/O 中存在同步长耗时路径。
- Global Agent 外层超时短于部分 Task 内层执行时限。

### 风险

- 请求超时后，后台副作用仍可能继续发生。
- 用户或模型重试可能造成重复采集、重复生成或重复平台操作。
- 无法准确区分失败、超时和“结果未知”。
- 进程重启后无法恢复同步长操作的真实终态。

### 整改要求

- 所有同步阻塞 I/O 必须接收并使用 `execution.bounded_timeout_seconds()`。
- 内层 deadline 必须受外层剩余时间约束，不能自行扩展。
- 无法在短时限内可靠完成的能力应转成持久化异步 Job。
- 超时后的有副作用操作必须进入可查询的 `active_job` 或 `result_unknown` 状态，不能直接报告普通失败。
- 对不支持取消的第三方调用，应使用 operation key 和终态查询保证恢复安全。

### 验收标准

- 自动化测试能够证明底层 HTTP/SDK 调用收到有界 timeout。
- 外层剩余时间短于内层默认值时，实际采用外层剩余时间。
- 超时后不会把状态不明的副作用记录成普通失败并自动重试。
- 长耗时能力返回 Job 引用，并能在进程重启后继续查询终态。

## 5. P2：异步研究任务未进入通用 active_job 生命周期

### 问题

`research_hot_products_search` 会创建后台异步研究运行，但返回结果没有采用通用 `JobReferenceResult`。Controller 因此会在后台任务刚创建后就把步骤标记为 completed，而不是进入 `active_job` 并等待真实终态。

当前的通用 Job 状态读取实现又主要绑定 PublishingBus，尚不能统一处理研究、采集、图片和物流等异步领域。

### 证据

- `erp_web/runtime_units/research_capabilities.py`
- `research_hot_products_search` 启动后台运行后直接返回领域结果。
- Controller 只有识别到通用 Job 引用时才会进入 `active_job`。
- `erp_web/facades/global_task_facade.py` 中的状态读取器仍以发布任务为主要实现。

### 风险

- Task 显示完成时，实际研究任务可能仍在运行或已经失败。
- 任务刷新、恢复、取消和错误传播对不同领域表现不一致。
- 后续步骤可能在研究结果尚未完成时提前执行。
- 进程重启后，Controller 无法通过统一协议恢复研究任务。

### 整改要求

- 所有后台异步能力统一返回领域无关的 Job 引用。
- Job 引用至少包含 `job_id`、`job_type`、状态读取绑定信息和 operation key。
- 建立通用 Job Status Reader 注册机制，由领域适配器实现状态查询，不让 Controller 依赖 PublishingBus。
- Controller 只有在 Job 达到成功终态后才能完成步骤；失败、取消和结果未知必须分别传播。

### 验收标准

- 研究任务创建后，步骤状态为 `active_job`，而不是 `completed`。
- 后台任务成功、失败、取消和查询异常都能映射到明确的 Task 状态。
- Controller 不直接导入研究、发布、采集、图片或物流领域模块。
- 自动化测试覆盖轮询恢复、服务重启恢复、重复刷新和终态幂等。

## 最终通过条件

以上 5 项全部满足以下条件后，才进入最终验收：

- 实现已完成，且不存在旧路径或旁路入口。
- 对应单元测试、集成测试和架构守卫测试已经补齐。
- 后端全量测试、前端类型检查和前端单测全部通过。
- 审批、超时、异步恢复和副作用声明均通过负向测试验证。
- 文档、Capability Catalog、HTTP/AI 入口和运行时行为保持一致。
