# 全局 Agent 实施偏差与架构更正记录

> 状态：已实施（2026-08-13）
>
> 设计基线：[`docs/global-agent-next-stage.md`](docs/global-agent-next-stage.md)
>
> 当前入口：[`docs/ai-context-map.md`](docs/ai-context-map.md)

本文只记录实施时相对设计基线发生的偏差、补全和架构更正。没有列在这里的设计约束继续按原文
执行。本项目仍处于 Demo / 初始开发阶段，因此不会为未发布旧方案保留双路径；唯一例外是下文有
真实持久化数据证据的 SQLite v5 → 当前版本原子迁移。

## 1. Planning ToolSet 收窄为单个 `drafts_query`

- **设计基线**：规划阶段举例包含草稿查询、商品读取和发布校验读取等少量只读 Tool。
- **实际实现**：`erp_web/runtime_units/global_task_tools.py` 只声明并绑定一个
  `side_effect="none"` 的 `drafts_query`。主 Agent 可以规划九项 Capability，但不能把这些写能力或
  领域读取 adapter 当作 Tool 调用。
- **理由**：首轮规划真正需要模型交互读取的是草稿统计、稳定排序和序号指代。商品、店铺、草稿和
  发布身份都可以由 Controller、ProductStore 与领域 Capability 从可信上下文读取；扩大 ToolSet 只会
  增加模型可见面、prompt 噪声和重复业务入口。
- **未采用方案**：没有把九项 Capability 全部注解成 Tool，也没有增加动态 Catalog 检索或运行时挂载；
  也没有为了与设计示例逐字一致而复制 `product.read` / `publish.validate` 的 planning-only 包装。
- **影响**：planning run 保持只读且最小权限；确定性读取、修改、校验和发布仍由 Controller 直接调用
  静态 Capability。以后若新增 planning Tool，必须先证明模型确实需要该读取，并单独加入显式 allowlist。

## 2. `action=answer` 使用确定性快照渲染

- **设计基线**：主 Agent 可以返回 `action=answer` 和 `query_snapshot_id`，但文档同时要求不得信任模型
  自行填写的数量或草稿 ID。
- **实际实现**：当前直接回答只覆盖草稿数量。Controller 忽略模型 `answer` 中的业务数字，从
  `DraftQuerySnapshot.total` 生成“当前查询匹配 N 个草稿”。快照不存在时任务失败，不回退到模型文字。
- **理由**：仅校验模型附带了 snapshot ID 并不能证明其自然语言数字正确；把最终数字交给 Controller
  是最小且可测试的安全闭环。
- **未采用方案**：没有正则修补模型文字、让模型重述 Tool 输出或在 UI 猜测数量。
- **影响**：数量回答稳定可复现；更丰富的只读回答需要增加对应的确定性 renderer，不能直接放行任意
  模型陈述。

## 3. 补充持久化输入 owner、类型化输入和受控计划参数

- **设计缺口**：原示例中的 `LocalTaskStep` 没有保存输入；`RequiredInput` 只有 key/label/reason/options；
  任务状态不能区分“规划澄清”与“Capability 缺字段”；计划也没有结构化表达“第二个草稿”、目标平台、
  属性值或核价输入。
- **实际实现**：
  - `LocalTaskStep.inputs` 成为补资料和重启继续的 durable owner；
  - `LocalGlobalTaskState.pending_input_owner` 明确为 `planning` 或 `capability`；
  - `RequiredInput.input_type` 支持 `text`、`select`、`json_object`、`string_list`，前端按类型解析后再提交；
  - `RequiredInput.input_owner` 明确为 `step`、`provided_attributes` 或 `pricing_input`，Controller 按 owner
    将补充值合并回持久化步骤输入，而不是由 facade 猜测字段归属；
  - `GlobalTaskPlanProposal` 增加 `draft_position`、`target_platform` 和
    `GlobalTaskPlanParameters`；parameters 只容纳属性值、核价资料和是否重写文案等业务值；
  - Controller 从快照解析 `draft_position`，再把稳定 `draft_id` 注入步骤；商品、店铺和资产身份不允许
    由模型通过 parameters 覆盖。
- **理由**：没有步骤输入就无法在进程重启后继续同一步；没有 owner 就会把明确字段误送回主 Agent 或
  把规划澄清误合并到业务步骤；没有输入类型，`product.attributes.update` 所需 JSON object 会被 UI
  当成普通字符串。核价 owner 若靠字段白名单，还会把 `shipping_quote_mode`、`domestic_freight`、
  `commission_percent` 等合法核价资料误写为商品属性并反复追问。受控 parameters 则让用户目标中的
  明确值可传给 Capability，同时保持稳定身份可信。
- **未采用方案**：没有把所有补资料都拼成自然语言重新规划，没有保存不透明任意 args，也没有允许模型
  直接生成 product_id、draft_id、店铺身份或发布确认上下文。
- **影响**：任务 schema 与前端类型比设计示例多出上述字段；这是当前唯一契约，不保留旧的无 inputs
  状态分支。领域 Capability 仍会对所有计划值和用户值重新验证。带候选项的类目/枚举值使用
  `select`，草稿序号和图片资产等集合使用 `string_list`；`target_platform` 与计划内部 `platform`
  别名只在 facade 统一归一化，缺失时暂停请求用户选择，不能让 Pydantic 类型错误代替 needs_input。
  核价或属性将来新增字段时由 Capability 声明 owner 即可，不再维护易漏的字段名集合。

## 4. SQLite 从完整 v5 原子迁移到当前版本

- **设计基线**：新增 LocalGlobalTaskStore、快照和发布幂等持久化；Demo 阶段默认不为无证据旧格式建设
  兼容链。
- **实际证据**：实施前工作区存在完整 `PRAGMA user_version=5` 的本地数据库，已经保存商品、草稿、
  发布任务和 AI Work 记录。这属于真实持久化数据，而不是为了让旧测试通过而假设的兼容需求。
- **实际实现**：全局任务首轮落地时数据库版本为 v7；仅对表集合和关键列都完全符合预期的 v5 库，
  在一个事务中：
  1. 为 `publish_jobs` 增加不可空 `idempotency_key`；
  2. 为既有 job 写入 `migrated-publish-job:<job_id>` 和从旧状态提取的幂等事实；
  3. 创建 `global_tasks`、`draft_query_snapshots` 及唯一索引；
  4. 最后更新到当时的 `user_version=7`。
- **理由**：直接拒绝或删除 v5 会破坏用户真实数据；直接 v5 → v7 可避免把实施过程中未发布的 v6
  变成新的兼容契约。
- **未采用方案**：没有恢复 v1-v4 迁移链，没有接受未知/不完整 schema，没有建立运行时 legacy fallback，
  也没有双写旧表。
- **影响**：完整 v5 可一次升级且保留业务数据；空库直接创建当前 schema；其他版本或
  不完整结构继续 fail closed。这里的“完整”同时校验列集合和两个关键唯一索引：发布幂等键唯一、同一
  全局对话仅一个活动任务；只保留同名但列或 partial 属性错误的索引也会拒绝启动，不能降级成仅靠
  进程锁的弱约束。

### 4.1 AI Work 会话层级使当前 schema 升级到 v8

- **新增需求**：用户主对话和其 planning / focused Agent 执行记录需要保留审计关系，但内部执行记录
  不应全部占据左侧一级对话列表。
- **实际证据**：工作区已经存在完整 v7 数据库和 AI Work JSONL；因此 v7 也成为必须保留的真实
  持久化格式。当前 schema 升为 v8，`ai_sessions.parent_session_id` 持久化直接父会话并建立查询索引。
  完整 v7 通过一次原子迁移增加列、索引并回填关系；完整 v5 仍可直接迁移到 v8，不建立中间双写。
- **回填规则**：只接受服务端已保存的 `global.agent_execution_link`，以及
  `global_tasks.ai_work_conversation_id` 与任务状态中 execution conversation IDs 的明确关系；无法唯一
  确认、指向自身或产生冲突的旧记录继续作为根会话，不按 `use_case_id` 猜测归属。
- **理由**：父子关系是列表过滤、限量、排序和重启恢复所需的查询事实；每次扫描所有 JSONL 或仅在
  浏览器隐藏某类 use case 都会产生误判、N+1 和历史压缩后关系丢失。v7 → v8 迁移则是保护当前真实
  本地数据的必要兼容，不是为废弃产品路径增加 fallback。
- **影响**：默认列表在 SQL 过滤后再应用 limit，只展示根会话；开发者可显式查看内部会话。单个子会话
  仍可按 ID 读取，主对话通过 children API 展示可折叠执行详情。稳定 `global.agent.chat` 的 lifecycle
  status 继续保持 running，最近一次 LocalGlobalTask 状态通过独立 `latest_task_status` 展示，不能用
  某次任务终态结束长期聊天的事件轮询。

## 5. DraftQuerySnapshot 的“总量 + 有界页”语义

- **设计歧义**：原文要求快照保存稳定有序 ID、统计和序号指代，但没有说明 `limit` 是否只限制模型
  返回 items，还是也限制快照中的 ID。
- **实际实现**：过滤和排序先作用于完整匹配集合；`total`、`count_by_platform`、`count_by_status` 统计
  完整集合；`items` 与 `snapshot.draft_ids` 只保存前 `limit` 条（当前 1..100 的有界页）。
  `draft_position` 只能引用该页内的一基序号；解析后重新从 ProductStore 读取当前事实。
- **理由**：把任意数量草稿 ID 和摘要写入 Agent 上下文及本地快照，会违背有界输出目标；序号必须对应
  用户/模型实际看到的同一有序页，不能在隐藏结果中漂移。
- **未采用方案**：没有让 `total` 假装等于页大小，没有把完整匹配集全部复制进快照，也没有让模型根据
  当前 ProductStore 顺序重新猜序号。
- **影响**：当 `total > len(items)` 时，数量回答仍报告总量，但“第 N 个”只在当前页有效。要访问页外
  草稿，应收窄过滤条件或在上限内提高 `limit`；当前没有伪装成完整游标分页 API。

### 5.1 `view` 从展示提示改为真实字段投影

- **设计风险**：`summary`、`workflow`、`publish_readiness`、`detail` 若返回完全相同的对象，会让公开
  枚举沦为无效果参数，也无法兑现有界上下文。
- **实际实现**：所有视图仍返回同一个稳定 `DraftSummary` schema，但按用途清空无关字段：`summary`
  只有身份与状态，`workflow` 增加预检/错误统计，`publish_readiness` 增加类目、文案、图片、属性等
  准备事实，`detail` 才返回完整紧凑摘要。快照重放沿用创建时的 view。
- **理由与影响**：固定 schema 便于 Tool/前端消费，字段投影又能限制模型所见内容；没有建立四套近似
  DTO，也没有让 snapshot 重放因客户端临时指定另一 view 而改变原查询语义。

## 6. 记录 focused Agent 的执行 conversation ID

- **设计基线**：稳定全局对话与每次主 Agent run 分离，并可投影 Agent 执行链接；高层市场准备还会在
  内部复用类目和属性 focused Agent。
- **实际实现**：`category.match` 和 `product.attributes.fill` 的完成、暂停和 focused run 后失败都携带
  各自 `conversation_id`；`draft.prepare_for_market` 按真实执行顺序聚合为
  `agent_execution_conversation_ids`。Controller 同时识别正式 metadata、直接 ID 和结果聚合列表，
  去重后先保存任务事实，再写 `global.agent_execution_link`，最后迁移 Capability 状态。
- **理由**：只记录 planning run 会丢失真正执行类目导航或属性枚举的模型证据；把 transcript 复制进
  任务又会制造第二份日志和无界状态。
- **未采用方案**：没有复用稳定 chat conversation 作为 focused run，没有复制完整消息、Tool 输出、
  token usage 或商品 payload 到 LocalGlobalTaskState。
- **影响**：`/aiWork` 可以从稳定对话跳转到 planning/focused 执行记录；即使 focused Agent 返回后
  Capability 等待资料或后续持久化失败，链接也不会丢失。任务状态仅保存 ID 引用，AI Work journal
  继续拥有详细执行内容。

## 7. AI Work 使用长期稳定业务对话，而不是复用 Agent run

- **设计具体化**：原文要求同一对话连续创建多个任务，但现有 AI Work conversation 原本主要表达一次
  Provider/Agent run，终态语义与长期聊天并不相同。
- **实际实现**：`start_global_agent_conversation()` 创建独立 `use_case_id=global.agent.chat` 的稳定业务
  conversation；后续任务复用该 ID。投影追加通过专用打开路径，不写虚假的 `RUN_RESUMED`，并拒绝把
  普通执行 conversation 当作全局对话。允许的投影事件固定为 user message、assistant message、task
  state 和 execution link。
- **理由**：一个聊天可以包含多个成功、失败或取消的 LocalGlobalTask；若直接复用某次 planning run，
  run 的终态、模型配置和任务边界都会与聊天生命周期冲突。
- **未采用方案**：没有为每条用户消息创建新的可见聊天，没有把 LocalGlobalTaskState 存进 journal，
  没有让 AI Work 事件决定任务或发布成功。
- **影响**：稳定 chat 在 AI Work 列表中是持续可追加的业务会话；每次 planning/focused run 有独立
  conversation ID。投影失败只影响展示，任务与 PublishingBus 真相不变。

为使“有界”同时约束单条载荷和事件总量，稳定业务对话保留创建元数据首事件及最近 500 条展示事件；
压缩采用同目录临时文件原子替换，原始序号与 SQLite `last_seq` 保持单调，因此增量读取允许出现历史
序号空洞但不会重复。未采用每次追加全量扫描计算序号；追加以 `ai_sessions.last_seq` 为准，只有超过
保留上限时才执行压缩。普通 Agent/Provider run 不参与这项长期聊天保留策略。

## 8. 目标市场 runtime 拆分并由 facade 注入 focused 执行

- **设计基线**：原文件清单用一个 `market_prepare_capabilities.py` 表示市场准备、类目、属性、图片和
  核价的高层 Capability adapter。
- **实际实现**：按 owner 拆为：
  - `market_capability_support.py`：草稿/目标选择、类目详情与持久化支撑；
  - `category_capabilities.py`：类目 focused adapter；
  - `attribute_fill_capabilities.py`：规则与 focused 属性填充；
  - `market_pricing_capability.py`：确定性核价；
  - `market_prepare_capabilities.py`：只保留高层顺序编排；
  - `product_capabilities.py` / `publish_capabilities.py`：商品与发布 focused adapter。
  `GlobalAgentFacade` 注入 ProductStore、现有 `category_match_facade.match_category`、配置 loader 和其他
  函数引用；目标草稿 claim、Direct Copy 的 app_dir、发布上下文/预检、PublishingBus 也显式绑定同一个
  `AppContext`，避免测试上下文或未来多个本地实例误用进程默认 Context。runtime unit 不反向 import
  facade。
- **理由**：把类目匹配、属性 Agent、核价、草稿持久化和高层编排塞入一个大文件会混合 owner，并容易
  为调用现有 facade 形成反向依赖或循环。显式函数注入让测试只 mock 外部模型/平台边界。
- **未采用方案**：没有复制现有类目/属性 prompt 和 Agent loop，没有在 runtime 内调用 HTTP facade，
  没有增加动态注册表或空 Capability。
- **影响**：文件数比设计清单多，但依赖方向更清晰；`draft.prepare_for_market` 仍是 Controller 看到的
  单个高层能力，内部步骤都写入真实 ProductStore owner。

商品内容 mutation 还增加了比原文更严格的发布状态失效规则：已发布目标稳定拒绝修改；属性修改只约束
选中目标，图片属于共享内容，任一目标已发布都拒绝改图。未发布内容变化会在同次持久化中清空旧
`validation_errors`、`category_precheck`、`last_precheck`、`last_precheck_target`、
`last_publish_task`、`publish_status` 并由 ProductStore 重算 workflow status。发布历史仍由
PublishingBus/publish logs 保存，不让草稿继续携带指向旧 payload 的“就绪/已发布”引用。

`regenerate_copy=true` 另增加稳定的 `global-task:<task_id>:step:<step_id>:copy` operation key。
`copy_operation_key` 与生成后的文案在 ProductStore 同次保存；若进程在领域保存成功、Controller 保存
步骤结果前退出，重启重跑会从草稿验证 marker 并跳过重复模型调用。未采用仅存内存标志或只在
Capability 返回后更新任务状态，因为两者都无法封闭该崩溃窗口。

## 9. 发布确认绑定店铺身份，并加强 PublishingBus 幂等事实

- **设计基线**：发布确认绑定 validation digest，Controller 生成稳定 idempotency key，PublishingBus
  重试返回同一个 job。
- **实际实现补强**：
  - 发布摘要从已授权配置提取平台账号稳定标识，哈希为脱敏 `store_identity`；缺少稳定身份时阻断确认；
  - validation digest 绑定 `product_id + draft_id + platform + site + store_identity + canonical payload`；
  - 确认后 enqueue 前重新构造 payload 并用常量时间比较 digest；通过后将已批准 payload、digest 和
    脱敏店铺身份作为 job 内部恢复事实原子持久化，绝不保存店铺凭据；
  - worker 现取凭据，但不再重建全局 Agent 已确认的 payload；外发前重新验证店铺身份、完整 digest 和
    `validate_payload`，然后直接调用平台 `publish_payload`。事实变化或内部 payload 被篡改都会在网络前失败；
  - PublishingBus 强制所有调用方提交可信幂等键，并原子绑定
    `product_id + platforms + targets(draft_id/site/product_id) + confirmation digests`；
  - `product.publish.request` 在重新校验和队列准入之前，先按
    `idempotency_key + product_id + draft_id + platform + site + validation_digest` 查询既有 job；
    因而 job 已落库但 GlobalTask 尚未保存 `job_id` 时，重启仍可恢复同一 job，包括平台已成功并回写
    `published` 的情形；
  - 同键同事实返回既有 job 且不重复 submit，同键不同事实返回
    `PUBLISH_IDEMPOTENCY_CONFLICT`；job 更新不能更换幂等键；
  - 普通人工页面队列由服务端生成 `manual:<uuid>`，避免客户端遗漏键，同时不与全局任务稳定键混用。
- **理由**：只绑定 payload 而不绑定店铺账号，会在用户切换授权店铺后错误复用旧确认；确认后由 worker
  重新构造 payload，会让真正发送内容绕开人工确认；只按 key 查 job 而不比较发布事实，会把编程错误
  伪装成成功重放；等到重新校验后才查旧 job，则会在草稿已发布时被准入检查挡住。只在进程内去重也
  无法覆盖重启和并发请求。
- **未采用方案**：没有把明文凭据/账号写进任务或 digest，没有使用进程内 set 作为幂等真相，没有让
  客户端自由选择全局任务发布键，也没有为了重试增加第二条发布路径。
- **未采用方案补充**：没有引入通用 outbox、分布式 lease 或第二条全局 Agent 发布路径；手工发布仍是
  独立的动态构造流程，不伪装成已经有人确认过的冻结 payload。
- **影响**：店铺身份、站点、草稿或 payload 任一变化都会使旧确认失效并要求重新校验；入队后则只允许
  精确的已批准 payload 外发。重启、重复确认和 HTTP 重试只会得到原 job；非法键复用会稳定失败而不是
  错误发布。公开 job 状态会剥离 approved payload、digest、店铺身份、幂等键和内部 facts。草稿含多个
  目标而请求没有明确平台/站点时稳定返回 `DRAFT_TARGET_AMBIGUOUS`；只给平台但该平台仍有多个站点时返回
  `DRAFT_TARGET_SITE_AMBIGUOUS`，不会沿用旧发布代码“取第一个”或默认站点的猜测行为。

## 10. 维护约束

- 新增或删除全局任务 Capability 时，必须同时更新 facade 静态 map、
  `GlobalAgentService` allowlist、prompt、本文、`docs/ai-context-map.md` 和架构测试；不得只改模型 prompt。
- planning ToolSet 继续默认最小暴露；普通 Capability 不为形式统一注册成 Tool。
- 新的持久化兼容只在存在真实生产/本地数据证据时增加；不得从本次 v5 / v7 → v8 特例推导通用旧版支持。
- AI Work 只保存有界展示和执行引用；LocalGlobalTaskStore 与 PublishingBus 的 owner 不得迁入投影层。
- `/state`、`/cancel` 等普通边界只装配 Controller，不得提前加载模型配置或构造 PublishingBus；planner
  与发布状态 reader 必须延迟到对应状态实际需要时解析。未知 HTTP 500 只记录服务端日志，客户端不得
  收到原始异常文本。
