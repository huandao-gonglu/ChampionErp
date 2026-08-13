# ERP AI Task、工具调用与工作流架构

> 状态：目标架构；阶段 0～6 已完成
>
> 更新日期：2026-08-01
>
> 首个落地场景：`category.product_match`
>
> 核心依赖：[Pydantic AI](https://pydantic.dev/docs/ai/)

本文中的“API Provider”专指 `connection_type=api` 的 AI 模型推理连接，不包含 1688、店铺、
物流等普通业务 API。CLI 与浏览器 AI 是明确的非 API Provider，继续使用项目自己的运行适配器。

## 1. 最终结论

系统不再维护自定义的通用 Agent Runtime，也不把“普通 chat/image 不需要 Agent”解释为
“可以继续维护第二套通用 AI API 请求栈”。所有 `connection_type=api` 的 AI 推理请求都必须
先由统一 Model Factory 创建 Pydantic `Model`/`Provider`：

- 需要工具循环、类型化输出或 deferred tool 的用例使用 Pydantic `Agent`；
- 不需要 Agent 的普通文本、JSON 和流式调用使用 Pydantic Direct Model Requests；
- 图片生成优先使用锁定版本提供的 Pydantic capability/native tool；图片编辑或其他锁定版本
  确实不支持的厂商能力，只能作为登记过的窄范围例外，不能恢复通用 HTTP Provider 栈；
- CLI 与浏览器 Provider 不经过 Pydantic Model/Provider，由各自适配器执行。

Pydantic Agent 负责原生 function calling、model → tool → model 循环、结构化输出、重试、
用量限制、消息模型、流式事件和 deferred tool 协议。Pydantic Model/Provider 则负责全部 API
模型请求的厂商连接、认证和 wire protocol；两者不是同一个范围。

ERP 仍然拥有业务能力、安全边界、长期任务状态和面向用户的 AI Work 产品能力：

1. **Workflow / AI Work 是业务控制平面**：持久化任务、阶段、业务事件、审批、恢复信息和
   最终业务状态。
2. **Pydantic Model/Provider 是 API 模型传输平面**：Agent 和非 Agent 调用共享相同的
   厂商连接、请求格式、错误和观测边界。
3. **Pydantic Agent 是 Agent 运行平面**：调用模型、选择工具、推进 Agent 循环并返回类型化
   输出或 deferred 请求。
4. **ERP Tool Runtime 是安全执行平面**：校验工具白名单、用户权限、业务作用域、审批、
   幂等、deadline 和输出约束，然后调用确定性的领域代码。
5. **Pydantic instrumentation 是技术观测平面**：记录模型、工具、token、成本、耗时和异常
   trace；它不替代 AI Work 的业务任务、业务事件和用户界面。

```text
前端 / Workflow / Main Agent
  → 稳定业务 Capability（例如 category.match）
  → connection_type=api
      → centralized Pydantic Model Factory
          → 需要 Agent：Pydantic Agent
              ├─ 类型化 output
              └─ Pydantic Tool / ToolSet
                   → ERP Tool Bridge
                   → AiToolRuntime（权限、审批、幂等、作用域、审计）
                   → 领域 executor
          → 不需要 Agent：Pydantic Direct Model Request / Capability
  → connection_type=cli|browser
      → CLI / Browser Adapter
  → 业务结果写入 Workflow / AI Work

Pydantic instrumentation
  → OpenTelemetry / Logfire 等技术观测后端
```

Main Agent、页面和领域工作流依赖的是稳定 Capability，而不是某个 prompt、Agent 类或
Provider API。例如类目匹配统一调用 `category.match`。Capability 内部可以使用普通代码、
单次 AI Task 或领域 Agent，上层不需要知道内部是否发生了 function call。

## 2. 架构边界

### 2.1 必须坚持的边界

- 项目只保留一套 Agent 循环，由 Pydantic Agent 提供；不得继续维护自定义
  `AiTaskRunner` 作为并行路径或 fallback。
- 项目只保留一套 API 模型请求抽象。所有 `connection_type=api` 的推理请求必须使用
  centralized Pydantic Model Factory 创建的 `Model`/`Provider`；领域 service 不得直接使用
  `urllib`、厂商 SDK 或自建通用 Provider 发送模型请求。
- Agent 与非 Agent 是同一 API 请求抽象的两种上层调用方式。保留普通 chat/image 产品能力，
  不等于保留其旧 HTTP/SDK 传输实现。
- 锁定版本确实不支持的厂商专属能力必须登记能力、厂商、调用方和移除条件；例外适配器只实现
  该能力，不得承担通用 chat、JSON、工具调用或任意请求体转发。
- 不再使用要求模型输出约定 JSON 的自定义 Tool Protocol；支持 function calling 的模型由
  Pydantic AI 的 Model/Provider 集成处理。
- AI 只能看到当前 Execution Profile 明确绑定的 ToolSet。
- 工具不得暴露 shell、SQL、任意 HTTP、文件系统、凭据或动态平台选择。
- Main Agent 默认只调用高层 Capability，不永久拥有所有低层读写工具。
- AI 输出和 Pydantic 的 schema 校验成功都不等于业务操作已获授权；写操作必须经过 ERP
  Runtime 的权限、审批和确定性业务校验。
- Pydantic AI 类型集中在 Model Factory、Agent Factory、Direct Request Service、Capability
  Adapter、Tool Bridge、运行适配和消息持久化边界，不向类目、发布、商品等领域模块扩散。
- AI Work 不复制完整的模型请求、响应和 tool span；技术细节进入 instrumentation，AI Work
  只保存产品需要的任务状态、业务事件和必要摘要。
- Pydantic Agent 的一次 run 不是持久化工作流。跨进程暂停、人工审批、恢复、队列重试和最终
  状态仍由 Workflow / AI Work 持久化。

### 2.2 不采用的设计

- 不只抽取 Pydantic AI 的字段转换代码并在项目里复制维护。
- 不让前端直接提交 Pydantic AI 的类名、构造参数或 Provider 私有字段。
- 不同时保留 `JsonToolTurnProviderAdapter` 和 Pydantic Agent 两套运行协议。
- 不为普通 chat/JSON/stream 继续保留与 Pydantic Model/Provider 平行的通用 HTTP Provider
  注册表、请求体构造器或流解析器。
- 不因为某个 Capability 不需要 Agent，就绕过 Pydantic Direct Model Requests。
- 不把 `AiToolRuntime` 整体删除后直接让 Pydantic tool 调用领域写操作。
- 不把 Logfire 或其他 tracing 后端当成 AI Work 任务数据库。
- 不为了将来可能回滚而增加 feature flag、shadow run、双写或 legacy fallback。

## 3. 配置与 Provider 适配

### 3.1 前端配置保持产品语义

Pydantic AI 不提供一套可以直接嵌入本项目的通用 Provider 配置表单。前端继续使用项目自己的
稳定字段，例如：

```json
{
  "provider": "openai_compatible",
  "model": "example-model",
  "base_url": "https://example.invalid/v1",
  "api_key": "***",
  "temperature": 0.2,
  "max_tokens": 2000
}
```

这些字段是产品 API，不是 Pydantic AI API。前端不需要随着 Pydantic AI 的构造函数变化而
整改，也不得依赖 `OpenAIProvider`、`OpenAIModel` 等具体 Python 类型名称。

### 3.2 后端集中转换

后端按以下顺序完成转换：

```text
前端 AiProviderConfig
  → app_config 读取、归一化、密钥解析
  → Pydantic Model Factory
  → Pydantic Provider + Model + ModelSettings
      ├─ Agent 用例 → Agent Factory
      ├─ 普通文本/JSON/stream → Pydantic Direct Model Requests
      └─ 图片等模型能力 → Pydantic Capability/native tool 或登记过的窄范围例外
```

转换逻辑必须集中，不能散落在各个 facade 或领域 Agent 中。该边界负责：

- 将项目 provider 名称映射到 Pydantic AI 支持的 Provider/Model；
- 处理 `base_url`、认证、超时、重试和 model settings；
- 拒绝当前模型不支持的能力，而不是静默降级到自定义 JSON 协议；
- 对外隐藏 API key，并禁止把密钥写入 AI Work 事件或 instrumentation attributes；
- 为升级 Pydantic AI 提供单一修改点和契约测试。

远端模型列表、账号元数据等不产生模型推理的管理端点可以使用 focused discovery client；它们
不属于模型请求，但不得顺带承担 chat、Responses、图片生成或图片编辑。连接测试只要会产生
一次模型推理，就必须通过与生产调用相同的 Pydantic Model/Provider 边界。

“引入 Pydantic AI 以获得长期升级兼容”成立的前提，是使用其公开 Agent、Model、Tool 和消息
接口，同时将适配集中。若其内部类型渗透整个业务层，升级成本仍会转移回本项目。

## 4. Pydantic Agent 执行层

### 4.1 Execution Profile

每个 AI 用例由项目自己的 Execution Profile 描述：

```text
use_case_id
+ instructions / prompt reference
+ model binding
+ ToolSet
+ dependency type
+ output type
+ usage limits
+ ERP execution policy
```

`category.product_match` 的目标配置为：

- Agent runtime：Pydantic Agent；
- toolset：`category.search`；
- permission：`category.read`；
- 最大有效搜索次数：3；
- 总 deadline：60 秒；
- output type：`CategoryMatchDecision`；
- 业务结果版本：`category_match.v1`。

Execution Profile 是本项目的稳定配置，不应变成对 Pydantic `Agent(...)` 参数的无约束透传。

### 4.2 Agent Factory

Agent Factory 是 Pydantic AI 的唯一主要装配入口，负责：

- 根据规范化配置创建 Model/Provider；
- 设置 instructions、`deps_type`、`output_type` 和 model settings；
- 将当前 profile 的 ERP 工具转换为 Pydantic ToolSet；
- 配置 Pydantic usage limits 和 instrumentation；
- 执行 `agent.run(...)` 或需要的流式/事件接口；
- 把 Pydantic 输出、消息和 deferred 请求转换为项目边界类型。

Agent Factory 不包含类目评分、发布校验、平台选择或商品写入逻辑。

### 4.3 Run dependencies

请求级状态通过 Pydantic `RunContext` 的 dependencies 提供。建议依赖对象至少包含：

```text
invocation_id / ai_work_id
user_id / tenant_id
permissions
business scope
deadline
approved tool call ids
idempotency context
business event recorder
use-case state（例如 Candidate Ledger）
```

Agent 实例可以复用，但 dependencies 必须按运行创建，不得将用户、凭据、Ledger 或审批状态
放入全局 Agent 对象。

### 4.4 Pydantic Agent 拥有的职责

- Provider/Model 请求与响应格式；
- 原生 function calling 消息；
- model → tool → model 循环；
- 工具参数 schema 的模型侧表达；
- 最终 output type 的解析和校验；
- 模型输出校验失败后的受控重试；
- 消息历史的运行时表示；
- 流式 Agent 事件；
- deferred tool request/result 协议；
- 模型请求、工具调用、token、成本和耗时 instrumentation。

项目不得再为这些职责定义第二套通用轮次协议。

## 5. ERP Tool Bridge 与安全 Runtime

### 5.1 调用链

Pydantic Tool 不是领域 executor 的直接别名。每个暴露给模型的工具都通过统一 Bridge：

```text
Pydantic FunctionTool
  → 从 RunContext 取得 ERP execution context
  → AiToolRuntime.execute(...)
  → permission / scope / side effect / approval / idempotency 校验
  → 领域 executor
  → 规范化业务结果
  → 转换为 Pydantic tool result 或受控异常
```

Tool Bridge 负责 Pydantic 类型与 ERP 类型之间的转换，不复制业务安全规则。

### 5.2 工具定义

工具参数优先由类型化函数签名或 Pydantic model 生成 schema，避免同时手写函数参数、JSON
Schema 和另一套 wire DTO。项目 registry 仍然保存 Pydantic AI 不知道的 ERP 元数据：

- 工具稳定名称和版本；
- 所属 ToolSet；
- required permissions；
- side-effect classification；
- approval policy；
- timeout 和输出上限；
- executor binding；
- 业务审计策略。

`AiToolCall`、`AiToolTurn`、`AiToolTurnRequest`、`protocol_version` 等自定义模型轮次 wire
类型不再属于目标架构。若 Runtime 内部仍需要调用对象，应收敛为不承担 Provider 协议的轻量
ERP command。

### 5.3 AiToolRuntime 保留的职责

`AiToolRuntime` 只负责：

- 在当前 ToolSet allowlist 中查找工具；
- 校验输入和规范化输出；
- permission、tenant、business scope 校验；
- side effect 和 approval 校验；
- 幂等键、`tool_call_id` 和相同参数去重；
- ERP 侧次数、输出大小和总 deadline 限制；
- 调用领域 executor；
- 记录安全审计与必要业务事件；
- 将内部异常归一化为安全、可展示的错误。

它不再负责：

- 调用模型；
- 维护 Agent 轮次；
- 解析模型生成的 JSON tool protocol；
- 拼装 Provider 消息；
- 判断何时再次请求模型；
- 保存完整模型请求和响应。

Pydantic usage limits 与 ERP Runtime 限制可以同时存在，但语义不同：前者保护 Agent run 的
模型/工具资源使用，后者保证业务安全和领域约束。安全相关限制不能只依赖第三方默认行为。

## 6. AI Work、审批与可观测性

### 6.1 AI Work 的产品职责

AI Work 继续负责：

- 面向用户的任务列表、会话和运行状态；
- `queued/running/waiting_approval/succeeded/failed/cancelled` 等业务状态；
- 业务输入摘要、进度、最终结果和可展示错误；
- 审批请求、审批人、审批结果和审批时间；
- 与用户、商品、店铺、平台和 Workflow 的关联；
- 恢复 Agent run 所需的消息历史及 deferred 状态引用；
- 长轮询、流式 UI 或其他前端事件通道；
- 业务保留期限和审计策略。

AI Work Recorder 仍可作为领域代码与持久化实现之间的 seam，但应收敛为业务事件接口，不再
复制 Pydantic instrumentation 已提供的通用 model/tool span。

### 6.2 Pydantic instrumentation 的技术职责

Pydantic AI instrumentation 负责：

- Agent run 与 model request spans；
- tool execution spans；
- provider/model、token usage、成本、耗时和异常；
- OpenTelemetry trace 传播；
- 调试和性能分析。

可使用 Logfire，也可以发送到其他 OpenTelemetry-compatible backend。具体观测后端不是业务
契约，AI Work 不应依赖某个厂商的 trace 存储才能正常运行。

### 6.3 两套数据的关联

每次运行至少建立以下关联：

```text
AI Work task id
  ↔ invocation id / agent run id
  ↔ OpenTelemetry trace id
  ↔ business entity ids
```

AI Work 页面可以通过 trace id 跳转到技术观测系统，但不把完整 trace 复制进业务数据库。

| 信息 | AI Work | Instrumentation |
|---|---:|---:|
| 用户可见任务状态 | 是 | 否 |
| 最终业务结果 | 是 | 可仅记录摘要 |
| 审批记录 | 是 | 可记录 span 事件 |
| 模型请求耗时 | 可汇总 | 是 |
| token / cost | 可汇总 | 是 |
| 每次 tool span | 仅关键业务事件 | 是 |
| 完整业务关联与恢复状态 | 是 | 否 |

### 6.4 Deferred tools 与人工审批

Pydantic AI 的 deferred tool/approval 类型用于表达“本次 Agent run 需要暂停并等待外部结果”。
真正的暂停和恢复流程由项目控制：

```text
工具触发 ApprovalRequired / deferred request
  → Agent 返回未解决的 DeferredToolRequests
  → AI Work 持久化消息历史、请求、业务上下文和审批状态
  → 前端展示审批
  → 用户批准或拒绝
  → ERP 再次验证用户权限、业务状态和幂等条件
  → 原子领取有限 lease；写工具执行前持久化不可自动重放检查点
  → 构造 DeferredToolResults
  → 携带原消息历史恢复 Agent run
  → 先持久化 ready 结果，再由业务终检写入最终状态
```

批准不能只依赖 `tool_call_approved=True`。恢复时仍必须由 ERP Runtime 重新检查审批人与当前
业务权限、目标对象状态、deadline 和幂等条件。

恢复进程在写工具执行前失败时，可在 lease 过期后安全释放 claim；写工具已经开始但未形成
durable `ready` 结果时，状态必须进入 `in_doubt`，禁止自动重放。Agent 已形成的类型化结果、
消息、usage、run/attempt/trace ID 必须先落盘为 `ready`，这样进程在业务终检前退出时可以只
重放结果而不再次调用模型或工具。

消息历史和 deferred 数据必须使用 Pydantic AI 的公开序列化接口，并包在项目自己的版本化
持久化 envelope 中。升级依赖时应提供读取迁移测试；不得直接 pickle 第三方内部对象。

## 7. 类目匹配的唯一设计

### 7.1 平台多态接口

AI 可调用的领域接口只有：

```python
class CategorySearcher(Protocol):
    def search_categories(self, keyword: str) -> CategorySearchResult:
        ...
```

这里故意没有 `platform` 和 `site`。任务入口已经知道当前目标平台，并只实例化一次对应对象：

```text
mercadolibre → MercadoLibreCategorySearcher(site=MLM, ...)
ozon          → OzonCategorySearcher(site=global, ...)
```

后续工具执行始终是：

```python
searcher.search_categories(keyword)
```

平台、站点、凭据作用域、候选上限和 deadline 都属于具体对象的构造上下文。工具层不再传
平台，也不写 `if platform == ...`。

平台实现：

- Mercado Libre：调用 `domain_discovery/search`，返回轻量候选；
- Ozon：搜索服务端缓存的可发布商品类型；展平语料以压缩 JSON 持久化 24 小时，
  瞬时网络错误时可回退到最多 7 天的旧缓存，认证错误不回退；树展平和 corpus hash
  在缓存周期内只计算一次，完整类目树不发送给 AI；
- 新平台：新增具体 `CategorySearcher` 并注册创建工厂，不修改 AI 工具协议。

### 7.2 首轮模型输入

首轮不做后端候选预召回，也不上传类目树、候选、评分、语料身份或检索诊断。

只发送有助于判断商品主体的裁剪事实：

```json
{
  "target": {
    "platform": "mercadolibre",
    "site": "MLM",
    "language": "es-MX"
  },
  "product": {
    "source": {
      "language": "zh-CN",
      "title": "便携式 USB 风扇",
      "description": "桌面静音风扇，USB 供电。"
    },
    "target": {
      "language": "es-MX",
      "title": "Ventilador portátil USB",
      "description": "Ventilador silencioso para escritorio."
    },
    "facts": {
      "source_category": "",
      "brand": "",
      "model": "",
      "bullets": [],
      "attributes": {"Power": "USB"}
    }
  }
}
```

清洗规则：去 HTML、压缩空白、限制长度和数量，删除价格、支付、物流、促销、声明、纯数字
字段名和复杂原始对象。原语言与目标市场语言字段都保留；没有的字段为空。

`mode: tool_loop` 不再是 Provider wire protocol 字段。是否允许工具调用由 Agent 和 ToolSet
配置决定。

### 7.3 唯一 AI 工具

模型看到的工具等价于：

```json
{
  "name": "search_categories",
  "arguments": {"keyword": "ventilador de mesa"}
}
```

工具返回给模型的字段保持最小：

```json
{
  "keyword": "ventilador de mesa",
  "candidates": [
    {
      "category_id": "MLM-FAN",
      "name": "Ventiladores",
      "path_segments": ["Hogar", "Ventiladores"]
    }
  ]
}
```

不返回 `platform/site/raw/corpus/score/provider payload`。平台和站点已绑定，重复发送既浪费
上下文，也会给模型制造越权改变作用域的错觉。

类目详情与属性不是 AI 工具。它们只在模型选出 ID 后由服务端做最终验证。

### 7.4 多轮行为

```text
商品双语事实
  → Agent 提炼目标市场语言的商品主体词
  → search_categories(keyword #1)
  → 有合适候选？
      ├─ 是：返回类型化 CategoryMatchDecision
      └─ 否：改变关键词或搜索角度
             → keyword #2
             → keyword #3
             → 仍无匹配则 abstain
```

领域约束：

- 模型至少成功发起一次搜索后才能选择结果；
- 没有匹配时，必须使用不同角度完成最多 3 次搜索后才能 abstain；
- 相同工具参数由 ERP Runtime 去重，不能伪装成多次搜索；
- 最终 ID 必须存在于 Candidate Ledger，模型不能创造 ID；
- 一旦找到合适候选即可提前结束，不要求无意义地用满 3 次。

这些约束通过 instructions、类型化 output validator、Candidate Ledger 和确定性终检共同实现，
不能只依赖 prompt。

### 7.5 Candidate Ledger 与终检

Ledger 只记录本任务中工具真实返回过的内部候选。AI 看到的是裁剪版本，服务端保留终检所需
的 `platform/site/publishable/type_id/description_category_id`。

最终依次验证：

1. ID 是否在 Ledger；
2. platform/site 是否属于当前目标；
3. 类目是否可发布；
4. 实时详情是否仍存在且 ID 未变化；
5. Ozon 的 `type_id + description_category_id` 是否成对；
6. 必填/选填属性结构是否可读取；
7. 总 deadline 是否仍有效。

模型 confidence 只做诊断，不代替以上确定性验证，也不直接成为自动发布门槛。

HTTP 业务结果不重复返回每轮完整搜索结果，只保留最后搜索词、去重轻量候选、最终决策与
`search_count`。每轮模型和工具技术细节进入 instrumentation；需要向用户解释的关键搜索词、
选择和终检结果可作为 AI Work 业务事件保存。

## 8. 失败语义

必须区分：

| 类别 | 示例错误码 | 含义 |
|---|---|---|
| 输入 | `TARGET_REQUIRED` | 缺少当前平台或站点 |
| 配置 | `AI_MODEL_CONFIGURATION_INVALID` | 前端配置无法创建 Pydantic Model |
| 模型能力 | `AI_MODEL_TOOL_CALLING_UNSUPPORTED` | 当前模型不满足用例能力要求 |
| 搜索依赖 | `CATEGORY_CREDENTIALS_MISSING` | 平台凭据缺失 |
| 平台调用 | `CATEGORY_PROVIDER_TIMEOUT` | 搜索 API 或缓存加载超时 |
| Agent 约束 | `CATEGORY_SEARCH_REQUIRED` | 未调用工具就结束 |
| Agent 约束 | `CATEGORY_SEARCH_INCOMPLETE` | 未匹配却提前 abstain |
| Agent 约束 | `MODEL_SELECTED_UNKNOWN_CATEGORY` | 创造或越界选择 ID |
| 工具安全 | `AI_TOOL_PERMISSION_DENIED` | 当前调用上下文无工具权限 |
| 工具审批 | `AI_TOOL_APPROVAL_REQUIRED` | 需要持久化并等待人工审批 |
| 业务终检 | `CATEGORY_NOT_PUBLISHABLE` | 类目不可发布或 Ozon ID 不完整 |
| 正常未决 | `ABSTAIN_NO_MATCH` | 3 次搜索后仍无合适类目 |

`unresolved` 是可预期业务结果，返回 HTTP 200 并交给人工确认；系统、配置、依赖、Agent 或
安全失败返回 `failed`，由 facade 映射为稳定的项目错误。不得把 Pydantic、Provider 或观测
后端的原始异常直接暴露给前端。

## 9. Main Agent 与未来自动工作流

未来 Main Agent 可按工作流允许的能力推进：

```text
选品 → 采集 → 文案 → 图片处理 → category.match → 属性填写 → 预检 → 上架
```

它调用的是高层 Capability，例如：

- `copy.generate`
- `image.translate`
- `category.match`
- `publish.precheck`
- `publish.enqueue`

Capability 内部可以是普通代码、Pydantic Agent 或组合工作流。Main Agent 只等待结构化结果，
不需要知道内部是否发生了 function call。

写操作继续遵守最小权限、幂等键、确定性前置校验、必要审批、结果持久化和完整业务审计。
不能通过给 Main Agent 全库读写权限来弥补尚未实现的领域能力。

复杂、长期、可暂停的跨领域流程仍由 Workflow / AI Work 持久化编排。Pydantic Agent 可以在
单个阶段内决策，也可以用 deferred tools 表达暂停点，但不取代项目的 durable workflow
owner。

## 10. 目标代码边界

迁移完成后的主要职责应收敛为：

| 职责 | 目标入口或 owner | 处理方式 |
|---|---|---|
| 前端 AI 配置 | `front` 现有配置页面与 API types | 保留产品字段 |
| 配置归一化 | `erp_web/app_config.py` | 保留 |
| Pydantic Model/Provider 创建 | focused Model Factory | 新建或从现有 provider owner 重构 |
| Agent 装配与运行 | focused Agent Factory/Service | 新建，唯一 Agent 入口 |
| 非 Agent API 模型请求 | focused Pydantic Direct Request Service | 新建，复用同一 Model Factory |
| 图片生成等模型能力 | focused Pydantic Capability Adapter | 使用锁定版本公开能力；不支持项显式登记例外 |
| CLI / 浏览器 AI | focused CLI / Browser Adapter | 保留，不进入 Pydantic Model/Provider |
| Pydantic ToolSet 转换 | focused Tool Bridge | 新建 |
| ERP 工具校验执行 | `erp_web/services/ai_tool_runtime.py` | 保留并精简 |
| ERP ToolSet 元数据 | `erp_web/services/ai_tool_registry.py` | 保留并适配 |
| 调用上下文与业务 recorder | `erp_web/services/ai_invocation.py` | 精简 |
| AI Work 持久化 | `erp_web/services/ai_work_service.py` | 保留并增强恢复状态 |
| AI Work HTTP | `erp_web/http_route_units/ai_work_routes.py` | 保留 |
| AI Work 前端 | `front/src/views/AiWorkView.vue` | 保留 |
| 类目搜索接口 | `erp_web/marketplaces/category_provider.py` | 保留 |
| 平台搜索对象 | `erp_web/runtime_units/category_searchers.py` | 保留 |
| 平台 API 适配 | `erp_web/runtime_units/category_providers.py` | 保留 |
| 类目工具 executor | `erp_web/runtime_units/category_tools.py` | 保留并接入 Tool Bridge |
| 类目匹配编排 | `erp_web/facades/category_match_facade.py` | 改为调用 Agent Service |
| Prompt | `config/prompts/category_product_match.json` | 保留并适配 instructions/output |

目标架构中不存在以下 owner：

- `erp_web/services/ai_task_runner.py`；
- `JsonToolTurnProviderAdapter`；
- `_JSON_TOOL_PROTOCOL_SYSTEM`；
- `AiToolTurnProvider` / `AiToolTurnRequest`；
- `CAPABILITY_TOOL_TURN`；
- 仅用于自定义 tool-turn wire protocol 的 fake provider 和序列化模型。
- 为 API chat/JSON/stream 提供第二套厂商协议的通用 HTTP Provider；
- 绕过 Pydantic Model Factory 创建 API 厂商 SDK client 的业务 service。

## 11. 阶段 0～6 实施清单

> 阶段 0～5 已完成的是 Agent/tool-loop 迁移。它们没有完成全部 API Provider 请求收敛；
> 阶段 5 保留普通 chat/image 旧传输栈的决定已被本次架构复核纠正。当前最终验收以阶段 6、
> 本文第 1～10 节和第 12 节为准。

阶段划分只控制交付顺序，不授权双运行。阶段 1 不创建第二个生产 Agent 入口；阶段 2 必须把
`category.product_match` 一次性切到 Pydantic Agent，不增加 feature flag、shadow run、fallback
或双写。每个阶段只有在交付物、删除项、验收条件和测试全部完成后才能进入下一阶段。新增公共
入口必须在当阶段更新 `docs/ai-context-map.md`。

### 阶段 0：基线保护、依赖锁定与 API 定版（已完成）

**交付物**

- 记录 `git status`，识别并保护用户已有修改；后续只在当前工作树上做增量合并。
- 在项目依赖清单中锁定明确的 Pydantic AI 版本，不使用无上限范围。
- 以锁定版本的安装包和官方文档核对 Model、Provider、`ModelSettings`、Agent、
  `RunContext`、Tool/ToolSet、测试模型、structured output、deferred 和 instrumentation
  的公开 API。
- 建立旧路径删除清册：runner、JSON protocol、provider adapter、wire types、旧 fake、旧
  prompt 和旧测试。

**删除项**

- 删除未锁定或重复的 Pydantic AI 依赖声明，只保留一个版本 owner。
- 本阶段不删除或切换 `category.product_match` 生产代码。

**验收条件**

- 新环境可安装出声明的准确版本，运行时版本与依赖声明一致。
- 本迁移采用的类型都能从锁定版本的公开模块导入，不依赖第三方内部实现。
- 类目匹配生产调用链不变，且没有兼容开关、shadow path 或第二个生产入口。

**测试**

- 依赖版本和公开 import smoke test。
- 当前版本 `TestModel` / `FunctionModel` 的最小构造测试。
- 现有类目匹配回归测试。

### 阶段 1：Model/Provider Factory、dependencies 与 Tool Bridge（已完成）

**交付物**

- 建立 `erp_web/services/ai_model_factory.py`，把 `app_config` 归一化后的产品配置集中映射为
  Pydantic Provider、Model 和 `ModelSettings`；前端继续只保存 provider-neutral 字段。
- Factory 集中处理 provider/api style、`base_url`、认证、timeout、generation settings 和
  capability 拒绝，错误中不得泄露 API key。
- 建立 `erp_web/services/ai_agent_dependencies.py`，请求级承载 invocation / AI Work ID、
  user / tenant、permissions、business scope、deadline、approved call IDs、幂等上下文、
  recorder 和 use-case state；不得把这些状态放进全局 Agent。
- 建立 `erp_web/services/ai_tool_bridge.py`，把显式 ERP `AiToolSet` 转成 Pydantic toolset；每次
  调用必须把 Pydantic `tool_call_id` 和参数交给同一个 `AiToolRuntime.execute(...)`，不得直调
  executor。Bridge 工具串行执行，避免绕过 Runtime 的调用预算和去重状态。
- 更新 `docs/ai-context-map.md`，同时明确 `category.product_match` 尚未切换。

**删除项**

- 本阶段不删除旧生产入口；现有 runner 仍是 `category.product_match` 的唯一生产入口。
- 新模块不得 import `AiTaskRunner`、`JsonToolTurnProviderAdapter`、`AiToolTurn`、
  `AiToolTurnRequest` 或 `_JSON_TOOL_PROTOCOL_SYSTEM`，也不得包含 fallback。

**验收条件**

- 前端/API shape 不含 Pydantic 类名或 Provider 私有字段；Pydantic Model/Provider 创建只有一个
  owner，领域 facade 不直接创建 Provider SDK client。
- dependencies 每次运行独立，且 Bridge、Runtime、execution context、recorder 和 ToolSet 绑定
  一致，不能替换成不同权限或业务作用域的对象。
- 所有 Bridge 调用都可证明经过 `AiToolRuntime`，权限、写入策略、审批、幂等、deadline、输入
  输出校验和输出大小限制继续生效。
- Pydantic 类型只存在于 Factory、Bridge 和后续 Agent 运行适配边界；
  `category_match_facade` 尚未创建或调用 Pydantic Agent。

**测试**

- Factory：有效配置、Chat/Responses 映射、`ModelSettings`、环境密钥、无效配置、能力不支持
  和密钥不泄露。
- dependencies：运行隔离、不可变权限/作用域、deadline、审批与幂等上下文传播。
- Tool Bridge：真实 Pydantic tool call、权限拒绝、写入/审批、幂等重复、deadline、输出上限、
  参数 schema、ToolSet/Runtime 绑定和 executor 实际调用次数。
- 架构测试：唯一 Factory/Bridge owner、Bridge 必经 Runtime、Pydantic 类型不扩散、类目生产
  入口未切换。

### 阶段 2：`category.product_match` 首个 Agent 与原子切换（已完成）

**交付物**

- 建立 focused Category Match Agent Service / Execution Profile：`category.product_match`、
  `category.search`、`category.read`、最多 3 次有效搜索、60 秒总 deadline、类型化
  `CategoryMatchDecision` 和 `category_match.v1`。
- 复用 `CategorySearcher`、`CategoryCandidateLedger`、`build_category_search_toolset` 与阶段 1
  Bridge；唯一工具为 `search_categories(keyword)`，不复制搜索、Ledger 或安全执行逻辑。
- 接通类型化 output validator 和确定性终检，拒绝未搜索结束、提前 abstain、重复关键词、调用
  超限、未知/越界 ID、跨站点、不可发布、Ozon ID 不完整和过期 deadline。
- 原子修改 `erp_web/facades/category_match_facade.py`，使 `/api/category-match` 唯一路径只调用
  新 Agent Service；失败直接映射稳定错误，不回退旧 runner。
- 调整 `config/prompts/category_product_match.json` 为 Pydantic instructions/output 语义，保持
  HTTP 路由、前端候选边界和首轮裁剪事实不变。

准确改动范围为：

- `erp_web/facades/category_match_facade.py`；
- 新建 `erp_web/services/ai_agent_factory.py`（唯一 Agent 装配/运行边界）；
- 新建 `erp_web/services/category_match_agent_service.py`（类目 profile、instructions、类型化
  output validator 与 Agent 错误归一化）；
- `erp_web/services/ai_model_config.py`（给 `category.product_match` 增加 `tool_calling` 能力要求，
  删除旧 `execution_mode: tool_loop` Provider 协议元数据）；
- `erp_web/schemas/category.py`（只调整类型化 output 与稳定业务结果所需 shape）；
- `config/prompts/category_product_match.json`；
- `tests/test_category_match_facade.py`、`tests/test_category_tools.py`、新增 Agent 契约测试和架构
  守卫；
- `docs/ai-context-map.md`。

除非测试暴露真实契约缺口，本阶段不修改 `category_routes.py`、前端 `matchCategory` API、平台
搜索器、平台 API 适配或 `erp_web/runtime_units/category_tools.py`；后者直接复用现有
`build_category_search_toolset`、Ledger 和 executor，由新 Agent Service 在外部接入阶段 1 Bridge。

**删除项**

- 从类目生产链删除 `AiTaskRunner`、`JsonToolTurnProviderAdapter`、`CAPABILITY_TOOL_TURN` 和
  provider payload 中的 `mode/execution_mode: tool_loop`。
- 删除类目专用手工 turn 解析、JSON protocol prompt 和只验证旧类目运行路径的测试。
- 通用旧模块和 wire types 的物理删除留到阶段 5，但它们不得再有生产调用方。

**验收条件**

- 唯一生产链为 `/api/category-match` → facade → Category Match Agent Service → Pydantic Agent
  → Tool Bridge → `AiToolRuntime`，不存在旧 runner、fallback、shadow 或双写。
- 首轮请求无 candidates、retrieval、corpus、raw 或完整类目树；ToolSet 只包含
  `search_categories(keyword)`。
- 至少一次、最多三次有效搜索，参数去重、Ledger 约束和确定性终检全部生效。
- Pydantic/Provider 原始异常不暴露给 HTTP。

**测试**

- 使用锁定版本的 `TestModel` / `FunctionModel`，不重新实现 fake wire protocol。
- 覆盖首次上下文、换词、提前结束、三次 abstain、重复参数、未搜索结束、未知 ID、跨站点、
  不可发布、Ozon ID 配对、属性终检、deadline 和 Provider 失败不 fallback。
- 架构测试断言类目 facade 不再 import 旧 runner/adapter。

### 阶段 3：Instrumentation 与 AI Work 业务投影（已完成）

**交付物**

- 接入 Pydantic instrumentation，记录 Agent、model、tool、usage、cost、耗时和异常 span。
- 关联 AI Work task ID、invocation/run ID、trace ID 和业务实体 ID。
- AI Work 只保存业务状态、必要业务事件、结果摘要和可展示错误；建立统一敏感字段脱敏策略。

**删除项**

- 删除 AI Work 中与 instrumentation 重复的通用 model/tool span 或完整请求响应副本。
- 删除把观测后端当成业务任务状态 owner 的依赖。

**验收条件**

- 观测后端不可用不影响业务运行或最终状态持久化。
- trace 可关联 AI Work，但 AI Work 不依赖 trace 才能恢复。
- instrumentation 不含 API key、完整凭据或禁止记录的敏感商品字段。

**测试**

- instrumentation span、trace 关联、usage/cost 和异常记录测试。
- 敏感字段脱敏、业务事件最小投影和观测后端故障隔离测试。

### 阶段 4：Deferred approval、持久化恢复与错误语义（已完成）

**交付物**

- 使用 Pydantic 公开消息序列化接口，把消息历史和 deferred 状态写入项目版本化 envelope。
- 持久化 DeferredToolRequests、审批状态和恢复引用；批准、拒绝和恢复时重新校验审批人权限、
  业务状态、deadline、scope 和幂等条件。
- 使用有限 lease 原子 claim；写工具执行前记录 checkpoint，完成 Agent 恢复后先持久化
  `ready` 结果。安全重试仅允许发生在工具尚未开始时；无法确认的执行进入 `in_doubt`，不得
  自动重放。
- 收口配置、模型能力、工具安全、Agent 约束、终检和正常 abstain 的稳定项目错误。

**删除项**

- 删除 pickle、第三方内部对象直接持久化和自定义 provider turn/deferred DTO。
- 删除只检查 `tool_call_approved=True` 而不重新执行 ERP 校验的路径。

**验收条件**

- 暂停后可跨进程恢复；拒绝、批准和重复恢复均有确定结果且不重复执行副作用。
- 业务终检前崩溃可重放 durable `ready` 结果而不重新执行 model/tool；lease 过期时只有未开始
  工具的 claim 可回收，已开始写工具的状态稳定进入 `in_doubt`。
- 版本化 envelope 可 round-trip，并为依赖升级后的旧数据提供读取迁移。
- 原始 Pydantic/Provider 异常均在边界转换为稳定项目错误。

**测试**

- deferred 请求持久化、拒绝、批准、恢复、重复恢复幂等、过期 deadline 和权限变化。
- claim lease、执行前 checkpoint、失败前安全释放、写后 `in_doubt`、ready 结果重放和
  ready→业务终态测试。
- 消息 envelope round-trip / 迁移读取和错误码/HTTP 状态契约测试。

### 阶段 5：旧 Runtime/JSON 协议彻底删除与唯一入口守卫（已完成）

**交付物**

- 完成全库旧符号、旧配置和旧架构说明清理；若 Runtime 仍接收 `AiToolCall`，改为不承担
  Provider wire protocol 的轻量 ERP command。
- 更新 `docs/ai-context-map.md`、本架构文档和架构测试，使 Pydantic Agent 成为唯一 Agent
  loop owner。当时保留了普通 chat/image 产品能力及其旧 Provider 传输实现；前者仍然保留，
  后者不是最终目标架构，并由阶段 6 收敛。

**删除项**

- `erp_web/services/ai_task_runner.py`。
- JSON/native tool-turn adapter、`AiTaskExecutionError`、`JsonToolTurnProviderAdapter`、
  `_JSON_TOOL_PROTOCOL_SYSTEM`、`AiToolTurnProvider`、`AiToolTurnRequest`、
  `CAPABILITY_TOOL_TURN`。
- `AiToolTurn`、`protocol_version`、仅服务旧协议的 serializer/validator/fake/mock、
  `tests/test_ai_task_runner.py`、旧 prompt/config 字段和过期文档断言。
- 所有 fallback、dead code 和旧调用链说明。

**验收条件**

- 活跃代码无旧 runner、JSON tool protocol、旧 adapter 或旧 wire types；所有 Agent tool loop
  只由 Pydantic Agent 执行。
- 所有 ERP 工具仍只经 `AiToolRuntime`，`category.product_match` 仍只有
  `/api/category-match` 一个业务入口。
- 全库扫描只允许删除清单和架构负向断言引用旧符号，不允许 import、实例化或运行调用残留。

**测试**

- 全量 `.venv/bin/python -m pytest tests -q`，以及 import/compile 和架构测试。
- 旧模块不可导入、旧符号无生产调用方、唯一 Agent/Tool Bridge/Runtime 路径守卫。
- Provider Factory、Tool Bridge、structured output、消息恢复、deferred approval 和
  instrumentation 全套契约测试。

### 阶段 6：API Provider 全量收敛（已完成）

**目标**

- 保留普通 chat、JSON、stream、图片生成/编辑等产品能力；删除它们与 Pydantic 平行的通用
  API 请求实现。
- `connection_type=api` 与“是否使用 Agent”完全解耦：Agent 用例走 `Agent`，非 Agent 用例走
  Pydantic Direct Model Requests 或 Pydantic capability，但二者共享同一 Model Factory。

**交付物**

- 盘点 `copy.generate`、`copy.preview`、`research.web_search`、图片生成/编辑、模型测试和其他
  API 推理调用，逐项标注目标 Pydantic 入口。
- 普通文本、JSON 和流式调用迁移到 Pydantic Direct Model Requests；业务 service 只消费
  项目稳定输入/输出，不构造厂商 URL、header、request body 或流事件。
- 图片生成迁移到锁定版本支持的 Pydantic capability/native tool。图片编辑等不支持项必须先
  形成例外登记，记录缺失能力、限定调用方、测试和移除条件，再保留 focused adapter。
- 模型连接测试通过生产同款 Model/Provider 发起；远端模型列表等纯 discovery 行为与推理请求
  分离。
- AI Work、重试、超时、错误归一化、流式事件和 instrumentation 在 Agent/Direct 两条上层
  调用方式中保持一致语义。

**实现结果**

- `ai_gateway_http_providers.py`、`ai_image_provider.py` 及 API Provider 注册分支已删除；
- chat/JSON/stream 由 `ai_direct_request_service.py` 调用 Pydantic Direct Model Requests；
- 模型测试的推理探针与生产请求共享 Model Factory，模型目录发现已拆成非推理模块；
- 图片 generate/edit 使用登记过的 `OpenAIImagesModel` focused Pydantic Model：它只能由
  Model Factory 创建，只能经 Direct Model Request 调用，不具备通用 chat 能力；
- 图片 edit→generate fallback 已删除，调用失败会保持真实失败语义；
- 架构测试禁止旧模块、原始 API `urllib` 请求、第二个 Direct Model owner 和 API Provider
  注册分支。

**删除项**

- `OpenAICompatibleProvider`、`OpenAIResponsesProvider` 作为正式业务推理发送器的职责；
- 自建 chat/Responses URL、通用请求体、SSE 流解析和 `urllib` 模型请求路径；
- 已被 Pydantic capability 覆盖的直接厂商 SDK 图片请求；
- API、CLI、Browser 混合注册表中属于 API 推理派发的分支。CLI/Browser 适配器自身保留。

**验收条件**

- 每个 `connection_type=api` 的生产推理调用都可追溯到 centralized Model Factory 创建的
  Pydantic `Model`/`Provider`；不存在业务侧直连厂商 API。
- Agent tool loop 仍只有 `AiAgentFactory` 一个 owner；非 Agent 调用不得为了复用 Pydantic
  而伪装成 Agent。
- 例外清单之外，生产代码不存在 AI 厂商 SDK client、模型 `urllib` 请求或自建通用 wire
  protocol；例外清单中的适配器不能支持任意 chat/JSON 请求。
- 架构测试从“保护 HTTP Provider 模块存在”改为“禁止 API 推理绕过 Pydantic Model Factory”。

**测试**

- Direct request 的 Chat/Responses、JSON、stream、generation settings、usage、错误、超时和
  instrumentation 契约测试；
- Agent 与 Direct 共享 Model Factory、认证和 Provider profile 的架构测试；
- 图片能力的支持矩阵和每个显式例外的边界测试；
- 文案、产品调研、图片和模型连接测试的业务回归测试。

依赖升级继续遵守：锁定明确版本；升级前阅读 Pydantic AI upgrade guide；重点运行 Provider
Factory、Tool Bridge、消息恢复、deferred approval、structured output 和 instrumentation 契约
测试。若公开序列化格式变化，为已持久化消息增加迁移读取，不恢复已删除的自定义 Agent
Runtime。

## 12. 验收重点

### 12.1 架构

- 所有 AI tool loop 只通过 Pydantic Agent 运行；
- 所有 `connection_type=api` 的 AI 推理请求都通过 centralized Pydantic Model Factory；
- Agent 用例使用 Pydantic Agent，非 Agent 文本/JSON/stream 使用 Pydantic Direct Model
  Requests，二者不得形成两套厂商协议实现；
- 普通 chat/image 产品能力可以保留，但其 API 传输不能成为 Pydantic 之外的通用平行路径；
- CLI 与 Browser Provider 保持独立，不纳入 Pydantic API Model/Provider；
- 只有登记过且被锁定版本明确缺失的厂商能力可以使用 focused 例外适配器；
- 项目中不存在自定义 JSON Tool Protocol 或旧 `AiTaskRunner` 调用方；
- 领域 facade 不直接创建 Provider SDK client；
- Pydantic 类型没有扩散到领域 executor 和 HTTP API schema；
- 前端配置字段保持产品语义，由后端集中映射；
- 所有工具仍经过 `AiToolRuntime`，不能绕过权限、审批或幂等校验；
- AI Work 和技术 instrumentation 有明确 owner，并通过 trace id 关联而非复制数据。

### 12.2 类目匹配

- 首个 model request 中不存在 candidates、retrieval、corpus、raw 或完整类目树；
- ToolSet 只包含 `search_categories`，输入只允许 `keyword`；
- 工具定义与 executor 不接收 platform/site；
- 平台只在 `create_category_searcher` 创建处选择一次；
- Mercado Libre 走远端 discovery，Ozon 走服务端缓存搜索；
- 模型不搜索、提前 abstain、创造 ID、跨站点选择都被拒绝；
- 三次有效搜索、去重、输出大小和 60 秒总 deadline 生效；
- 最终详情和属性由服务端验证，不进入 AI 工具上下文；
- 自动匹配只有 `/api/category-match` 一个入口；
- Main Agent、页面和独立领域任务都可复用同一个 `category.match` Capability。

### 12.3 测试

- 使用 Pydantic AI 提供的测试模型或受支持的模型替身验证 Agent，不重新实现 fake wire
  protocol；
- 测试 Tool Bridge 能正确传递 dependencies、权限、deadline、审批和 Ledger；
- 测试输出校验失败、未知 category ID、重复搜索和 tool limit；
- 测试 deferred approval 的持久化、拒绝、批准、恢复和重复恢复幂等；
- 测试技术 trace 不包含 API key、完整凭据或不应记录的敏感商品字段；
- 架构测试验证旧 runner、旧 capability、旧协议类型和旧 prompt 不存在。

## 13. 官方参考

- [Agents](https://pydantic.dev/docs/ai/core-concepts/agent/)
- [Function Tools](https://pydantic.dev/docs/ai/tools-toolsets/tools/)
- [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
- [Debugging & Monitoring with Pydantic Logfire](https://pydantic.dev/docs/ai/integrations/logfire/)
