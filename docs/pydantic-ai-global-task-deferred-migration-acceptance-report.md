# Pydantic AI Global Task Deferred 迁移重新验收报告

## 1. 报告信息

| 项目 | 内容 |
| --- | --- |
| 验收日期 | 2026-08-22 |
| 验收对象 | 当前工作区中的 Pydantic AI Global Task Deferred 迁移及最新整改 |
| 对照文档 | `docs/pydantic-ai-global-task-deferred-migration-plan.md` |
| Pydantic AI 版本 | 2.22.0 |
| 验收方式 | 代码审查、官方 encoder/client 顺序比对、竞态与背压故障探针、全量自动化回归 |
| 验收结论 | **不通过：1 项 P1 未关闭，新增 1 项 P2** |
| 合并建议 | 修复 A-13 与 A-18 并补齐对应测试后重新验收；当前不建议合并或交付 |

本报告整体替换上一版报告。上一版要求复验的 A-01、A-10、A-13、A-17 中，A-01、A-10、
A-17 已关闭，A-13 仍未关闭；本轮新增 A-18。

## 2. 执行摘要

最新整改已经完成以下目标：

- A-01：多模型轮只实时发布首个 `start` / `start-step`，encoder 与客户端顺序一致；
- A-10：reconnect 不再取消 task-link 重试，任务卡旧写响应不再覆盖新任务；
- A-17：conversation 订阅队列已设为 256，溢出会显式要求 `resync_required`；
- A-13 的压力测试已改用 SDK v7 的真实 `delta` 字段，并增加了工具骨架和 `finish` 断言。

但 A-13 的实现仍不能兑现迁移计划中“结构事件、`finish` 与结束哨兵保证送达”的契约：

1. 候选缓冲溢出后只保留前 16 个结构 chunk；合法多工具回合可耗尽该区域，最终
   `finish-step`、`finish` 和 `[DONE]` 仍会丢失；
2. `[DONE]` 无法被 `_is_guaranteed_chunk()` 识别，单轮超长普通回复也会稳定缺少它；
3. 请求投递队列满时，各结构事件和 `None` 哨兵分别延迟重试，后发哨兵可能越过待重试事件。
   新增的慢 observer 测试在本轮重复运行中实际出现 `tool-input-start` 缺失。

此外，任务卡切换 `taskId` 时未清空 `rejectionReason`，旧任务的拒绝理由会进入新任务表单，
形成本轮新增的 A-18。

全量测试通过并不能覆盖上述窗口；当前结论仍为 **不通过**。

## 3. 原 R-01～R-07 状态

| 原编号 | 当前状态 | 本轮结论 |
| --- | --- | --- |
| R-01 | **已关闭** | 初始握手提交前仅发布首个 `start` / `start-step`，Direct → Deferred 多轮顺序已正确 |
| R-02 | **已关闭** | producer 与请求连接解耦，断线不取消已接受 run |
| R-03 | **已关闭** | continuation outbox 超限确定性降级为 resync-only |
| R-04 | **已关闭** | history、claim 与前端应用均遵守 generation/version 单调规则 |
| R-05 | **已关闭** | durable commit 后通知失败由 outbox 重投，不回滚业务状态 |
| R-06 | **已关闭** | 上一版列出的 task-link reconnect 与跨 taskId 写响应竞态均已关闭 |
| R-07 | **已关闭** | Deferred ledger 为必需依赖，旧 fallback 已删除 |

原始 R-01～R-07 均已关闭。当前阻断来自后续压力验收发现的 A-13，以及新增的前端状态隔离问题
A-18。

## 4. A 项处置矩阵

| 编号 | 当前状态 | 本轮结论 |
| --- | --- | --- |
| A-01 | **已关闭** | 多模型轮后续 `start-step` 不再越过前一轮内容；独立 encoder/client 完整序列相等 |
| A-10 | **已关闭** | reconnect retry 与旧成功、旧失败、busy 跨任务窗口均已修复并有测试 |
| A-13 | **未关闭 / P1** | held tail、投递重试和 `[DONE]` 仍可造成不完整官方 SSE |
| A-17 | **已关闭** | 有界订阅、溢出 resync、满队列 close 均通过探针 |
| A-02～A-09、A-11～A-12、A-14～A-16 | **维持已关闭** | 本轮未发现状态倒退，全量回归通过 |
| A-18 | **新增 / P2** | 切换任务卡未清空拒绝理由，表单状态跨任务泄漏 |

## 5. 当前阻断项

### A-13（P1）：有界缓冲仍会丢失结构事件与官方终止帧

相关位置：

- `erp_web/services/vercel_ai_ui_service.py:68-89`
- `erp_web/services/vercel_ai_ui_service.py:109-134`
- `erp_web/services/vercel_ai_ui_service.py:217-230`
- `erp_web/services/vercel_ai_ui_service.py:308-328`
- `docs/pydantic-ai-global-task-deferred-migration-plan.md:432-438`

迁移计划当前允许丢弃中间文本/推理 delta 和 `tool-input-delta`，但明确要求工具结构事件、
`finish` 与请求结束哨兵保证送达。实现仍有三条反例。

#### 5.1 结构尾部容量不足

`MAX_HELD_TAIL_CHUNKS = 16`，且 `hold()` 采用先到先占。以下普通非 Deferred run 完全处于
现有 12 次工具调用、16 次模型请求上限内：

1. 先输出 `MAX_HELD_CHUNKS + 100` 个文本片段，使候选缓冲溢出；
2. 同一模型轮调用 6 个合法 Direct `drafts_query`；
3. 下一模型轮正常返回最终文本。

确定性探针结果：

```text
held_cap=4096 tail_cap=16 received=4114
tool-input-start=6
tool-input-available=6
tool-output-available=4
finish-step=0
finish=0
DONE=0
claim=completed
history=1
outbox=0
```

后两个工具输出和全部闭合事件被静默丢弃；durable history 已成功，因此服务端把回合记为
`completed`。普通 run 又没有 Deferred outbox 可触发后台订阅对账，客户端得到的是不完整官方流。

#### 5.2 `[DONE]` 不在保证送达范围

`_chunk_payload()` 对 `[DONE]` 返回 `None`，而 `_is_guaranteed_chunk()` 只接受 JSON payload 中
六种类型。单轮普通超长回复即使保住 `finish`，仍稳定得到：

```text
finish=1
DONE=0
```

现有 `test_oversize_plain_run_still_closes_with_finish` 只断言 `finish`，没有断言官方
`[DONE]` 终止 chunk。

#### 5.3 请求队列的独立延迟重试破坏 FIFO 保证

请求队列满时，每个 guaranteed chunk 和 `None` 都通过独立的 `call_later(0.05, _put)` 重试。
后发哨兵可能在早先结构事件的定时重试前抢到空位，消费端遇到 `None` 后立即结束。

本轮首次定向套件直接失败：

```text
FAILED test_long_stream_and_slow_observer_stay_bounded_and_ordered
AssertionError: assert 'tool-input-start' in received_types
```

随后连续重跑该用例 5 次又出现 1 次相同失败；本机合计 6 次中 2 次失败。说明新测试不仅覆盖不全，
其声称保证的单工具慢 observer 路径本身仍存在竞态。

关闭条件：

1. guaranteed chunk 与终止哨兵必须经过同一有序投递机制，`None` 不得越过任何待送达事件；
2. 结构事件保留不能依赖会被合法工具数量耗尽的固定 16 条先到先占区；
3. `[DONE]`、文本/推理 start/end、工具 error/denied/approval 等非 delta 官方结构事件需有明确
   且一致的保留策略；
4. 增加“缓冲已溢出 + 多工具”和重复慢 observer 测试，断言所有调用生命周期、
   `finish-step`、`finish`、`[DONE]` 及最终对账。

## 6. 其余现存问题

### A-18（P2）：拒绝理由跨任务卡泄漏

相关位置：

- `front/src/components/ai-work/GlobalTaskApprovalCard.vue:27`
- `front/src/components/ai-work/GlobalTaskApprovalCard.vue:185-203`
- `front/src/components/ai-work/GlobalTaskApprovalCard.vue:264-272`

`taskId` watcher 已清空 `task`、`busyAction`、`actionError` 和 `inputValues`，但没有清空
`rejectionReason`。因此用户在任务 A 填写拒绝理由后切换到任务 B，B 的拒绝输入仍预填 A 的文字，
可能把旧任务理由误提交给新任务。

关闭条件：切换 `taskId` 时同步清空 `rejectionReason`，并增加“A 填写拒绝理由 → 切换 B”组件测试。

## 7. 本轮确认关闭项

### A-01 已关闭

- 组合事务入口前事件恰为 `[start, start-step]`；
- Direct → Deferred 两轮的 encoder/client 17 个结构类型完全相等；
- 普通 Direct → 文本两轮的 encoder/client 17 个结构类型完全相等；
- 定向 8 项通过，`tests/test_ai_chat_routes.py` 全文件 63 项通过。

普通多轮测试仍有一个测试质量问题：`text_deltas` 使用原始数组索引，`second_start_step` 使用过滤
后的数组索引，二者不在同一索引空间。独立完整序列探针已证明当前实现正确，因此不据此重开
A-01；建议后续统一索引口径。

### A-10 已关闭

- `disconnectEvents()` 不再清空同会话 task-link retry；
- approve/reject/input/cancel 均先校验请求时 `taskId`，再写 generation、task、error 或 busy；
- taskId watcher 会作废旧读请求并清除旧 busy/error；
- 两个前端定向文件 36 项通过。

`front/src/stores/aiChat.ts:95-99` 的注释仍称 disconnect 会清零重试，与实现不一致；这是文档维护项，
不影响 A-10 的运行时判定。

### A-17 已关闭

- 单订阅队列 `maxsize=256`；
- 5000 批次慢订阅探针保持 `qsize=256`，并标记 `overflowed=True`；
- 下一次 poll 抛 `ConversationResyncRequired`；
- SSE 转换为 `resync_required`，满队列 close 后 poll 确定性 `StopIteration`；
- 三项 A-17 定向测试通过。

## 8. 验证记录

### 8.1 自动化结果

| 验证项 | 结果 |
| --- | --- |
| 后端全量：`.venv/bin/python -m pytest tests -q` | **1092 passed，32 subtests passed（46.80s）** |
| `tests/test_ai_chat_routes.py` | **63 passed（18.40s）** |
| A-01 定向 | **8 passed** |
| A-13/A-17、架构与持久化定向 | **13 passed**；覆盖不足，见故障探针 |
| 前端全量：`pnpm test:run` | **33 files，246 tests passed** |
| A-10 前端定向 | **2 files，36 tests passed** |
| `pnpm typecheck` | **通过** |
| `pnpm lint:check` | **通过** |
| `pnpm build` | **通过** |
| `.venv/bin/python -m compileall -q erp_web tests` | **通过** |
| `git diff --check` | **通过** |

### 8.2 独立故障探针

| 探针 | 结果 | 判定 |
| --- | --- | --- |
| Direct → Deferred 多模型轮完整顺序 | pre-commit 仅首个信封；encoder/client 完全相等 | 通过，A-01 关闭 |
| Direct → 文本普通多模型轮完整顺序 | encoder/client 完全相等 | 通过，A-01 关闭 |
| reconnect 后 task-link 重试 | 250/500 ms 重试后 ready | 通过，A-10 关闭 |
| A 成功/失败写响应晚于 B 切换 | 不覆盖 B、不污染 B 错误和 busy | 通过，A-10 关闭 |
| event bus 慢订阅者 5000 批次 | qsize 256，显式 resync，close 有界 | 通过，A-17 关闭 |
| held 溢出后 6 个 Direct 工具 | 仅 4 个 output；无 finish-step/finish/[DONE] | **失败，A-13** |
| 单轮普通 held 溢出 | 有 finish、无 `[DONE]` | **失败，A-13** |
| 3000 段 + 慢 observer 重复运行 | 6 次中 2 次缺少 tool-input-start | **失败，A-13** |
| A 拒绝理由后切换 B | watcher 未清状态，B 继承旧理由 | **失败，A-18** |

## 9. 最终结论

当前迁移 **不通过**。

上一轮四项中，A-01、A-10、A-17 已满足关闭条件；A-13 仍是 P1 阻断，并且不是单纯测试缺口：
合法运行可得到 durable `completed`，同时向客户端返回缺少工具结果、`finish` 或 `[DONE]` 的官方
SSE。A-18 为新增 P2，应一并修复。

建议复验顺序：

1. 先修复 A-13 的单一有序背压/终止语义；
2. 增加溢出后多工具、全结构类型、`[DONE]` 和重复慢 observer 压力用例；
3. 清理 A-18 的跨任务拒绝理由并补组件测试；
4. 重跑后端、前端全量以及本报告第 8.2 节失败探针。
