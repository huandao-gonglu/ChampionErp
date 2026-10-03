# 在线商品 AI 接入

接入原则：按已有后端接口暴露能力，不按用户问法新增功能。工具只适配参数、类型、权限和任务回执，继续调用 `OnlineProductService`；不改动平台适配器、统计口径、库存写入或数据库结构。

| 现有接口 | 主 Agent 工具 | 执行方式 |
| --- | --- | --- |
| `GET /api/online-products`（列表或 id 详情） | `online_products_read` | 直接读取；也可在 Code Mode 中分页、筛选和计算 |
| `POST /api/online-products/change` | `online_products_change` | 一个入口处理既有 price / stock / content / sale_state，原生审批和后台任务 |
| `POST /api/online-products/sync` | `online_products_sync` | 沿用领域同步任务，仅读取平台并刷新本地快照 |
| `POST /api/online-products/refresh-status` | `online_products_refresh_status` | 按 listing_id 直接查询单件状态并更新本地记录，不扫描店铺、不创建 Job |
| `POST /api/online-products/reconcile` | `online_products_reconcile` | 只向平台回读已提交修改的结果，不重发修改 |
| `POST /api/online-products/retry` | `online_products_retry` | 原生审批后交由既有失败重试规则判断 |

`online_products_read` 沿用 GET 参数：`id,platform,q,status,market,page`。结果的 `items` 和 `item` 使用同一个公开商品契约，包含价格、库存范围、内容、买家链接、修改能力、版本和同步时间，不包含原始平台快照。列表附带原有摘要和操作记录；接口分支未使用的结果字段采用类型默认值，不更改 HTTP 返回。

`online_products_refresh_status` 接受真实在线刊登 `listing_id`，直接复用 `OnlineProductService.refresh_status`，只合并商品和关联市场状态。返回的 `status_checked_at` 是本次核验时间，`synced_at`、价格、库存、内容保持不变；不能据此声称其他字段也是实时的。工具仅写本地快照、无需平台写入审批，仍使用现有 Runtime 的可信执行身份和 Pydantic AI 原生工具生命周期。它不会替代 `online_products_reconcile` 确认待处理的修改任务。

列表每页包含 25 个组合父节点或独立商品：`groups.item_ids` 引用本页 `items`，组合不会拆到不同页，因而 `items` 可以超过 25 条。`total` 是筛选后的节点数，`listing_total` 是匹配刊登数，`summary.total` 是全店刊登数。完整读取继续按 `total/per_page` 翻页并累计 `items`；不能把父节点数当作 SKU 数，也不能把组 ID 用于商品修改。筛选只返回匹配的刊登，组的 `total_count` 用于解释部分命中。

不增加“查询 SKU 数量”“查零库存”“改红色库存”等工具，也不开放 SQL、任意网络或数据库写入。AI 可直接理解商品字段；需要循环和统计时，使用现有 `run_code` 调用相同读取函数，完整分页后返回必要摘要。未知库存、未授权、同步失败与零库存/零商品不同；统计结果只能表述为实际读取的同步快照。平台没有提供足够规格信息时须澄清，不能猜测变体。

统一修改输入复用 `OnlineChange`，字段为 `listing_id,version,operation,scope_id,changes`；HTTP 的 `ChangeRequest` 继承它并保留 `idempotency_key`。AI 提交键由现有可信执行身份机械生成，不由模型挑选或覆盖。库存使用绝对数量；价格保留范围和币种；内容仅修改后端允许字段，图片仍使用完整目标列表。业务校验、账号隔离、冲突检查、任务互斥和平台回读继续由原服务执行。

图片新增从 `GET /api/online-products/source-images?listing_id=...` 读取关联源草稿的资产选项；AI 使用现有 `online_products_read` 的 `id` 和 `include_source_images=true` 读取同一结果。提交 `{asset_id,fingerprint}`，不能手填新 URL 或任意平台图片 ID。关联同时核验发布账号和远端商品身份；没有唯一来源时仍可调整现有图集。后台只准备本次选择，保留源草稿和图片池；Mercado 复用上传接口取得图片 ID，Yandex 复用 HTTPS 交付服务。任务回执保存准备后的完整目标图集，回读据此确认顺序与图片身份，不再次上传。

Mercado 新图片的上传与关联依据官方[图片文档](https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/pictures)和 [User Products 图片更新规则](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt)。传统 Item 新图先关联 `/items/{id}/pictures` 再提交完整图集；User Products 上传后直接更新 `/global/user-products/{id}`。

修改与失败重试使用现有审批模式：`ask` 显示原生审批卡，`full` 使用已有自动批准机制。摘要和参数绑定在服务器生成，执行时重核；不能通过换参数或切换店铺复用审批。同步和回读不会修改平台商品，无需额外人工审批。所有持久操作均在原生 Deferred 提交后由现有 Agent Job Service 投递。

`OnlineProductJobReader` 把 `online_jobs` 投影成现有 `JobStateSnapshot`。只有 `confirmed` 为成功；`partial`、`outcome_unknown`、失败及自动回读耗尽以未完成成功的工具结果返回，`last_external_status` 保留领域原始状态。通用状态 `failed` 不代表平台明确拒绝写入；模型须看原状态和操作记录，不得重放未知结果。Reader 不触发模型、平台请求或新任务。

为什么直接使用 Pydantic AI 原生能力：项目当前安装 `pydantic-ai-slim==2.44.0`、Harness `0.34.0`，已有 Tool Bridge、Code Mode、审批及持久工具回执，足以覆盖本次接入。没有新增 Agent loop、审批协议、等待/恢复状态机或事件编码。已核对 [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) 和 [Code Mode](https://pydantic.dev/docs/ai/harness/code-mode/) 官方文档。

在线管理页面复用原有“读取背景”开关，只传递当前平台和选中的在线 `listing_id`；它是定位信息，不能作为商品事实或操作授权。

## 验证边界

`tests/test_online_product_capabilities.py` 通过真实服务与隔离数据库验证读取契约、跨页计算、统一修改、版本与审批绑定、失败重试、未知结果只回读，以及 `ask/full` 两种模式下的原生 Deferred → 业务 Job → 平台回读 → 模型结果链路。详情读取覆盖直接工具与 Python 并发调用、显式/省略平台参数，以及脚本缺少导入后由原生重试恢复的完整工具结果校验。模型和平台 I/O 使用受控替身，不把这些结果当成真实店铺写入或任意模型理解能力的证明。

Yandex、Mercado 复用已接通的在线服务；Ozon 的现有 API 权限阻碍不会因开放工具而消失。工具保留真实授权失败与能力限制，不把未取得数据描述为零库存。

2026-09-27 验收：后端全量 1897 项测试、47 项子测试通过；在线 AI 专项 23 项通过；前端 59 个测试文件、473 项测试通过，`vue-tsc -b`、Vite 构建、相关模块编译及 `git diff --check` 通过。构建保留既有大 chunk 提示。

本地后端已重新加载；通过生产 ToolSet 和只读 Runtime 验证 Yandex 200 件、Mercado 91 件及 Ozon 授权失败状态，后端 5050 与前端代理 3000 均返回 HTTP 200。重载及只读检查前后 291 件在线商品数据库快照哈希一致，未执行真实平台商品修改；本轮未进行真实模型自然语言或浏览器点击验收。

同日修复详情读取的 `TOOL_OUTPUT_SCHEMA_INVALID`：详情分支的顶层 `platform` 默认 `null`，原编译器把可空枚举展平后错误地拒绝该值。编译器现在保留 [Pydantic 生成的原生 `anyOf` 分支](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)，由现有 Runtime 验证；通用测试覆盖可空多值 Literal、单值 Literal 和 Enum 的合法/非法输入与输出。继续使用 Pydantic AI 原生工具校验、重试和 Code Mode，没有新增恢复机制或提高重试上限。

使用真实 HTTP 只读详情重放故障对话的原始脚本：漏导入的首次调用仍由原生机制要求纠正，补正后的第二次调用成功返回 2 件商品；原始补传平台参数的脚本也成功。回放使用受控模型和临时数据库，原对话历史及真实商品快照未修改。

修复后验收：后端全量 1921 项测试、47 项子测试通过；Catalog 与在线 AI 专项 57 项通过。模块编译和 `git diff --check` 通过；本地后端重新加载成功，故障查询仍返回 2 件商品，重载前后真实商品快照与原对话历史一致。
