# 在线商品 AI 接入

接入原则：按在线商品资源暴露能力，不按用户问法新增功能。工具通过 `OnlineProductService` 读取有界摘要、单件详情或提交领域任务；页面与 AI 共用组合身份、顺序和筛选，复用领域服务的校验、库存写入与持久化边界。

| 现有接口 | 主 Agent 工具 | 执行方式 |
| --- | --- | --- |
| `OnlineProductService.read_page` / `detail` | `online_products_read` | 列表为本地有界摘要；单件自动读取当前完整详情后投影所需公开字段，与主动刷新共用去重 |
| `GET /api/online-products/attributes` | `online_products_attributes` | 按真实刊登及版本读取实际类目、属性定义、当前值及分页候选，复用页面领域服务 |
| `POST /api/online-products/change` | `online_products_change` | 一个入口处理既有 price / stock / content / sale_state / archive，原生审批后入队即返回提交回执 |
| 同一 `OnlineProductService.change` | `online_products_changes_apply` | 按明确目标清单统一审批，固定操作和范围，逐项入队并计算真实成功/失败数；不调用平台批量写入旁路 |
| `OnlineProductService.job_detail` 的同源只读任务 | `online_products_job_read` | 查询本地状态和精简回执，不访问平台 |
| `POST /api/online-products/sync` | `online_products_sync` | 沿用领域同步任务，仅读取平台并刷新本地快照 |
| `POST /api/online-products/refresh-status` | `online_products_refresh_status` | 按 listing_id 直接查询单件状态并更新本地记录，不扫描店铺、不创建 Job |
| `POST /api/online-products/refresh-detail` | `online_products_refresh_detail` | 按 listing_id 显式读取当前单件详细数据并保存，不扫描店铺、不自动重试 |
| `POST /api/online-products/reconcile` | `online_products_reconcile` | 只向平台回读已提交修改的结果，不重发修改 |
| `POST /api/online-products/close` | `online_products_close` | 核对后通过原生审批结束未确认的本地操作，保留原结果未知记录，不撤销或重放平台请求 |
| `POST /api/online-products/retry` | `online_products_retry` | 原生审批后交由既有失败重试规则判断，入队即返回 |

`online_products_read` 通过 `id` 定位单件，或通过 `platform,catalog,q,status,market,page,limit,view,group_id` 读取列表，图片来源仍由 `include_source_images` 控制。两种入口均支持 `fields` 按需投影；不传 `fields` 时，单件返回完整公开详情，列表返回摘要。摘要保留身份、标题、状态、价格、库存及同步时间，不携带长描述、属性、图片、原始快照或完整历史任务。

读取优先选择任务所需的对象点路径。`fields` 的参数说明列出内容、保修、库存、价格及各操作能力的常用路径，保留其他公开字段的读取能力，不维护场景枚举。`content` 含长描述、图片和全部属性，局部任务应读取子字段；`capabilities` 按操作选择，不能以首个 SKU 的权限代表其他成员。父子路径重叠（如同时选择 `content` 和 `content.attributes`）在参数校验阶段拒绝，错误指出应删除哪一层；不静默改变 `values` 的键。

指定字段后的 `records` 固定包含真实刊登 ID、版本、同步与详情状态，所选值在 `values[字段路径]`。本轮已取得单件当前字段、操作能力和版本就直接复用，不再重复读取完整详情。列表字段仍是本地同步快照；生成内容修改前按目标 `id` 读取所需当前字段，后台完整读取不意味着必须把全部内容返回模型。字段缺失、`null`、零值继续区分；宽字段读取应减小 `limit`，条数上限不保证响应字节数。

`catalog=current` 默认排除归档商品，`archived` 仅读取归档商品，`all` 包含两者。分组、汇总和分页均使用所选目录；页面背景包含当前目录，AI 查询当前列表时传递同一 `catalog`。归档状态与库存、停售各自独立。

在线属性查询先取得真实 `listing_id` 和 `version`，再调用 `online_products_attributes`。
不传 `attribute_id` 读取定义，传入定义中的属性 ID 查询候选；分页沿用 `page.next_cursor`。
类目由刊登快照决定，不用标题搜索结果代替实际类目，不开放原始平台快照。
平台错误提示和必填定义须结合商品事实核实，鞋码、尺码表等资料不足时不能猜填。
`DISABLED_AUTOMATICALLY` 是平台自动下架；解除主动停售不等于平台审核通过，修改入队或内容回读成功也不能当作重新上架成功。

字段投影的自定义输入校验与其他编译工具统一接入原生 `args_validator`，返回具体字段原因供模型纠正。
直接调用和 Code Mode 均遵循原生重试上限；领域权限、审批和写入结果未知的处理不因此放宽。

`online_products_sync` 与 HTTP 同步入口接受 `catalog=current/archived`，默认仅普通商品；归档页明确提交 `archived`，两类不再合并同步。目录范围持久化到领域任务，失败重试和最新同步进度按原目录隔离；AI 同步当前页面时传递相同目录。其他平台暂不接受归档目录同步。旧任务仍可读取，缺少目录的历史记录保留原全店范围显示，旧任务重试按普通商品执行。

Yandex 全量同步仅调用目录、店铺商品、卡片及隐藏清单接口，按页保存基础资料、销售状态和错误警告；不读取独立价格、库存、仓库、账号价格设置或类目名称。卡片已返回的属性一并保存，不为查看属性重复请求整店。新任务结果以 `data_scope=basic` 记录实际读取范围，`completed` 仅表示基础同步完成，不表示详细价格、库存已刷新；历史任务不改写范围，其他平台沿用各自现有同步流程。

`basic_synced_at` 为基础资料读取时间；`synced_at` 为完整详情读取时间。新商品 `details_state=unloaded`，空价格/库存数组表示尚未读取，不能解释为零值或平台没有该数据。完整详情会同时更新内容、价格、库存及状态时间；仅基础同步时保留旧详细值及其时间。旧持久快照仍按原 `ready` 与 `synced_at` 读取，不清库或更改历史回执。

页面打开 Yandex 单件详情时通过 `refresh-detail` 显式查询一次，显示加载状态；失败保留已有资料和反馈，只提供用户主动再次读取，不增加计时重试。详情统一调用平台适配器的 `read`，按指定 SKU 重新读取目录内容、店铺状态、卡片属性与反馈、独立价格和库存，并关联类目属性名称。旧的仅补价格和库存的 `read_details` 路径已移除。并发更新、切换账号或活动任务冲突时拒绝覆盖。写前核验仍独立读取目标的完整平台事实，不能因 AI 取数结果存在而省略。

AI 的 `online_products_read(id=...)` 自动读取指定 SKU 的当前完整详情，包括内容，再按 `fields` 投影；不带 `id` 的列表及 HTTP GET 继续只读本地。主动 `online_products_refresh_detail` 与自动读取共用 `OnlineProductDetailReader`：同一原生执行（租户、操作者、Task/attempt）、同一账号和 SKU 的成功快照只读取一次，并发调用共用正在执行的请求。每次仍核对当前账号及快照；本地版本或读取状态/时间变化、提交修改或新执行会使原复用依据失效。成功标记仅在现有执行 deadline 内保留，不新增可配置缓存期限；失败不记为成功，也不自动重试。两种入口都读取完整事实，不会把仅刷过库存当作已取得最新内容。不新增 Agent 生命周期、后台计划或轮询；基础同步的成功回执仍不能扩大为全部字段实时。

列表默认 `view=groups`：按在线页面的相同顺序返回组合父节点或独立商品，每个节点只返回一个匹配刊登摘要。`groups.representative_id` 对应 `items.id`，`total_count` 为当前目录内整组 SKU 数，`matched_count` 为符合筛选的成员数；不能把代表刊登列表当作全部 SKU。查询“第一个商品”可直接使用 `page=1,limit=1`，无需关键词试探或发布日志。

`view=listings` 按相同分组顺序逐 SKU 分页，组合允许跨页；传递返回的 `group_id` 可读取指定组合的成员。两种视图都限制每页 `limit=1–50`（默认 25），`total` 是当前视图的匹配数，`listing_total` 是匹配刊登数，`summary.total` 是当前目录的刊登数，`next_page=null` 表示结束。完整 SKU 统计必须使用 `view=listings` 并遍历 `next_page`，不能把父节点数当作 SKU 数，也不能用组 ID 提交修改。

最新同步只返回 `OnlineSyncSummary` 的状态、进度和错误码，Store 直接查询固定字段，不加载历史任务的逐项回执。空筛选结果仍保留当前目录总数和同步可信度。HTTP 页面继续使用自身的组合展开载荷；它与 AI 的摘要视图具有独立用途，共享底层身份、顺序和筛选，没有旧 AI 读取 fallback。

运行时的 256 KiB 工具输出上限同时约束直接调用与脚本内调用，原始结果必须先通过校验才能进入 Python。Code Mode 负责逐页编排和最终汇总，不能绕过这个上限。若列表仍超限，应减小 `limit`；不得反复原样重试或枚举无关 SKU 前缀猜测页面顺序。

`online_products_refresh_status` 接受真实在线刊登 `listing_id`，直接复用 `OnlineProductService.refresh_status`，只合并商品和关联市场状态。返回的 `status_checked_at` 是本次核验时间，`synced_at`、价格、库存、内容保持不变；不能据此声称其他字段也是实时的。工具仅写本地快照、无需平台写入审批，仍使用现有 Runtime 的可信执行身份和 Pydantic AI 原生工具生命周期。它不会替代 `online_products_reconcile` 确认待处理的修改任务。

不增加“查询 SKU 数量”“查零库存”“改红色库存”等工具，也不开放 SQL、任意网络或数据库写入。AI 可直接理解商品字段；需要循环和统计时，使用现有 `run_code` 调用相同读取函数，完整分页后返回必要摘要。未知库存、未授权、同步失败与零库存/零商品不同；统计结果只能表述为实际读取的同步快照。平台没有提供足够规格信息时须澄清，不能猜测变体。

统一修改输入复用 `OnlineChange`，字段为 `listing_id,version,operation,scope_id,changes`；HTTP 的 `ChangeRequest` 继承它并保留 `idempotency_key`。AI 提交键由现有可信执行身份机械生成，不由模型挑选或覆盖。库存使用绝对数量；价格保留范围和币种；内容仅修改后端允许字段，图片仍使用完整目标列表。业务校验、账号隔离、冲突检查、任务互斥和平台回读继续由原服务执行。

`online_products_changes_apply` 接受至多 100 个 `OnlineChange`，同一批固定平台、账号、操作和范围，拒绝重复 SKU；不同 SKU 可有不同内容值。整组任务传入真实 `group_id`，审批与执行前按当前目录核对成员集合，漏项和组外目标在写入前拒绝。超过上限的目标须先完整读取，再按明确子集分批提交（子批不声明整组），最终按逐项回执核对完整覆盖。原生审批绑定全部参数和目标快照，执行时逐项复用 `change`；部分入队后取消或失败不会回滚已提交项。子项幂等键从可信调用身份和刊登 ID 生成，模型不能覆盖；失败子集恢复不重发成功项，不将批次等同于原子事务或远端原生批量接口。

任务查询、回读和人工结束通过 `online_product_tool_results.py` 返回紧凑回执，移除重复的 `before` 全商品快照和原始平台响应；完整领域记录和历史消息继续保留。同步保留成功/失败、新建/更新、发现数量、阶段及目录是否完整的统计；`failed_items` 最多返回十个失败样例，错误最多五百字符，`failed_items_truncated` 明确表示样例截断，不能将样例数当作失败总数。完整失败集合仍由既有重试入口读取。`recovery` 只描述该任务自身，不能证明没有其他阻塞或产生写权限。`partial/failed/confirmed/submitted/closed_unconfirmed` 无需再次结束；版本变化、结束状态不适用、回读状态不适用分别有稳定错误码。`can_close` 只是基本证据条件，执行仍在原事务边界重查时效、版本、账号及活动任务。

`GlobalAgentChatService` 通过既有 Factory 的原生 `output_validator` 接入 `online_product_completion.py`。它仅从本轮 `ModelMessage` 的实际修改尝试和回执派生检查：版本过期，或旧阻塞任务已终结而新修改尚未提交时，用 `ModelRetry` 要求模型继续核实。不会把读过的商品当作修改目标，不从用户自然语言生成权限，不保存工具计划，也不调用工具或开启第二个 Agent loop。新用户输入、审批拒绝、真实新阻塞、已入队回执及目标值已满足的明确读取证据均会解除相应检查；未知结果、等待平台和资源不足不强制推进。模型忽略检查时由原生有限重试终止，不能无限发“继续”。尚未被模型声明/尝试的意图没有可靠机器契约，因此不承诺任意自然语言任务都能被强制完成；整组覆盖由批量工具的明确 `group_id` 契约保证。

原生机制依据：已核对安装的 Pydantic AI Slim 2.44.0 / Harness 0.34.0 与官方 [Output validators](https://pydantic.dev/docs/ai/core-concepts/output/)；框架负责校验重试和流事件，ERP 只提供业务完成证据。适配缺口是框架不了解在线商品任务状态和目标覆盖；若领域入口以后直接完整处理这些恢复步骤，可移除相应输出检查，不保留双重恢复流程。

Yandex 在线属性支持补充缺失字段和修改枚举：`changes.attributes` 使用 `{id:字符串,parameterId:整数,value:平台原文,valueId:枚举编号字符串,unitId:单位整数}`，枚举及单位按类目要求提供；同一属性的多行代表完整目标值集合。页面经 `GET /api/online-products/attributes` 按刊登身份与版本读取类目字段及分页候选，AI 可复用类目查询能力获取真实候选，不能猜测枚举编号。审批、入队及发送前使用相同领域校验，保留未编辑属性，禁止改写分组或只读字段。其他平台仍沿用各自已有属性编辑契约。协议依据：[类目参数](https://yandex.ru/dev/market/partner-api/doc/en/reference/content/getCategoryContentParameters)、[商品更新](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/updateOfferMappings)。

Yandex 保留 `operation=sale_state` 的店铺停售，并增加 `operation=archive,scope_id=business,changes={archived:true|false}`。归档及恢复作用于商家全部店铺，保留库存和隐藏设置；恢复归档不保证立即可售。单件官方回执无失败项时确认归档成功并移入相应目录，逐项失败留在原目录，未知结果仅按需读取目标归档状态核实。AI 使用现有逐 SKU 修改工具及原生审批范围。

页面操作菜单通过 `change-batch/preview` 和 `change-batch` 支持跨商品批量停售/恢复、归档/恢复。勾选父节点时预览包含当前目录内该商品全部规格，单独勾选只处理相应 SKU；预览只读本地，提交按预览版本及同账号范围原子入队。后台复用逐 SKU 的领域记录和原有 worker，平台写入按停售 500、归档 200 SKU 的上限合批；部分失败分别落回各 SKU，未知批次不自动重放。此页面入口未改变 AI 的工具协议或审批方式。

图片新增从 `GET /api/online-products/source-images?listing_id=...` 读取关联源草稿的资产选项；AI 使用现有 `online_products_read` 的 `id` 和 `include_source_images=true` 读取同一结果。提交 `{asset_id,fingerprint}`，不能手填新 URL 或任意平台图片 ID。关联同时核验发布账号和远端商品身份；没有唯一来源时仍可调整现有图集。后台只准备本次选择，保留源草稿和图片池；Mercado 复用上传接口取得图片 ID，Yandex 复用 HTTPS 交付服务。任务回执保存准备后的完整目标图集，回读据此确认顺序与图片身份，不再次上传。

Mercado 新图片的上传与关联依据官方[图片文档](https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/pictures)和 [User Products 图片更新规则](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt)。传统 Item 新图先关联 `/items/{id}/pictures` 再提交完整图集；User Products 上传后直接更新 `/global/user-products/{id}`。

修改与失败重试使用现有审批模式：`ask` 显示原生审批卡，`full` 使用已有自动批准机制。摘要和参数绑定在服务器生成，执行时重核；不能通过换参数或切换店铺复用审批。同步和回读不会修改平台商品，无需额外人工审批。所有持久操作均在原生 Deferred 提交后由现有 Agent Job Service 投递。

修改和失败重试返回 `OnlineSubmissionResult`：`accepted=true` 只表示任务已持久化入队，附任务 ID、目标刊登 ID、平台、操作和当前任务状态，不表示平台已接收或生效。现有 Agent Job Service 把此回执作为原生 Deferred 结果返回，AI 继续提交剩余目标，汇总提交成功/失败后结束；不主动回读或等待远端结果。后台仍独立执行平台写入和回读，用户在统一任务队列查看结果，明确要求查询时仍可使用回读工具。同步用于取得后续读取的数据，继续等待领域同步完成。

`OnlineProductJobReader` 把同步及已持久化的待对账引用投影成现有 `JobStateSnapshot`。`confirmed` 与已受理的 `submitted` 为工具生命周期成功，后者仍不表示平台展示已生效；`partial` 返回独立的部分完成终态，不再映射为 `failed`，也不伪装成全部成功，`ok=false` 仅表示未全部完成。`outcome_unknown`、失败及自动回读耗尽继续按未完成成功返回，`last_external_status` 保留领域原始状态。通用状态 `failed` 不代表平台明确拒绝写入；模型须看原状态和操作记录，不得重放未知结果。Reader 不触发模型、平台请求或新任务。

同步汇总保留本轮所有实际尝试中已保存的成功结果，不能用最后一次失败覆盖整轮结论，也不能重复累加同一 SKU。部分完成后不因失败重复全量同步；目录完整且仍需继续时，沿用原审批调用 `online_products_retry` 仅处理失败项。目录发现中断时，已发现数不是全店总数，应重新发现目录。网络中断只说明传输失败，不能要求用户更换凭据，除非有实际授权错误。

`AgentJobService` 返回终态回执时，用当前快照更新任务引用中的状态和摘要，避免外层已结束而引用仍写着 `queued`。原生历史中的原始提交回执不改写。

本次核对安装的 Pydantic AI Slim 2.44.0 / Harness 0.34.0 与官方 [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)：原生 `DeferredToolResults` 可直接返回领域结果，框架不负责 ERP 的部分完成语义；修复限于领域快照、只读投影及工具指令，不另建 Agent 循环或恢复协议。若以后撤销通用 Job 快照适配，应将部分完成信息保留在原生工具返回值中。

人工结束使用 `CloseOnlineOperationRequest`：`job_id`、最近查询返回的 `expected_updated_at`、明确确认 `acknowledge=true`。必须先成功回读平台当前值，证据五分钟内有效，且平台无正在处理标记；查询失败、数据缺失、任务或商品变化、同目标任务执行中均拒绝结束。状态保存为 `closed_unconfirmed`，原请求、错误、回读值和人工决定留在原任务中。此终态仍不代表平台成功或明确失败，原请求可能延迟生效；旧提交键和外部请求保护保持有效。用户确认结束后可重新读取商品，通过原修改入口创建新操作，不能调用 retry 重放旧任务。AI 使用现有原生审批，快照绑定当前商品版本、任务版本和回读证据；不增加审批协议。

为什么直接使用 Pydantic AI 原生能力：项目当前安装 `pydantic-ai-slim==2.44.0`、Harness `0.34.0`，已有 Tool Bridge、Code Mode、审批及持久工具回执，足以覆盖本次接入、有界分页及提交即完成的工具结果。采用原生 `CallDeferred` / `DeferredToolResults` 完成可靠投递，只调整领域返回值，不改 Tool Bridge 或 Agent 生命周期。摘要、组合成员分页和同步进度是 ERP 领域查询职责；直接复用原生工具调用、Code Mode 和重试，不新增 AI 基础设施。没有新增 Agent loop、审批协议、等待/恢复状态机或事件编码。已核对 [Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) 和 [Code Mode](https://pydantic.dev/docs/ai/harness/code-mode/) 官方文档。

在线管理页面复用原有“读取背景”开关，传递当前平台和最近点击或键盘聚焦的 `group_id` / `listing_id`。展开组合只传节点 ID，点击或聚焦成员再传刊登 ID；管理详情优先使用该刊登。转到聊天输入框或关闭详情后保留最近商品，切换平台、页码、筛选、离开在线商品页或目标消失时清理。没有焦点 ID 时不得把首项猜成当前展开商品；背景只是定位信息，不是商品事实或授权。

2026-10-05 焦点与提交边界验收：后端 2202 项测试、47 项子测试，前端 535 项测试及类型检查通过。隔离验收覆盖 `ask/full` 下单件和批量入队后立即完成 AI 回合，以及远端随后超时不重新唤醒模型。该次验收时真实库存任务仍在旧进程中执行，当次未重启服务、重放业务修改或改写历史回执；该记录不代表当前服务进程的状态。

## 验证边界

`tests/test_online_agent_completion.py` 覆盖 74 项批量提交中单项冲突、完整成员校验、仓库范围隔离、审批绑定、取消后保留已提交项、原生 ask/full Deferred 恢复、精简回执与 `partial` 无需解锁，以及原生输出校验阻止可恢复遗漏、有限重试和真实退出条件。均为隔离数据库与受控模型/平台替身，不代表真实平台已经执行商品修改。

2026-10-28 窄字段读取验收：使用故障对话对应的 74 个 SKU 快照，生产库仅只读取样，领域服务和对话历史隔离到临时数据库；真实模型沿用当前 `global.chat` 配置与生产工具目录，只允许读取本地样本。任务要求先核对保修、库存和修改能力，不提交修改。`grok-4.7` 首次选择 `content.guarantee_period`、`stocks`、`capabilities.content`、`capabilities.stock`，并附标题和销售状态，未读取完整内容、图片或全部属性。两页分别覆盖 50、24 个 SKU，无重复遗漏；紧凑 JSON 响应为 67,798、33,068 字节，未触发输出超限。运行中有一次 `run_code` 重试，随后完成，共 4 次模型请求、约 101 秒。这验证了本次只读样本的字段选择和覆盖，不代表批量写入已验收，也不保证其他真实模型调用无需重试。

`tests/test_online_product_capabilities.py` 通过真实服务与隔离数据库验证读取契约、跨页计算、统一修改、版本与审批绑定、失败重试、未知结果只回读，以及 `ask/full` 两种模式下的原生审批 → Deferred → 业务任务入队 → 模型完成链路（单件及批量）；验证无需执行远端写入即可完成 AI 回合，后续平台超时不会唤醒已完成回合。大组合回归覆盖 198 个 SKU、超过 256 KiB 的页面载荷、大同步回执，以及直接工具和 Python 一次读取首项、完整成员分页无重漏；断言 AI 读取不调用页面完整载荷或加载历史任务。详情读取覆盖直接工具与 Python 并发调用、显式/省略平台参数，以及脚本缺少导入后由原生重试恢复的完整工具结果校验。模型和平台 I/O 使用受控替身，不把这些结果当成真实店铺写入或任意模型理解能力的证明。

Yandex、Mercado 复用已接通的在线服务；Ozon 的现有 API 权限阻碍不会因开放工具而消失。工具保留真实授权失败与能力限制，不把未取得数据描述为零库存。

2026-09-27 验收：后端全量 1897 项测试、47 项子测试通过；在线 AI 专项 23 项通过；前端 59 个测试文件、473 项测试通过，`vue-tsc -b`、Vite 构建、相关模块编译及 `git diff --check` 通过。构建保留既有大 chunk 提示。

本地后端已重新加载；通过生产 ToolSet 和只读 Runtime 验证 Yandex 200 件、Mercado 91 件及 Ozon 授权失败状态，后端 5050 与前端代理 3000 均返回 HTTP 200。重载及只读检查前后 291 件在线商品数据库快照哈希一致，未执行真实平台商品修改；本轮未进行真实模型自然语言或浏览器点击验收。

同日修复详情读取的 `TOOL_OUTPUT_SCHEMA_INVALID`：详情分支的顶层 `platform` 默认 `null`，原编译器把可空枚举展平后错误地拒绝该值。编译器现在保留 [Pydantic 生成的原生 `anyOf` 分支](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)，由现有 Runtime 验证；通用测试覆盖可空多值 Literal、单值 Literal 和 Enum 的合法/非法输入与输出。继续使用 Pydantic AI 原生工具校验、重试和 Code Mode，没有新增恢复机制或提高重试上限。

使用真实 HTTP 只读详情重放故障对话的原始脚本：漏导入的首次调用仍由原生机制要求纠正，补正后的第二次调用成功返回 2 件商品；原始补传平台参数的脚本也成功。回放使用受控模型和临时数据库，原对话历史及真实商品快照未修改。

修复后验收：后端全量 1921 项测试、47 项子测试通过；Catalog 与在线 AI 专项 57 项通过。模块编译和 `git diff --check` 通过；本地后端重新加载成功，故障查询仍返回 2 件商品，重载前后真实商品快照与原对话历史一致。

2026-10-05 有界查询修复验收：后端全量 2197 项测试、47 项子测试通过，变更文件编译及 `git diff --check` 通过。真实 Yandex 数据使用只读 SQLite 连接核对：原页面响应 1,765,907 字节，首项摘要 1,939 字节；首个组合 198 个成员按 25 条分 8 页读取，最大一页 24,430 字节，身份和顺序与页面一致，无重复或遗漏。未修改真实商品或历史对话。生产装配的直接工具与 Code Mode 使用受控模型验证一次读取首项及完整分页；这不代表真实模型自然语言行为已验收。开发服务已重启，后端、前端及前端 API 代理均返回 HTTP 200。
