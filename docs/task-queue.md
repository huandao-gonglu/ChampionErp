# 统一任务队列

首期接入商品发布和在线商品修改。原 `/publish` 路由和 `publish` 导航键保留，界面名称改为「任务队列」。采购、支付、跨境履约和图片任务未接入；Ozon 在线写入及未开放的 Mercado 商品族编辑不在范围内。

## 正常处理流程

1. 发布或在线修改先持久化原领域任务，再执行平台写入。任务保存实际发起方式（页面或 AI），历史缺失记为历史记录。
2. 明确同步成功立即完成，包括 Yandex 库存、隐藏、归档及 Mercado 有明确逐目标成功回执的同步修改。部分完成保留逐项结果。同步成功不创建确认计划。
3. 需要平台任务、审核或实际生效结果时，`TaskQueueService` 建立有限查询计划。只读查询继续使用原领域确认服务；查询成功、失败或部分完成均回写原任务，发布继续回填原草稿及发布记录。
4. 平台审核中继续等待；价格隔离立即显示需要处理并停止普通自动查询；确认到期或预算耗尽也显示需要处理，保留真实业务状态和必要的冲突保护，不判为平台失败、不重放写入。

任务详情展示目标、逐项回执、最后/下次查询、自动确认剩余轮数、真实 HTTP 请求预算、截止时间及保护范围。列表支持来源、发起方式、状态、平台、店铺、操作、日期、任务号和商品筛选。页面每五秒只读本地结果，与后台是否继续确认无关。

## 查询预算

数值是 ERP 的初始查询策略，不是平台 SLA，定义在 `schemas/task_queue.py`，创建计划时冻结。

| 操作 | 首次延迟 | 后续间隔 | 最大轮数 | 总期限 | HTTP 请求预算 |
| --- | --- | --- | --- | --- | --- |
| Yandex 发布、内容修改 | 60 秒 | 30 分钟 | 24 | 24 小时 | 每目标 120，至少 120 |
| Yandex 价格修改 | 30 秒 | 60 秒 | 10 | 15 分钟 | 40 |
| Ozon 发布、Mercado 异步及其他可查询的未知修改 | 30 秒 | 60 秒 | 10 | 15 分钟 | 每目标 40，至少 80 |

自动确认在轮数、请求数、期限中任一耗尽时停止。每轮最长 120 秒，每个外部请求仍受原请求超时、配额和阻断规则约束。自动计划有效时，人工查询与自动查询共享剩余额度；所有查询共用账号占位及最少 30 秒冷却。

自动确认已停止或历史任务未开启自动确认时，用户仍可显式发起单次人工核验。每次最多使用冻结策略中的 HTTP 请求上限，最长 120 秒，实际请求继续累计计数；它不重置自动期限、累计用量或重启自动查询。人工核验取得近期证据后，在线修改可继续走原人工结束流程，避免过期任务无法解锁。没有可查询远端回执时仍须到平台后台核对。

`task-confirmations.sqlite3` 仅保存原任务引用、冻结策略、已用预算、时间和查询租约，业务结果仍在 `publish_jobs` / `online_jobs`。原任务号作为 `operation_id` 关联真实请求审计。`request_operation.before_send` 在每笔真实请求发出前扣减预算；分页与 Token 刷新同样计数。Ozon `/v1/product/import/info` 明确使用只读语义。

旧任务全部保留并进入列表，缺失发起方式的记录不启动新的自动查询。原有提交时间和已知查询次数继续保留；过期记录不因升级重置自动期限。查询过程中重启后，已过期的自动查询租约可重新领取；手动核验中断不重新开启已停止的自动计划。历史自动确认字段不再驱动执行。

## 结果与保护

- Ozon 导入返回逐商品错误或 `failed` 是明确业务失败，不是查询失败。
- Yandex 内容确认继续检查目标字段与卡片审核；价格隔离是需要处理。
- Mercado 传统 `/global/items` 成功 PUT 允许空对象，站点改价按实际写入的站点 Item 校验身份；User Products 依据目标身份和逐项 `success/errors`，共享库存的 `shared` 不作为站点 ID，站点价格成功回执可以没有根级 `success`。返回 `task_id` 才进入相应任务查询；`finished` 后按逐个 User Product 的结果结算。
- `confirmed` 表示本次操作满足其完成契约，不表示商品一定可售；解除隐藏、恢复归档仍不绕过平台审核。完整快照可按需刷新。
- 在线修改人工结束复用 `OnlineOperationRecovery` 和原 `close_operation`：近期成功回读、当前版本、无仍在处理的任务及用户明确确认。终态为 `closed_unconfirmed`，不证明原操作成功或失败。
- 发布平台终态已持久化而本地回填失败时，只重试原幂等回填，不再次请求平台。

平台契约依据：[Mercado 同步/异步修改及库存](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt)、[传统刊登修改](https://global-selling.mercadolibre.com/devsite/sync-and-modify-listings-gs)、[Yandex 卡片状态](https://yandex.ru/dev/market/partner-api/doc/en/reference/content/getOfferCardsContentStatus)。Mercado User Products 继续只开放当前已核验的库存模式，不将传统库存规则套用到未接入的 Stock Locations。

## 验证入口

- `tests/test_task_queue.py`：正常自动完成、原任务回写、同步立即完成、期限/预算、超期后受限人工核验与解锁、真实 HTTP 计数、人工/自动互斥、历史记录与租约中断恢复、本地回填恢复、Mercado 库存及站点改价回执。
- `tests/test_publish_result_confirmation.py`、`test_online_products.py`、`test_ozon_publish.py`：领域确认、失败分类、回执及防重放。
- `front/src/components/domain/__tests__/TaskQueuePanel.test.ts`：筛选、显式确认、页面轮询仅读本地、需要处理及人工核对入口。

这些验证使用隔离平台替身和本地传输，不代表已完成真实店铺发布或库存修改验收。
