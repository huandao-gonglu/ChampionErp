# AI Tool 注解化升级方案

> 状态：已实施（2026-08-11，落地阶段 1 元数据/Compiler/Catalog、阶段 2 类目属性枚举
> 只读试点、阶段 3 类目匹配只读能力注解化与阶段 5 架构收口；阶段 4 写工具仍按第 12
> 节门控待评估）
>
> 本阶段目标：在不改变现有 Agent、Tool Runtime、领域终检和业务回填流程的前提下，
> 用类型化能力函数、`@ai_tool` 元数据和显式 Catalog 消除手写 Tool Schema、机械 executor
> adapter 与重复 ToolSet 绑定代码。
>
> 关联已实施阶段：[全局 Agent 已实施方案](./global-agent-next-stage.md)；其 planning run 复用本方案的
> 注解、Compiler、显式 Catalog、可信 Scope 和只读 Tool Runtime 边界。

## 1. 结论

本次升级不是把所有现有 executor 直接删除，也不是让 AI 自动获得所有业务函数。

最终职责必须收敛为：

```text
类型化领域能力函数
  → 拥有真实领域行为、作用域校验和领域状态更新

@ai_tool + AiToolCompiler
  → 声明稳定工具契约
  → 从类型生成输入、输出 Schema
  → 生成只负责解析、注入、调用和序列化的 executor adapter

显式 AiToolCatalog + 场景 allowlist
  → 决定哪些能力可以被发现
  → 决定本次 Agent 可以看到哪些能力

AiToolRuntime
  → 权限、deadline、预算、审批、运行级幂等前置检查、审计和输入输出边界

领域 Capability / Store
  → 消费稳定 operation key
  → 保证跨进程、跨恢复和真实写入的业务幂等

领域 validator / facade
  → 决定 Agent 最终结果能否进入业务数据
```

注解化消除的是接入样板代码，不会自动消除领域行为。

## 2. 当前问题的准确边界

当前新增一个 AI Tool 通常需要同时编写：

- `AiToolDefinition` 及手写输入、输出 JSON Schema；
- executor 的参数读取、类型转换和输出投影；
- `deadline_aware_tool_executor(...)` 包装；
- `AiToolSet.bind(...)` 的 definition/executor 映射。

其中只有一部分属于机械重复。现有 executor 还可能拥有真实领域职责，不能直接删除。

以类目属性枚举为例，当前 executor 除了调用
`fetch_category_attribute_values`，还负责：

- 校验 `attribute_id` 是否属于当前类目允许集合；
- 区分平台强制枚举和本地建议 options；
- 记录查询尝试、失败和真实候选到 `CategoryAttributeValueLedger`；
- 对批量请求逐项执行和映射安全错误；
- 将平台结果裁剪成模型可见结构。

后续商品属性回填不发生在 Tool executor 内，而是继续由
`category_attribute_ai_fill.py::_validated_agent_attributes(...)` 使用 Ledger 校验 Agent
最终 assignments，再由 `apply_ai_model_attribute_fill(...)` 合并到
`draft["attributes"]`。

因此本阶段必须先把 executor 中的领域行为提炼成类型化领域能力函数，再用通用 adapter
替换剩余的机械代码。不得直接注解底层平台读取函数后删除 Ledger、allowlist 或错误映射。

## 3. 升级目标

- 一个业务行为只有一个真实实现；AI adapter 不复制平台查询、领域校验或持久化逻辑。
- 类型化能力通过 `@ai_tool` 声明稳定工具契约。
- 使用 Pydantic `TypeAdapter` 生成并执行输入、输出类型契约。
- 自动生成的 executor adapter 只负责解析、可信注入、调用和 JSON 序列化。
- Catalog 采用显式清单，不扫描整个项目，不通过 import side effect 修改全局注册表。
- 每个 Execution Profile 继续维护独立 allowlist。
- 平台、店铺、租户、类目、凭据、Ledger、deadline 和幂等上下文不得由模型提供。
- 所有工具继续经过现有 `AiToolRuntime` 和 Pydantic Tool Bridge。
- 现有领域 validator 和业务回填路径保持不变。
- 为已实施的全局 Agent planning run 提供显式、默认最小暴露的只读能力目录。

## 4. 非目标

- 本次注解化升级本身不负责全局 Agent、Memory 或动态任务编排；全局 Agent 已由
  [独立方案](./global-agent-next-stage.md) 以轻量顺序 Controller 落地，不改变本方案的 Tool 边界。
- 不实现 `search_operations` 或运行中动态挂载工具。已实施的全局 Agent 使用静态九项 Capability map，
  planning run 只绑定一个 `drafts_query` 只读 Tool，不依赖动态发现或挂载。
- 不允许 AI 任意调用所有 HTTP handler、业务函数或数据库操作。
- 不把路由 handler、Store 原语、任意 SQL、任意 HTTP、shell 或文件系统注册为 AI Tool。
- 不把现有领域 validator 迁入装饰器或通用 Runtime。
- 不让查询 Tool 隐式产生写副作用。
- 不因为注解化而放宽写操作权限、审批、幂等或恢复约束。
- 不保留手写 Tool 与注解 Tool 两条生产路径、旧名称 alias 或 feature flag。

## 5. 设计原则

### 5.1 能力声明、能力发现和能力授权分离

```text
@ai_tool
  = 该函数具备成为 AI Tool 的稳定契约

AiToolCatalog
  = 应用明确收录的 AI 能力全集

Execution Profile allowlist
  = 当前用例理论上允许使用的能力

run-scoped AiToolSet
  = 本次用户、权限和业务 scope 实际可见的能力

AiToolRuntime
  = 本次具体调用能否执行
```

给函数增加注解不等于自动授权给任何 Agent。

### 5.2 注解只保存不可变元数据

`@ai_tool` 只在函数对象上附加不可变元数据，不在 import 时注册，不读取配置，不创建
Runtime，也不执行领域逻辑。

建议接口：

```python
def ai_tool(
    *,
    name: str,
    description: str,
    permission: str,
    side_effect: Literal["none", "write"] = "none",
    approval_required: bool | None = None,
    idempotency: Literal["none", "required"] = "none",
    idempotency_keys: tuple[str, ...] = (),
    version: str = "1",
) -> Callable[[ToolFunctionT], ToolFunctionT]:
    ...
```

约束：

- `name` 是 Catalog、allowlist、Prompt、日志和 Provider 共用的唯一正式名称；必须使用全局唯一的
  snake_case，并满足 `^[A-Za-z0-9_-]{1,64}$`，不得包含 `.`；领域归属由 ToolSet/Catalog 组织，
  不编码进 Provider 不支持的标点；
- `description` 是面向模型的稳定契约，必须显式提供，不能仅依赖容易被顺手修改的业务 docstring；
- `version` 在输入 Schema、输出 Schema、副作用或可观察语义改变时必须升级；
- `side_effect="none"` 的函数不得写 Store、远端平台或领域状态；允许更新仅用于本次调用安全终检的
  request-scoped Ledger，但必须在文档中显式说明；
- 只读工具的 `approval_required=None` 由 Compiler 规范化为 `False`；
- `side_effect="write"` 必须显式声明 `approval_required=True|False`，不得使用隐式默认值；
- `side_effect="write"` 必须声明 `idempotency="required"`，并声明非空、稳定的
  `idempotency_keys`；
- `idempotency_keys` 表示 Runtime 在执行前必须从可信 `AiExecutionContext.idempotency_context`
  取得的键；模型不可提供这些值；
- Compiler 必须把 idempotency policy 编译进 `AiToolDefinition` 或等价的不可绕过
  `AiToolBinding` policy，不能只把它留在装饰器 metadata 中；
- 领域 Capability/Store 必须实际使用这些键建立唯一 operation 或写入约束；Runtime 的存在性检查
  不能代替业务持久化幂等；
- 注解不得包含某个 Agent/Profile 名称，工具能力和 Agent 授权策略保持分离。

### 5.3 类型化领域能力拥有业务行为

试点的目标形态不是直接注解底层 `fetch_category_attribute_values`，而是先形成完整的类型化能力：

```python
@ai_tool(
    name="category_attribute_values_search",
    description=(
        "批量查询当前类目属性允许使用的真实枚举值，并返回后续终检可验证的候选。"
    ),
    permission="category.attribute.read",
    side_effect="none",
    version="1",
)
def search_category_attribute_values(
    request: CategoryAttributeValueSearchRequest,
    scope: Annotated[CategoryAttributeToolScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> CategoryAttributeValueSearchResult:
    """领域能力实现；不是通用 adapter。"""

    # 这里保留当前 executor 的真实领域行为：
    # 1. attribute allowlist
    # 2. strict enum / local options 分流
    # 3. provider 查询和 deadline 传播
    # 4. Ledger 尝试、失败和候选记录
    # 5. 安全错误与输出 shape
    ...
```

这个函数可以调用现有 Store/Provider 业务入口，但不能复制它们的实现。它的价值是把原来藏在
闭包 executor 中的领域行为提升成可独立测试、可复用、类型明确的能力。

### 5.4 通用 executor adapter 只做机械转换

Compiler 生成的 executor adapter 固定执行：

```text
arguments dict
  → Request TypeAdapter.validate_python(...)
  → 从绑定 scope 解析 Injected 参数
  → 注入本次 AiExecutionContext
  → 调用类型化领域能力函数
  → Result TypeAdapter.validate_python(...)
  → Result TypeAdapter.dump_python(mode="json")
  → 交回 AiToolRuntime 做统一输出边界校验和审计
```

adapter 不得包含类目、商品、平台、发布或 Ledger 分支。

## 6. 支持的函数签名

第一阶段只支持刻意收窄的签名，避免演变成参数映射 DSL：

- 恰好一个模型可见的 `request` 参数；
- `request` 必须是 Pydantic Model，生成的根 Schema 必须是 object；
- 一个明确的 Pydantic Model 返回类型；
- 其余参数必须使用 `Annotated[T, Injected()]`；
- 写函数必须包含 `Annotated[AiExecutionContext, Injected()]`，以消费可信幂等、deadline 和审批
  上下文；
- 不支持 `*args`、`**kwargs`、位置专用参数、重载和未解析前向引用；
- 第一阶段只支持同步函数；异步函数必须在 Runtime 明确支持 await 后单独引入；
- 不支持递归 Model、任意 discriminated union 或自定义不可证明的 JSON serializer；
- 模型可见请求必须拒绝未知字段。

业务函数签名不适合直接暴露时，可以增加薄的类型化能力 adapter。该 adapter 可以做输入输出
shape 转换和上下文装配，但不能复制业务逻辑。

## 7. Schema 编译与运行时校验

### 7.1 不能直接透传 Pydantic 原始 Schema

Pydantic 嵌套 Model 通常生成 `$defs`、`$ref`、`anyOf` 等结构，而当前
`AiToolRuntime` 的轻量 JSON Schema 校验器只完整执行受控子集。仅仅验证“Schema 是合法
JSON Schema”不足以证明 Runtime 真正执行了所有约束。

`AiToolCompiler` 必须：

1. 校验工具名称满足 Provider 通用的 `^[A-Za-z0-9_-]{1,64}$` 约束；
2. 使用 Request/Result `TypeAdapter` 生成 Schema；
3. 展开本地 `$defs/$ref`，拒绝递归引用；
4. 将简单 nullable union 规范化为 Runtime 支持的 `type: [T, "null"]`；
5. 保留 object/array/基础类型、required、properties、`additionalProperties: false`、enum、
   const、长度、数量和数值边界；
6. 遇到无法等价编译的关键字或类型时启动失败，而不是静默忽略；
7. 用编译后的 Schema 创建 `AiToolDefinition`；
8. 运行时仍使用 TypeAdapter 做精确输入/输出类型校验，Runtime 的 Schema 校验继续作为统一
   安全边界。

Compiler 必须使用显式关键字策略，不能笼统地区分“关键”与“不关键”：

- `$schema`、`$id` 等不参与当前工具契约的声明关键字可以删除；
- `title`、`description`、`default`、`examples` 等非断言 annotation 可以保留，Runtime 不需要把
  它们当作安全约束；默认值最终仍由 Request TypeAdapter 应用；
- `pattern`、带校验语义的 `format`、`exclusiveMinimum`、`exclusiveMaximum`、`multipleOf`、
  `uniqueItems`、复杂 `oneOf/anyOf/allOf/not` 等 Runtime 尚不能等价执行的断言关键字必须启动
  失败，除非 Compiler 已实现等价规范化；
- 未进入明确 allowlist 的未知关键字一律启动失败，禁止依赖当前轻量 Runtime “忽略未知关键字”
  的行为。

### 7.2 TypeAdapter 错误语义

Runtime 的轻量 Schema 校验通过后，TypeAdapter 仍可能因为字段 validator、默认值装配或精确类型
规则拒绝数据。通用 adapter 必须把边界错误转换为稳定项目错误：

- Request TypeAdapter 失败：`TOOL_INPUT_SCHEMA_INVALID`；
- 领域函数返回值无法通过 Result TypeAdapter：`TOOL_OUTPUT_SCHEMA_INVALID`；
- `dump_python(mode="json")` 失败或产生非 JSON 数据：`TOOL_OUTPUT_SCHEMA_INVALID`；
- 领域函数可抛出 `AiToolExecutionError(code, message, retryable=...)` 声明可公开的结构化错误；
- `code` 不依赖中央枚举表，Runtime、Bridge 和 Agent 边界必须原样透传
  `code/message/retryable`；
- 未显式声明为安全错误的异常统一返回 `TOOL_EXECUTION_FAILED` 和安全文案，原始异常不得穿透
  Tool 边界或暴露给模型和用户。

`AiToolRuntime` 需要保留这些显式错误码，不能把 adapter 的输入、输出错误重新折叠为普通执行
失败，也不能把未知错误折叠为权限错误。AI Work 统一展示 `code + message`，前端不维护错误码
文案枚举。

### 7.3 契约版本

Compiler 为每个工具生成规范化契约指纹，至少覆盖：

- name/version/description；
- 输入和输出规范化 Schema；
- permission、side effect、approval 和 idempotency policy；
- Injected 参数类型集合。

测试保存当前契约快照。同一工具契约指纹变化但 `version` 未变化时测试失败，防止 deferred
恢复或持久化消息在不知情的情况下使用了新语义。

`AiToolSet` 还必须生成覆盖其全部工具契约指纹的 `toolset_contract_fingerprint`。新建 deferred
状态时持久化该值，恢复前与当前 ToolSet 比较；当前仅由 `name@version` 拼接的 signature 应被
直接替换。由于 Agent state 是真实持久化格式，实施时必须为旧 envelope 提供读取迁移，不能只
修改新写入格式。

## 8. 可信上下文注入

以下值默认不得由模型提供：

- tenant/user/store ID；
- platform/site/category/product ID；
- credential、Provider/client；
- permission、deadline、预算、审批记录和幂等键；
- 当前任务允许访问的属性、商品或类目集合；
- Ledger、业务事件 recorder 和持久化 owner。

### 8.1 不允许依赖 `AiAgentDependencies` 绑定 Catalog

当前构造顺序是：

```text
AiToolSet
  → AiToolRuntime
      → AiAgentDependencies
```

因此 `AiToolCatalog.bind(..., dependencies=AiAgentDependencies)` 会形成
`dependencies → runtime → toolset → bind(dependencies)` 构造环，禁止采用。

Catalog 使用独立的绑定作用域，并显式接收 Execution Profile 声明的权限：

```python
@dataclass(frozen=True)
class AiToolBindingScope:
    providers: Mapping[type[Any], Any]


class AiToolCatalog:
    def bind(
        self,
        *,
        toolset_id: str,
        allowed_tools: Collection[str],
        scope: AiToolBindingScope,
        declared_permissions: Collection[str],
    ) -> AiToolSet:
        ...


toolset = catalog.bind(
    toolset_id=CATEGORY_ATTRIBUTE_VALUE_TOOLSET_ID,
    allowed_tools=("category_attribute_values_search",),
    scope=AiToolBindingScope.from_values(
        CategoryAttributeToolScope(
            platform=platform,
            category_record=category_record,
            ledger=ledger,
        ),
    ),
    declared_permissions={"category.attribute.read"},
)
```

绑定 scope 只提供领域对象；本次 `AiExecutionContext` 继续由 `AiToolRuntime` 调用 executor 时
动态提供。Compiler 必须检查每个 Injected 类型只有一个可信 provider，缺失或重复都应在绑定时
失败。

Catalog bind 此时看不到 invocation-specific `AiExecutionContext`，因此它只能验证写工具已经
声明 idempotency policy、所需 key 和 execution 注入，不能验证本次幂等值是否存在。实际值必须
由 `AiToolRuntime.execute(...)` 在调用 executor 前检查；缺失时返回稳定
`TOOL_IDEMPOTENCY_CONTEXT_REQUIRED`，不得进入领域函数。

模型只会看到 Request Model 的字段，无法通过同名 JSON 字段覆盖 Injected 参数。

## 9. 显式能力目录与场景 ToolSet

### 9.1 显式清单

第一阶段不扫描整个包，也不依赖装饰器 import side effect。`AiToolCatalog` 只编译调用方显式
提供的函数引用：

```python
CATEGORY_ATTRIBUTE_AI_TOOLS = (
    search_category_attribute_values,
)

CATEGORY_ATTRIBUTE_TOOL_CATALOG = AiToolCatalog.compile(
    CATEGORY_ATTRIBUTE_AI_TOOLS,
)
```

各用例在自己的 composition root 编译并绑定最小领域清单；已实施的全局 Agent 也只绑定自己的
`drafts_query` 清单，不合并应用全量 Catalog。领域无关的 Catalog/Compiler 不得反向 import
类目、平台、发布或商品模块。

启动或测试必须检查：

- 工具名称是否全局重复；
- 工具名称是否满足 Provider 通用字符集和长度限制；
- 函数是否真的带有合法 `@ai_tool` 元数据；
- Request/Result 和 Injected 签名是否受支持；
- Schema 能否无损编译到 Runtime 支持子集；
- 权限、副作用、审批和幂等声明是否完整；
- 契约版本是否与快照一致。

### 9.2 场景 allowlist 仍然显式

```python
CATEGORY_ATTRIBUTE_FILL_TOOLS = (
    "category_attribute_values_search",
)
```

`AiToolCatalog.bind(...)` 必须同时拒绝：

- allowlist 引用了 Catalog 不存在的工具；
- scope 无法满足工具的 Injected 参数；
- 当前 Execution Profile 未声明工具需要的 permission；
- write 工具缺少明确审批策略、幂等策略、非空 `idempotency_keys` 或 execution context 注入。

未进入场景 allowlist 的工具对模型不可见，也不能通过 Runtime 按名称执行。

## 10. 写工具策略

只读试点稳定之前不迁移写工具。写工具接入时必须同时满足：

- `side_effect="write"`；
- 显式 `approval_required=True|False`；
- `idempotency="required"`；
- 非空、稳定的 `idempotency_keys`；
- Execution Profile 明确 `allow_write=True`；
- Runtime 在 executor 前确认本次可信 idempotency context 包含全部所需键；
- 业务函数从 Injected execution/scope 消费稳定幂等键，并由 Capability/Store 建立持久化唯一
  operation；
- Runtime 在真实写入前继续持久化执行 checkpoint；
- 写入后执行确定性领域校验并返回持久化结果标识；
- deferred approval 恢复时重新校验权限、scope、版本和幂等上下文。

Runtime 当前基于 call signature 的单次运行去重不能代替业务持久化幂等。

幂等责任拆分如下：

```text
Compiler / Catalog
  → 声明并验证“这个工具要求哪些幂等键”

AiToolRuntime
  → 验证“本次可信上下文是否提供全部幂等键”

Capability / Store
  → 用这些键保证同一业务 operation 不重复写入或重复提交远端任务
```

查询和写入必须是两个显式工具。例如：

```text
category_attribute_values_search  # 只读
product_attributes_update         # 写入
```

不得让原本只读的查询 Tool 隐式回填业务数据。

## 11. 类目属性枚举试点

### 11.1 保持不变

- `fetch_category_attribute_values` 继续作为平台枚举读取业务入口；
- `CategoryAttributeValueLedger` 继续记录真实候选和失败属性；
- 强制枚举必须匹配本次 Ledger 中的 `dictionary_value_id + value`；
- 非强制枚举没有合适候选时仍可填写有商品依据的自定义文本；
- `_validated_agent_attributes(...)` 和 `apply_ai_model_attribute_fill(...)` 继续负责终检与回填；
- 所有调用继续经过 `AiToolRuntime` 和 Pydantic Tool Bridge。

### 11.2 需要替换

- 将闭包 executor 中的领域行为提炼为 `search_category_attribute_values(...)`；
- 为请求、逐项结果和总结果建立 Pydantic Model；
- 用 `@ai_tool` 生成 definition 和机械 adapter；
- 用显式 Catalog + allowlist 替换手写 definition/executor 字典；
- 采用全局唯一且 Provider 安全的 snake_case 名称 `category_attribute_values_search`；
- 原子删除旧 `search_attribute_values` definition、旧绑定、旧测试断言和旧文档说明；不保留 alias。

## 12. 实施步骤

### 阶段 1：元数据、Compiler 与 Catalog

1. 新增 dependency-light `@ai_tool`、`Injected` 和不可变 metadata。
2. 实现受限签名检查和 TypeAdapter 编译。
3. 实现 `$defs/$ref` 展开、支持子集规范化和拒绝未知约束。
4. 实现通用 executor adapter。
5. 实现显式 `AiToolCatalog.compile(...)` 和重复名称检查。
6. 实现独立 `AiToolBindingScope`，不得依赖 `AiAgentDependencies`。
7. 扩展 `AiToolDefinition`/`AiToolBinding` 的幂等 policy，并在 Runtime 执行前验证可信 key。
8. 用完整契约生成 `toolset_contract_fingerprint`，接入 deferred state 创建、恢复和旧 envelope
   读取迁移。
9. 删除现有无生产调用方的 `AiToolRegistry` 容器；`AiToolCatalog` 成为唯一 Catalog 抽象，
   `AiToolSet` 继续表示单次场景 allowlist，不保留两个含义重叠的 Registry/Catalog owner。
10. 增加契约指纹、版本快照和错误语义测试。

### 阶段 2：只读试点原子迁移

1. 建立属性枚举 Request/Result Pydantic Model。
2. 把当前 executor 领域逻辑提炼为类型化能力函数。
3. 用注解 Catalog 生成 ToolSet。
4. 删除对应手写 Schema、definition 和机械 executor 绑定。
5. 更新 focused Agent prompt、测试和文档中的工具名称。
6. 检索并删除旧工具符号与名称残留。

### 阶段 3：扩展低风险只读能力

依次评估类目关键字搜索和树导航。只有当领域状态、调用预算和候选 Ledger 已进入类型化能力
函数后，才能删除其手写 executor。

### 阶段 4：写工具

在审批、持久化幂等、恢复和 checkpoint 契约完成后迁移写能力。不得为了让注解化“看起来完整”
提前引入不安全写工具。

### 阶段 5：架构说明收口

实施完成后更新 `docs/ai-context-map.md` 和 `tests/test_ai_context_architecture.py`，说明：

- Tool Runtime 和 Bridge 仍是唯一执行边界；
- definition 和机械 adapter 由 Compiler 生成；
- 领域行为只能位于 focused capability/service/runtime unit；
- Catalog 使用显式清单，不存在动态 import 注册表；
- 旧手写入口已经删除。

## 13. 测试与验收标准

### 13.1 Compiler

- 重复工具名、缺少 description、无版本或非法 metadata 启动失败；
- 缺少类型、多个可见参数、未知 Injected 类型、异步函数等不支持签名启动失败；
- 嵌套 Model 的 `$defs/$ref` 被正确展开；
- 无法无损编译的 union、递归类型或自定义 Schema 启动失败；
- 非断言 annotation 被按规则保留或删除，未知/不支持的断言关键字启动失败；
- 输入 dict 经 Request TypeAdapter 验证，返回值经 Result TypeAdapter 验证并 dump 为 JSON；
- Request/Result TypeAdapter 和 JSON dump 失败分别返回稳定输入/输出错误，不折叠为执行失败；
- 任意新的 `AiToolExecutionError.code` 无需登记枚举即可完整进入 Bridge、Agent 和 AI Work；
- 未声明的普通异常不会把原始消息泄露到 Tool 事件或 `RUN_ERROR`；
- 契约变化但 version 未升级时测试失败。

### 13.2 绑定与安全

- 模型 Schema 不包含任何 Injected 参数；
- 模型提交同名字段不能覆盖注入值；
- allowlist 外工具不可见且 Runtime 返回 `TOOL_NOT_ALLOWED`；
- scope provider 缺失或重复时绑定失败；
- 权限、deadline、预算、审计和输出大小限制保持现有行为；
- bind 阶段拒绝缺少幂等声明/key/execution 注入的写工具；Runtime 阶段拒绝本次可信幂等值缺失；
- deferred 恢复比较完整 `toolset_contract_fingerprint`，旧 Agent state envelope 可迁移读取；
- 业务幂等测试证明相同 operation key 跨新 run、恢复和进程重启不会重复写入。

### 13.3 属性枚举领域行为

- 只能查询当前类目定义中的属性 ID；
- strict enum 只接受平台真实返回并写入 Ledger 的候选；
- 本地 options 分支、批量上限、失败记录和安全错误映射保持当前行为；
- Tool 输出不包含绑定平台、site、credential 或 Provider 元数据；
- Agent 最终 assignments 仍经过 `_validated_agent_attributes(...)`；
- 商品草稿回填结果与迁移前当前产品契约一致；
- 项目不存在同一业务能力的 AI 专用复制实现；
- 项目不存在旧 definition、旧 executor binding 或旧工具名称残留。

## 14. 最终原则

```text
一个业务行为只有一个实现
一个注解声明稳定 AI Tool 契约
一个显式 Catalog 决定系统收录哪些能力
场景 allowlist 决定 Agent 当前能看到什么
Binding Scope 提供可信领域对象
AiToolRuntime 决定本次调用能否安全执行
领域 validator 决定结果能否进入业务数据
```
