# AI 工具上下文边界与写入一致性修复计划

## 1. 文档信息

- 状态：已实施（见下方实施记录）
- 日期：2026-08-21
- 触发对话：`conversation_global_chat_79ed55595119491998b05f3c8da9806f`
- 适用范围：Global Chat、Global Task、AI Capability、Pydantic AI Tool Result、商品与草稿写入
- 优先级：P0

### 实施记录（2026-08-22）

- Phase 1 写回执/部分补丁/写后异常语义：已实施并有测试覆盖。
- Phase 2 focused write（product_profile_patch / draft_stock_update /
  draft_pricing_apply）：已实施；通用 product_save / draft_save 移入
  INTERNAL_ONLY_CAPABILITIES。
- Phase 3 needs_input input_owner 路径合并：已实施（后端），前端类型已对齐。
- Phase 4 pricing errors 数组修复 + 手动售价持久化：已实施；类型化 pricing
  target 模型为部分完成（关键错误码与手动定价已可用）。
- Section 14 实时流式 + 提交屏障：已实施并改写相关安全测试。
- Section 15 上下文投影模块 + ProcessHistory 接入 + 架构守卫：已实施；
  **15.5 canonical history 合并（canonical_prefix + new_messages）暂未落地**，
  因与现有按 `all_messages()` 字节相等断言的持久化测试冲突，需后续单独处理。
- Section 11 架构守卫：已在 `tests/test_ai_context_architecture.py` 落地。
- Section 9 数据修复：已用 focused Capability 对 a296370a0f9db8ee /
  d2b4e9f048b92 执行（属性 85 经用户确认取「Нет бренда」）；发布校验因环境
  前置（类目 Schema 刷新、图片 HTTPS provider）暂未通过，未进入发布。
- 验证：后端 1119 用例、前端 244 用例全部通过，前端 vue-tsc 无错误。

## 2. 结论

本问题不是简单的“上下文窗口不足”，而是模型侧 Tool Context Boundary、写操作回执和持久化语义没有分离：

```text
数据库完整聚合对象
    ↓
Capability 执行写入
    ↓
直接返回完整商品/草稿
    ↓
AiToolRuntime 在写入后检查输出大小
    ↓
输出超过 64 KiB，被错误标记为 failed
```

修复必须同时满足：

1. 写 Capability 只返回有界、类型化的 mutation receipt，不返回完整业务聚合对象。
2. Global Task 必须保留部分补丁的 `fields_set`，不得把未提供字段展开成空值。
3. 写入执行后的编码、Schema 或大小异常不得被解释成“业务没有执行”。
4. 完整数据仍由 focused read、字段投影或分页读取，不进入普通写回执。
5. 保留 Pydantic AI 对 Agent run、Deferred Tool、message history 和事件编码的唯一所有权，不创建第二套 Agent loop。

## 3. 已确认的现场事实

### 3.1 两次写入均已提交，但任务被标记为失败

| Task | Capability | 工具错误 | 数据库实际结果 |
|---|---|---|---|
| `gtask_595947745d954b1bb1b4cf6271061635` | `product_save` | `112056 > 65536` | 商品主档库存已写为 `200` |
| `gtask_f8f8930c96154b3c9fba8d4406628fb8` | `draft_save` | `212114 > 65536` | Ozon 草稿库存已写为 `10` |

`erp_web/services/ai_tool_runtime.py` 当前先执行 executor，再序列化和检查输出大小。因此 `TOOL_OUTPUT_TOO_LARGE` 是结果投影阶段错误，不是业务写入阶段错误。

### 3.2 当前数据已经出现分裂

| 字段 | 当前状态 |
|---|---|
| 商品主档库存 | `200` |
| Ozon 草稿库存 | `10` |
| 最终售价 | 未保存，`pricing.targets={}` |
| UPC | 空 |
| 属性 85 | 缺失，`validation_errors=["85"]` |
| 商品主档 brand/model/cost/weight | 已被空默认字段覆盖 |
| source 中的可恢复事实 | `金诚海蓝 / bxt-cq2 / 9 / 0.04` |

### 3.3 部分补丁被展开为完整空对象

模型调用 `product_save` 时只提交了：

```json
{
  "product": {
    "product_id": "a296370a0f9db8ee",
    "stock": "200"
  }
}
```

但 `GlobalTaskController._build_steps()` 两次 dump 均未使用 `exclude_unset=True`，最终持久化参数包含约 34 个字段，大量字段变成显式空值。执行时这些空值被当成真实 patch，覆盖了已有商品资料。

### 3.4 完整聚合对象被直接当成 Tool Result

- `product_save` 返回完整 `product`。
- `draft_save` 返回完整 `draft` 和完整 `product_context`。
- `product_context` 包含 `raw` 商品。
- 完整商品加载时又会组合关联草稿、图片、属性和其他上下文。
- 中文、俄文等内容按 UTF-8 编码后通常每个字符占多个字节。

64 KiB 限制是最后一道保护措施；正常成功路径频繁触发它，说明返回契约设计错误，不应通过提高上限解决。

## 4. 目标架构

### 4.1 写入路径

```text
模型提交类型化 patch
    ↓
Global Task 保留 exclude_unset 语义
    ↓
Capability 执行领域写入
    ↓
Capability 生成紧凑 mutation receipt
    ↓
AiToolRuntime 校验 receipt
    ↓
Global Task 继续后续步骤
```

### 4.2 读取路径

```text
需要确认关键事实
    ↓
focused read / readiness projection
    ↓
只返回当前决策需要的字段
```

完整商品、完整草稿、图片池、类目 Schema 和 `raw` 数据不得作为普通写回执返回给模型。

## 5. Phase 1：P0 写回执与部分补丁修复

### 5.1 替换 `ProductSaveResult`

修改：

- `erp_web/schemas/product_write_capabilities.py`
- `erp_web/runtime_units/product_write_capabilities.py`

建议契约：

```python
class ProductSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str
    changed_fields: tuple[str, ...] = ()
    updated_at: str = ""
    changed: bool = False
```

保存流程可以在内部继续使用完整 `saved` 对象，但只能投影紧凑结果：

```python
patch = request.product.model_dump(mode="json", exclude_unset=True)
saved = scope.products.save_product_profile(patch)

return ProductSaveResult(
    product_id=str(saved.get("product_id") or ""),
    changed_fields=tuple(
        sorted(key for key in patch if key != "product_id")
    ),
    updated_at=str(saved.get("updated_at") or ""),
    changed=True,
)
```

禁止在回执中重新携带完整 `product`。

### 5.2 替换 `DraftSaveResult`

建议契约：

```python
class DraftSaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    product_id: str
    platform: str
    changed_fields: tuple[str, ...] = ()
    updated_at: str = ""
    changed: bool = False
```

禁止返回：

- 完整 `draft`
- 完整 `product_context`
- `product_context.raw`
- products/drafts index
- 图片池或完整类目 Schema

如果调用方需要保存后的详情，应通过独立只读 Capability 获取。

### 5.3 保留 `exclude_unset` 语义

修改 `erp_web/services/global_task_controller.py::_build_steps()`：

```python
normalized = selection.arguments.model_dump(
    mode="json",
    exclude_unset=True,
)

validated = tool.request_adapter.validate_python(normalized)

dumped = tool.request_adapter.dump_python(
    validated,
    mode="json",
    exclude_unset=True,
)
```

需要同时验证：

1. 嵌套 Pydantic model 的 fields set 能够保留。
2. Task SQLite round-trip 后仍只包含实际提供字段。
3. worker 重新校验请求时不会重新引入默认空字段。

### 5.4 定义写后异常语义

修改 `erp_web/services/ai_tool_runtime.py`。

如果 executor 已完成，随后发生以下错误：

- `TOOL_OUTPUT_SCHEMA_INVALID`
- 输出 JSON 编码失败
- `TOOL_OUTPUT_TOO_LARGE`

并且工具声明 `side_effect != "none"`，错误详情至少必须包含：

```json
{
  "outcome_unknown": true,
  "failure_stage": "result_projection",
  "side_effect_may_have_completed": true
}
```

`GlobalTaskController` 已具有 `outcome_unknown` 分支，应复用现有语义，不新增第二套状态协议。

紧凑回执完成后，这一分支只作为异常兜底，不能成为正常业务路径。

### 5.5 输出预算

建议设定比运行时上限更严格的设计预算：

| 输出类型 | 设计预算 |
|---|---:|
| 普通写回执 | 8 KiB 以内 |
| 普通只读摘要 | 16 KiB 以内 |
| 详情读取 | 32 KiB 以内，超出时分页或字段投影 |
| Runtime 硬上限 | 保持 64 KiB |

不得依靠提高 Runtime 硬上限解决契约问题。

## 6. Phase 2：Focused Write Capability

通用 `product_save`、`draft_save` 容易造成 owner 选择错误和上下文膨胀。高频业务字段应逐步迁移到 focused Capability：

| Capability | Owner | 最小结果 |
|---|---|---|
| `draft_stock_update` | 平台草稿 | draft_id、stock、updated_at、changed |
| `draft_pricing_apply` | 平台草稿定价 | draft_id、target_key、applied_price、fingerprint |
| `product_profile_patch` | 商品主档 | product_id、changed_fields、updated_at |
| `product_attributes_update` | 平台草稿属性 | draft_id、changed_keys、changed |

发布流程中的库存和售价以平台草稿为 owner；商品主档库存只能作为默认值或来源事实，不得替代目标市场草稿库存。

在 focused Capability 覆盖常用写入后，从 Global Task 常用 allowlist 中移除容易误用的通用保存能力。项目处于 Demo 阶段，不保留 legacy fallback 或双路径。

## 7. Phase 3：`needs_input` 契约修复

同一对话还暴露了嵌套补资料路径丢失问题：

1. `CapabilityInputRequired` 持有 `input_owner`。
2. `ai_tool_compiler` 转换为 `AiToolRequiredInput` 时丢失 owner/path。
3. 前端把属性 ID 作为顶层参数提交。
4. Controller 只进行顶层浅合并。
5. 实际请求两次返回 `GLOBAL_TASK_INPUT_SCHEMA_INVALID`，随后任务被取消。

需要：

- 在 `AiToolRequiredInput` 和 `RequiredInput` 中保留稳定的 `input_owner` 或 `submission_path`。
- Controller 按路径合并：
  - `provided_attributes.<attribute_id>`
  - `pricing_input.<field>`
  - 顶层 step 参数
- 枚举 options 使用结构化 `{label, value}`，其中 value 可以携带规范 JSON。
- `category_attribute_values_query` 返回可以直接交给更新/填写工具的规范值。
- malformed enum 必须在持久化前返回精确错误，不得先删除旧值再暂停。
- `cancelled` 状态保留 `cancelled_by`、`cancelled_at`、`cancel_reason`、`previous_status` 和最后一个 blocker 摘要。

严格枚举属性的规范值示例：

```json
{
  "85": {
    "values": [
      {
        "dictionary_value_id": "126745801",
        "value": "Нет бренда"
      }
    ]
  }
}
```

## 8. Phase 4：核价契约修复

`pricing_calculate` 当前只读取底层结果的顶层 `error`，但确定性核价实际返回 `errors` 数组，因此具体字段错误被统一抹成 `PRICING_CALCULATE_FAILED`。

需要：

1. 把 pricing target 从任意 `dict[str, JsonValue]` 改为类型化模型。
2. 明确区分：
   - `target_profit_amount`
   - `target_margin_percent`
   - `markup_percent`
   - `manual_price`
3. 金额必须携带 currency；百分比不得携带 currency。
4. 确定性校验失败返回结构化字段错误或 `CapabilityInputRequired`。
5. 只有基础设施异常使用 `PRICING_CALCULATE_FAILED`。
6. 最终售价由 `draft_pricing_apply` 或 `draft_prepare_for_market.pricing_input` 持久化，不能只计算不应用。

本次样例的正确手动核价输入应表达为：

```json
{
  "platform": "ozon",
  "site": "global",
  "pricing_mode": "manual",
  "manual_price": {
    "amount": "200",
    "currency": "CNY"
  },
  "shipping_quote_mode": "manual",
  "shipping_currency": "CNY",
  "shipping_amount": "10"
}
```

按当前默认 Ozon 佣金 20%、成本 9 CNY、运费 10 CNY，确定性计算结果应为利润 141 CNY、实际利润率 70.5%。

## 9. 当前数据修复

代码修复完成后，再对 `a296370a0f9db8ee` / `d2b4e9f048b92` 执行一次明确、可审计的数据修复：

1. 暂停该草稿发布。
2. 从仍保留的 source 事实恢复商品主档：
   - brand：`金诚海蓝`
   - model：`bxt-cq2`
   - cost：`9`
   - weight_kg：`0.04`
3. 以用户最后一次明确输入为准，将 Ozon 草稿库存确认成 `10`。
4. 不直接沿用商品主档库存 `200` 作为发布库存。
5. 通过手动定价模式应用最终售价 `200 CNY`。
6. 重新处理属性 85；源品牌与“无品牌”枚举存在冲突，必须依据可信商品事实确认，不能直接猜测。
7. 分配 UPC。
8. 重新运行 `product_publish_validate`。
9. 只有发布校验通过后才进入发布审批或直接发布流程。

数据修复必须使用修复后的 focused Capability，不直接修改 SQLite JSON。

## 10. 测试计划

### 10.1 单元测试

- `ProductSaveResult` 不包含完整 product。
- `DraftSaveResult` 不包含完整 draft/product_context。
- mutation receipt 序列化结果小于 8 KiB。
- 写后输出投影异常对 write Capability 设置 `outcome_unknown=true`。
- read-only Capability 的相同异常不伪造写入结果。

### 10.2 Global Task 参数测试

- 只提供 `product_id + stock` 时，持久化 arguments 只包含这两个字段。
- Task store round-trip 后仍保留部分 patch。
- worker 重新校验不会增加默认字段。
- 更新库存不会修改 brand、model、cost、weight 或其他未提供字段。

### 10.3 集成测试

- 使用真实大商品执行 `product_save`，任务成功且结果小于 64 KiB。
- 使用真实大草稿执行 `draft_save`，任务成功且后续 UPC/校验步骤继续执行。
- `product_attributes_fill → needs_input → UI submit → resume` 能完成同一任务。
- 严格枚举值可以从查询结果直接提交，不需要模型重新拼装 shape。
- 手动售价 200 CNY 的 Ozon 核价返回利润 141 CNY、利润率 70.5%。

### 10.4 对话回放测试

使用本次对话构造端到端回放，至少验证：

- Agent 不再把“目标利润 100 CNY”解释成“利润率 100%”。
- Agent 不再声称已提交的库存写入失败。
- Agent 不使用 `product_save` 修改发布草稿库存。
- 最终售价进入实际写步骤。
- 发布就绪结论以 `product_publish_validate` 为事实 owner。
- 不产生重复、无增量的 `global_task_get`。
- Tool return 和累计上下文显著下降。

### 10.5 必跑命令

```bash
.venv/bin/python -m pytest tests/test_ai_tool_bridge.py -q
.venv/bin/python -m pytest tests/test_global_task_controller.py -q
.venv/bin/python -m pytest tests/test_domain_write_capabilities.py -q
.venv/bin/python -m pytest tests/test_global_agent_vertical_integration.py -q
.venv/bin/python -m pytest tests/test_ai_context_architecture.py -q
.venv/bin/python -m pytest tests -q
```

前端补资料契约变更后还必须运行对应组件和 API 测试。

## 11. 架构守卫

需要在 `tests/test_ai_context_architecture.py` 或对应 architecture tests 中加入：

1. `side_effect="write"` 的 Capability 输出不得包含无界完整业务聚合对象。
2. 保存类结果模型不得使用无界 `dict[str, JsonValue]` 作为顶层完整资源返回。
3. Global Task 参数持久化必须保留 `exclude_unset`。
4. write executor 后错误必须带有明确副作用状态。
5. focused read 与 focused write 分离。
6. 不新增自研 Agent loop、等待/恢复状态机或消息历史协议。

## 12. 验收标准

全部满足后才算完成：

- [ ] `product_save` 和 `draft_save` 正常成功路径的输出均小于 8 KiB。
- [ ] 大商品/大草稿不再触发 `TOOL_OUTPUT_TOO_LARGE`。
- [ ] 已提交的写入不会被展示为普通失败。
- [ ] 部分 patch 不会覆盖未提供字段。
- [ ] 当前商品主档与 Ozon 草稿的数据分裂已完成对账和修复。
- [ ] `needs_input` 可以通过受信 UI 恢复同一任务。
- [ ] 取消任务仍保留可审计的最后 blocker 和取消信息。
- [ ] 核价返回结构化字段错误，支持手动售价持久化。
- [ ] 发布就绪状态只由确定性 validator 给出。
- [ ] Pydantic AI Deferred 链路继续作为唯一 Agent 暂停/恢复机制。
- [ ] 后端全量测试、前端相关测试和架构测试全部通过。

## 13. 非目标

- 不通过提高 64 KiB 上限掩盖问题。
- 不增加 feature flag、shadow path、legacy fallback 或双写。
- 不重建 Pydantic AI 已提供的 Agent loop、Deferred Tool 或 message history 协议。
- 不让模型直接操作 SQLite 或自行判断写入是否提交。
- 不把完整数据库对象作为模型上下文缓存。

## 14. 追加 P0：恢复 Global Chat 实时流式输出

### 14.1 问题定性

当前 Global Chat 的 SSE 端点仍然存在，但普通回复已经不再实时传输文本增量。
`erp_web/services/vercel_ai_ui_service.py::_produce_and_finalize()` 只立即发送整条
run 的 `start` 和首个 `start-step`，其余 Pydantic AI 官方编码事件均进入
`held_chunks`：

```text
start / 首个 start-step
    ↓ 实时发送
text-delta / 后续 start-step / tool event / finish-step / finish
    ↓ 整轮缓冲
run 完成或 Deferred 握手提交成功
    ↓
一次性补发
```

这使用户在模型和工具实际运行期间只能看到“正在回复”，直到整轮结束后才看到
整段文字。它不会必然增加模型执行时间，但会把首个可见文本延迟到整轮终态，
显著放大用户感知延迟。

候选缓冲还存在两种硬上限：

- `MAX_HELD_CHUNKS = 4096`
- `MAX_HELD_TOTAL_BYTES = 4 MiB`

超过任一上限后，中间内容 chunk 会被丢弃，只保留有限的结构闭合事件；前端最终
依赖 `/ui-messages` 重读已提交历史补全。这不是可接受的普通聊天流式体验，也不应
通过提高缓冲上限解决。

### 14.2 错误耦合

现实现把两个不同事实绑定成了同一个发布条件：

1. 模型正在生成的临时展示内容。
2. Global Task、message history 和 Deferred link 是否已完成持久化提交。

由于模型可能在 run 后期才调用 `global_task_start`，服务端在 run 开始时无法预知
它最终是否进入 Deferred。当前实现因此选择从开始就缓冲所有 run，但这只是现有
实现为满足“提交前任何内容均不可见”所做的取舍，不是 Pydantic AI Deferred 的
固有要求。

产品真正需要保证的不变量应当是：

```text
模型临时 text/reasoning 可以实时展示；
任务卡、Tool Result 成功态以及写入/发布等结构化业务终态，
只能在对应提交成功后展示。
```

这里的提交屏障只约束结构化业务事实，不对自然语言做语义审查。模型临时文本和
thinking 在提交前始终标记为运行中内容，不能作为任务创建、写入或发布成功的事实源；
系统 prompt 应要求模型在获得受信 Tool Result 前不下成功结论，但该要求不是事务安全
边界。事务安全只由已提交的 Tool Result、Global Task 状态、task link 和确定性
validator 保证。

不再要求：

```text
只要 run 未来可能进入 Deferred，提交前一个文本字符也不能展示。
```

### 14.3 目标交互

保持一个统一的 Global Chat 输入框，不要求用户选择“普通聊天”或“执行任务”模式：

```text
用户发送消息
    ↓
Pydantic AI 官方 text/reasoning/progress 事件实时展示（当前消息为运行中临时态）
    ↓
├─ 未调用 global_task_start
│      保存正常历史并发送成功终态
│
└─ 调用 global_task_start
       原子提交 history + Deferred link ready + 必要 outbox
           ↓
       ├─ 提交成功：发送任务受理/成功终态
       └─ 提交失败：发送官方 error，前端重读已提交历史并丢弃临时态
```

用户可在提交前看到：

- “正在检查草稿……”等过程文本。
- 普通文本回答的增量内容。
- Provider 实际返回的 thinking/reasoning 增量。
- 不代表业务成功的工具执行进度。

提交前不得发布或由 UI 构造以下结构化既成事实：

- Global Task 已受理的任务卡或成功状态。
- 商品/草稿已保存成功的 Tool Result 成功态。
- 商品已发布的成功状态。
- 任何需要数据库或外部平台终态才能成立的结构化成功标识。

已经实时展示的自然语言和 thinking 始终属于可回滚临时态；提交失败时由官方 error
闭合并以 `/ui-messages` 的已提交快照替换。不得为了禁止临时文本中的某个词而新增文本
分类器、语义过滤器或第二套事件协议。

### 14.4 后端修改要求

修改 `erp_web/services/vercel_ai_ui_service.py`，并遵守以下边界：

1. 继续使用 Pydantic AI 的原生 Agent run、Deferred Tool、消息和
   `VercelAIEventStream` 编码，不创建第二套 Agent loop 或事件协议。
2. 删除“因为 run 可能在未来进入 Deferred，所以从开始缓冲全部内容事件”的
   整轮候选缓冲策略。
3. `text-delta`、`reasoning-delta` 及非终态官方进度事件应在产生时立即写入当前请求
   流；后续模型轮的 `start-step` 也不得无条件延迟到终态。
4. Deferred 的 Tool Result 成功态、`finish-step`/`finish` 成功终态和任务受理展示必须
   继续受组合事务提交屏障保护；不得在 link `ready` 和 history 提交前发布结构化成功
   状态。工具输入骨架可以实时展示，但不能据此创建任务卡。
5. Deferred 组合提交失败时，使用官方 `error`/闭合事件结束当前流；前端随后以
   `/ui-messages` 的已提交历史为事实源进行对账。
6. 普通非 Deferred run 不得经过整轮 `held_chunks`；长文本不应因为候选缓冲
   4096 条上限而丢失中间内容。
7. 当前请求断开不得取消已进入受信业务执行边界的 Agent/Task；断线恢复仍使用
   现有已提交 history、link 和 outbox，不新增平行持久化协议。
8. UI 文本和 thinking 不能作为商品写入、任务创建或发布成功的事实 owner；这些状态
   继续由已提交 Tool Result、Global Task 状态、task link 和确定性 validator 决定。

如果安全要求坚持“任何临时文本在提交前都不得可见”，则只能另选以下方案之一，
不得继续用全局整轮缓冲掩盖取舍：

- 在 UI 明确提供预先确定的 chat-only 执行入口，其 ToolSet 不包含
  `global_task_start`。
- 把官方编码增量按小批次先写入 durable outbox，批次提交后再发布。

当前产品默认采用统一聊天入口和“临时文本实时、业务终态提交后确认”的方案。

### 14.5 前端修改要求

1. 当前 run 的 assistant text 和 reasoning 在收到成功终态前按运行中临时态展示。
2. 普通文本继续逐段追加，不等待 `onFinish` 才首次显示。
3. 收到官方 error、检测到历史版本不连续或 Deferred 提交失败时，重读
   `/ui-messages`，用服务端已提交历史替换当前临时态。
4. 任务卡片只能根据已提交的 task link / Global Task 状态出现，不能仅根据模型
   文本或未提交的工具调用参数判定任务已创建。
5. `/events` 继续承担已提交批次通知和断线恢复，不把 batch 通知伪装成逐 token
   文本流。

### 14.6 测试修复

现有
`test_real_socket_receives_incremental_chunks_before_finish` 只验证 `start` 在
`finish` 前到达，并未验证 `text-delta` 实时到达。应改为两阶段模型：

```text
模型输出第一段 text-delta
    ↓
模型在 gate 上暂停
    ↓
客户端必须已经收到第一段 text-delta，且尚未收到 finish
    ↓
解除 gate
    ↓
模型输出剩余文本并完成
```

至少增加以下测试：

- 普通非 Deferred run 的首个 `text-delta` 在模型完成前到达真实 socket。
- 普通非 Deferred run 的首个 `reasoning-delta` 在模型完成前到达真实 socket。
- 多个 `text-delta` 按产生顺序逐步到达，而不是在 `finish` 前同一批补发。
- `reasoning-delta` 与 `text-delta` 按官方 encoder 顺序逐步到达，不在终态补发。
- 普通 run 不进入整轮候选缓冲，也不会触发候选缓冲溢出警告。
- Deferred run 可以展示临时 text/reasoning，但提交成功前不得出现任务卡、Tool Result
  成功态或任务已受理终态。
- Deferred 组合提交失败时，流以官方 error 闭合，服务端历史不包含未提交结果，
  前端对账后不保留临时 text/reasoning 或成功状态。
- Deferred 提交成功后，history、link ready、outbox 和任务卡片保持一致。
- 慢客户端和断线恢复不乱序、不产生重复业务状态；必要时允许快照重同步，但普通
  网络条件下不得静默丢失文本增量。
- 原有工具骨架、`finish-step`、`finish` 完整闭合与 Provider secret 不泄漏测试
  继续通过。
- 删除或改写“Deferred 提交前不得泄露任何文本/reasoning”的旧测试；新的安全断言只
  禁止未提交的结构化业务成功状态。

### 14.7 追加验收标准

以下条件应并入第 12 节总体验收，全部满足后才算修复完成：

- [ ] 普通 Global Chat 在模型完成前可见首个真实 `text-delta`。
- [ ] 普通 Global Chat 在模型完成前可见首个真实 `reasoning-delta`。
- [ ] 普通回复恢复逐段增量展示，不再终态一次性补发。
- [ ] thinking/reasoning 恢复逐段增量展示，不再进入整轮候选缓冲。
- [ ] 普通非 Deferred run 不使用整轮 `held_chunks`。
- [ ] 长 thinking/长文本不会再触发候选缓冲上限并丢失中间文本。
- [ ] Deferred 提交前不会展示任务卡、Tool Result 成功态或业务成功终态。
- [ ] Deferred 提交失败后，临时 UI 内容可被官方 error + 服务端快照正确纠正。
- [ ] 用户继续使用统一聊天入口，无需预先判断消息属于聊天还是任务。
- [ ] Pydantic AI 继续拥有 Agent lifecycle、Deferred、message history 和官方事件编码。

## 15. 追加 P0：Thinking 完整留存与 Pydantic AI 上下文边界

### 15.1 问题定性

当前项目已经能把 Provider 实际返回的 `ThinkingPart` 保存为 Pydantic 官方
`ModelMessage`，`/ui-messages` 也能把它恢复为 reasoning 展示；但是持久化历史和
下一轮模型输入使用的是同一份未投影列表：

```text
PydanticMessageStore.messages_json（包含完整 ThinkingPart）
    ↓
GlobalAgentChatService.trusted_history()
    ↓
AiAgentStreamSession._stream(message_history=完整历史)
    ↓
下一轮模型请求再次携带全部历史 thinking
```

因此，第 14 节负责 thinking 增量的实时展示，本节负责完整历史与模型输入视图的分离。
首期不把“必须显著删除 Global Chat thinking”设为硬目标：当当前模型请求暴露工具时，
Provider 可能要求完整回传历史 reasoning，项目不得自行猜测并删除。只有在 Pydantic AI
公开运行上下文确认当前请求不暴露工具时，才允许通过官方 `ProcessHistory` 删除旧完成
轮次中可省略的 thinking。

目标必须把以下两个概念分开：

- **规范持久化历史（canonical history）**：本地会话的完整事实，保留 Provider
  实际返回的 `ThinkingPart`、`provider_details`、text、tool call/result、Deferred
  开口和消息元数据。
- **模型输入历史（model context projection）**：每次请求前由 Pydantic AI
  `ProcessHistory` 从规范历史派生的临时视图；工具可见时完整保留 thinking，工具不可见
  时才执行最小、确定性的旧 thinking 投影。

两者仍使用 Pydantic 官方 `ModelMessage[]`，不得创建第二套项目自有 message schema、
第二张思考记录表或第二套 Agent history 协议。

### 15.2 目标数据流

```text
                         ┌─→ /ui-messages / 本地会话回放
                         │      保留完整 text / thinking / tool 记录
SQLite canonical history ┤
                         │
                         └─→ Pydantic ProcessHistory / context projection
                                工具可见：完整保留 ThinkingPart
                                工具不可见：删除可省略的旧 ThinkingPart
                                保留用户消息、最终回答和必要工具事实
                                完整保护 tool-call thinking、当前 run
                                与未闭合 Deferred/tool 尾部
                                      ↓
                         Pydantic Provider adapter
                                ↓
                         Provider model request
```

本地保存完整 thinking 与模型输入投影不是冲突目标：SQLite 中保存的是审计和展示事实，
模型收到的是由 Pydantic AI 在本次请求边界生成的派生视图。派生视图不得回写覆盖规范
历史；工具可见时允许派生视图与规范历史同样完整，安全性优先于压缩率。

### 15.3 Pydantic AI 原生边界

当前项目固定使用 `pydantic-ai-slim[openai]==2.22.0`。该版本提供官方
`pydantic_ai.capabilities.ProcessHistory`，应把历史投影接入这一原生
Capability，而不是在 HTTP、SSE、Vercel UI adapter 或 Provider SDK 旁路中自行修改
请求。

P0 依赖决策已经确定：

- 继续使用当前 `pydantic-ai-slim[openai]==2.22.0`；
- **不安装、不引入** `pydantic_ai_harness`；
- 不引入 `TieredCompaction`、`SlidingWindowCompaction`、LLM 摘要、token-based 通用
  压缩或相关兼容层；后续需求必须另立设计，不属于本计划。

实施约束：

1. `AiAgentFactory` 仍是 Agent 的唯一装配和运行入口。
2. 通过 `ProcessHistory` 在模型请求边界调用最小纯投影函数；processor 使用 Pydantic
   公开 `RunContext.available_tool_names` 判断当前请求是否暴露工具，输入和输出均为
   `list[ModelMessage]`。
3. 投影函数必须返回新列表和新的被修改消息对象，不得原地修改从
   `PydanticMessageStore` 读取的规范对象。
4. 项目投影函数只执行与 Provider 无关的保守结构规则，不读取模型名称、不判断
   Provider profile，也不生成或解析 `reasoning_content` 等协议字段；只要当前请求暴露
   任意工具，就不得删除任何历史 `ThinkingPart`。
5. Pydantic Provider adapter 是 thinking 字段名、签名、`provider_details`、DeepSeek
   回传要求和跨 Provider 映射的唯一 owner；项目不得复制这些规则。
6. Pydantic AI 继续负责 run、当前 run 内的 thinking/tool 循环、Deferred、事件编码
   和 `new_messages()` 边界。
7. 不直接调用私有 `_agent_graph`、不读取 Pydantic 内部 `new_message_index`，也不复制
   Pydantic Agent loop。
8. 本计划不得新增 `pydantic_ai_harness` 依赖、导入、配置或 fallback；上下文边界只使用
   当前 Pydantic AI 2.22.0 已提供的公开 `ProcessHistory`、`RunContext`、
   `ModelMessage` 和 `result.new_messages()`。

`ProcessHistory` 只能解决“发给模型什么”，不能单独解决“最终保存什么”。它处理过的
历史会成为本次 run 看到的历史；如果随后继续用 `result.all_messages()` 替换 SQLite，
会把已经从模型视图删除的旧 thinking 同时从本地记录中删除。因此必须同时实施下一节
的规范历史合并规则。

### 15.4 Thinking 投影规则

新增 dependency-light 的 focused 模块，例如：

```text
erp_web/services/ai_model_context_projection.py
```

该模块只处理 Pydantic `RunContext` 与 `ModelMessage` 值，不读取数据库、不调用模型、
不发布事件，也不执行工具。它是传给官方 `ProcessHistory` 的最小安全 processor，不是
自研上下文管理框架。首期规则固定如下，不增加 feature flag 或双路径：

1. **本地完整保存**
   - Provider 实际返回的 `ThinkingPart.content`、签名和 `provider_details` 按官方
     adapter 原样持久化。
   - Provider 未返回、只在服务端内部存在的隐藏思维无法也不得伪造。
2. **工具可见时完整保留 thinking**
   - processor 通过 Pydantic 公开 `RunContext.available_tool_names` 判断当前模型请求是否
     暴露任意工具。
   - 只要当前请求暴露工具，模型输入视图中的全部历史 `ThinkingPart` 必须原样保留；
     不能用“该响应是否实际包含 `ToolCallPart`”作为删除条件。
   - DeepSeek 官方 Thinking Mode 规定：请求携带 `tools` 时，assistant 的
     `reasoning_content` 在后续请求中必须完整回传，即使该次响应没有实际调用工具；
     遗漏会返回 **400**。
   - 项目只负责保留 Pydantic `ThinkingPart`；如何映射为 `reasoning_content`、签名或
     其他 Provider 字段，完全交给 Pydantic adapter。
   - 依据：[DeepSeek 官方思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。
3. **仅在工具不可见时执行最小投影**
   - 当前请求未暴露任何工具时，才允许从早于当前 run、已经完成的
     `ModelResponse` 中移除 `ThinkingPart`。
   - 首期不改写、摘要或伪造 thinking；用户消息、最终回答和其他 part 原样保留。
   - 如果 Pydantic 公开上下文无法可靠确认工具不可见，安全回退为完整保留，而不是自行
     判断 Provider 或工具协议。
4. **当前 run 内 thinking 始终保留**
   - 同一 run 内模型刚产生的 thinking 在后续 tool call → tool return → model 循环中
     保持原样，由 Pydantic AI 和 Provider adapter 管理。
   - 不得为了节省上下文删除当前工具循环所需的 reasoning 签名或
     `provider_details`。
5. **未闭合 Deferred/tool 尾部完整保护**
   - 包含未匹配 `ToolReturnPart` 的 `ToolCallPart`、`state="suspended"` 消息以及恢复
     该调用所需的连续消息后缀必须原样保留。
   - continuation 前不得删除、摘要或重排该后缀中的 `ThinkingPart`、tool call ID、
     Provider 元数据。
6. **消息结构保持合法**
   - 删除 thinking 后若某个已完成 `ModelResponse` 不再包含任何有意义 part，应删除
     整个空响应，而不是向 Provider 发送空 assistant 消息。
   - tool call/result 必须继续一一配对；不得产生孤立 `ToolReturnPart`，不得跨消息
     重排调用顺序。
   - `conversation_id`、`run_id`、时间戳和现有 part 内容不得因投影被意外改写。
7. **不引入 Harness**
   - 本模块不得导入或模拟 `pydantic_ai_harness`、Tiered/Sliding Window compaction、
     LLM 摘要或 token 阈值压缩。
   - processor 只实现上述工具可见性安全门和工具不可见时的最小旧 thinking 删除。

### 15.5 规范历史合并与提交

`PydanticMessageStore` 继续保存完整规范历史。一次 run 同时维护两个只读概念：

```text
canonical_prefix = run 开始前从 SQLite 读取的完整历史
projected_history = ProcessHistory 生成、只供模型使用的视图
```

成功结束后的持久化内容必须按官方新消息边界生成：

```text
canonical_after = canonical_prefix + result.new_messages()
```

不得把以下内容直接作为 SQLite 全量替换值：

- `projected_history`；
- `result.all_messages()`（其中旧前缀可能已经被投影）；
- Vercel UI adapter 为展示生成的 `UIMessage[]`；
- 客户端回传历史。

`AiAgentStreamSession` 应提供唯一的规范提交方法或只读结果，例如
`canonical_messages_for_commit()`，集中封装“完整旧前缀 + 官方本轮新消息”。以下路径
必须全部改用同一入口：

- `AiAgentFactory._complete_with_result()` 的普通成功保存；
- `vercel_ai_ui_service.py` 的首次 Deferred history + link ready + outbox 组合事务；
- `global_task_continuation_service.py` 的 continuation CAS 终态提交；
- 非 Deferred 失败路径中允许持久化的官方 interrupted/本轮增量消息。

失败路径不得用经过投影的 captured 全历史覆盖 SQLite。若无法从 Pydantic 官方消息和
当前 `run_id` 安全确定本轮增量，则保留原 canonical history 不变，并把失败详情留在
技术 trace；不得以保存部分失败消息为代价删除以前完整保存的 thinking。

### 15.6 展示、存储与观测

- `/ui-messages` 和 AiWork 历史页面继续只读取 canonical history，因此历史 thinking
  仍可在本地会话中展示；前端可折叠，但后端不得因折叠而删除内容。
- SSE 中当前 run 的 `reasoning-delta` 继续实时发送，不因历史投影恢复整轮缓冲。
- 只记录投影计数和大小，不把 thinking 正文复制到普通日志：
  - `canonical_history_bytes`
  - `projected_history_bytes`
  - `removed_thinking_parts`
  - `protected_active_parts`
  - `protected_tool_visible_thinking_parts`
- P0 不采集或推断 Provider 协议字段，不引入 Harness、摘要或 token-based compaction；
  不得把规范本地历史直接改写成投影结果。
- 实施新增模块或入口后同步更新 `docs/ai-context-map.md`。

### 15.7 测试要求

至少增加以下测试：

1. **两轮历史隔离（工具不可见）**
   - 使用不暴露任何工具的测试 profile，第一轮产生 `ThinkingPart + TextPart` 并保存；
   - 第二轮经 `ProcessHistory` 得到的模型输入不包含第一轮原始 `ThinkingPart`；
   - 第二轮结束后 SQLite 仍包含第一轮完整 thinking 和第二轮新 thinking。
2. **UI 回放不受投影影响**
   - `/ui-messages` 在多轮运行后仍返回 reasoning part；
   - text、tool call/result 与 reasoning 顺序可正确回放。
3. **投影纯函数**
   - 输入 `ModelMessage[]` 在调用前后按官方 adapter 序列化结果完全一致；
   - 输出删除目标 thinking，但不修改输入对象。
4. **工具可见时完整保护 thinking**
   - 第一轮产生 tool-call thinking、工具调用闭合并完成不含 `ToolCallPart` 的最终回答；
   - 第二个用户轮次仍暴露工具时，Pydantic 模型输入保留第一轮所有
     `ThinkingPart`，包括最终回答中的 thinking；
   - 通过 Pydantic DeepSeek adapter 集成测试确认其映射结果包含完整
     `reasoning_content`，避免 Provider 400。
5. **Provider 协议所有权守卫**
   - 项目投影模块不包含 `reasoning_content`、Provider profile key 或模型名称分支；
   - Pydantic adapter 对保留下来的 `ThinkingPart` 负责协议映射；
   - `requirements.txt` 不新增 `pydantic_ai_harness`，项目代码不存在相关导入或 fallback。
6. **当前工具循环保护**
   - 当前 run 的 thinking + tool call 在工具返回后的下一次模型请求中仍存在；
   - Provider reasoning 签名和 `provider_details` 不丢失。
7. **Deferred 开口保护**
   - 首次 Deferred history 中的未闭合 tool call 和关联 thinking 原样保存；
   - continuation 投影完整保留受保护后缀；恢复后 tool call/result 正确闭合。
8. **三类提交一致性**
   - 普通成功、Deferred 组合提交和 continuation CAS 提交均使用 canonical prefix +
     `result.new_messages()`；
   - 任一路径都不会因为模型视图投影而删除旧 thinking。
9. **失败保护**
   - 模型、工具、编码或提交失败时，旧 canonical history 不被 projected/captured
     history 覆盖；
   - 不产生孤立 tool return 或损坏的 Pydantic 消息 JSON。
10. **上下文投影证据**
   - 工具不可见 profile 中，构造包含大段历史 thinking 的会话，断言
     `projected_history_bytes` 小于 `canonical_history_bytes`；
   - Global Chat 等工具可见请求中，断言 processor 完整保留所有 `ThinkingPart`，不把
     压缩率作为 P0 验收条件；
   - 两类请求都保持最终回答所需用户文本、业务事实和消息结构完整。

### 15.8 追加验收标准

以下条件并入第 12 节总体验收：

- [ ] Provider 实际返回的完整 `ThinkingPart` 保存在 SQLite canonical history。
- [ ] `/ui-messages` 在后续多轮运行后仍能回放已保存 reasoning。
- [ ] 工具不可见请求中，旧完成轮次 thinking 可由 `ProcessHistory` 从模型输入移除。
- [ ] 工具可见请求中，所有历史 assistant `ThinkingPart` 均不被项目投影删除，包括不含
  `ToolCallPart` 的最终回答；DeepSeek `reasoning_content` 继续由 Pydantic adapter
  正确回传。
- [ ] 当前 run 和未闭合 Deferred/tool 尾部的 thinking、签名及调用关系保持完整。
- [ ] 普通成功、Deferred 握手、continuation 和失败路径都不会用投影历史覆盖规范历史。
- [ ] 模型历史投影使用 Pydantic AI 官方 Capability 和 `ModelMessage`，没有第二套 Agent
  loop、message schema 或持久化事实源。
- [ ] P0 不安装、不引入 `pydantic_ai_harness`，项目代码不实现 Provider thinking 协议
  映射或 Harness fallback。
- [ ] 工具不可见会话的模型输入小于本地规范历史；工具可见会话优先保证完整 reasoning
  回传、任务恢复和最终回答正确，不以压缩率作为验收条件。
