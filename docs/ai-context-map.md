# AI 上下文地图

本文是按需查阅的维护索引，记录当前入口及不明显的业务边界；全项目开发约定见 [AGENTS.md](../AGENTS.md)。字段和函数签名以代码及 Schema 为准，当前依赖版本见 `requirements.txt` 和 `front/package.json`；下文带版本的核对记录只表示当时采用该能力的依据。

文档作用域：

- 当前领域契约：见各节链接的 SKU、AI Work、在线商品、外部请求、订单和物流说明。
- 历史方案与验收：类目 Schema 分离、币种迁移、原生 Agent 重构及旧 Global Task 验收记录只用于追溯；其中的实施步骤、未勾选清单和数据删除授权不作为当前任务指令。
- 产品 Agent 指令：`config/agents.md`、`config/prompts/` 及服务中的运行时指令由产品加载，与开发规范分开。

## 主 Agent 的通用 Python 执行

`erp_web/services/ai_code_mode.py` 装配官方 CodeMode、原生调用额度检查和脚本输出限制；
`ai_agent_factory.py` 依据 Execution Profile 接入，主对话启用。
`DirectAndPythonToolset.get_tools` 通过官方公开扩展点保留业务工具直接入口，并提供可选的 `run_code`；
简单操作直接调用，循环、转换和计算才使用 Python。两种入口复用同一个 Tool Bridge 和业务执行边界，
调用、REPL、审批与生命周期仍由官方实现负责，不另建数据接口或业务批量 DSL。
当前依赖为 Pydantic AI 2.44.0 / Harness 0.34.0 / Monty 0.0.23；旧条目中的版本号记录当时核对依据。
职责、限制、原生机制选型及验收见 [通用 Python 执行](agent-python-execution.md)。

`ai_tool_bridge.py` 将已有输出 Schema 通过原生 `Tool.prepare` / `ToolDefinition.return_schema`
交给 CodeMode 生成返回类型；`ai_tool_contract_description.py` 只补充 Python 签名不显示的参数约束说明。
`draft_attributes_read(scope=sku)` 默认读取全部已选启用 SKU，可用 `limit` 和 `next_offset` 主动分段。
返回 `updated_at` 与具名 `DraftAttributeSku` 字段，脚本无需为了猜测结构反复读取。
`schemas/draft_changes.py` 定义成组局部修改契约；`runtime_units/draft_changes_capability.py`
提供 `draft_changes_apply`：同一草稿的公共/SKU 属性、包装和库存可混合，平台校验在商品锁外，
锁内重读版本、范围和状态，全部通过后只调用一次 `save_draft_content`。
`category_attribute_updates.AttributeUpdateValidation` 在一次提交内复用类目定义及已核验候选；
`product_attribute_patch.py` 由单项和成组工具共用纯属性合并规则，不包含脚本执行或模型循环。


内部业务通过 `ProductStore.load_draft_content` / `save_draft_content` 读取和保存规范化内容；
后者是草稿保存规则的唯一实现，保留互斥、版本冲突、归一化和发布状态失效检查。
`load_draft_detail_from_index` / `save_draft_detail` 为页面薄包装，另行生成商品上下文和列表索引。
属性、图片、文案、核价及 SKU 包装/勾选操作使用内容入口，避免逐项扫描全库页面列表。

## 运行时边界

- `erp_web/runtime.py` 与原 `runtime_units` 兼容转发模块已经删除，不得重建聚合入口。
- 直接 import 具体 facade、service、store 或 schema owner。
- 新 HTTP 行为从 `erp_web/http_route_units/` 的显式 handler map 进入；路由把编排交给
  `erp_web/facades/` 或职责单一的 service。
- `erp_web/runtime_units/json_store.py`：运行时 JSON 文件原子读写的依赖轻量 owner；
  配置、发布产物和其他领域模块不得再从类目 Store 借用通用文件写入能力。

## 统一外部请求管理

自动查询、延迟确认、失败重试及仅本地轮询的逐项审核清单见 [外部 API 查询审核](external-api-query-audit.md)。

- `schemas/external_requests.py` 定义 RequestContext、阻断与写入未知契约；
  `ExternalRequestNotSent` 表示传输层确认尚未发送 HTTP 请求。管理器将它记录为
  `not_sent` 本地拒绝，释放租约且不触发写入结果未知阻断；平台错误由 `schemas/platform_errors.py` 持有。
- `services/external_request_context.py` 关联 HTTP、领域 Job、AI Tool 的来源与取消/时间边界，仅读本地账号绑定。
- `services/external_request_manager.py` 是 urllib API 外发唯一入口；普通 JSON、表单、图片上传与采集请求均接入。
  `services/external_httpx_transport.py` 经原生 Provider 的 http_client 注入 SDK，同样检查共享状态，原生 SDK 自动重试关闭。
  流式审计只结算一次：HTTP EOF 记成功，SDK 主动关闭记 `stream_closed`，实际读取异常才记网络中断。
  `GeneratorExit` 不累计网络失败；传输层不解析模型终止事件，也不推断 Agent 是否完成。
  Pydantic AI 仍独占模型/Agent 生命周期，不新增模型请求旁路。
- `services/platform_request_policy.py` 纯解析 HTTP 与业务拒绝；`stores/external_request_store.py` 用独立
  `external-requests.sqlite3` 保存尝试、限额、阻断与恢复，不改变 ERP 主库版本。
  放行与租约分配在短事务内完成；账号停用跨模块、重启及换 Key 保留，接口拒绝只影响该接口。
  `image_hosting:public` 的匿名 401/403 仅拒绝本次请求，不认定 S3 凭据失效；
  用户修正桶公开权限后可再次显式检查。
- 默认不自动重试；显式只读预算最多 3 次，写入未知禁止重放。平台适配器解释业务，管理器不推进领域任务。
- `scripts/external_requests.py` 提供 query/blocks/configure/recover/recoveries；恢复不发送探测请求。
  具体范围、平台依据、保留策略及 01–11 项验收见 [实施与运维说明](external-request-management.md)。

## 草稿操作后的继续保存

- `copy_facade.generate_copy_payload` 通过 `current_draft_id` 绑定用户选中的独立草稿。`copy_generation.save_copy_result` 将该操作上下文交给 `ProductStore.save_draft_copy_result`；Store 必须在商品归一化前取出草稿 ID，不能丢失后改为同平台最新草稿。文案仅合入指定草稿的最新内容，已删除的目标不得被重建。
- `/api/category-precheck` 会持久化目标的预检结论，因此响应包含保存后的 `draft`、`productContext` 和索引。前端 `runCategoryPrecheck` 与类目动作同步这份草稿及其 `updatedAt`，后续保存沿用新版本；预检记录只保留检查结论，不嵌套整个保存响应。
- `/api/save-draft` 继续拒绝真正过期的版本；不得通过省略 `updated_at` 或自动强制重试绕过并发修改保护。`tests/test_draft_save_after_actions.py` 验证同商品同平台双草稿的文案隔离、预检后继续保存以及旧版本仍被拒绝。

## 商品采集

- 1688 图片由 `source_collect_parsers.py` 优先读取 `gallery.fields.mainImage`，再通过 `html_extract_service.fetch_1688_detail_html` 读取独立 `description.fields.detailUrl` 的详情内容；SKU 图仅由规格关联入池。`collect_helpers.normalize_collect_source_images` 统一下载全部公共图和规格图，以原 URL 保留稳定资产身份，不再截断前五张，也不重复保存本地路径和远程 URL。详情读取失败显式报告采集失败，不能以已有 SKU 图片假报完整采集。

- 商品库推到草稿统一从 `/api/claim-products` → `collect_facade.claim_products_payload` → `collect_helpers.claim_products_to_platforms`，请求必须显式携带 `product_ids` 与 `targets`（平台、销售市场、注册语言）。每个商品按语言创建一份独立草稿，市场只包含该语言下勾选的项目；美客多销售市场按授权物流操作归入 CBT 的 `sites_to_sell`。响应的 `claimed_count` 统计成功商品数，`draft_count` 统计实际创建草稿数。
- 顶部批量与单行操作共用 `claimProductsToDrafts`，只传各自商品 ID 和顶部市场选择，不使用当前平台或整库兜底。AI 认领与市场准备任务仍使用其显式平台参数，复用同一个商品复制、草稿持久化循环。

- `source_sites.py` 的采集质量门槛与核价/发布条件分开：1688 有标题和图片且未被验证拦截即可入库。缺失包装长宽高、重量通过 `collect_helpers.py` 的 `missing_fields` / `next_action` 提示在 SKU 页补齐，不触发采集失败；有规格时逐 SKU 判断包装完整性，不用首项资料代表整组。
- `front/src/views/workflow/CollectView.vue` 组织采集方式；`BrowserCollector.vue` 负责浏览器页面选择，
  `CollectBatchManager.vue` 负责 URL 增删改、状态筛选和失败重试。
- 批量列表状态为 `pending`（未开始）、`running`（正在采集）、`waiting_verification`（等待验证）、`success`（完成）、`failed`（失败）。部分结果保留资料提示并显示失败。
- `front/src/stores/workflow/actions/collection.ts` 逐条提交 `/api/collect-batch`。遇到登录或验证码，后端在解析、下载、商品写入之前返回 `waiting_verification`；其 `verification` 契约由 `schemas/collection.py` 定义，仅含标签 ID、原商品 URL、平台。
- `/api/collect-verification` 由 `collect_routes` → `collect_facade` → `source_collect_verification.inspect_collection_verification` 只读检查原标签。前端每两秒检查一次，不导航、不刷新；验证完成后用原标签 ID 调用 `/api/collect-from-browser-tab`，再次验证页面身份并采集，然后继续后续链接。标签被关闭、离开商品或无法读取时返回可操作提示，不改采集目标。
- 用户可以取消等待，未开始的链接保持原状。`collectQueue.ts` 本地保留等待项的标签 ID 和 URL，刷新应用或取消后点击开始采集可恢复；采集应用关闭后不承诺后台执行。不保存凭据或整份商品。正在实际采集时刷新仍标记结果未确认，避免假报成功。删除列表行不删除入库商品。
- `/api/collect-from-browser-tab` 的显式 URL 或标签 ID 只选择指定目标；`save_only` 只保存 HTML/截图，不解析、不下载商品图片、不修改商品。验证码页不会作为部分商品写入。
- 上述等待是 ERP 浏览器采集的领域交互，不创建 Agent run、审批或消息协议，不涉及 Pydantic AI 生命周期。
- `source_collect_browser.cdp_target_for_url` 复用相同 URL 或同一 1688 offer 路径的页面，后者允许验证后查询参数变化；其他商品仍通过 Chrome DevTools 的 `PUT /json/new` 创建目标页。打开操作直接返回目标句柄，采集不重复查找或强制刷新，保留人工登录和验证后的页面；协议依据：[Chrome DevTools HTTP endpoints](https://chromedevtools.github.io/devtools-protocol/)。

## 草稿描述

- 商品采集和主档只保存标题、属性、SKU、图片等事实资料，不采集或维护描述与独立卖点；采集诊断不再统计描述。新建草稿的描述为空，不从来源继承，也不拼接模板描述。
- `services/copy_service.py` 根据标题、商品/来源属性和 SKU 规格生成平台草稿 `description`，允许在草稿中人工编辑；发布与生图读取草稿文案。缺少事实依据的优势、用途、认证等不得编造。
- `stores/product_description_migration.py` 在数据库读取边界移除历史商品/来源描述及卖点，保留各草稿的描述，历史草稿卖点并入各自描述。保存后仅写入当前契约；不改变 SQLite schema，也不改写发布历史。
- 文案生成继续使用 Pydantic AI 原生 `PromptedOutput` 和输出校验，没有新增 Agent 基础设施。

## SQLite 数据库版本边界

- `erp_web/db.py` 是 schema 与版本门禁的唯一 owner，当前 `SCHEMA_VERSION=16`。
  真正空库才自动建表；已有库只接受完整当前结构，运行时不修补、不升级。
- 完整 v15 库使用 `scripts/migrate_online_products.py <数据库>` 显式升级到 v16。
  脚本先创建权限 0600 的 SQLite 一致性备份，再在事务内校验原结构、添加在线商品两表及索引。
  商品、草稿、授权、发布及 Agent 持久数据全部保留。其他版本或残缺结构在写入前拒绝。
- `upc_pool.json` 是已购买 UPC 的显式资产导入，不属于 schema 迁移。
- 2026-09-28 请求管理开发中间版本曾将 `external_*` 审计表写入 v16 主库。
  `scripts/migrate_external_request_audit.py <主库>` 仅识别这套完整遗留结构：先保存权限 0600 的
  一致性备份，再迁到独立审计库；目标提交成功后才移除主库审计表，业务数据及主库版本不变。
  运行时不会自动迁移；结构拒绝信息列出多余、缺失或定义不一致的对象，不再把同版本结构差异误报为版本过旧。

## 在线商品管理

- 离线模板导出：`POST /api/online-products/ozon-export/preview` / `download` →
  `online_product_facade.export_ozon` → `OnlineOzonExportService`；只读取当前 Yandex 店铺快照，
  不调用平台 API、不修改在线商品或草稿、不进入发布队列。
  `ozon_category_template.py` 解析官方 XLSX 的隐藏元数据并替换商品数据区，保留字典、校验和样式；
  当前仅支持已核对的中文圣诞装饰品 CNY 模板，其他类目或必填契约变化显式拒绝。
  生成时重建预览并检查 `preview_fingerprint`，编辑只作用于本次请求的导出覆盖值。
  前端 `OzonExportDialog.vue` 复用 `WorkspaceDialog`；列表仅增加选择列和筛选栏的“操作”菜单。
  字段与输出契约见 `schemas/ozon_template_export.py`，操作与验证说明见 [Ozon 模板导出](ozon-template-export.md)。
- `http_route_units/online_product_routes.py` 是列表/详情及同步、单件状态刷新、修改、回读、失败重试的唯一 HTTP 入口。
  `facades/online_product_facade.py` 负责请求转换；`facades/online_product_factory.py` 显式装配三个平台适配器。
- `services/online_product_service.py` 编排 ERP 领域任务，通过注入访问适配器，不反向导入 runtime。
  `refresh_status` 直接查询单件状态；`POST /api/online-products/refresh-status` 不入同步队列，返回更新后的公开商品。
  `stores/online_product_store.py::update_status` 校验并发版本后合并状态、平台反馈及 `status_checked_at`，保留价格、库存、内容和完整同步时间。
  `services/online_product_sync.py` 消费 `OnlineSyncBatch`、保存目录记录与详情进度，详情失败保留旧业务快照。
  `services/online_product_changes.py` 负责确定性变更校验与字段回读比较；详情未完整同步时禁止修改。
  `services/online_product_images.py` 按发布记录中的远端身份和账号精确关联源草稿，
  `GET /api/online-products/source-images?listing_id=...` 返回可选图片资产与内容版本；
  新图仅接受 `{asset_id, fingerprint}`，提交及执行前均校验归属和版本，禁止手填地址绕过来源。
  `online_products_read` 的 `include_source_images=true` 在读取单件详情时复用相同选图查询，不新增工具。
  HTTPS 图集复用 `ImageDeliveryService`，Mercado 图集经 `online_mercadolibre_images.py`
  复用现有上传客户端取得图片 ID；传统 Item 先关联新图片，User Products 提交完整图片 ID 列表。
  准备后的实际目标图集写入领域任务回执，人工回读比较该图集，不比较本地资产引用。
  图片准备不修改源草稿、SKU 默认图或源图片池，临时上传文件随准备作用域清理。
  `services/online_product_listing.py` 从快照中的平台组合标识生成父节点，筛选 SKU 后按节点分页，组合不跨页；
  `total` 统计节点，`listing_total` 统计匹配刊登，公开 `groups.item_ids` 引用本页 `items`，父节点不接受修改。
  `groups.feedback_summary` 汇总整个组合的受影响 SKU 数、平台错误和警告条数，覆盖当前筛选隐藏的 SKU；
  前端父行在折叠时也显示汇总，单件状态刷新按该 SKU 的变化调整父行计数，不影响其他 SKU 的反馈。
  AI 列表唯一入口为 `OnlineProductService.read_page` → `listing_summary_page`，复用页面的组合身份、顺序和筛选。
  默认 `view=groups` 仅返回父节点和代表刊登；`view=listings` 按 SKU 分页，可用 `group_id` 限定成员。
  `limit` 为 1–50，按 `next_page` 遍历，组合可跨页；默认摘要不携带内容详情、完整成员 ID 列表或历史任务。
  `fields` 支持公开业务字段和对象点路径，批量与单件均通过 `records` 返回所选 `values`，
  身份、版本、同步时间、详情状态和错误固定保留，缺失路径列入 `missing_fields`，不混同于真实 null 或零值。
  价格、库存、属性等数组整体读取，保留币种、仓库、名称和单位；平台原始 `snapshot` 不开放。
  编号匹配、条件组合、排序、关联和统计由主 Agent 在原生 `run_code` 中处理，分页取数只输出必要汇总；
  不逐件读取已能按页取得的字段。既有 `q` 仅匹配标题、卖家 SKU 和远端编号，不作为链接或属性搜索。
  `OnlineProductStore.latest_sync_summary` 仅读取最新同步的固定状态与进度；AI 详情和写入仍复用原有领域服务。
- `runtime_units/online_mercadolibre.py`、`online_yandex.py`、`online_ozon.py` 负责平台发现、读取和最小变更。
  复用现有授权与 HTTP 客户端；Mercado mapping 身份校验抽至 `marketplaces/mercadolibre_mapping.py`。
  `runtime_units/online_yandex_read.py` 负责完整目录分页、隐藏清单分页和每批 100 个 SKU 的详情读取，最多 3 个接口并发；
  `online_yandex_snapshot.py` 只做响应投影。全店同步不再循环调用单件 `read`；单件完整读取只用于修改前校验。
  Yandex 属性名称通过统一 `CategoryCatalog.attribute_definitions` 按类目关联，适配器一次运行内共用名称查询；
  名称查询失败保留属性值及平台反馈，并公开 `content.attribute_names_error`，不让整件详情同步失败。
  `online_yandex_snapshot.py::card_issues` 将卡片 `errors` / `warnings` 投影为具名 `PlatformIssue`，保留平台原文及补充说明；
  完整同步、单件状态刷新及内容回读共用此投影。缺少目标卡片响应时状态查询失败，避免误清除旧反馈。
  `runtime_units/online_change_confirmation.py` 按修改字段和范围回读，不重复下载完整聚合。
  `runtime_units/online_product_status.py` 是三个平台的单件状态读取入口，限定目标身份，不扫描全店，不调用独立价格或库存接口。
  `online_mercadolibre_read.py` 先完整扫描目录，再用最多 3 件的并发窗口读取父商品及关联站点，保留 User Products mapping 校验；
  `online_ozon_read.py` 先扫描 ALL 与 ARCHIVED 目录，再每批最多 100 件读取详情与价格，`online_ozon_snapshot.py` 只做投影。
  三个平台统一产出同步批次，`marketplaces/online_sync.py` 仅提供目录占位和授权/限流中断规则，旧通用串行读取器已移除。
- `schemas/online_products.py` 定义平台刊登、市场、具名价格/库存范围、平台错误/警告及变更契约；
  `platform_issues` 与 ERP 同步错误 `errors` 分开，旧快照缺字段时默认空列表；平台反馈不改变销售状态。
  属性展示名称、名称读取提示及平台反馈不参与业务内容版本；编号、值和单位仍参与并发校验。
  `stores/online_product_store.py` 独占 `online_listings` / `online_jobs`。远端商品不要求有本地商品或草稿，
  不伪造 publication，不修改 `ProductStore` 的归属。
- `marketplaces/online_buyer_links.py` 纯函数提取平台买家链接，规范化为 `BuyerLink`，随列表/详情返回。
  Yandex 使用 B2C `showcaseUrls`，Mercado 使用站点子刊登 `permalink`；不猜测缺失地址。
  `scripts/backfill_online_buyer_links.py` 补齐当前账号存量链接，只更新导航元数据，保留业务版本及同步时间。
- 任务以提交键防重，同目标互斥；全店同步与该账号修改互斥。写前核对当前店铺及平台业务版本，
  写前日志区分已发送与未发送；未知结果只回读、不自动重放。每次领取生成独立租约，过期执行者不可提交结果。
  工作线程在应用启动时恢复队列，前端轮询仅负责展示；部分同步失败保留旧快照，不推断删除。
  在线修改提交后仅接受人工确认，连续查询间隔 30 秒；失败也保留冷却时间。历史自动确认标记不再参与任务领取。
  局部确认更新版本与对应字段，保留完整同步时间。
- `runtime_units/online_product_capabilities.py` 将现有读取、单件状态刷新、统一修改、同步、回读、重试接口直接装配进主 Agent。
  `schemas/online_product_capabilities.py` 只声明现有接口形状；公共 `OnlineProduct` 与 `OnlineChange` 契约从持久快照/HTTP 请求中复用。
  商品数量、SKU 分析等由读取返回值与已有 Code Mode 组合完成，不新增场景工具、查询 DSL 或平台写入旁路。
- 审批和持久调用直接使用现有 Tool Bridge 的 Pydantic Deferred 机制；修改和重试返回 `OnlineSubmissionResult`，任务入队即完成 AI 工具调用，批量提交继续处理剩余目标，不等待远端终态。
  平台写入与回读由后台独立处理，在操作记录查看；`online_product_job_reader.py` 继续为同步及已持久化引用读取领域回执。
  `confirmed` 才确认成功；部分完成、未知结果和等待人工确认保留原状态并结束本次工具等待，不自动重放。
  改库存、调价、内容与停售共用一个 `online_products_change`，所需权限、审批和提交身份由原有 Runtime 处理。
- 前端复用工作台的 `/online-products` 导航及 `OnlineProductsPanel.vue` / `OnlineContentEditor.vue`。
  在线图片编辑使用内嵌源草稿多选区，图集第一张为主图；排序和移除按钮提供禁用原因及操作提示。
  `OnlinePicturePreview.vue` 提供失败占位与重试；进入预览时保留编辑组件，返回继续编辑。
  `OnlineBuyerLinks.vue` 在列表和详情提供单链接直达、多站点选择及缺失提示；点击不调用后端或 AI。
  `OnlineProductDetails.vue` 在详情直接展示属性名称、编号、值和平台错误/警告，保留换行及原文；
  `onlineAttributeDisplay.ts` 统一详情及编辑表单的展示。列表显示反馈数量，需关注统计包含平台反馈，
  Yandex 卡片处理中、修改未被接受与销售状态分别展示；刷新状态会更新反馈，属性名称由完整同步补齐。
  页面背景传递当前平台、最近点击或键盘聚焦的 `group_id` / `listing_id`，区分组合与单件；转入聊天保留定位，切换平台、筛选、页码和操作记录或目标消失时清理。无焦点不能推断首项，不混用本地商品或草稿 ID，也不作为授权。
  旧本地 publication 列表、旧独立暂停 HTTP/AI 工具及其前端已删除；持久化 publication 的读取迁移保留。
- AI 接入边界、平台限制及验收方式见 [在线商品 AI](online-product-ai.md)。

## AI Provider 与 AI Work

### 当前统一边界

最终目标不是只统一 Agent loop，而是统一全部 `connection_type=api` 的 AI 推理请求：需要工具
循环的用例使用 Pydantic Agent，不需要 Agent 的普通 chat/JSON/stream/typed output 使用
Pydantic Direct Model Requests，图片等能力使用锁定版本提供的 Pydantic capability/native tool。两类调用都
必须复用 `erp_web/services/ai_model_factory.py` 创建的 Model/Provider。

阶段 6 已完成：普通 chat/JSON/stream 统一由 Pydantic Direct Model Requests 发送；图片请求
也先进入 Pydantic Direct Model，再由集中 Factory 创建的 focused Images Model 执行。CLI 与
浏览器 AI 不属于 API Provider，继续使用独立适配器。

- `erp_web/services/ai_provider_contracts.py`：CLI/Browser 等独立连接的最小产品能力协议；不拥有
  API 厂商 wire protocol。
- `erp_web/services/ai_gateway.py`：稳定 AI gateway 门面，不包含协议解析或 SDK client。
- `erp_web/services/ai_gateway_providers.py`：业务调用编排和 `AiProviderClient`；API 分支直接进入
  `ai_direct_request_service`，非 API 注册表只包含 CLI/Browser。
- `erp_web/services/ai_provider_catalog.py`：产品已正式接入的 Provider Catalog、旧配置迁移和
  Pydantic Provider 公共构造器的唯一 owner。前端只消费 Catalog，不扫描 Pydantic 包，也不
  根据 Base URL 或模型名猜测厂商。
- `erp_web/services/ai_direct_request_service.py`：普通 API chat/JSON/stream/typed output/image 的唯一
  Pydantic Direct Model 执行入口，负责公开 Pydantic message/event 转换和项目结果归一化；类型化输出
  使用 `OutputObjectDefinition` 的 prompted output，不在业务 Prompt 重复字段清单。用户前台请求携带
  presentation scope 时，由该边界把 Direct Model 原生 `Part*Event` 交给统一 observer，并用
  `PydanticMessageStore` 保存预留 conversation 的官方 `ModelMessage[]`；无 presentation 时不改变 Provider 契约。
- `erp_web/services/ai_structured_output.py`：非 Agent 类型化输出的 dependency-light Schema 适配边界；
  从同一个 Pydantic 类型生成 JSON Schema 并验证返回值。CLI/Browser 尚无 Pydantic `Model` 适配器，
  因而仅在这两个非 API 边界附加自动生成的 Schema；适配器就绪后删除该提示式分支。
- `erp_web/services/ai_gateway_probe.py`：API/CLI/Browser 共用的能力探测编排、四态结果
  （`supported` / `unsupported` / `unavailable` / `inconclusive`）、确定性探测素材、连接指纹与
  versioned capability profile owner；探测结果归一化进 capability profile；
  未接入能力不得静默跳过，临时网络错误不得记为不支持。
- `erp_web/services/ai_model_probe_service.py`：API 模型能力探测 adapter；chat、JSON、联网、
  Function Call、图片生成和图片编辑全部使用独立 probe binding。探测不读取待测 capability
  声明，Function Call 必须完成 tool call → tool result → final response 的完整往返。
- `erp_web/services/ai_model_config.py`：保存带 `configuration_fingerprint` 的 v2 能力证明；Provider、
  Base URL、模型名、API style、模型 Thinking 开关、transport 配置或受控 `extra` 改变后，规范化阶段会移除失效证明及
  对应 capability，旧版无指纹配置仍可读取并通过重新探测升级。
- `erp_web/services/ai_model_discovery.py`：与推理解耦的远端模型目录发现；按 Catalog 的可选发现
  策略复用 Pydantic Provider 持有的 client，目录不可用不改变推理能力判定。
- `erp_web/services/ai_model_errors.py`：Provider/Pydantic Model 错误的最薄脱敏透传边界；保留
  HTTP 状态、Provider code/message/request ID，不得改写为其他业务含义。
- `erp_web/services/ai_gateway_cli_provider.py`：CLI Provider 实现。
- `erp_web/services/ai_gateway_browser_provider.py`：浏览器 Provider 实现。
- `erp_web/services/ai_gateway_provider_types.py`：Provider 共享请求 shape。
- `erp_web/services/ai_generation_settings.py`：模型 Thinking 默认值、功能绑定统一生成配置的
  归一化、能力描述与 Pydantic `ModelSettings`/受控 `extra_body` 映射；功能绑定覆盖模型默认，
  模型默认覆盖高级请求 JSON。oMLX 通过显式 Catalog 项映射 `chat_template_kwargs.enable_thinking`，
  能力探测和正式请求共享映射；业务层不得直接拼接 `reasoning_effort`、`enable_thinking` 等厂商字段。
- `erp_web/services/ai_model_factory.py`：Pydantic AI Model/Provider 的唯一创建入口；正式业务
  使用 `create_pydantic_model_binding` 并校验已启用能力，能力发现使用
  `create_pydantic_probe_binding`，只根据待测操作选择 Chat/Responses/Images Model，不允许用
  尚未产生的 capability 声明阻断探测。两条入口共享私有构造器和同一套 API style、认证、
  timeout、模型类型与密钥脱敏规则。显式 Thinking 设置若会被原生模型 profile 忽略，
  Factory 在请求前报配置错误，防止界面选择关闭后仍发送默认请求。
- `erp_web/services/ai_agent_factory.py`：唯一 Pydantic Agent 装配与运行入口。`ai_model_errors.py` 统一保留嵌套 Direct Model 错误的 HTTP 状态、可重试性和安全原因，供 Agent 与业务工具边界使用。
  原生 `UsageLimits` 限制模型请求和工具调用；`PrepareTools` 在预算耗尽后隐藏业务工具，
  不预留可执行的额外额度。`Hooks.before_node_run` 在 `UserPromptNode` 和
  `ModelRequestNode` 开始前更新页面背景，通过 `RunContext.enqueue` 接收用户更新；
  消息注入和事件编码由原生队列负责，不按用户文本生成或重置工具权限。
  主 Agent 使用 `str | DeferredToolRequests`，focused Agent 保留其类型化输出与独立领域能力。
- `erp_web/services/copy_service.py`：`copy.generate` 通过同一工厂运行独立文案 Agent。
  使用原生 `PromptedOutput` 校验输出结构，生成后不再额外调用 AI 复核语言、事实或营销措辞。
  字段类型、必填内容及平台标题长度校验通过后直接交给领域层保存。格式错误的原生重试
  最多两次，整个生成过程最多三次模型请求，并受总计 240 秒的 deadline 约束。
  事实摘要保留重量的 kg 单位，不复制主 Agent 的全部消息。已核对安装的 Pydantic AI 2.43.0
  与[原生结构化输出文档](https://ai.pydantic.dev/output/#prompted-output)：直接复用原生格式
  校验及重试，不增加业务 Agent loop。文案生成使用集中 Agent 工厂支持的 API 模型连接。
- `erp_web/services/ai_model_context_projection.py`：原生模型请求 Hook 的纯输入投影；旧大工具结果只在发送副本中形成摘要，完整原生 run 历史与持久化不裁剪。保留工具配对、本轮结果及 Provider 需要的思考信息，不维护第二套历史或恢复协议。
- `erp_web/services/ai_tool_bridge.py`：`Tool.from_schema` 的输入校验通过原生
  `args_validator` 在执行前复用现有 JSON Schema 与可选的纯领域参数校验器；参数错误抛出 `ModelRetry`
  让模型纠正，授权、执行及输出错误仍由 Runtime 处理，不重试已产生副作用的操作。
- `erp_web/services/ai_agent_budget.py`：通用的本地额度诊断与动态指令。保留资源类型、限制值、
  实际用量和拒绝阶段；本地额度异常不冒充 Provider 限流或领域无匹配。不修改原生消息、
  不实现第二套 Agent loop；重试次数与逐工具请求/返回的闭合仍由 Pydantic AI 管理。
- `erp_web/services/ai_agent_instrumentation.py`：独立 OpenTelemetry 技术 trace owner；
  关闭 prompt/tool 内容采集并在 JSONL exporter 再次脱敏。观测写失败不影响业务结果。
- `erp_web/services/ai_pydantic_image_model.py`：登记过的 focused 例外，见下文；它是仅支持
  Images API 的 Pydantic `Model`，只能由 `ai_model_factory` 创建，并且只能经 Pydantic Direct
  Request 调用。
- `front/src/views/AiWorkView.vue`：AI Work 页面。左侧 conversation 列表按 `updated_at` 倒序；
  右侧按优先级选择数据源——前台 presentation（observe Chat 实时消息）、活动 `global.chat`
  （共享 `Chat.messages`）或服务端 `/ui-messages` 只读派生历史；“原始消息”辅助标签提供
  规范 Pydantic JSON 树、Raw JSON 与下载；支持 `conversation_id` / `presentation_id` query 定位。
  全局对话与草稿箱批量准备共享 `front/src/stores/aiChat.ts`。原生工具卡提交批准/拒绝；
  运行期间仍可提交用户消息，收件箱显示接收状态。后台只发送历史版本变更通知，
  前端用官方 Adapter 派生的历史恢复消息，不在浏览器推进 Agent。
  前台短句 `initialUserMessage` 经 reserve 的 `initial_user_message` 传入 presentation scope，
  由 `AiAgentFactory` 保存到原生输入 metadata 的 `presentation_user_message`；只用于展示，
  不替换模型输入、不作为授权证据。`/ui-messages` 在官方转换后逐条恢复短句（空标记隐藏输入）；
  未标记的用户消息原样保留，不按 conversation ID 过滤。原始消息保留完整输入，
  历史展示不依赖浏览器缓存或仍然存活的 presentation registry。

```text
API use case
  → centralized Pydantic Model Factory
      ├─ Agent use case → AiAgentFactory → Pydantic Agent
      ├─ plain chat/json/stream/typed output → Pydantic Direct Model Requests
      └─ image/model capability → Pydantic capability 或登记过的 focused 例外

CLI / Browser use case
  → focused CLI / Browser Adapter
```

不得为了让普通调用复用 Pydantic 而把它们包装成虚假 Agent；也不得因为普通调用不需要 Agent，
就继续维护 Pydantic 之外的通用 API Provider 请求栈。

### Focused 例外登记：专用 Images API

- 引入时的能力缺口：当时核对的 `pydantic-ai-slim[openai]==2.43.0` 支持 Responses 原生图片工具，但没有
  能绑定 `gpt-image-*` 专用模型并表达 `images.generate` / `images.edit` 的公开 Model。
- 限定范围：只有 `erp_web/services/ai_pydantic_image_model.py::OpenAIImagesModel`；不支持
  chat、JSON、function tool 或供应商级实时增量，不能加入非 API Provider 注册表。
- 强制路径：`ai_model_factory` 创建 → `ai_direct_request_service` 调用
  `pydantic_ai.direct.model_request` / `model_request_stream` → focused Model 使用同一
  Pydantic Provider client。Images API 始终执行非流式供应商请求；需要 AI Work 展示时，
  `request_stream()` 使用 Pydantic `CompletedStreamedResponse(replay_events=True)` 把完成响应
  转成官方一次性事件流，不自定义第二套事件协议。
- 行为约束：图片编辑失败直接报错，不允许 edit→generate fallback。
- 移除条件：锁定的 Pydantic AI 版本提供覆盖专用 Images generate/edit 的公开 Model/capability
  后，以原生实现直接替换并删除 focused Model。
- 守卫：`tests/test_ai_context_architecture.py` 禁止旧 HTTP/Image Provider、原始 `urllib`
  推理和 Direct Model 第二 owner。

统一生成配置、覆盖顺序与当前 Provider 映射见
`docs/ai-provider-generation-settings.md`。

## AI Tool Task 执行层

- `erp_web/schemas/ai_tools.py`：ERP `AiToolDefinition`、轻量内部
  `AiToolCommand`、`AiToolResult` 和 JSON schema 边界校验；不承担 Provider wire protocol。
- `erp_web/schemas/ai_trace.py`：执行 ID、deadline、权限和预算上下文。
- `erp_web/services/ai_tool_declaration.py`：dependency-light `@ai_tool`、`Injected` 与
  不可变契约元数据；装饰时不注册、不读取配置，也不执行领域逻辑。
- `erp_web/services/ai_tool_compiler.py`：受限同步函数签名、Pydantic `TypeAdapter`、本地
  `$defs/$ref` 展开、Schema 支持子集和机械 executor adapter 的唯一编译 owner。
  保留 Pydantic 原生 `anyOf` 分支，包含可空枚举与常量；不得把非空分支约束展平到 `null`。
- `erp_web/services/ai_tool_catalog.py`：调用方显式函数清单、场景 allowlist、Execution Profile
  权限与独立可信 Binding Scope 的唯一 Catalog 抽象；不扫描包或依赖 import side effect。
- `erp_web/services/ai_tool_registry.py`：不可变 run-scoped ToolSet 与
  definition/executor 映射；旧 `AiToolRegistry` 容器已删除。同步 executor 必须通过
  `deadline_aware_tool_executor` 显式声明 cooperative deadline 契约，并把
  `AiExecutionContext.bounded_timeout_seconds()` 用于每个阻塞 I/O；Runtime 不用
  无法安全中止的后台线程伪装 hard cancellation。
- `erp_web/services/ai_tool_runtime.py`：工具查找、校验、权限、去重、预算、执行和
  最小业务审计；写工具在 executor 前检查可信幂等键，恢复写工具在真正调用 executor 前先
  持久化执行检查点。
- `erp_web/services/ai_agent_dependencies.py`：请求级 Agent dependencies，绑定唯一
  execution context、recorder、Tool Runtime、tenant、business scope、审批、幂等和
  use-case state。
- `erp_web/services/ai_tool_bridge.py`：把显式 ERP ToolSet 转换为 Pydantic
  `FunctionToolset`；Pydantic tool 只调用 `AiToolRuntime.execute(...)`，不直接调用
  领域 executor；原生 Pydantic 调度独立同步工具的并发，Runtime 仅按 call ID 去重。
- `erp_web/services/ai_agent_factory.py`：由 Pydantic Agent 独占 model → tool → model
  循环、类型化 output、重试和 usage limit；`run_sync(...)` 与 `open_stream_run(...)`
  是统一同步/流式入口，保留完整 canonical history，仅追加原生 `new_messages()`；不能把模型上下文裁剪结果写回历史。
  审批与外部工具暂停由原生 Deferred 请求和结果表达。
- `erp_web/stores/pydantic_message_store.py`：Pydantic 官方 `ModelMessage` 历史的唯一持久化
  边界；`messages_json` 用 `ModelMessagesTypeAdapter` 校验与序列化，是消息的唯一事实来源。

依赖方向：

```text
facade / focused Agent service
  → AiAgentFactory
      → Pydantic Agent → centralized Model Factory
      → Pydantic Tool Bridge
          → AiToolRuntime
              → run-scoped AiToolSet
                  ← explicit AiToolCatalog + scene allowlist + Binding Scope
                      ← @ai_tool + AiToolCompiler ← typed domain capability
```

`AiToolRuntime` 不得 import 类目、平台、发布或其他具体领域模块。主 Agent 的用例编排位于上层服务，不进入通用 Tool Runtime。
真实领域 ToolSet 在所属 runtime unit 中显式构造，不注册到动态全局表。

旧自定义 runner、JSON Tool Protocol 和 Agent tool-turn provider adapter 已物理删除。当前
不存在 feature flag、shadow run、fallback、旧 API HTTP/SDK 请求栈或第二条 Agent 生产路径。

## 单主 Agent（global.chat）与领域能力

主 Agent 直接调用 focused ERP 工具，读取结果后继续决定下一步。草稿箱的“AI 准备所选”
只把目标 `draft_id` 和用户目标提交给同一入口。不存在固定步骤计划或第二个执行 Agent。

```text
POST /api/v1/ai-chat/runs（Vercel SubmitMessage，可带 target_draft_ids）
  → ai_chat_facade → VercelAiUiService → GlobalAgentChatService
    → AiAgentFactory → Pydantic Agent → 原生 Tool → AiToolRuntime → 领域 Capability
      ├─ 普通结果/缺字段 → 原生模型继续决策
      ├─ ApprovalRequired → 原生 HandleDeferredToolCalls（完全授权）或工具卡（询问审批）→ DeferredToolResults
      │   └─ product_publish_request → PublishingBus 接收 → 返回提交回执 → AI 汇报已提交并结束发布操作
      └─ CallDeferred → 原生请求和历史同事务落盘
          → AgentJobService 领取领域 Job → 真实终态 → DeferredToolResults → 同一入口恢复
```

当前 owner：

- `erp_web/ai_capability_composition.py`：显式 Catalog 与场景权限；全局工具包含领域读写工具。
- `erp_web/facades/agent_capability_facade.py`：应用能力 Scope、所选草稿范围、可信消息来源、Job Reader 装配。
- `erp_web/services/global_agent_chat_service.py`：主对话服务；主模型依据完整对话选择实际提供的工具，并按原生执行回执回答先前操作结果。`GLOBAL_RESPONSE_INSTRUCTIONS` 统一约束所有全局回答，装配在业务提示词和长期记忆之后：结果优先，只保留必要依据与限制；批量汇报覆盖及异常范围，合并同类失败。取数先在当前目标所属业务域完成，只有目标需要或必要事实有可信跨域关联时才扩大范围；搜索未命中不能当作其他字段无数据。额度不足时仍围绕当前问题回答，不强制纯查询或解释汇报写入。不增加回答压缩模型或工具授权推断层。
- `erp_web/runtime_units/collect_helpers.py::claim_products_to_markets`：AI 认领和商品库市场选择共享按语言分组逻辑；全部市场从真实店铺绑定及平台注册表解析。
- `erp_web/services/ai_tool_bridge.py`：机械参数校验、原生并发、审批快照与 Deferred 转接；不选择下一步。
- `erp_web/services/tool_approval.py`：业务审批内容 digest，绑定工具名/版本、operation key、原生 call ID 和审批版本；执行前重核。
- `erp_web/schemas/ai_approval.py`、`erp_web/services/ai_approval_policy.py`：工具审批偏好 `ask/full` 与原生 `HandleDeferredToolCalls` 接法。完全授权只产生带原快照的 `DeferredToolResults.approvals`，不修改工具权限、不跳过执行校验，也不接管 Agent 循环。
- `erp_web/facades/ai_approval_facade.py`：`POST /api/ai/approval-mode` 的受信 UI 入口，要求 `X-Approval-Token`；模式存入 `app_config.ai_tool_approval_mode`，只读值随 `/api/state` 返回。普通设置保存保留最新授权偏好，模型没有修改模式的工具。
- `front/src/components/ai-work/AiApprovalModeSelect.vue`：主对话与浮动对话共用的“询问审批 / 完全授权”选择器，全局保存。完全授权覆盖后续调用和未决定的审批，既有批准/拒绝不改写；普通未返回工具只显示“等待工具结果”，审批标签以服务端 `pending_tool_calls` 为准。
- `erp_web/services/agent_run_storage.py`：原生 hook 到消息 CAS、输入收件箱与副作用检查点的适配。
- `config/agents.md`、`erp_web/services/agent_memory.py`：ERP 主 Agent 的长期业务记忆与唯一文件读取边界。`GlobalAgentChatService.instructions()` 在每次新运行或 Deferred 恢复时加载当前应用目录下的文件，沿现有 Factory 的原生 `instructions` 注入系统上下文；不增加 Agent loop 或消息协议。它不受页面背景眼睛开关影响，不加载仓库开发用 `AGENTS.md`，不回退到其他实例的记忆。文件为 UTF-8、上限 32 KiB；缺失或空文件表示没有附加记忆，读取失败、非法编码和超限明确报错，不静默截断规则。文件编辑在下次 run 生效；当前仅提供文件读取，未提供 Agent 自主写入记忆工具。属性填写现由主对话承担，确定性校验仍归领域工具。
- `erp_web/schemas/ai_page_context.py`：主对话页面背景的有界契约与中文指令渲染，只接受页面枚举和资源 ID。`vercel_ai_ui_service.py` 校验请求的 `page_context` 后写入原生消息 metadata，缺省表示本条消息不携带背景；客户端消息 metadata 仍被丢弃。`AgentRunStorage` 随用户输入更新背景，Factory 使用 Pydantic AI 原生动态 `instructions` 注入模型系统上下文，不创建独立系统消息历史或新 Agent loop；Deferred 恢复复用已保存快照，后续关闭开关会清除当前背景。
- `front/src/stores/aiPageContext.ts`、`useAiPageContext.ts`：可见页面及编辑区域提供定位信息，输入框眼睛开关默认开启并在本机记住选择；普通发送和运行中追加消息均在发送瞬间复制背景。关闭编辑器、切换页面或 KeepAlive 停用时撤销该区域背景，页面背景不作为写权限，也不携带表单未保存值。
- `erp_web/stores/agent_call_store.py`：原生 Deferred 请求/结果序列化、收件箱和领域执行回执；不存步骤计划、Agent 状态或事件副本。
- `erp_web/runtime_units/publish_capabilities.py::product_publish_request`：原生审批通过后同步提交 PublishingBus，返回 `ProductPublishRequestResult` 即结束 AI 的发布操作；不使用 `CallDeferred`、`JobReferenceResult` 或发布 Job Reader 等待平台终态。平台发布及结果确认继续由发布领域负责，用户可在发布任务界面查看。
- `erp_web/services/agent_job_service.py`：固定大小线程池领取和对账需要结果的领域 Job；scanner 不执行模型。选品研究、在线商品等后台工具通过 `DeferredToolResults` 返回结果后恢复原生 Agent。
- `erp_web/runtime_units/domain_job_readers.py`：选品研究 Job 的只读终态和有界活动证据。
- `erp_web/schemas/domain_jobs.py`：Job Reader 的有界生命周期/活动证据，供模型了解真实终态。
- `erp_web/stores/product_mutation.py`：商品聚合的短期互斥；`ProductStore` 的局部读改写保护、草稿旧快照冲突检测。锁不跨模型调用或人工等待。
- `erp_web/runtime_units/conversation_fact_capabilities.py`：按草稿、会话归属查询真实用户消息；引用不能由模型自报可信标志替代。

原生 Deferred 要求当前批次全部调用结果/审批齐备才恢复模型。部分结果先落盘，独立领域 Job 可并发。
发布仅使用原生审批和普通工具返回：已核对当前安装的 Pydantic AI 2.44.0 及[官方审批文档](https://ai.pydantic.dev/deferred-tools/)，原生能力满足需求，无需自建完成协议或等待状态机。提交回执表示队列接收成功，不表示平台发布成功；模型不得主动轮询、等待或因后台失败重新发布，用户另行请求查看或处理时才继续。
新增用户消息始终可接收，在下一个原生模型边界生效。取消不会撤销已经发出的平台操作；未开始的调用先返回用户要求已更新，等待模型重新决定。

缺资料是工具业务结果，包含字段、原因、选项和工具参数路径；主 Agent 可以先读取关联商品、草稿、平台和有权限历史。
商品共用事实与各草稿/站点决定分开保存。`source_message_id`/`source_conversation_id` 只定位实际消息，服务端检查归属、实体范围与值；已保存销售目标可以复用。

`DraftQuerySnapshot.total` 覆盖完整匹配集合，`draft_ids/items` 只保存有界页。主 Agent 使用返回的稳定 `draft_id` 调用写工具。
目标平台/站点不明确时返回 `DRAFT_TARGET_AMBIGUOUS` 或 `DRAFT_TARGET_SITE_AMBIGUOUS`，不能静默选首项。

### Endpoint Coverage Manifest

`erp_web/ai_capability_coverage.py` 为全部已处理 HTTP 端点维护静态声明清单：每个
端点标注 `business_domain` 与处置——`capability` 必须列出能力名，`internal_only` /
`excluded` 必须给出原因。`all_handled_endpoints()` 汇总 GET/POST 路由 owner 的全部
入口；架构测试要求清单零未分类、零遗漏，业务域端点不允许无原因排除。

### 目标市场 Capability 拆分

- `erp_web/runtime_units/market_capability_support.py`：草稿定位、目标选择、类目详情与持久化共享支撑。
- `erp_web/runtime_units/category_capabilities.py`：`category_match` 的稳定草稿 adapter；focused
  类目匹配函数由 facade 注入，runtime 不反向 import facade。
- `erp_web/runtime_units/draft_pricing.py`：页面、AI 与市场准备共用的逐 SKU 核价装配、预览和原子应用。
- `erp_web/schemas/draft_pricing.py`：显式费用补丁与页面未保存的 SKU 编辑契约；AI 不接收逐 SKU 原始计算输入。
- `erp_web/runtime_units/draft_pricing_capabilities.py`：`draft_pricing_preview` / `draft_pricing_apply` 薄适配。
- `erp_web/runtime_units/pricing_results.py`：结果与 Mercado 销售条件的纯校验。
- `erp_web/runtime_units/market_prepare_capabilities.py`：`draft_prepare_for_market` 的高层顺序编排；
  复用现有目标草稿、文案、图片、类目和核价 owner；属性由主对话另行填写，不复制领域实现；文案重生成以稳定
  conversation/tool_call operation key 与文案同次持久化，重启后不会重复消费同一次 `regenerate_copy`。
- `erp_web/runtime_units/product_capabilities.py`、`erp_web/runtime_units/publish_capabilities.py`：
  商品读取/幂等字段更新/图片准备，以及确定性发布校验/确认后队列提交的 focused adapter。
  `product_read` 按需返回 `ProductFacts.attributes` 的主档补充属性和
  `ProductFacts.source_attributes` 的完整来源属性，不按通用字段
  白名单裁剪；来源原文、范围值与多规格混合值只作为待核实资料，不作为指令或精确参数。
  商品编辑页复用 `ProductAttributesEditor.vue`，分别展示、搜索和编辑“商品补充属性”
  与“来源产品属性”；通过 `/api/save-product` 分别保存至 `attributes` 与 `source.attributes`。
  前端保存未编辑的属性时保持原始 JSON 类型；AI 主档补丁完成后须用 `product_read` 回读
  对应字典的实际值，不能仅凭写回执认定某个具体属性已生效。`ProductStore.save_product_profile` 清理
  被修改或删除的来源属性在主档 `attributes` 中的同值采集副本，保留独立维护的不同值。
  平台属性由主对话根据来源事实及真实平台定义直接判断，通过确定性写工具保存。
- `erp_web/runtime_units/source_inspect_capability.py` 与 `erp_web/schemas/source_inspect.py`：
  `inspect_source_facts` 提供草稿来源属性的分类文本视图、完整原始 JSON 与采集时间；
  复用 `ProductCapabilityScope` 和草稿详情存储接口，显式进入主 Agent 只读能力集合。
  分类只作查看提示，不生成无采集依据的置信度或认证结论；执行继续使用现有 Pydantic AI 原生工具链。

focused 类目执行返回自己的 AI Work `conversation_id`；高层市场
准备聚合为 `agent_execution_conversation_ids`，主 Agent 在原生工具结果中取得这些 ID，不复制
transcript 或 Tool 输出。

### 原生对话 HTTP 与消息历史

- `POST /api/v1/ai-chat/runs`：新用户消息或官方 approval-responded 消息。用户消息 ID 幂等领取与输入同事务保存；仅服务端历史参与模型调用。
- `POST /api/v1/ai-chat/cancel`：接收 `{id, message_id}`，直接取消本轮原生令牌；不排队发送取消 prompt。相同消息的停止幂等，已知旧消息不能取消新回合；取消先于发送到达时，现有 claim 幂等记录阻止迟到消息启动。
- 同会话有活动 run 或未齐备 Deferred 时返回 HTTP 202 接收凭据。批准/拒绝还需 `X-Approval-Token`，客户端不能更改服务端 call ID、工具名或参数。
- `GET /api/v1/ai-work/conversations` 与 `/{id}`：历史索引和 canonical Pydantic JSON。
- `GET /api/v1/ai-work/conversations/{id}/ui-messages`：官方 `VercelAIAdapter.dump_messages()` 输出，加有界待处理调用、输入接收状态与运行错误；`run_active`、`run_status`、`latest_message_id` 分别来自运行互斥表及原有 claim，供停止确认和目标关联。
- `GET /api/v1/ai-work/conversations/{id}/events`：只通知 `history_version` 变更，前端重新读取 `/ui-messages`；不保存、重编码或重放自定义 Agent 事件。
- `ai_chat_run_registry.py`：进程内同会话互斥和 Pydantic `CancellationToken` 关联；历史提交额外使用 SQLite CAS 防止旧写者覆盖。
- `ai_chat_turn_claim_store.py`：用户输入幂等身份/归属/安全错误码，不含消息正文。
- `agent_run_storage.py`：失败原因和安全 Provider 诊断随实际原生消息的 `metadata.ai_run_error`
  经现有 CAS 保存，按真实用户消息 ID 关联；不合成模型回复、另存消息或改数据库结构。
  `/ui-messages` 读取当前失败回合的同一原因，不用通用提示覆盖实时错误；旧历史只有计费错误码时
  显示已知计费原因，不补造 Provider 原文。HTTP 错误保留脱敏后的原始消息、代码、状态与 request ID。
  已核对 Pydantic AI 2.44.0 原生 `ModelMessage.metadata` 和
  [消息历史持久化](https://pydantic.dev/docs/ai/core-concepts/message-history/)；框架保存应用 metadata，
  UI Adapter 不负责恢复运行异常，因此仅在现有 UI 外层 `run_error` 投影展示诊断。
- `pydantic_message_store.py`：完整原生消息的读取、校验；`agent_call_store.py` 组合提交同一消息表及 Deferred，二者不各存一份历史。

实时流为 `AgentStreamEvent → VercelAIEventStream → SSE → @ai-sdk/vue Chat`。HTTP 断线不取消后台执行。
慢客户端超过有界队列后收到官方编码的 error/finish，再从已提交历史恢复。最终消息先持久化再下发终态。
未知副作用保留回执，重启不盲目重发；实际平台 Job 自己保留幂等和对账状态。

AI Work 选中普通对话后，直接使用本次 `/ui-messages` 响应绑定共享 Chat 并显示输入框；
无需额外点击继续，也不重复读取历史。打开对话只读取历史并订阅更新，发送消息才提交新的 run。
历史响应必须仍匹配当前选择，旧会话回调不得断开当前订阅；业务 Agent 执行记录仍只读展示。

### 对话停止与框架边界

取消机制引入时核对了 Pydantic AI 2.43.0、[官方取消文档](https://pydantic.dev/docs/ai/core-concepts/agent/#cancelling-a-run)及安装源码。
直接使用原生 `CancellationToken`、`RunCancelled.all_messages()` 与 Vercel `abort` 编码；
没有自研 Agent 取消状态机或事件编码。`services/ai_run_cancellation.py` 只通过 ContextVar
把同一个官方令牌传给主 Agent、同步工具中的嵌套 Agent 和该范围内领取的后台领域任务。
工具 I/O 的既有 `bounded_timeout_seconds()` 检查点同时检查取消，阻止后续批次；
已阻塞的同步 I/O 和已提交的平台操作无法由 Python 线程取消强制撤销，保留实际回执并继续对账。
Agent 取消后保留框架快照，未闭合工具历史在新用户回合由框架自动修复。

领域侧只撤下尚未执行的 queued 工具、未处理输入和原生 Deferred 恢复请求；
不会为停止再调用模型生成确认，也不会由后台扫描重新启动已取消操作。
新的明确用户输入可建立新的令牌；旧操作的工作线程仍持有旧令牌，不能被新输入重新激活。
前端立即调用 `Chat.stop()` 结束可见输出，同时独立请求后端取消；收到后端确认前显示“正在停止”。
后台 Deferred 等待期间也提供停止按钮。浏览器断线仍不等价于用户明确停止。
对话输入区连续按两次 Esc 复用同一停止入口：第一次把按钮显示为 `Esc`，第二次触发停止；
焦点变化、窗口失焦、切换会话、输入其他按键或开始组合输入时取消等待，长按重复事件不算第二次。
输入法组合期间不拦截 Enter/Tab 执行发送或命令，也不把候选框 Esc 当作停止；
按[浏览器 IME 事件说明](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event#keydown_events_with_ime)
同时检查组合事件状态、`isComposing` 和 `keyCode === 229`，覆盖 compositionend 先于提交按键的顺序。

### AI Presentation 通用可观测层

前台 AI 请求通过 `withAiForeground` 预留展示，HTTP 公共边界以 `X-AI-Presentation-ID`
关联原业务请求。`AiAgentFactory` / `ai_direct_request_service` 发布原生事件，
`ai_presentation_service` 使用官方 Vercel 编码；业务结果仍由原接口裁定。
`ai_presentation_registry` 只保存短期展示缓冲，完整历史归 `PydanticMessageStore`。
HTTP、单 root/lease、断连及前端接入契约统一见 [AI Work](aiworkpage.md)。

已登记的第三方网关适配：部分 Responses 网关在完整增量后发送
`response.completed.response.output=null`，Pydantic AI 2.44.0 会迭代该空值。
Direct 边界仅在异常确实来自此终态的 Pydantic OpenAI adapter、API style 为
`openai_responses` 且 `response_stream.get()` 已有有效 parts 时恢复为正常 EOF；
其他异常继续抛出。上游支持或全部已支持网关遵守官方 Schema 后删除此适配。

## 类目平台搜索与规则读取层

- `erp_web/marketplaces/category_provider.py`：定义绑定式 `CategorySearcher` 与
  `CategoryNavigator`，以及类目详情、属性定义和枚举分页的 `CategoryProvider` ABC；
  注册平台必须显式实现核心规则读取契约。
- `erp_web/schemas/category_definition.py`：内部 `CategoryDefinition`、有界公共属性/枚举
  分页 View 与稳定 fingerprint 的唯一 shape owner。内部定义不含平台原始 `raw` 或完整
  枚举全集，公共 View 也不暴露 `platform_binding`。
- Yandex“其他属性”（57046341）在 Provider 中声明 `name_value_lines` 文本格式；
  `schemas/category.py` 统一校验每行的“属性名:属性值”。公共摘要仅投影 `text_format` 和
  `format_hint`，供主 Agent 生成属性与前端多行编辑使用，不暴露内部 constraints。
  属性写工具、草稿发布预检和 payload 编译均执行同一规则；该选填字段只保留没有专用
  平台字段的来源事实，不重复材质等已映射属性，不把整组 SKU 的概括描述套给各规格。
- `erp_web/runtime_units/category_catalog.py`：业务消费者的统一类目读取入口；负责 Provider
  解析、定义 Loader 注入与有界公共投影。类目匹配、属性填充、预检、payload 编译、前端和
  Agent 工具不得绕过 Catalog/注入 Loader 直接读取平台规则。
- `erp_web/runtime_units/category_providers.py`：Mercado Libre、Ozon、Yandex 的显式
  `CategoryProvider` 实现与注册表；平台 API shape 在这里归一化为当前定义和明确
  `platform_binding`。Mercado Libre CBT 类目预测固定调用
  `/marketplace/domain_discovery/search`，并统一通过
  `store_credentials.get_mercadolibre_access_token()` 读取已保存的
  Global Selling Access Token；只有实际业务请求明确返回 401 后才执行一次共享刷新；Provider 不得直接读取凭据。区域站点仍调用
  `/sites/{site}/domain_discovery/search`。
- `erp_web/runtime_units/category_definition_cache.py`：统一属性定义持久缓存 owner；24 小时
  fresh、最多 7 天 transient stale，401/403、凭据缺失、禁用类目和结构错误不得用 stale
  掩盖。缓存不进入商品、草稿、任务或 Agent history。
- `erp_web/schemas/category_grouping.py`：刊登分组字段识别和值派生的唯一纯规则入口。
  Ozon 按类目字段名称识别，Yandex 使用分组属性 200；组合展示使用 `draft.grouping.name`
  或标题，独立刊登省略可选分组值，必填值按 SKU 卖家编码派生。公共属性页的
  `CategoryAttributeSummary.managed_by=listing_grouping` 表示由系统填写，保持平台的
  `required/read_only` 事实不变；该标记在公共投影时计算，不依赖旧缓存是否携带标记。
  普通属性和 SKU 属性 AI 填写都排除此字段；`category_model` 必填检查、SKU 发布投影与
  Ozon wire 构造使用同一规则。旧属性值不能改变分组设置，也不作为第二份可编辑来源。
  仅调整领域输入与确定性派生，不新增或修改 Agent 生命周期、模型循环或重试机制。
- `erp_web/runtime_units/category_searchers.py`：任务入口根据当前平台实例化具体
  检索对象。Mercado Libre 调用 `domain_discovery/search`；Ozon 同一绑定对象同时
  保留人工关键词搜索能力并为自动匹配实现树导航；Yandex 只在本地缓存类目树上做
  规范化关键词匹配（source `yandex_cache`），并把限流/认证/缺凭据错误统一分类。
  平台选择只发生在对象创建处。
- Mercado Libre OAuth 的 `code_verifier` 只在生成授权链接到兑换 code 期间存在；兑换
  成功后必须删除，授权检查清单在 Access Token 与 Refresh Token 已就绪时不再把它显示为
  缺失项。
- `erp_web/runtime_units/ozon_category_api.py`：Ozon 类目语料的刷新、人工搜索和树导航入口；
  24 小时内复用缓存，远端瞬时网络错误时最多使用 7 天旧语料，认证错误不允许
  stale fallback。完整树不会进入 AI 上下文。
- `erp_web/runtime_units/ozon_category_cache.py`：Ozon 展平类目语料的版本化压缩 JSON
  持久化 owner；使用原子替换写入，文件只含 Client ID 单向摘要，不保存凭据。
- `erp_web/runtime_units/yandex_category_api.py`：Yandex 类目树与类目参数语料的刷新
  与缓存 owner；类目树按语言与凭据作用域缓存（内存 + gzip 持久化 JSON），6 小时
  新鲜窗口用于避开 Yandex 每小时限额，远端瞬时错误最多使用 7 天旧语料；底层类目参数
  还有短 TTL 内存缓存，归一化后的 `CategoryDefinition` 统一进入上述持久定义缓存。
  缓存文件只含凭据作用域单向摘要，不保存凭据。
- `erp_web/schemas/category.py`：规范化候选、搜索结果、匹配结果 shape，以及
  Agent Service 与领域工具共同使用的请求级 `CategoryCandidateLedger`；
  `normalize_category_attribute_definition` 为带单位属性暴露 `unit_options/default_unit`。
- `tests/test_category_searchers.py`、`tests/test_ozon_category_api.py`：平台对象选择、
  远端/缓存搜索、错误分类和 Ozon ID 配对测试；含 Yandex 关键词搜索与
  HTTP 420 限流（可重试）/401 认证失败（终态）分类。

`erp_web/runtime_units/category_query_capabilities.py::category_search`（v3）为通用 AI 查询入口，
接收 `product_type` 与 `keywords` 列表，合并查询固定语言的通用名和补充词，复用
`category_keyword_search.py` 的并发与合并逻辑；`limit` 限制合并
结果总量，返回逐词错误与命中词，同时保留 Ozon 类目 ID 配对。
`erp_web/runtime_units/category_store.py::search_categories_live` 继续服务人工关键词搜索；
自动匹配按绑定对象能力选择 `CategoryNavigator` 或 `CategorySearcher`，两者不互相 fallback。

## 通用文本翻译

- `erp_web/http_route_units/translation_routes.py`：唯一公开入口 `POST /api/text-translate`；
  只接受 `target_language` 与扁平 `content` 键值对象。
- `erp_web/facades/translation_facade.py`：HTTP 状态映射，不包含类目或属性领域分支。
- `erp_web/runtime_units/text_translation.py`：唯一 `text.translate` AI 用例调用方；校验请求与
  模型响应拥有完全相同的 key 集合，并统一返回扁平 `{key: value}`。
- `config/prompts/text_translate.json`：唯一“翻译” Prompt。调用方负责注入目标语言和具体文本；
  类目候选与平台属性在前端各自独立组装内容并触发，不共享领域 payload。

已退役的类目结果翻译、类目属性翻译端点、用例、Prompt 和 runtime unit 不提供兼容路径。

## 类目匹配 Capability

- `POST /api/category-attrs` 返回平台类目属性定义；Ozon 字典字段保留
  `dictionary_id/is_dictionary/is_collection/max_value_count/category_dependent`，不把大字典内联到类目响应。
  带单位的平台属性（Yandex）通过共享的 `unit_options/default_unit` 暴露可选单位，
  属性值以 `{value, unit}` 提交；`dictionary_value_id` 按字符串传输与校验，
  不做数值化（Yandex 字典 ID 超出安全整数范围）。
- `POST /api/category-attribute-values` 是平台枚举值的唯一公开读取入口；
  `erp_web/runtime_units/category_store.py` 通过 `CategoryProvider.attribute_values` 分派，
  Ozon 由 `erp_web/runtime_units/ozon_category_api.py` 调用独立的
  `description-category/attribute/values` 接口分页，并把非空检索交给平台的
  `description-category/attribute/values/search` 大字典搜索接口；结果短时缓存。品牌空查询首屏
  会用当前类目的实时搜索结果置顶官方“无品牌”候选；`无品牌/其他/Generic/no brand` 等查询别名
  只转换为平台原文检索词，枚举 ID 不做跨类目硬编码。
- `erp_web/schemas/category_brand.py`：平台品牌身份及无品牌查询词；仅采用平台实际返回的候选。
- `erp_web/product_model/category_model.py`：类目选择、属性有效性与发布必填项判断。
- `erp_web/runtime_units/product_capabilities.py`：属性任务优先使用 `draft_attributes_read(scope=common)`，一次返回 `product` 商品/来源事实、`targets` 全部目标的类目及完整已填公共属性和 SKU 数量，不携带图片或 SKU 明细。指定 `scope=sku` 和明确平台/站点后，一次读取全部已选启用 SKU 的有效事实和差异属性；大量数据可用 `limit` / `next_offset` 分段。
- `category_attributes_query` 默认 `scope=common`，SKU 任务使用 `scope=sku`，检查全部定义可用 `scope=all`；`write_scope` 标明可写范围，排除字段通过 `excluded_attributes` 的精简记录说明。过滤沿用原始分页游标，空页仍按 `has_more` 继续；不丢弃必填或可选属性。`category_attribute_values_query` 查询真实候选，小字典优先空查询，独立目标/候选并行，同一目标集中写入。主对话负责事实判断、语义匹配、翻译和缺资料时询问用户；不设前 20 个可选属性的限制。
- `product_attributes_update` / `draft_sku_attributes_update` 分别保存公共属性和指定 SKU 的差异属性。`erp_web/runtime_units/category_attribute_updates.py` 只执行确定性校验：服务端重读类目定义，核对作用域、只读字段、值类型/数量/单位以及平台枚举 ID 与原文。网络校验后在商品锁内重读当前目标，再局部合并本次字段，拒绝变化后的类目、停用或未选 SKU。
- `erp_web/runtime_units/category_attribute_access.py` 是查询与写入共用的纯作用域规则：公共、SKU、托管及只读字段一致判定。托管字段在枚举查询前拒绝；Ozon 单字符枚举值用字典分页按 ID 和原文精确核对，不走至少两字符的搜索端点，不跳过枚举真实性校验。本地查询参数错误不可重试，网络故障保留可重试属性。
- 属性填写只有主对话这一条 AI 路径。页面入口、专用属性 Agent/复核模型、局部 HTTP 调用和复合草稿准备里的隐式属性步骤均已删除。`draft_prepare_for_market` 返回的完成步骤只包含目标、文案、图片、类目和定价；主对话随后按需直接填写属性。
- `erp_web/services/global_agent_chat_service.py` 与 `config/agents.md` 规定事实复用、公共/SKU 边界、无品牌优先及缺口汇报。继续使用已安装的 Pydantic AI 2.44.0 原生工具调用、消息历史和指令装配；此次不需要新增 Agent loop 或生命周期。
- `front/src/composables/useAiDraftSync.ts` 在原生对话历史提交、流结束及返回草稿工作台时读取最新业务数据，统一同步属性、包装、库存、核价参数、SKU 售价和保存版本。无未保存编辑时自动更新；有编辑时保留页面并提示重新加载，覆盖前需确认。重新加载是只读操作，不重放 AI 工具或提交表单；过期读取响应不能覆盖切换后的草稿。`DraftWorkspacePanel` 在工作台内持续显示错误与“重新加载最新数据”入口。眼睛开关继续控制发送时的页面背景。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`（ID 原样按字符串存取，不做数值化），搜索输入不进入草稿；
  实时候选按 `next_cursor/has_more` 追加并按 ID 去重，大品牌字典通过“加载更多”继续读取；
  带 `unit_options` 的属性通过数值输入 + 单位下拉生成 `{value, unit}`；
  发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories`（v4）。先在 `product_identity` 摘出原始规格中的实物结构，再填写固定语言的
  `product_type / alternative_names / keywords`，三组词在一次工具调用中合并查询。
  不额外启动规划 Agent；工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/schemas/category_search_language.py`：根据注册平台/站点确定唯一检索语言，独立于商品
  原文和草稿语言。Yandex/Ozon 俄语，MLB 葡语，其余美客多本地站西语，CBT 预测接口固定英语。
  CBT 覆盖定义在 `marketplace_registry.py`，不改变刊登语言。执行前整批拒绝中文或错误文字体系，
  被拒的批次不调用搜索器；拉丁字母短词的英/西/葡语语义仍需要模型遵循契约，不能将文字体系
  检查宣称为完整语言识别。
- `erp_web/runtime_units/category_keyword_search.py`：关键词批量 I/O owner；每个匹配任务内
  去重并复用成功查询，最多 3 个并发查询，沿用入口绑定的 provider 超时和任务 deadline。
  每词最多 8 个候选，交错合并后最多返回 24 个新增候选，保留 `matched_keywords`、逐词错误和
  `truncated`。后续查询不重复展开已见类目的全文，改用 `repeated_candidate_ids` 引用历史；
  已见类目仍可最终选择，新增候选优先使用返回名额。`truncated` 与 `remaining_candidate_count`
  只表示缓存中尚有未展示的新候选；`query_candidate_counts` 是每词排序返回量，不是全库覆盖率。
  仅将实际返回模型的候选登记到账本；缩小关键词组可从缓存取回被裁剪的候选。
  64 个词仅作为异常入参保护，不设置总关键词配额；工具调用次数仍由 Pydantic AI 原生预算管理。
- `erp_web/facades/category_match_facade.py`：`category_match` 共享业务阶段；
  首轮发送裁剪后的双语商品事实；绑定导航器时发送真实顶层节点并允许最多四次树导航，
  绑定搜索器时使用关键词列表批量发现，所有平台复用相同批量契约。最终选择必须经过叶子候选账本、站点、可发布状态、
  详情和 Ozon ID 配对校验；匹配阶段不读取属性定义，属性由用户选择类目后的独立加载步骤读取，
  避免属性接口超时使已完成的匹配失败。达到资源上限时返回 failed 并保留通用额度错误和 details。
  仅模型主动、有效地 abstain 返回 unresolved；检索未确认不等于平台没有类目，不静默改选。
  `prepare_category_match_input / setup_category_match_search / finalize_category_match`
  被 主 Agent 领域工具 与同步 focused HTTP 入口共用，行为一致。
- `erp_web/services/category_match_agent_service.py`：`category.product_match` 的 focused
  Execution Profile、prompt 渲染、类型化 `CategoryMatchAgentOutput` 与 Ledger output
  validator；关键词模式首轮一次规划基于实物的主要相关方向，批量检索后优先提交结果；
  仅有具体缺口才补查，不按最低关键词数量阻止 abstain，也不额外启动规划 Agent。
  最多八次实际工具调用，模型请求上限为十二次，给越界纠正及最终输出的原生校验重试留出余量；
  总 deadline 为 150 秒。保留针对具体缺口的补查能力。输出 `category_match.v2` 携带完整路径、
  实物类型关系与中文结构对照；路径必须来自候选账本，明确结构冲突或不确定时不得选择。
  原始标题/规格优先于翻译和营销扩写；同一用途或材质不能把相邻叶子变成上位类目。
  这些约束不能证明模型的语义判断始终正确，须用真实模型评测而非旧 AI 选择作为正确答案。
  输出校验、重试、调用预算和生命周期均直接使用 Pydantic AI 2.43.0 原生能力；
  已核对官方文档：https://pydantic.dev/docs/ai/core-concepts/output/ 与
  https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/ 。
  只通过统一 `AiAgentFactory` 的流式 `open_stream_run` 运行（同步执行路径已删除）。
- `erp_web/http_route_units/category_routes.py::handle_category_match`：
  `POST /api/v1/category-match` 同步 focused 入口。类型化业务结果由本接口独占，始终
  返回 200 与类型化 `CategoryMatchResult`（`ok=false` 属于业务判断型结果；subject
  错误仍映射其 4xx）；实时展示关联由 HTTP 公共边界的 `X-AI-Presentation-ID` claim
  完成，route 不读取 presentation header，不导入 registry/SSE。
  `category_facade.py::load_category_match_subject` 只做草稿上下文加载；其完整路径为
  `erp_web/facades/category_facade.py`。
- `config/prompts/category_product_match.json`：`category.product_match`
  Execution Profile 的可配置 prompt。
- `front/src/api/workflow/publishing.ts::matchCategory`：经通用 `withAiForeground`
  wrapper 调用同步 `POST /api/v1/category-match`；presentation ID 通过 axios config
  `aiPresentationId` 注入并由拦截器转换为 `X-AI-Presentation-ID` header，不进入 JSON
  body；显式 timeout 为 180 秒，覆盖后端 150 秒 deadline 并留余量；类型化结果适配到现有人工候选 shape。
- `front/src/stores/workflow/actions/publishing.ts::autoSuggestCategoriesForDraft`：
  自动匹配唯一入口，逐目标站点调用 `matchCategory`；不包含运行时开关或第二条
  自动匹配分支。属性填写统一从主对话执行。
- `tests/test_category_match_facade.py`、`tests/test_category_tools.py`：首次上下文裁剪、
  Ozon 逐层导航与有限回退、Mercado Libre 多轮换词、未知 ID、deadline、凭据和工具去重测试。
- `tests/test_ai_agent_budget.py`：使用原生 FunctionModel/Agent 验证单批和跨批第五次调用不执行、
  RetryPrompt 正确闭合工具消息、预算错误保持通用含义。
- `scripts/evaluate_category_match.py`：读取历史商品事实或 JSON，使用当前配置的真实模型和平台
  检索器，记录首批完成率、候选召回、调用数、重试、耗时与结构判断。评测使用隔离数据库，
  不修改原会话、商品或草稿；可临时指定已配置模型比较，不能把旧 AI 结果当作已确认标准。

endpoint 内部只使用 `category_id/path_segments`。前端只在 API 边界转换成人工
选择组件需要的 `id/path`，且不会自动写入模型首选；用户仍需点击候选确认。HTTP
结果只返回最后检索位置与去重后的轻量叶子候选。完整技术 spans 和 usage 进入 instrumentation；
AI Work 保存脱敏且有界的 Agent 输入、每轮模型消息、工具参数/结果、trace 关联和最终业务摘要，
用于区分模型选错、validator 拒绝、工具失败与资源上限。

Ozon 自动类目召回不再要求模型猜中平台类目关键词。后端从
`erp_web/runtime_units/ozon_category_api.py` 的现有扁平商品类型语料恢复真实父子关系，
首次输入提供顶层节点，`erp_web/runtime_units/category_tools.py::browse_categories`
每次最多展开两个真实分支。标准流程逐层到达 `product_type`，必要时在最多四次导航内
回退到尚未展开的备选分支；只有工具真实返回的叶子 `category_id` 可以进入详情终检。
Mercado Libre 仍使用其独立的远端 domain discovery 关键字能力，不作为 Ozon fallback。

## 发布币种与核价

- 页面入口为 `/api/draft-pricing/preview` 与 `/api/draft-pricing/apply`，经
  `facades/draft_pricing_facade.py` 调用 `runtime_units/draft_pricing.py::price_draft`。
  AI 的 `draft_pricing_preview` / `draft_pricing_apply` 及市场准备也使用此业务入口。
  请求为草稿 ID、目标范围与本次费用修改；省略参数复用草稿保存配置。系统读取已勾选且启用
  SKU 的采购成本、包装资料和逐 SKU 费用覆盖，绝不以商品主档成本替代缺失规格成本。
  `common.domestic_freight_cny` 仅表示国内物流，目标的 `shipping_amount` 仅表示国际运费。
  页面通过类型化 `sku_updates` 携带未保存的成本/尺寸覆盖和选择，通过 `target_selections`
  携带当前 Mercado 销售条件；后端验证成员范围。其他页面编辑保留在前端。
- 预览不保存草稿或已应用售价；应用重新执行同样计算并验证全部 SKU × 市场结果，
  成功后通过 Store 一次保存公共参数模板和 `sku_items[].pricing`。发布只使用逐 SKU 结果。
  计算前后比较商品、草稿和店铺配置，网络报价在商品锁外进行，保存时在商品锁内重读，
  冲突返回 `DRAFT_CHANGED`。公共模板不保存首个 SKU 的结果冒充所有 SKU 的售价。
  缺失信息返回 SKU、市场和字段；AI 只补问实际缺失项，正常核价无需先读取成本。
  显式查看成本可用 `draft_attributes_read(scope=sku)` 的 `cost_cny` / `cost_source`。
- 内部批量引擎 `pricing_batch.calculate_sku_prices` 使用 `pricing_runtime.PricingSession`
  和 `services/pricing_shipping.py` → `international_shipping/ShippingModule.quote()`。
  `items: [{sku_id, input}]` 仅是内部引擎契约；HTTP 和 Agent 不再自行拼装。
  一批共享店铺、汇率、Mercado token、Ozon 仓库/配送渠道；每个 SKU 独立计算成本与运费。
  批次返回逐 SKU 结果和 `metrics`；`erp.pricing` 记录每 25 个 SKU 的进度与耗时。
  模块负责 Ozon/Yandex 费率版本和三平台物流计算，返回全部候选；ERP 使用既有汇率
  换成物流字段 CNY/USD 后选最低价。模块通过回调使用当前售价算法校验货值，
  不反向导入 ERP。Mercado 的内置运费表已删除，多销售国家分别请求报价并计算售价。
  凭据由项目装配，Mercado token 仍通过 `get_mercadolibre_access_token()` 取得。
  证据进入既有核价依据和指纹；逐 SKU 核价固定模板版本。无新增 tariff HTTP 端点，
  模板导入/预览/启用由模块 CLI 维护，详见 `erp_web/international_shipping/README.md`。
- 店铺授权配置（`store_auth.auth_detail_json`）中的 `listing_currency` 是核价与发布
  的唯一币种事实源。注册表、国家、站点、草稿历史值和前端 option 都不是发布币种
  来源，也不得作为 fallback。
- `erp_web/marketplace_registry.py`：只维护站点身份、标签、语言、平台能力与店铺绑定
  字段；不再携带 `market_currency`/`listing_currency`。Yandex 声明
  `store_binding_fields=("business_id", "campaign_id")`：发布确认的店铺身份要求两个字段
  同时存在，可变的 `shop_name`、脱敏 token 或单独 `business_id` 都不能作为绑定身份。
- `erp_web/services/listing_currency_service.py`：无平台分支的纯状态机服务。负责
  远端发现结果归一化（单值锁定 / 多值待选 / 无能力人工 / 失败 refresh_failed）、
  人工选择校验、ISO 4217 校验与币种指纹计算；不做网络请求、持久化或站点推断。
- `erp_web/runtime_units/store_credentials.py` 与
  `erp_web/runtime_units/mercadolibre_auth.py`：授权 tester 在凭据校验成功后调用平台
  远端能力（Ozon `/v1/seller/info`、Yandex Business settings、Mercado Libre
  `/users/me` + `/marketplace/users/{user_id}` 账号映射；区域账号再读取站点元数据），
  返回统一发现结果，由共享状态机持久化到店铺授权配置。CBT 是 Global Selling
  父账号/全局刊登命名空间，不是普通国家站点：OAuth token 与 `account_user_id`
  只绑定这个 CBT parent；子 marketplace user 只作为 `site_id + seller_id + logistic_type`
  operation 保存，不能拆成多套本地店铺账号。禁止请求 `/sites/CBT`；标准 CBT 按
  官方发布契约把 USD 作为 `locked` 发现结果持久化，来源为
  `global_selling_contract`。账号实际启用的子市场与物流方式持久化在
  `marketplace_bindings`，不得从静态注册表推断。
  授权失败或凭据/身份变化会清除币种 ready 状态；核价层不再有远端币种补取副作用。
  `store_credentials.get_mercadolibre_access_token()` 是业务代码取得 Mercado Libre
  token 的唯一入口：普通读取从 SQLite 取得最新值，不请求 `/users/me` 或回写配置。
  真实业务请求明确返回 401 时，以凭据锁和 CAS 语义刷新一次 access/refresh token。类目、订单、图片与发布调用不得
  从配置字典直接提取 token；刷新实现不再属于 `publish_mercadolibre.py`。
- `erp_web/http_route_units/auth_config_routes.py::/api/store-auth/currency`：受控人工
  币种选择/填写接口；`/api/save-settings` 只接受注册表凭据字段与非敏感静态字段，
  币种派生字段一律由后端授权/币种服务写入。
- `erp_web/services/pricing_service.py`：所有发布售价使用 `{amount, currency}` Money；
  商品成本、物流与利润先统一在 CNY 核算，再按店铺 `listing_currency` 换算。
  每个目标分别保存 `calculation_basis`（含 `currency_fingerprint`）与 SHA-256 指纹。
- `erp_web/runtime_units/draft_publish_context.py`：从 `pricing.targets[platform:site]`
  投影当前目标的发布价格，并提供 `build_store_publish_context()`。持久化草稿没有含义
  不明的顶层 `price/currency`。
- `erp_web/runtime_units/publish_validation.py`：发布前重新加载当前店铺配置，核对店铺
  币种 ready 状态、草稿币种快照、币种指纹、Money 币种、核价指纹、商品成本及包装
  尺寸；任何变化都会把旧核价判为 stale（STORE_CURRENCY_CHANGED/PRICING_STALE）并要求
  重新核价。
- Mercado Libre CBT 草稿中的 `site=CBT` 只表示 Global Selling 刊登范围与 CBT 类目
  命名空间；真实销售目的地保存在当前目标的 `sites_to_sell[]`，每项必须精确匹配
  授权同步的 `marketplace_bindings` 中的 `site_id + logistic_type`。不得把 CBT 写入
  `sites_to_sell[].site_id`，也不得自动选择账号的全部子市场。只要任一 child binding
  的 `business_model=CBT CN Fulfillment Managed`，整个 CBT seller 就必须在标准售价
  流程中显式阻断且不提供标准销售目标选项，不能误用 `price/currency_id` 流程。
- CBT 是内部 provider/发布目标，不是前端语言或销售市场。草稿与 CBT target 的
  `language` 表示当前文案语言；前端按该语言展示并勾选 child market，保存时把选择
  映射到同一个 CBT target 的 `sites_to_sell[]`。草稿箱市场选择器是唯一人工选择入口；
  文案编辑区不得再维护第二套市场勾选或按市场拆分的标题状态。
- `erp_web/services/mercadolibre_listing_model.py`：根据 `/users` 返回的 CBT 身份与
  `user_product_seller` tag 派生唯一 `listing_model`。有 tag 时为 `user_products`，
  无 tag 时为 `traditional_global_items`；两者是 Mercado 账号侧互斥合同。缺少 tag
  只会禁止 User Products wire contract，不得阻断传统 `/global/items`，也不得在某个
  endpoint 报错后切换模型。区域账号不映射到任何发布模型，本项目仍不提供区域
  `/items` 直发。
- CBT 核价的销售国家/物流必须通过当前账号 `marketplace_bindings`、文案语言和
  `listing_model` 契约校验。AI 预览/应用复用草稿已有选择；复合市场准备若传入
  `sales_target`，必须有 `source_message_id` 对应的真实用户选择依据。
  页面选择通过 `target_selections` 提交，选择与成功报价在一次保存中生效；
  核价失败不会留下只保存了部分选择的中间状态。各 SKU 的 `price/net_proceeds`
  只写入各自的 `pricing.targets[key].sites_to_sell`，公共目标保留非金额销售条件。
- `sites_to_sell[]` 同时属于核价指纹和发布审批快照：任一销售国家或物流方式
  及其市场级 `price/net_proceeds/listing_type_id/free_shipping/sale_terms/status` 变化都会清除旧核价、
  预检与发布就绪状态，撤销旧发布预览，但保留已发生的远端商品身份。人工审批摘要
  必须可读地列出每个 `site_id/logistic_type`，目的地、销售条件或
  店铺映射在批准后变化时，旧批准必须判定为 stale。
- 对 `listing_model=user_products`，`marketplace_bindings[].pricing_model` 是 Mercado
  计价模式事实源。同一个 Siteless User Product 只能使用一种模式：普通售价模式发送根级 `price` 与市场级 `price`；
  明确启用 net proceeds 的账号发送根级 `global_net_proceeds` 与市场级
  `net_proceeds`。两组字段互斥，选中市场的账号模式不一致、目标同时携带两种价格、
  或普通价格账号试图发送 `net_proceeds` 时必须在本地失败，不能靠远端报错或静默
  fallback。Fully Managed 虽然也使用 `global_net_proceeds`，但它是独立发布契约，仍由
  上述标准流程阻断。传统 Global Items 账号则按其独立合同发送根级和市场级
  `price`；不得把 User Products binding 的计价校验反向套到已经明确识别的传统模型。
- `erp_web/marketplaces/yandex_currency.py`：Yandex wire 编码边界（内部 RUB ↔ wire
  RUR），只作用于最终 payload 与发现归一化，不是币种来源。
- 商品 payload 的读取兼容保留在归一化边界；旧数字售价和无指纹核价只标记
  失效，不自动猜币种；这是商品 payload 兼容，不是 SQLite 旧版本运行时迁移。
- `erp_web/stores/store_currency_migration.py`：发布币种事实源切换的一次性内容迁移
  （幂等）：Ozon 旧合同币种迁移为 locked+ready 并删除 `contract_currency`；Yandex /
  Mercado Libre 的静态推断重置为 unresolved；静态 JSON 剥离派生币种字段并按授权状态
  迁移 `account_site_id`。

## 商品与草稿

- `erp_web/http_route_units/product_routes.py`：商品与草稿 HTTP 入口；路由只负责通过
  `validate_request_payload(..., endpoint=handler.path)` 校验请求并把编排交给
  `erp_web/facades/product_facade.py`。
- `POST /api/duplicate-draft` 的当前唯一语义是：以一个独立草稿为来源，创建具有新
  `draft_id` 的新刊登草稿。它复制来源草稿的可编辑内容及待复核项，但必须重置卖家
  SKU、UPC、预检结果、发布状态及全部远端刊登身份；不得复用来源草稿身份、把操作退化为
  复制 ID/文本，也不得保留旧复制路径作为 fallback。
- 复制草稿保留逐 SKU 已应用核价及其币种快照；报价不绑定草稿/卖家编码。
  预检仍按当前店铺币种、成本、包装、费用及销售目标检查有效性。缺失核价币种快照
  报 `PRICING_STALE`，仅非空快照与当前币种不符时报告币种变化。
- `erp_web/facades/product_facade.py::duplicate_draft_payload`：草稿复制请求的唯一 HTTP
  编排入口；`erp_web/stores/product_store.py::ProductStore` 仍是草稿规范化、复制、
  持久化和索引更新的唯一 owner。
- `runtime_units/draft_edit_capabilities.py` 暴露 `draft_duplicate` 和
  `draft_sku_selection_update` 两个可组合操作，共用 `ProductWriteCapabilityScope`。
  AI 逐次复制多份，再用完整 `selected_sku_ids` 集合设置每份的发布选择；空集合取消
  全选，不删除商品 SKU。`ProductStore.update_draft_sku_selection` 在商品锁内校验
  SKU 归属及启用状态，仅提交勾选变化，复用编辑器保存和预检失效规则。
- `facades/agent_draft_scope.py` 从同会话的可信 `draft_duplicate` 成功回执解析新草稿
  的来源，只允许当前所选草稿及其副本后代进入后续写入范围；不自动开放同商品的兄弟
  草稿，不信任模型提交的来源声明。后续用户消息仍按当前所选范围与操作权限检查。
- 工具参数、身份回执和勾选契约位于 `schemas/product_write_capabilities.py`。
  复制防重继续复用 Pydantic AI 原生 tool call ID 与现有 Tool Bridge 的持久回执：
  同一调用重放返回原回执，新调用可创建另一份副本；结果未知时沿用现有阻断机制。
  本次只增加业务工具和范围校验，不新增 Agent loop、恢复协议或审批状态机。
  已核对本地 `pydantic-ai-slim==2.43.0` 与官方 Function Tools / Toolsets 文档。

## 商品发布

- `erp_web/product_model/sku_model.py`：商品实际 SKU、来源快照、草稿选品与每行卖家编码的唯一契约。商品保存全部实际规格；草稿通过 `sku_id` 引用，卖家编码绑定 `draft_id + sku_id`。已发送的编码和远端关联不能由普通保存覆盖。
- `erp_web/runtime_units/publish_context.py`：一次发布评估共享类目定义及类目币种查询结果。币种结果仅在当前上下文及其 SKU 投影间复用（包括空结果），按类目与授权隔离，不跨请求缓存或持久化。
- `erp_web/runtime_units/sku_publish_projection.py`：逐 SKU 合并草稿覆盖值、目标属性和独立核价结果，并校验平台组合条件。先缩小输入再复制：临时单品视图只带当前 SKU、当前平台草稿和图片池，不带整组选品、来源规格或历史预检结果，也不写回主档。组内校验只保留 `SkuGroupingMember` 的身份与属性，不保留所有商品投影；重复组合逐组返回有界说明，完整规格列表保存在 `PublishValidationIssue.affected_skus`。
- `erp_web/runtime_units/sku_precheck.py`：平台无关的纯预检问题汇总。以错误码、类目差异字段和受影响 SKU 集合关联必填缺失与整组空值检查；其它组合约束独立阻断。以 `erp_web/schemas/publish_capabilities.py` 中的 `PublishIssueSku`、`PublishRelatedIssue` 保留规格身份与关联校验，并按类目定义定位填写入口。
- `erp_web/runtime_units/sku_publish_adapter.py`：注册表唯一发布入口，委托 `sku_precheck.py` 汇总 SKU 预检错误和提醒；编译带每项身份的冻结 SKU 清单；平台叶子适配器保留原生单品 I/O。每项写前落盘、写后保存响应，成功项按内容指纹跳过，未知结果禁止再次创建，异步确认仅推进原任务。
- 前端 `ProductSkuEditor.vue` 维护商品事实，`DraftSkuPanel.vue` 负责草稿选品和覆盖，`actions/pricing.ts` 按 SKU × 目标调用现有核价引擎。包装资料或费用改变后必须重新应用售价。
- `erp_web/product_model/sku_image_model.py`：SKU 图片资产引用与原图地址迁移的纯函数 owner。采集统一下载规格图并按来源去重；内部 `image_asset_id` 是唯一关联，草稿以同名覆盖字段单独选图。旧持久化 `image` 只在读取边界迁移，发布不再按 URL/路径匹配。
- 前端 `SkuImagePicker.vue` 从素材池选图；图片页“关联 SKU”复用商品/草稿保存入口。`replace_selected` 处理结果只替换当前草稿的相应 SKU 引用，换图不使核价失效。无调用方的 `set_sku` 素材标记 action 已删除。
- 商品 schema 当前为 4；保留本地商品及草稿的 SKU 图片地址迁移能力，不恢复旧的按下标选品格式。详见 `docs/sku-workflow.md`。
- `erp_web/http_route_units/publish_routes.py`：发布预检、payload 预览、非 Mercado 平台同步发布、
  发布队列及 `POST /api/publish-bus/reconcile`。reconcile 只查询已持久化任务，不重放发布修改。
  Mercado Libre 只允许预览、确认与 PublishingBus 持久队列。
- `erp_web/http_route_units/get_routes.py`：发布任务列表与指定 Job 详情。
  店铺商品发现、查询和销售状态管理由在线商品模块负责。
- `erp_web/facades/publish_facade.py`：HTTP 层唯一发布 facade；业务编排进入
  `erp_web/runtime_units/publish_workflows.py`。
- `erp_web/runtime_units/publish_result_confirmation.py`：发布结果确认的唯一 owner。
  提交回执持久化后进入 `pending_confirmation`，释放发布 worker，等待用户点击查询最新结果。
  不保留后台延迟查询调度器，重启不恢复历史 `confirmation.next_check_at` 计划；
  该字段保留历史读取，新写入为空。`confirmation.last_checked_at/check_error` 保存最近
  检查时间与查询错误；读失败不改变发布结果，不重放写请求，不释放活动发布锁。
  `POST /api/publish-bus/reconcile` 仅接受 `trigger=manual`，连续手动查询冷却 30 秒。
  进入发布列表、打开/切换详情及其他本地刷新都不触发远端查询。
  同一任务/平台在进程内合并并发查询；平台回执及店铺身份校验仍保留。
  Yandex 的已批准写步骤由 `advance_yandex_submission` 推进，未完成时是
  `pending_submission`；`poll_yandex_publish_status` 只能只读确认，不能推进写入。
  本机制属于 ERP 领域任务调度，不属于 Agent 生命周期。已核对安装的 Pydantic AI
  2.44.0 及 [Deferred Tools 官方文档](https://ai.pydantic.dev/deferred-tools/)：Agent 暂停与
  恢复继续使用原生 Deferred Tools，Job Reader 仅投影持久结果；不新增 Agent loop、
  消息历史或事件协议，也不通过定时器调用模型。
- `erp_web/runtime_units/publish_adapter.py`：发布平台适配器注册表。只有这里注册且
  在 `marketplace_registry.py` 声明 `CAP_PUBLISH` 的平台才允许进入真实发布流程。
- `erp_web/services/mercadolibre_target_contract.py` 与
  `erp_web/services/mercadolibre_market_precheck.py`：Mercado Libre 多市场预检的确定性
  契约与展示投影。前者按用户原始 `sites_to_sell[]` 顺序校验每个市场 operation，后者把
  结果分为父级与逐市场 `blocked/passed`；任一确定性错误必须保持顶层 `ok=false`。
  当前店铺授权 operation 为 `CBT CN International Drop Shipping + remote` 时，按官方
  Cainiao 规则检查长≤60cm、宽≤40cm、高≤35cm、三边和≤135cm；墨西哥、智利、
  哥伦比亚、巴西和阿根廷的包装重量≤15kg，乌拉圭≤20kg。市场级规则通过独立的
  scope 元数据投影，字段仍指向真实的 `package_dimensions`，投影后不得把内部 scope
  元数据返回给客户端。平台运行时的 `item.shipping.mode.not_supported`，或远端仅返回
  `can't send the product in this kind of shipment` message 时，只映射为中性的“物流方式
  不支持”，不得仅凭一次类目切换实验诊断为类目不兼容；当前不可运营市场也
  只根据真实远端响应映射，不固化为永久预检规则。新确定性规则必须有当前官方路线文档，
  并严格限定 business model、物流和市场，不能把旧承运商或单次发布结果扩大化。
- `erp_web/runtime_units/publish_mercadolibre.py`：Mercado Libre 专属发布与错误处理。每个 SKU 按发布目标在
  `sku_items[].publications[target].result.publication` 保存远端事实；其中 `model` 明确区分 `user_products` 与
  `traditional_global_items`，前者以 `siteless_user_product_id` 为全局身份，后者以
  `parent_item_id` 为 CBT 全局身份，`publication.markets[]` 统一保存各销售市场的
  item/user-product 投影。
- `erp_web/marketplaces/publishing.py`：只按已验证授权写入 payload 的
  `_listing_model` 显式分发，远端错误不会触发 fallback。User Products 首次创建向
  `POST /global/user-products/families` 发送单元素数组，并要求响应 cardinality、
  Siteless ID 与每个市场的 Item/Local UP 映射严格闭包。已有 User Product 新增市场时
  先调用 `POST /global/user-products/{id}` 并确认映射；只有当前 payload 与本地
  `confirmed_payload` 存在可证明的字段差异时才执行共享字段 `PUT`，纯新增市场不发送
  无关更新，缺少可信旧快照的复杂字段不猜测重提。若 PUT
  异步则按统一确认策略单次读取 `/user-products-families/tasks/{task_id}`，任务根必须 `finished` 且每个
  User Product 都有明确 succeeded/failed 终态。确认阶段不得再发新增市场 mutation。
  写响应身份漂移、确认响应畸形、写请求结果不明或崩溃窗口进入 `outcome_unknown`，保留活动
  锁并禁止自动重放。存在 task ID 时，用户可从发布任务页触发只读对账；只有确认
  `applied/partially_applied/not_applied` 后才把 job 收敛到终态并释放同草稿/平台锁，
  初次 unknown 与最终对账结论分别保存审计日志。没有 task ID 的未知
  创建仍必须通过 Mercado 后台或支持渠道人工确认，不能猜测或强制解锁。传统模型首次
  创建使用完整 `POST /global/items` 并保留 `parent-item-info: true`；已有父项只用最小
  `sites_to_sell` 请求调用 `POST /global/items/{parent_item_id}` 添加尚无 `item_id` 的
  operation，已有 Item 绝不重复 POST。标准发布不执行全量 PUT；父根 payload 与已创建
  市场字段由 `confirmed_payload` 锁定，变更时必须创建新的 Global Item。响应只把通过
  operation 闭包校验的 `item_id/site_items` 作为真实成功身份；绝不恢复区域 `/items`。
- `erp_web/runtime_units/platform_query_capabilities.py` 提供商品、订单及发布任务查询；
  Agent 发布统一经过预览确认与持久队列；在线商品也向主 Agent 开放领域能力，见 [在线商品 AI 接入](online-product-ai.md)。
- `erp_web/runtime_units/publish_ozon.py`：Ozon `/v3/product/import` payload、
  草稿目标站点中的 `type_id/category_id + description_category_id` 配对、异步导入
  终态确认及错误字段映射；不得从商品级 `local_platform_categories` 回捞发布类目。
- `erp_web/marketplaces/yandex_http.py`：Yandex Market Partner API 的唯一 HTTP 边界
  （Api-Key 认证、HTTP 420 限流识别、`errors[]/warnings[]` 解析），覆盖 token 信息、
  campaign、商品映射、价格、库存与类目请求；HTTP/网络错误分类为类型化
  `PublishAdapterError(retryable)`，不包含发布编排。
  `GET /v2/campaigns/{campaignId}` 响应按顶层 `campaign` 解析（无 result 包装）；
  HTTPError 响应体中的平台 `errors[]/warnings[]` 同样解析并逐字段脱敏后保留。
  403/404 按请求上下文分类，不得一律判定为“API-Key 权限不足”：Campaign 端点的
  403/404 提示 Campaign ID 不属于当前 API-Key 所在柜台（确认填写的不是 Business ID），
  token/仓库/价格端点的 403 分别提示对应方法权限（如 INVENTORY_AND_ORDER_PROCESSING、PRICING）。
- `erp_web/runtime_units/publish_yandex.py`：Yandex 发布 payload 构造（按
  “目录商品 / 上架条件 / 价格 / 库存”分组）、`validate_yandex_draft()` 平台校验、
  checkpoint 状态机与错误映射。`publish_yandex_payload()` 只执行第一个尚未完成的
  远端 mutation；`advance_yandex_submission()` 依据已持久化 checkpoint 推进剩余写步骤，
  `poll_yandex_publish_status()` 只读确认；
  重启恢复不重复执行已完成写步骤。价格写入按已验证 `only_default_price` 分流
  Business/Campaign 级接口，价格进入隔离区时阻断自动确认。
- `erp_web/schemas/yandex.py`：Yandex wire 与发布状态机 shape（token/campaign/
  checkpoint/result 等）的唯一 owner。
- `erp_web/stores/config_store.py`：店铺授权摘要与 `_auth_status_label`。Yandex 在线
  派生的动态授权元数据（`business_id`、scopes、价格/库存能力、仓库等）只持久化到
  SQLite `store_auth` auth detail，不进入静态 JSON；真实 token 或 Campaign ID 变化时，
  同一次保存原子清除旧派生能力与成功态，状态回到“已保存，未测试”。
- `erp_web/facades/product_facade.py`：保存 Ozon 草稿时，若只提供 `type_id/category_id`，
  通过当前 Ozon 类目缓存自动解析并持久化隐藏的 `description_category_id`。
- `erp_web/runtime_units/runtime_api.py::publish_product`：平台无关的预检、artifact、
  日志与商品发布状态持久化；成功结果中的 `item_id/product_id/offer_id` 会写入草稿
  `last_publish_task`，作为后续更新同一远端刊登的身份依据。
- `erp_web/runtime_units/publishing_bus_core.py`：SQLite 发布任务和并发执行；适配器
  必须返回可验证的远端成功证据。所有 enqueue 都必须提供可信 `idempotency_key`；SQLite 原子占用
  该键，并把 `product_id + platforms + draft_id/site/product_id targets + confirmation digests` 保存为
  不可变事实。同键同事实
  返回原 job 且不重复提交，同键不同事实返回 `PUBLISH_IDEMPOTENCY_CONFLICT`。人工页面队列入口使用
  服务端生成的 `manual:<uuid>`，不复用全局任务的稳定键。`GET /api/publish-bus/jobs` 返回按时间倒序的轻量任务摘要，
  支持 cursor、状态、平台和商品筛选；平台摘要从不可变的已批准 payload 白名单投影
  `sites_to_sell[].site_id/logistic_type`，使 CBT 父刊登下的实际销售市场可见，但不返回
  售价、标题、属性或其他 payload 内容。`GET /api/publish-bus/status` 只返回指定 Job 的完整详情。
  `POST /api/publish-bus/reconcile` 对已有任务做单次只读确认，适用状态与触发时间遵循
  本节的发布结果确认契约；对账期间保留活动锁，终态后执行草稿补偿持久化。
  两个读取接口都不返回 worker 恢复专用的完整商品快照、approved payload、digest、店铺 identity 或
  幂等事实。
- `erp_web/runtime_units/publish_capabilities.py`：发布摘要包含当前已授权店铺的脱敏稳定
  `store_identity`；validation digest 同时绑定商品、草稿、平台、站点、店铺身份和最终 payload。
  `product_publish_validate` 是严格只读边界，不调用平台 `prepare_product`，因此普通上架预检
  不会上传图片或改写商品。写工具 `product_publish_prepare` 与受信的
  `publish-payload-preview` 共用 `prepare_and_evaluate_publish_validation`：Yandex/Ozon
  先校验草稿与源素材，再将本地图片上传到当前默认 S3 托管目标，短锁写回并回读后
  做最终校验；Mercado 先预检，通过后才上传图片并写回 picture ID。
  准备工具从数据库回读校验并保存结果，返回类型化 validation；只进入写场景 allowlist，
  继续使用现有 Pydantic AI 工具注入、回执与范围约束，不新增 Agent 生命周期。
  `check_public_access=true` 可按需探测 Yandex/Ozon 所选图片的实际 URL，结果独立返回，
  不作为发布审批或平台抓取成功的证据。
  确认后提交会重新执行确定性校验并常量时间比较 digest，在短锁内复核当前图片交付、
  选择和草稿输入，随后把已批准 payload/digest/identity 写入
  PublishingBus job。worker 现取凭据，但外发前复核店铺身份与完整 digest，并直接发送冻结 payload；
  不会重新构造已确认内容。Capability 还会在重校验与队列准入前按完整确认事实恢复既有 job，封闭
  “job 已落库、工具回执尚未保存 job_id”的崩溃窗口。店铺切换、payload 篡改或事实冲突都会在网络
  调用前安全失败。
- 发布错误类型化契约：PublishingBus 不自动重放失败的业务提交；写入结果未知进入
  `outcome_unknown` 并保留待核实状态。HTTP 420/429 不返回可立即重试标志，认证及限流
  类型化错误经工具边界继续保留。只有统一请求管理器可以按显式只读预算重试。
- `erp_web/schemas/image_hosting.py`：S3 配置、交付字段、错误及测试结果的共享契约。
  `app_config.py` / `stores/config_store.py` 仍是唯一配置 owner：`image_hosting` 保存多个
  稳定 id 配置和一个默认项；Access Key ID 与 Secret Access Key 仅写入 SQLite
  `runtime_secrets`，公开响应只提供配置状态。省略或提交掩码保留秘密，`clear_secrets`
  显式清空；默认项先解除或切换才能删除，删除配置不触碰远端业务对象。
- `erp_web/services/image_hosting_config.py`：纯字段校验、目标指纹及公开 URL 拼接。
  上传 Endpoint 与公开入口独立配置；目标指纹绑定 Endpoint、Region、Bucket、路径、
  公开入口与寻址方式。改名与凭据轮换不改变对象身份，凭据轮换使测试版本失效。
- `erp_web/services/image_content.py`：纯图片字节校验与格式识别；统一限制读取大小，
  提供真实内容哈希，独立于 S3、网络和持久化。
- `erp_web/services/s3_image_storage.py`：唯一 S3 SDK 装配边界，使用固定版本 botocore
  的公开 `before-send` hook 将已签名请求交给统一外部请求管理器。显式注入凭据，
  不读取机器默认 AWS profile；禁用 SDK 重试、区域重定向及可选校验和协议。
  生产交付仅用 HeadObject / PutObject，按真实字节与格式生成
  `<key_prefix>/assets/<sha256前两位>/<sha256>.<扩展名>`，检查 SHA-256 元数据和长度。
  只有明确 404 才上传，冲突、403、限流与未知结果均停止，下一次显式准备先查对象。
- `erp_web/services/image_delivery_service.py`：发布图片 HTTPS delivery 唯一边界。
  外部 HTTPS 直链是素材来源；本地图片与已管理交付使用当前默认 S3 目标，记录
  `hosting_profile_id/delivery_fingerprint/delivery_provider/storage_key/content_sha256/url`。
  `inspect_product(stage="source")` 只读检查源素材，最终阶段要求交付与当前目标一致；
  普通 HTTP 预检和 `product_publish_validate` 均不上传。显式准备覆盖草稿及所选 SKU 图片。
  无默认配置、源丢失、尚未准备及目标变化分别返回类型化错误和下一步提示。
- `erp_web/runtime_units/image_delivery_persistence.py`：网络完成后在商品与配置短锁内
  检查源内容、图片选择和目标快照，只合并交付字段，保留其他用户编辑。
  队列准入再次复核，已经入队的 worker 发送冻结 URL，不因默认托管切换重新上传。
- `erp_web/services/image_hosting_transport.py`：S3 和匿名图片请求共用的 HTTPS 安全传输。
  拒绝私网、云元数据和重定向，连接固定已验证的公网 IP，保留原主机的 TLS 校验；
  下载受时间、字节数与图片尺寸限制。DNS、建连及 TLS 握手的发送前失败返回
  `ExternalRequestNotSent`；Fake-IP 保留地址给出明确的 DNS/代理排查提示。
- `erp_web/services/image_public_access.py`：统一请求管理器下的匿名 GET 检查，不携带
  存储凭据、店铺授权或 Cookie。验证实际图片类型和内容；结果仅代表当前服务器视角，
  不作为市场抓取成功或确定性 digest 的证据。
- `erp_web/facades/image_hosting_facade.py` / `http_route_units/image_hosting_routes.py`：
  图片托管配置的薄 HTTP 编排。`GET /api/image-hosting` 读取脱敏列表；
  `POST /api/image-hosting/save|default|delete` 仅改配置，`POST /api/image-hosting/test`
  对本次表单配置显式上传小图片、检查对象并匿名 GET，最后尽力删除本次独立测试 key。
  上传、公开读取与清理分开报告，测试结果绑定配置版本，不静默保存未保存表单。
  测试结果记录上传和匿名检查是否已执行；PUT 发送前被拦截时跳过匿名 GET 与
  DELETE，不声称有残留对象。PUT 后 HEAD 失败仍尝试精确清理本次测试 key。
- `front/src/components/auth/ImageHostingSettingsPanel.vue`：设置中的列表、凭据表单、
  默认项与显式测试。复用 WorkspaceDialog，提交中禁止关闭和重复提交；修改表单后
  旧测试结果失效。高级设置提供可取消的“移除已保存凭据”，保存时一次移除两项；
  默认配置须先解除默认，普通输入留空保留原凭据。
  批量发布允许尚未准备的图片进入“准备素材与发布预览”，最终通过
  并确认后才能入队。Mercado Libre 保持平台图片接口与 `ml-id:*`，不走 S3。

Ozon 创建/更新商品是异步操作。提交 `/v3/product/import` 获得 `task_id` 后，必须
按统一延迟确认策略读取 `/v1/product/import/info`；只有每个商品返回 `status=imported` 且没有逐项错误，
才写入 `real_publish_success`。拿到 `task_id` 本身不算发布成功。

Yandex 上架确认同样是异步操作。`offer-mappings/update` 提交后通过统一延迟确认策略回读
Business 商品映射与 Campaign 商品状态确认终态；每次显式确认只查询一次，
pending 状态不得记为发布成功，也不触发无限自动轮询。确认时 cardStatus（官方 OfferCardStatusType，无
PUBLISHED 值）先于 Campaign 状态裁决：`HAS_CARD_CAN_UPDATE_ERRORS`/`NO_CARD_ERRORS`
表示本次变更未被接受（即使 Campaign 仍为 PUBLISHED），审核中状态保留待确认；
只有卡片接受态配合 Campaign `PUBLISHED` 才判定成功。Business 级库存（无仓库组）
写入单一选定发布仓库，避免单一库存数复制到多个仓库造成放大。

## Product Research

- `http_route_units/product_research_routes.py` → `facades/product_research_facade.py`：商品查询、货源查询、确认入库的 HTTP 入口。
- `product_research_config.py`：保留市场和既有配置读取，旧独立 AI 选品方法迁移为 Sorftime；新凭据由 app_config 管理，公开配置递归脱敏。
- `services/product_research_service.py`：调研 Job 与 SQLite 运行记录；HTTP 和主 Agent Deferred 调用均启动异步任务，状态轮询不调用供应商。历史候选继续可读。
- `services/product_research_methods.py`：Amazon US 关键词查询一页，返回 ASIN、美元售价、评分数和 Listing 级预估月销量；不生成热度/利润评分，不把缺失指标改成零。
- `services/sorftime_client.py`：固定开放 API 地址、BasicAuth 授权，经统一外发管理器单次发送；不自动重试、翻页或扩词。以 RequestConsumed/RequestLeft 保存实际额度回执，网络错误不泄漏授权或上游原文。
- `services/product_research_sourcing.py`：图片/中文关键词查询 1688，结果与候选一起持久化；相同查询复用结果。确认接口只接受该候选已查到的货源编号，查详情和 SKU 后由 `ProductStore.import_research_product` 幂等入库。网络查询在商品锁外完成，重复入库不覆盖人工修改。
- `schemas/product_research.py`：调研数据形状；`schemas/collect_capabilities.py`：主 Agent 的关键词参数。独立 `research.web_search` AI 模型绑定和提示词已退役。
- Amazon Price 为分、1688 Price 为元；包装原始字段保留在选品证据中，单位未经核实不得写入核价字段。没有有效 SKU 时停止入库，找货结果不代表已确认同款。
- `front/src/components/domain/ProductResearchPanel.vue` 和 `ProductResearchSourcingPanel.vue`：市场查询与人工核对入口；`ProductResearchSettingsPanel.vue`：Sorftime 授权与额度验证。

## 架构与回归入口

- `tests/test_ai_context_architecture.py` 与 `tests/architecture/`：依赖方向、持久化、平台契约和退役入口守卫。
- `tests/test_ai_capability_architecture.py` 与 `tests/test_ai_capability_coverage.py`：能力声明、权限及 HTTP 入口覆盖。
- `tests/test_pydantic_native_contracts.py`、`tests/test_native_agent_integration.py` 和 `tests/test_native_reliability.py`：原生 Agent 契约、持久化与故障恢复。
- 领域回归按相关模块选择；前端测试就近位于 `front/src/`。验证命令见根目录约定及前端规范。

### SKU 平台属性填写

主对话通过 `draft_attributes_read(scope=sku)` 按目标读取 SKU 事实（默认全部，可主动分段），多项修改通过 `draft_changes_apply` 一次提交差异，单项可用 `draft_sku_attributes_update`；后端不再分批调用其他模型。公共属性写入拒绝变体字段，SKU 写入拒绝公共字段，均不改来源商品事实。`DraftSkuAttributesEditor.vue` 继续复用平台枚举、集合和单位控件进行手工编辑；`DraftSkuPanel.vue` 只负责选品及详情位置。

`sku_custom_attributes.py` 继续负责 Mercado User Products 自定义规格的纯契约，发布编译与组合预检共用。来源规格和历史 `source_option_translations` 数据仍可读取，当前属性填写由主对话直接按事实判断。

SKU 新草稿默认选品由 `sku_model.new_draft_sku_rows` 定义：全部启用规格选中，停用规格不选中。`collect_helpers.py` 的两条新建草稿路径共同调用它，卖家编码待取得真实草稿 ID 后生成；`DraftSkuPanel.vue` 对新增事实采用相同默认值，并以表头父复选框表示全选、半选和全未选。已有显式取消选择不会因刷新重新选中。空白采集数据不生成 `single` 规格，真实来源删除的旧 SKU 仍保留身份并停用。

### 主对话工具权限与执行回执

- 主对话工具由 `ai_capability_composition.py` 的显式 Catalog/allowlist、Execution Profile 权限与 `AiToolRuntime` 的代码校验决定。用户文本、页面背景和历史消息 metadata 均不产生工具授权表；不再调用额外授权模型，也不持久化按回合推断的写权限。所选草稿及其合法副本范围、字段校验、发布/删除审批继续在既有执行边界生效。
- 主模型结合完整对话理解“是、继续”等指代，工具可用不等于应当调用。问“刚才写没写”时先依据真实回执回答；计划、工具名、发送调用或当前字段存在均不能证明写入成功。只有结果未知或需核实当前状态时才查询。
- 已核对安装的 Pydantic AI 2.43.0 与[原生工具准备](https://ai.pydantic.dev/tools-advanced/#agent-wide-dynamic-tools)、[Deferred Tools](https://ai.pydantic.dev/deferred-tools/) 文档。直接使用原生 `PrepareTools` 管理额度/收尾时间、`RunContext.enqueue` 注入用户消息，以及原生 Deferred 审批/恢复；无需另建授权 Agent、消息历史协议或状态机。
- `draft_sku_package_update` 是 SKU 包装资料写入口，声明在 `runtime_units/draft_edit_capabilities.py`，契约在 `schemas/draft_package.py`，写入由 `ProductStore.update_draft_sku_package` 持锁执行。请求明确 `draft_id`、`sku_ids` 和 `package_dimensions` 局部字段；仅更新指定已选启用 SKU 的 `overrides.package_dimensions`，保留其他字段、逐 SKU 重量和全部目标市场，返回实际应用字段、SKU 范围及变更数量。零值、非有限数、未知/停用/未选 SKU 和已发布对象在写入前拒绝。
- `draft_sku_attributes_update` 仅写平台类目属性，不能写包装字段。通用 `draft_save` 仍为 internal，不为包装编辑重新开放整对象保存。

### 属性事实边界

主对话不能将混合 SKU 的汇总描述套给每个规格，不能从图片比例猜测尺寸、重量、品牌或认证。当前读工具提供来源文字与结构化 SKU 事实；资料不足时在主对话询问用户，不启动额外的属性图片填写或复核 Agent。

草稿根级 `stock`、`sku`、`upc`、`package_dimensions` 和 `publication` 已退役，`draft_stock_update` 已删除。`draft_read` 按所选 SKU 返回库存、编码和逐目标售价摘要；`draft_attributes_read(scope=sku)` 返回 SKU 的有效条码、成本、包装和差异属性。公共费用/定价规则、图片、属性和整组发布状态继续保留。单 SKU 平台请求使用 `sku_model.single_sku_publish_draft` 临时派生销售资料，不持久化第二份草稿销售字段。UPC 的 `ProductStore.assign_upcs_to_product` 在商品锁内重读后，通过数据库事务为缺条码的启用 SKU 各分配唯一号码；HTTP `/api/assign-upc` 与 AI `upc_assign` 均要求明确商品，允许限定 SKU，保留已有条码，号码不足整批回滚。

## 订单通知与订单快照

- HTTP 唯一入口：`erp_web/http_route_units/order_routes.py`，覆盖 Mercado Libre/Ozon/Yandex 回调和 `/api/orders` 本地查询及明确用户命令。
- 领域装配：`erp_web/facades/order_notification_facade.py`。平台差异集中在 `runtime_units/order_notifications.py` 与 `runtime_units/orders_{mercadolibre,ozon,yandex}.py`。
- 后台处理：`erp_web/services/order_notification_service.py`，领域任务领取、平台隔离和回调重试；不参与 Agent 生命周期。导航进入订单中心时检查自动同步；`useOrderCenterEntrySync` 只监听实际导航进入，初次挂载、焦点恢复、详情参数变化和本地轮询不触发。后端订单库 `settings` 按平台账号原子保存自动同步尝试时间，间隔由系统设置指定（默认及最低 5 小时），失败也计入且跨窗口/重启生效；手动同步不受该间隔限制。主动同步失败不自动重试，历史重试和过期执行记录转为失败；后台不再创建定期对账任务，旧 `sync_schedule` 表不再读取。
- 持久化：`erp_web/stores/order_notification_store.py`，独立订单库中的收件箱、快照、租约和未读提醒；`order_notification_migration.py` 仅执行主库历史通知的幂等导入。
- 共享契约：`erp_web/schemas/orders.py`。AI `platform_orders_query` 与界面读取同一份本地快照，不触发同步远端查询，不获取回调凭据。
- 金额：`orders_yandex.py::normalize_yandex_amount` 负责订单及 SKU 行的十进制金额归一化，`OrderAmountBreakdown` 保留付款、补贴和积分抵扣。`OrderAmountDetails.vue` 明确商品金额与旧付款快照的口径，不将其标成净到账；明细按平台行小计展示，不再乘数量。
- 发货日期：Yandex 保留 `delivery.shipment` 的日期/时间精度，Mercado Libre 读取卖家发货 SLA，Ozon 使用 `shipment_date`；`orderPresentation.ts::deadline` 仅对带时区时间计算倒计时。
- 前端：`front/src/stores/orderNotifications.ts`、`OrderSummaryCard.vue`、`OrderCenterPanel.vue`、`OrderDetailPanel.vue` 和 `OrderAlertBanner.vue`；原发布 store 中的单平台订单状态已移除。
- 原 `/api/mercadolibre/orders` 和 `mercadolibre_orders.py` 已退役；接入、状态映射、持久化与验证范围见 [订单通知说明](order-notifications.md)。

### 订单采购

- `facades/order_procurement_facade.py` 负责装配；`services/order_procurement_service.py` 负责唯一来源匹配、人工确认和详情契约。
- `schemas/order_procurement.py` 定义采购来源、发布 SKU 关联、订单行及采购记录；`stores/order_procurement_store.py` 在订单领域库中管理绑定、来源版本、采购幂等与数量约束。
- `runtime_units/order_source_bindings.py` 从冻结发布任务提取关联；`publish_bus.py` 在发布终态持久保存，采购服务装配时幂等回填历史任务。禁止按 SKU 编码反解、按标题猜配或以当前草稿替代发布时事实。
- `OrderCenterPanel.vue` 负责订单页面编排与筛选，`OrderListTable.vue` 承载紧凑列表与分页；`OrderDetailPanel.vue` 为可直达的订单详情抽屉，`OrderNotificationsDrawer.vue` 承载通知、同步异常与重试。
Yandex 交货信息由 `runtime_units/orders_yandex.py` 在订单同步及通知读取时调用 `services/order_handover_service.py`，按订单页批量查询并严格验证批次归属；`schemas/order_handover.py` 的结果随 `OrderSnapshot.handover` 一起持久化，由普通订单详情返回。`OrderHandoverPanel.vue` 只展示本地快照，不独立外发或刷新。批次分页失败走订单任务的失败与重试，保留旧快照；旧数据缺少该字段仍可读取。外部管理器仅把确切的批次搜索 PUT 归为只读，订单请求范围包含其 campaign 配额。
地址备注由 `facades/order_address_note_facade.py` → `stores/order_address_note_store.py` 在订单领域库独立持久化；契约见 `schemas/order_address_notes.py`，GET/POST `/api/orders/address-note` 只读写本地。按平台、店铺和原地址匹配，平台同步不覆盖；保存校验订单地址与备注版本。`OrderAddressNote.vue` 提供点击展开、关闭自动保存的气泡，复用完整遮罩指针手势并向外层订单传播锁定状态。

- `order_source_bindings.py::published_sku_image` 从冻结 SKU 图片覆盖/事实引用或公共主图解析缩略图；`OrderProcurementService.present_orders` 按受信店铺与远端 SKU 身份为列表、详情补充相同图片，不读取当前草稿或远端商品。图片不参与绑定身份，历史绑定在装配时仅补齐缺失图片。
- `OrderThumbnail.vue` 统一商品缩略图及加载失败占位；`OrderProcurementLine.vue` 按 SKU 卡片承载来源确认与采购操作，`OrderPurchaseRecords.vue` 展示可展开的采购历史，`OrderSourceDialog.vue` 和 `OrderPurchaseDialog.vue` 分别负责来源编辑、采购登记。所有弹窗、抽屉共用 `WorkspaceDialog.vue` 与完整遮罩手势校验。
- `OrderIntegrationSettings.vue` 承载设置页回调接入。未验证的平台规格链接只能作为商品链接展示，修改来源规格或链接后必须重新核验直达能力。

### 1688 采购状态与物流

- `AuthSettingsPanel.vue` 的「1688 授权」沿用 `1688_api` 配置与 `ConfigStore` 的运行时秘密存储；空值或掩码沿用已保存凭据。采购查询需要 AppKey、AppSecret、Access Token，测试类型 `1688_order` 用输入订单号真实读取；商品采集的原有设置继续保留。
- POST `/api/orders/purchase-query` → `order_procurement_facade.query_purchase` → `services/alibaba_purchase_query_service.py`。请求只接受销售订单 ID、采购记录 ID 和查询种类；服务端读取冻结采购单号，网络前后校验店铺归属和记录未作废，不跨网络持有事务。请求与返回形状见 `schemas/alibaba_orders.py`。
- `order_notification_facade.py` 向 `OrderNotificationService` 显式注入 `services/order_progress_sync_service.py`：导航进入订单中心且满足 5 小时自动同步间隔、点击同步订单及平台通知保存快照后，同轮刷新有效采购的 1688 状态/物流，以及已关联或创建待核实的仓库单。单轮同订单去重，每笔网络请求前续租并验证账号与停止状态；查询期间不持数据库锁。单笔失败继续其他查询，各领域保留旧快照和错误，订单任务汇总部分失败且不自动重试附属查询。
- 列表、详情及履约页签定时刷新只读取数据库；详情编辑和提交期间暂停替换内容，旧响应不得覆盖较新的手动结果。采购与仓库单笔刷新入口保留。
- `services/alibaba_api_client.py` 固定 1688 HTTPS AOP 网关及三个买家只读接口，以 HMAC-SHA1 签名，通过 `managed_urlopen` 发送，显式声明 read 语义和 token 指纹，禁止重定向，不自动刷新 Token。物流先查运单，再按订单一次查询轨迹并按物流 ID 匹配；轨迹失败保留运单并返回警告。
- POST `/api/orders/purchase-sync` → `order_procurement_facade.sync_purchase` → `alibaba_purchase_sync_service.py`。登记成功后自动同步一次，幂等登记不重复查询；失败保留采购登记和最近成功快照。`purchase_progress_store.py` 持久化同步快照、代次与短期占位，网络不持锁，写入前重新核对有效采购、账号与代次。
- 订单状态与物流分别记录更新时间。物流接口的 `500_2` 按官方定义表示未发货，归一为空物流；其他物流失败只返回警告，仍保存成功读取的订单状态，保留旧运单及其原始更新时间，不触发包裹合并。授权变化与采购作废仍拒绝写入。
- `OrderProcurementStore.tracking_summaries` 为当前列表与详情批量读取有效采购的本地状态摘要，排除作废记录和变更规格；不额外查询远端。`orderPresentation.ts` 在无国内运单时展示采购订单状态，有远端或人工登记运单后切换到预报准备状态，已有预报优先显示仓库进度；多笔采购保留状态差异，物流签收不等同仓库发货。
- `alibaba_purchase_assignment.py` 用冻结商品 ID、SKU、订单子项、包裹商品数量证明归属；远端采购数量须与登记数量一致。缺失证据或同一远端 SKU 被多条采购使用时待人工分配；已证明的分批发货可逐批追加。`FulfillmentService.merge_purchase_parcels` 复用数量校验、版本和锁定边界，同运单幂等，差异不覆盖人工包裹。人工保存与确认快照在同一事务，使旧查询失效。
- `OrderPurchaseTracking.vue` 在采购和履约页签提供统一「刷新采购进度」，从采购记录的持久快照展示订单、物流及分配状态；关闭重开仍保留，不自动轮询远端。待分配/冲突通过 `FulfillmentParcelDialog.vue` 确认，`purchaseLogistics.ts` 仅提供人工分配时的运单候选。自动预报继续遵守既有开关和完整资料校验。响应不包含联系人、地址或授权原文。

## 跨境巴士履约 V1

`http_route_units/fulfillment_routes.py` → `facades/fulfillment_facade.py` →
`services/fulfillment_service.py` 是唯一履约入口。`AppContext.fulfillment` 装配并持有后台任务，
服务只接收当前平台账号和采购详情提供函数，不反向导入 runtime unit。
仓库状态及创建/取消结果核实随订单同步或单笔同步按钮触发，打开“跨境履约”页签仅读取本地快照；
`POST /api/orders/fulfillment/sync` 在请求内完成单轮只读核实并返回最新快照，不排队或失败重试。
后台只执行自动预报、已请求的资料更新和取消传播，不轮询已关联订单的状态。
`schemas/fulfillment.py` 定义内部配送及公开履约契约；`stores/fulfillment_store.py` 共用订单 SQLite，
负责唯一订单、版本 CAS、创建/取消占位、编辑租约和重启后的未知状态保留。
`services/crossborderbus_client.py` 适配官方协议和企业 Token 刷新，全部请求经过统一外部请求管理。
`services/platform_label_service.py` 负责 Yandex 单箱平台 PDF 与箱条码读取，复用现有店铺授权，不自动分箱或推进平台发货状态。`POST /api/orders/fulfillment/fetch-label` 为带版本校验的人工获取入口；默认自动预报在资料齐备后通过同一占位流程获取面单，失败独立记录，页面提示用户手动重新获取，不自动重试。

`services/fulfillment_label_service.py` 使用现有 `S3ImageStorage` SDK 边界交付 PDF 面单并核验公开内容。
原有平台订单身份及状态不变；列表另行读取履约摘要，详情承载履约操作及异常处理。
授权和默认方案在已有授权模块中配置；Yandex 本单可直接选择报单仓库与服务，不依赖全局默认方案。
`services/fulfillment_routing.py` 从交货批次提取目标：IMPORT 使用 destination，WITHDRAW 使用 origin，取消或歧义批次不能报单。平台交货点 ID 与跨境巴士仓库 ID 分属不同体系；`fulfillment_warehouse_links` 保存人工确认的对应，作用域包含店铺、跨境巴士账号及交货点身份和地址指纹。同一交货点跨批次复用，账号/地址变化后重新确认；无平台 ID 时仅用明确地址标识，不模糊猜仓。
`FulfillmentPlanDialog.vue` 打开时读取最新合作仓库，Yandex 只显示对应平台下的合作仓；已对应的仓库默认固定，重新对应须明确确认。`FulfillmentPlanFields.vue` 实时读取所选仓服务，基础服务单选，附加服务多选并显示费用。保存本单方案与仓库对应处于同一短事务，以履约版本、对应版本、当前订单地址和账号校验；网络读取不持有事务。
目的国按官方创建接口为可选字段：没有时不发送、不阻止报单。Yandex 不再把商品库存仓 ID 当作交货仓必填条件。提交前仍重读合作仓和服务并核对交货点，确保旧方案不能发往变更后的仓库；无自动预报规则时须点击“立即提交”。
协议、产品闭环、核实限制和验收见 [跨境履约说明](crossborder-fulfillment.md)。

### 平台请求中断与恢复

- `http_route_units/external_request_routes.py` → `facades/external_request_facade.py` → `services/external_request_control_service.py` 为授权页统一中断查询与恢复入口；契约在 `schemas/external_request_control.py`。恢复只改变外部请求 Store 的放行条件，不执行业务请求。
  请求级明确拒绝与未知写入分开展示：前者只限制原操作中的原请求，填写处理原因可解除；后者须核对业务回执。服务与 Store 共用 `schemas/external_requests.py` 的明确拒绝判定，存储事务校验故障及创建时间，旧快照不能解除重新产生的同类故障。
  授权页同类明确拒绝按平台、真实账号、审计关联接口与完整拒绝状态分组；组身份绑定所有成员快照。聚合只影响展示与明确恢复选择，不扩大实际阻断范围，恢复在同一事务内逐条留审计。
- `stores/external_request_store.py` 在短事务中持久化冷却、唯一探测租约、退避及恢复记录；只读临时故障可自愈，明确平台限制与未知写入不得隐式解除。旧永久阻断仅凭完整只读临时失败审计迁入冷却。
- `http_handler.py` 与 `external_request_context.py` 仅收集真实请求拒绝和领域操作关联；前端 `ExternalRequestNotice.vue` 提示用户发起的操作受阻并直达授权页，`ExternalRequestControlPanel.vue` 负责统一倒计时、记录及人工恢复。提示不替代业务结果，不创建第二套任务协议。
- 订单同步状态从完整历史和当前账号的真实外发范围汇总，不受最近 50 条通知限制。Yandex 订单归属 campaign，外部请求阻断归属 business；不得混用。详情见 [外部请求管理](external-request-management.md)。


### 1688 订单采购

- 入口：订单详情的商品采购区域 →「1688 采购」。`order_routes.py` 的 `/api/orders/alibaba-purchase` 及 `/preview`、`/create`、`/reconcile`、`/cashier` 经 `facades/alibaba_self_purchase_facade.py` 装配，不再从在线商品发起独立采购。
- `services/alibaba_self_purchase_service.py` 读取销售订单已确认来源和剩余数量；只有待发货订单可新增采购。预览绑定订单行、来源版本、数量、地址、人民币分金额和凭据摘要，有效期十分钟。下单参数仅取采集并冻结到上架记录的商品编号、SKU 和 specId，再使用已选且核对后的地址进行实时预览；缺失、歧义或不一致时返回 blocked_reason 并禁止预览/创建，不借用历史订单或临时商品查询补猜。起订限制以 1688 返回为准。
- `services/alibaba_purchase_address.py` 归一化全部 1688 保存地址，并从订单交货快照和 `OrderAddressNoteStore` 读取揽收／交货地址及本地备注。GET `/api/orders/alibaba-purchase/addresses` 提供候选，POST `/parse-address` 只在本地提取完整地址中的明确字段；缺失和歧义不猜测，用户修改并确认后才能预览，不改写原始地址或备注。保存地址使用内容指纹选择，选择后到预览前发生变更须重选；预览之后按已确认快照提交，不静默改为账号最新默认地址。
- `AlibabaPurchaseAddressPicker.vue` 提供地址选择、粘贴解析和字段核对，`AlibabaSelfPurchaseDialog.vue` 单独展示订单预览与支付方式。地址或数量变化使旧预览失效；支付渠道只使用本次预览返回的可用值。
- `stores/alibaba_self_purchase_store.py` 由 AppContext 持有，未提交预览仅在内存中短期保留（十分钟、最多 256 条），不写数据库、不出现在采购历史中；关闭弹窗或刷新页面后重新预览，服务重启后预览失效。用户提交时才在独立的 `data/alibaba-self-purchases.sqlite3` 原子保存请求，随后允许调用创建接口。版本 2 将旧 listing_id 列迁移为 target_key，历史数据保留读取能力，旧持久化预览不展示也不能提交。未知请求阻止同订单行重复采购，换授权不能绕过防重。
- 创建接口使用采购解决方案（买家自用版）对应的 `alibaba.trade.fastCreateOrder`，不调用分销方案的 `alibaba.trade.fenxiaoOrder.create`。只发送该接口声明的参数，预选支付渠道不是付款操作。预览、网关参数校验或替身测试成功，都不能表述为真实下单已成功；验收须以实际创建回执和订单查询为准。
- `OrderProcurementStore` 在订单库短事务预留采购数量；人工登记和来源修改共同遵守该预留，锁不跨网络。创建前再次核对价格和渠道，使用持久化请求号作为 outOrderId；回执不明只查原请求，不重发。确定未创建才释放数量。
- 1688 创建回执先持久化，再幂等回填销售订单原有采购登记和数量；采购弹窗只展示当前结果或待处理回执，不再列出第二份采购历史。关联失败保留远端订单号，可通过查询原订单重试。关联后沿用采购记录的状态、物流查询。远端取消经查询确认后作废关联记录并释放数量；未确认取消的自动采购不能只在本地作废。
- 支付渠道支持预选 alipay / shegou（先采后付），创建后通过官方收银台确认支付与账期；此入口不调用自动扣款接口。

- 1688 API 与网页采集均保留原始 `skus[].offer_id/spec_id`，`product_model/sku_model.py` 将其保留为 `source_offer_id/source_spec_id` 及来源快照。重新采集更新这些只读来源标识，销售内容覆盖不能覆盖它们。
- `product_model/alibaba_purchase_model.py` 在冻结发布关联时核对原始采集 SKU、规格标识和实际销售规格；不一致记入 `purchase_block_reason`。自动采购还须命中当前店铺销售 SKU 的已上架关联，人工填写的来源不能作为自动采购依据。旧资料仍可读取和人工采购，缺少完整证据的旧预览不能再提交。

### 系统设置

- 左侧栏底部 `AppSidebar.vue` → `/?tab=systemSettings` → `SystemSettingsPanel.vue` 在主区域提供订单自动同步间隔设置；复用工作台导航，支持刷新及浏览器前进、后退。
- GET/POST `/api/system-settings` → `auth_config_facade` → `ConfigStore.system_settings/save_system_settings`；单项更新保存在 `app_config.system_settings`，通用授权配置合并忽略该区段，避免旧页面覆盖。字段契约由 `schemas/config.py` 定义，前端类型自动生成。旧配置缺少区段时默认 5 小时。
- `OrderNotificationService` 通过显式 provider 在每次导航自动同步时读取最新间隔；修改设置不发起外部同步，也不重置既有同步尝试时间。
