# 外部 API 自动查询与轮询审核清单

核查日期：2026-10-08。范围为本仓库后端、前端及启动脚本中的定时器、后台 worker、延迟确认、失败重试、分页和按需缓存刷新；不包含第三方平台网页自身的网络行为。依据当前源码及本地请求审计库，未为盘点额外请求远端平台。

已按用户审核决定修改 A1、A3–A6；A2 回调处理、A7/A8 具体业务执行和 L 类本地读取保留。下表区分原行为与当前行为，其他按需外部 API 继续列出供审核。

“一次同步”指一次业务查询流程，不保证只有一个 HTTP 请求：分页、补充信息及按需刷新 Token 都可能增加请求数。下文将外部 API 请求、本地快照轮询、纯界面计时分别列出。

## 1. 已修改：跨境履约的外部状态同步

| 项目 | 原行为 | 修改后的行为 |
| --- | --- | --- |
| 已关联预报单 | 后台每单约 60 秒查询一次，与页面无关 | 进入订单详情的“跨境履约”页签时查询一次；停留期间不查，点击“同步”再查 |
| 查询失败 | 约 120 秒后再次自动查询 | 保留上次进度和错误，等待再次进入页面或手动同步 |
| 创建结果未知 | 后台持续查询预报列表核实 | 保留禁止重复创建的约束，只在页面进入/点击核实时查询 |
| 取消已发送、结果未确认 | 后台持续查询仓库状态 | 等待页面进入/点击核实；不自动重发取消 |
| 页面快照刷新 | 每 5 秒 GET 本地履约详情 | 保留本地刷新以展示业务进度及面单错误，不触发外部查询；“同步”按钮才按关联状态查询远端 |
| 同步命令 | 写入调度标记，后台异步执行 | 当前请求内只读核实并返回结果；不代报单、取消或更新包裹 |
| 旧调度字段 | `next_attempt` / `force_sync` 可推动状态轮询 | 已保存数据继续可读，但旧标记不能再触发后台状态查询 |

未关联且没有创建待确认事实的订单，只读取本地资料，不尝试认领其他来源的远端预报。查询期间遵守订单版本、占位和授权身份检查；重复/过期请求不外发。离开页签后迟到的本地读取不会启动远端查询；已发送的 HTTP 查询可能完成，但不会续订下一轮。

仍保留自动预报、资料更新及平台取消传播等业务任务。实际更新/取消之前的状态检查属于该次写入的前置检查，失败后停止，不能变成后台查询循环。自动获取面单首次保留，失败停止并提示人工重新获取，见 A3。

源码：[履约服务](../erp_web/services/fulfillment_service.py)、[履约页面](../front/src/components/domain/OrderFulfillmentPanel.vue)、[跨境巴士客户端](../erp_web/services/crossborderbus_client.py)。

## 2. A1–A8 审核决定与当前行为

| 编号 | 实际用途及原行为 | 审核后当前行为 | 外部调用边界 |
| --- | --- | --- | --- |
| A1 | 后端每店铺约 300 秒主动拉取平台订单列表，并补查本地未完成订单 | 已改为进入订单中心一次 + “同步订单”按钮；切筛选、翻页和本地刷新不再次外发。主动查询失败停止并显示错误，不自动重试 | 一次同步仍需完成分页及补查；同账号已有任务时合并，不创建重复任务 |
| A2 | 被动接收平台回调后读取对应订单，并在失败时重试 | 保留回调接收、去重、处理及其原有失败重试；与 A1 主动对账分开 | 回调 HTTP 本身只落盘，后台为完成该通知可能调用外部 API。普通退避 10、20、40…秒，上限 1800 秒、通常最多 8 次；接口冷却按恢复时间重试，不受该 8 次上限约束 |
| A3 | 自动预报资料齐全时获取 Yandex 平台面单；原失败每 300 秒再试 | 首次获取保留；失败后保存原因，页面弹出提示并提供“重新获取面单”，不自动重试 | 一次获取可能含箱号、PDF、S3 Head/Put 和公开下载验证。旧面单重试时间不能重新启动任务 |
| A4 | 在线商品修改受理后约 120 秒自动查询是否生效 | 已删除延迟自动确认；任务详情“查询平台结果”手动执行 | 连续人工查询间隔 30 秒，首次可立即查询。历史自动确认标记和到期时间不再驱动外发 |
| A5 | 商品发布受理后约 120 秒自动查一次结果，重启恢复计划 | 已删除查询调度线程及重启恢复；发布任务“查询最新结果”手动执行 | 保留回执、未知状态、禁止重复提交和并发占位；连续手动查询冷却 30 秒 |
| A6 | 进入发布列表或选中任务时，前端经后端批量查询外部平台 | 已删除自动外发；进页、选详情和本地刷新只读取 ERP 保存结果 | 原行为确实调用外部 API，并非单纯前后端读取；后端也拒绝旧 `view/scheduled` 自动触发来源 |
| A7 | 已提交发布的后续写步骤，例如 Yandex 商品资料受理后继续写库存 | 保留完成明确业务的步骤执行 | `pending_submission` 的步骤等待默认 2 秒、范围 0.5–30 秒；不是反复查询发布结果。结果确认独立走 A5 |
| A8 | 已配置的自动报单、用户要求的包裹/资料更新、平台取消后的取消传播 | 保留具体业务任务；被动仓库进度和未知结果不后台轮询 | 本地扫描每轮结束等 2 秒。写操作必要前置检查保留，前置检查失败停止；正常已关联订单没有后台状态查询 |

历史数据仍可读取：A1 旧 `sync_schedule` 不再参与调度，旧主动同步重试/过期执行记录等待人工重发；A4/A5 旧确认计划不恢复自动执行。发布和修改回执、幂等约束、未完成业务步骤仍保留。

### A1/A2 的具体订单接口与请求放大因素

| 平台 | 外部接口 | 每轮可能增加的请求 |
| --- | --- | --- |
| Yandex | `POST /v1/businesses/{business}/orders` | 每页 50 单；按当前接口默认窗口读取，窗口外未结束订单逐单查 |
| Yandex 交货批次 | `PUT /v2/campaigns/{campaign}/first-mile/shipments`（只读搜索） | 订单页中符合条件的 FBS 订单追加批次查询，每页 30 条、最多 10 页 |
| Ozon | `POST /v4/posting/fbs/list`、`POST /v3/posting/fbo/list` | FBS/FBO 各自遍历最近 30 天，每页 100 条 |
| Ozon 单单核实 | `POST /v3/posting/fbs/get`、`POST /v2/posting/fbo/get` | 回调及列表未覆盖的在途订单可能逐单调用；部分回调会转全量同步 |
| Mercado Libre | `GET /orders/search/recent`、`GET /orders/{id}`、`GET /shipments/{id}` | 最近订单每页 50 条；物流回调缺订单归属时会转全量同步 |
| Mercado Libre SLA | `GET /shipments/{id}/sla` 或 `GET /marketplace/shipments/{id}/sla` | 待发货订单存在 shipment ID 时逐单补查 SLA |

修改前，如果后端全天运行且每次任务足够快，A1 每账号约 288 轮/天；现在没有这项定时调用。这只是轮数，不是请求数，分页、SLA、批次和失败重试都会增加实际尝试。外部管理器本地拒绝的尝试不计为实际外发，但仍有本地调度和数据库开销。

源码入口：

- A1/A2：[订单 worker](../erp_web/services/order_notification_service.py)、[调度及重试 Store](../erp_web/stores/order_notification_store.py)、[Yandex](../erp_web/runtime_units/orders_yandex.py)、[Ozon](../erp_web/runtime_units/orders_ozon.py)、[Mercado Libre](../erp_web/runtime_units/orders_mercadolibre.py)、[交货批次](../erp_web/services/order_handover_service.py)。
- A3/A8：[履约服务](../erp_web/services/fulfillment_service.py)、[平台面单](../erp_web/services/platform_label_service.py)、[面单托管](../erp_web/services/fulfillment_label_service.py)。
- A4：[在线商品服务](../erp_web/services/online_product_service.py)、[任务领取](../erp_web/stores/online_product_store.py)、[按变更字段回读](../erp_web/runtime_units/online_change_confirmation.py)。
- A5/A6：[发布结果确认](../erp_web/runtime_units/publish_result_confirmation.py)、[页面进入检查](../front/src/stores/workflow/publishing.ts)。
- A7：[发布执行](../erp_web/runtime_units/publishing_bus_core.py)、[步骤间隔](../erp_web/runtime_units/publish_adapter.py)。

## 3. 外部 API 的其他调用方式：未发现独立定时轮询

| 范围 | 触发与外部请求 | 缓存/重试边界与审核提示 |
| --- | --- | --- |
| 跨境巴士配置和服务 | 验证授权、刷新合作仓库、选择渠道/仓库、保存方案时读取合作范围及服务；创建/更新/取消由业务动作触发 | Token 临近过期时，在业务请求内刷新；没有独立 Token 定时器。列表查询分页最多 100 页 |
| 三平台在线商品同步/详情/状态 | 人工或已授权工具提交同步后，任务分页、批量或逐商品读取；刷新状态按钮一次回读；普通本地列表 GET 不外发 | 任务完成停止，没有定时整店商品同步；一次显式同步可能产生大量 API 请求 |
| 授权验证、账号能力、店铺币种 | 授权/验证/保存等操作按对应入口请求平台；Mercado Libre 按业务需要刷新 Token | 不等同于常驻状态轮询；凭据更新不应隐式重放未知写入 |
| 类目树、类目属性、字典、仓库、物流报价 | 类目/字典/报价等明确业务入口按需查询，部分有缓存、分页或备用查询路径 | 前端字典搜索有 250 ms 防抖，一次输入变化仍可调用外部 API；没有独立周期刷新线程 |
| 汇率 | 核价需要汇率或人工强制刷新时调用配置的汇率 API，默认 `https://open.er-api.com/v6/latest/USD` | 默认缓存 3600 秒；缓存过期后由下一次业务调用刷新，不是每小时主动刷新 |
| Sorftime 选品、1688 搜索/详情、HTML 采集 | 用户/工具启动的领域任务调用配置供应商；Sorftime 当前单次请求 `max_attempts=1`，关键词查询一页 | 选品页面的任务轮询只读本地，不能按页面刷新次数计算 Sorftime 调用次数 |
| 图片下载、翻译/生成、S3 上传及公开验证 | 按图片操作、面单交付或发布素材准备执行，可能逐素材外发 | S3 `total_max_attempts=1`；Head 不存在时上传，再核验对象与公开内容，不是周期查询 |
| AI API | 已发起的 Agent run、工具回执续跑或明确推理任务调用模型 | SDK HTTP 自动重试关闭，HTTPX retries=0；原生 Agent 的工具/输出验证重试独立存在（主 Agent 配置 retries=2，类目 Agent retries=4，文案结构验证 retries=2 且最多 3 次模型请求），可能增加模型回合，不是页面轮询 |
| 通用外部请求管理 | 收到业务请求后检查限额/冷却/租约，再决定发送 | 默认 max_attempts=1，Schema 允许显式只读预算最多 3 次。冷却到期不会自行造请求，只允许下一次业务请求探测；写入未知不重放 |
| Browser/CLI 连接 | 仅在已启动的采集或模型任务中等待结果 | 浏览器适配器的 0.4–1 秒检查主要读取 CDP/DOM，有任务超时；网页自身网络行为不由本清单覆盖 |

源码补充：[汇率](../erp_web/runtime_units/pricing_runtime.py)、[类目字段搜索](../front/src/components/domain/CategoryAttributesPanel.vue)、[Sorftime](../erp_web/services/sorftime_client.py)、[供应商采集](../erp_web/runtime_units/source_collect_1688_api.py)、[S3](../erp_web/services/s3_image_storage.py)、[外部请求管理](../erp_web/services/external_request_manager.py)、[HTTPX](../erp_web/services/external_httpx_transport.py)、[请求 Schema](../erp_web/schemas/external_requests.py)、[AI Provider](../erp_web/services/ai_provider_catalog.py)、[主 Agent](../erp_web/services/global_agent_chat_service.py)。

## 4. 仅访问本地后端或状态的轮询（按用户决定保留）

这些行为有浏览器、Python 和数据库开销，但计时器本身不会向第三方重复查状态。

| 编号 | 位置 | 频率、触发与停止条件 | 是否外发 |
| --- | --- | --- | --- |
| L1 | 订单提醒与订单列表 | 前端 store 启动后每轮完成等待 5 秒读摘要；订单中心活动时同时读本地列表；store.stop 时停止 | 否；周期本地读取不触发 A1，A1 仅进页/按钮发起 |
| L2 | 在线商品页面 | 有 queued/running 任务且页面可见时每 5 秒读本地；隐藏/卸载停止定时读取 | 否；运行中任务的本地刷新保留，已删除依赖旧 A4 计划的无效定时器 |
| L3 | 选品页面 | 运行中每 2 秒读任务状态；隐藏时跳过，终态/错误/卸载停止 | 否 |
| L4 | 全局外部请求异常提醒 | 有被观察操作 ID 时每轮约 5 秒查询 `/api/external-requests/status`；卸载停止 | 否，只读审计 |
| L5 | 授权页请求管理面板 | 页面打开每轮约 5 秒读取请求状态；另每 1 秒更新倒计时；卸载停止 | 否，只读审计；倒计时不发请求 |
| L6 | AI 聊天恢复 | 优先 SSE；无 EventSource、停止中或回执同步时约 0.2–2 秒读本地；读取失败最多额外 3 次指数退避；轮询随任务结束停止，但选中聊天的 SSE 会继续重连，切换/释放聊天时清理 | 状态读取不执行模型；既有后台 Agent run 可能继续外发 |
| L7 | 商品采集人工验证等待 | 遇到登录/验证码后每 2 秒调用本地检查接口，经 CDP 读取原商品页；验证完成、取消或不可用时停止，无固定次数上限 | 检查不刷新远端页面；验证通过后自动继续原采集流程，可能产生采集请求 |
| L8 | Agent Job 扫描 | 后端每 1 秒读持久工具/领域 Job、投递已授权任务和回执；服务停止结束 | 本地对账不查询第三方；有待执行工具或原生续跑时会产生业务/模型请求 |
| L9 | 订单/在线商品/履约 worker 的空闲扫描 | 订单空闲等 2 秒、有工作等 0.1 秒；在线商品/履约每轮空闲约 2 秒 | 扫描本身只读本地，实际外发条件见 A1–A8 |
| L10 | 跨境履约方案编辑租约 | 编辑弹窗打开时每 60 秒续期；保存/关闭/卸载停止 | 否，只更新本地编辑锁 |
| L11 | AI SSE 心跳与重连 | 每条 SSE 连接每秒检查本地历史版本，最多约 60 秒后关闭；前端约 1 秒后回读并重连，选中的聊天即使没有活跃运行也可持续连接 | 否，只读本地；本次不调整 |
| L12 | 订单截止时间、Toast、复制提示、下载 URL 释放 | 订单时钟每 60 秒；其余是一次性 UI 定时器 | 不产生 API 请求 |

源码：[订单前端 Store](../front/src/stores/orderNotifications.ts)、[在线商品页](../front/src/components/domain/OnlineProductsPanel.vue)、[选品页](../front/src/components/domain/ProductResearchPanel.vue)、[全局请求提醒](../front/src/components/common/ExternalRequestNotice.vue)、[请求管理页](../front/src/components/auth/ExternalRequestControlPanel.vue)、[AI Store](../front/src/stores/aiChat.ts)、[采集等待](../front/src/stores/workflow/actions/collection.ts)、[Job 扫描](../erp_web/services/agent_job_service.py)、[服务启动](../erp_web/server.py)。

## 5. 实际审计样本

采集时刻：2026-10-07 23:35:13（Asia/Shanghai）；统计该时刻向前 24 小时的 `data/external-requests.sqlite3.external_attempts`。只聚合平台，不输出账号、凭据、请求正文或业务内容。审计保留上限及实际服务运行时间可能影响样本，不能据此推算全天在线情况下的总量。

| 平台 | 记录的尝试 | 标记已发送 | 本地拒绝 |
| --- | ---: | ---: | ---: |
| Yandex | 299 | 60 | 239 |
| Mercado Libre | 272 | 2 | 270 |
| Ozon | 272 | 1 | 271 |
| AI DeepSeek | 12 | 12 | 0 |
| 跨境巴士 | 3 | 3 | 0 |
| Sorftime | 2 | 2 | 0 |

其中跨境巴士的 3 次是本次前序排查时明确发起的只读查询，不能当作轮询造成的开销。其他平台总数混合所有业务触发，不全部归属于订单定时对账；“标记已发送”也不等于供应商实际收费次数。

## 6. 本次范围及验收重点

- 目标只削减无业务动作触发的外部 API 查询，不统一删掉本地定时器、分页或具体业务执行。
- 订单中心挂载一次只提交一轮同步；停留和本地刷新不重复提交，手动按钮和重新进入页面可再次同步。
- 主动订单同步及面单失败不自动重试；平台回调仍可正常处理。
- 在线商品和发布任务的历史定时字段不能导致后台外发，手动查询仍可确认结果，失败不重放写入。
- A7/A8 后台完成已授权业务，所有其他按需外部请求见第 3 节；本次不改变其业务行为。
