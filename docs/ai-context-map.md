# AI 上下文地图

本文是按需查阅的维护索引，记录当前入口及不明显的业务边界；全项目开发约定见 [AGENTS.md](../AGENTS.md)。字段和函数签名以代码及 Schema 为准，当前依赖版本见 `requirements.txt` 和 `front/package.json`；下文带版本的核对记录只表示当时采用该能力的依据。

文档作用域：

- 当前领域契约：见各节链接的 SKU、AI Work、在线商品、外部请求、订单和物流说明。
- 历史方案与验收：类目 Schema 分离、币种迁移、原生 Agent 重构及旧 Global Task 验收记录只用于追溯；其中的实施步骤、未勾选清单和数据删除授权不作为当前任务指令。
- 产品 Agent 指令：`erp_web/assets/systemAgents.md`、`erp_web/assets/prompts/` 及服务中的运行时指令由产品加载，与开发规范分开。

## 主 Agent 的通用 Python 执行

`erp_web/services/ai_code_mode.py` 装配官方 CodeMode、原生调用额度检查和脚本输出限制；
`ai_agent_factory.py` 依据 Execution Profile 接入，主对话启用。
`DirectAndPythonToolset.get_tools` 通过官方公开扩展点保留业务工具直接入口，并提供可选的 `run_code`；
简单操作直接调用，循环、转换和计算才使用 Python。两种入口复用同一个 Tool Bridge 和业务执行边界，
调用、REPL、审批与生命周期仍由官方实现负责，不另建数据接口或业务批量 DSL。
当前依赖为 Pydantic AI 2.44.0 / Harness 0.34.0 / Monty 0.0.23；旧条目中的版本号记录当时核对依据。
职责、限制、原生机制选型及验收见 [通用 Python 执行](agent-python-execution.md)。
脚本对外结果通过原生输出 hook 转为 JSON 可编码值，避免 `type(...)` 等 Python
对象使整轮历史编码失败；内核与业务回执不变，版本缺口与移除条件见[共用业务数据读取](business-data-access.md)。

`ai_tool_bridge.py` 将已有输出 Schema 通过原生 `Tool.prepare` / `ToolDefinition.return_schema`
交给 CodeMode 生成返回类型；`ai_tool_contract_description.py` 只补充 Python 签名不显示的参数约束说明。
`draft_attributes_read(scope=sku)` 默认读取全部已选启用 SKU；`sku_ids` 用于局部核实，
任一 ID 超出已选启用范围即报错。分页在指定范围中过滤后进行，`sku_count` 为该范围总数，
可用 `limit` 和 `next_offset` 主动分段。
返回 `updated_at` 与具名 `DraftAttributeSku` 字段，脚本无需为了猜测结构反复读取。
`schemas/draft_changes.py` 定义成组局部修改契约；`runtime_units/draft_changes_capability.py`
提供 `draft_changes_apply`：同一草稿和平台/站点的公共/SKU 属性及库存可混合，平台校验在商品锁外，
锁内重读版本、范围和状态，全部通过后只调用一次 `save_draft_content`。
`category_attribute_updates.AttributeUpdateValidation` 在一次提交内复用类目定义及已核验候选；
`product_attribute_patch.py` 由单项和成组工具共用纯属性合并规则，不包含脚本执行或模型循环。
SKU 编排按本轮属性集合合并，避免按颜色、规格分别重读整批；原生 REPL 可保留读取结果，
跨脚本使用保存回执中的实际值与新版本。相关修改完成后统一核价、预检，窄任务不扩展。
回归见 `tests/test_draft_changes_capability.py`（多属性单次读写及跨脚本复用）和
`tests/test_main_chat_attributes.py`（局部读取、分页与范围校验）。这些受控模型测试验证
执行契约，不保证任意真实模型都遵循最少调用路径。


内部业务通过 `ProductStore.load_draft_content` / `save_draft_content` 读取和保存规范化内容；
后者是草稿保存规则的唯一实现，保留互斥、版本冲突、归一化和发布状态失效检查。
`load_draft_detail_from_index` / `save_draft_detail` 为页面薄包装，另行生成商品上下文和列表索引。
属性、图片、文案、核价及 SKU 包装/勾选操作使用内容入口，避免逐项扫描全库页面列表。

## 共用业务数据目录

`business_query_composition.py::BUSINESS_QUERIES` 是普通只读查询的单一入口：一份
`BusinessQuery` 定义自动派生 `business_query_routes.py` 的 GET handler、共用数据目录
和主 Agent 查询权限，不再分别登记页面、AI 或覆盖清单。默认普通路径为
`/api/business-queries/{name}`；原公开路径通过 `http_path` 保留。查询声明显式绑定真实
Store/service，发现时不执行 loader；不扫描包或依赖装饰器注册。`services/business_data.py` 统一处理发现、字段投影、过滤、分页
和 12,000 字符预算，保留逐项展开路径。HTTP `/api/business-data/catalog`、`/read`
与 `business_data_catalog` / `business_data_read` 共用同一目录，权限与空间由可信作用域决定。
报单货代配置 `logistics.settings`、`fulfillment.settings` 与设置页共用本地查询；枚举/合作
目录不等于已保存承接规则。公开业务结果不包含内部授权身份指纹，读取不刷新远端。
原运费/仓库/核价与报单查询之外，普通设置及索引/发布只读入口也已迁移；原生 Agent Profile 与 ToolSet 的查询权限均从同一组合根推导。
`business_query_inventory.py` 对每个配置段及 GET 记录查询 owner 或保留边界，见
[覆盖清单](business-query-coverage.md)。`services/config_queries.py` 由原 Store 生成不含凭据
及本机连接的公开业务视图，不刷新远端。
`services/pricing_data.py` 读取已保存和短期预览的原始核价结果，不触发模型或重新核价。
Yandex 新核价保留实际筛选与整组选仓依据；历史缺失依据不推测补造。
开发边界、字段展开、预算与验证见[共用业务数据读取](business-data-access.md)。

## 运行时边界

### 个人账号与空间隔离

- `accounts.py` 是交互式管理员初始化入口，先备份现有数据库、配置和素材；原目录绑定初始管理员，业务 ID 不变。账号库为根目录 `accounts.sqlite3`，新用户空间位于 `users/<id>/`。部署见[账号版本切换](server-deployment.md#账号切换记录)。
- `schemas/accounts.py`、`stores/account_store.py`、`services/account_service.py` 分别持有账号形状、SQLite 事实与 Argon2/限流业务。账号路由经 `account_facade.py` 编排，不加入模型工具清单。
- 公开注册发码由 `services/registration_verification.py` 校验 Turnstile 后发送 SMTP 邮件，账号库在建号事务内消费验证码；管理员建号仍走独立的角色校验入口。
- `schemas/newapi.py` 持有交接的共享形状；`services/newapi_bridge.py` 处理固定 New API 服务的自动登录票据、敏感验证与账号联动；`newapi_http.py` 是不记录凭据或票据的账号控制面传输，不使用业务请求历史。普通入口经当前会话与 CSRF 签发 60 秒票据，通过来源绑定的表单 POST 自动建立原生会话；Electron 主进程经本机一次性页面交接。敏感操作仍需重新验证 ERP 密码，已有 New API MFA 不降级。
- New API 票据和待同步撤销保存在账号库；退出只关联当前设备，改密撤销全部关联会话，启停保持顺序重试。网络失败不撤销已完成的 ERP 操作，HTTP 返回 `newapi_sync_pending`，账号扫描器每 30 秒重试。内部交换、会话核验和运营绑定查询只接受独立服务凭据，不接受浏览器 Cookie；不实例化个人业务上下文。
- `server.py::ERPHTTPServer` 为网页并行请求保留连接接入队列，账号 HTTP 回归复用同一入口。`Handler` → `services/account_request.py` 先检查来源、会话、CSRF 与角色，再由 `account_runtime.py::AccountRuntime` 取得业务上下文。客户端业务 ID、owner 和桌面连接标识不能选择空间；账号操作和外部回调不消费业务 presentation reservation。
- `context.py::bind_context` 用 ContextVar 绑定请求及后台执行，线程、线程池和 detached AI 协程延续原空间。默认上下文用于启动与离线入口，不在并发用户间切换。`AccountRuntime` 统一恢复与关闭资源，注册不启动领域 worker。
- 新用户只使用 `assets/account-defaults/` 的公开模板，不读取管理员 `.env` 或个人模型环境密钥。`workspace_files.py` 限制素材和模板路径；文件、AI 流、图片上传均受当前空间约束。
- 新平台回调带 owner 或归属 state，目标空间继续校验 token/state/PKCE；旧回调定位初始管理员。Electron 的 `account-session.cjs` 在主进程管理内存 Cookie，`desktop_connection_service.py` 校验空间和会话，失效时断开，不重放已发送操作。

完整范围和验收见[个人账号实施方案](personal-account-management-plan.md)。

### 配置与授权

`ConfigStore` 统一持有应用、店铺、跨境巴士、订单回调和微信绑定的授权读写入口；
密钥本体集中于主库 `store_auth` / `runtime_secrets`，业务 Store 不保存重复凭据。
1688 跨境 ERP 服务商应用及跨境巴士企业密钥由管理员在「系统设置 → 管理员共享设置」维护，保存至根库
`runtime_secrets / enterprise_applications / active`；尚未保存时可读取后端环境变量或既有根库凭据。
`ConfigStore.crossborderbus_credentials/save_crossborderbus_credentials` 负责企业密钥与用户授权绑定，
首次管理员保存会显式接管当前配置。凭据变化使用新的配置代次，旧 Token、待完成授权及 `app_uid` 失效，换回旧密钥也须重新授权。已有主库密钥保留读取及同值配置升级能力。
普通用户授权接口只接受账号和密码；配置方式及桌面分发边界见
[跨境履约说明](crossborder-fulfillment.md#开发入口)。
微信渠道库只保存主库凭据引用，收发队列、代次和游标仍属于渠道事务。
物流地址与履约规则保留在订单库，以维持订单创建时的事务校验。
旧数据通过 `scripts/migrate_credentials.py` 离线备份迁移；`server.main` 在后台任务启动前
调用 `assert_credentials_migrated`，拒绝未迁移布局。存储清单、迁移与恢复边界见
[配置与授权存储](configuration-storage.md)。

### 管理员共享设置与个人费率

- `AccountRuntime.context_for` 将每个空间的 `ConfigStore.shared_config` 和 `AppContext.shared_paths` 绑定到根配置与路径，客户端不能选择共享 owner。仅汇率 API 配置、禁售名单和企业应用使用此共享来源。个人核价模板、手动汇率、店铺授权与 Mercado 应用配置仍由个人持有。
- GET/POST `/api/admin/settings` 经 `managed_settings_facade`、`schemas/managed_settings.py` 和 `ConfigStore.managed_settings/save_managed_settings` 管理企业应用及汇率 API；请求带配置 revision，冲突拒绝整次更新。企业密钥只返回是否配置。通用设置和 AI 设置不能改写汇率 API，普通配置视图不下发该地址；汇率缓存记录来源，切换 API 不复用旧来源缓存。
- `AppContext.tariffs` 装配 `TariffSettingsService`。共享费率位于根 `data/tariffs`，普通用户覆盖位于其空间 `data/tariffs`，根管理员的个人覆盖位于 `data/personal-tariffs`。按平台选择来源，未自定义者自动跟随；自定义表记录 `customized_from`，管理员版本改变时提示更新，用户显式确认覆盖后恢复跟随，原版本文件保留。
- GET `/api/shipping/tariffs`、POST `/import` 与 `/upgrade` 位于 `pricing_workspace_routes`。仅管理员可写 shared scope，所有用户可写 personal scope。导入复用模块 XLSX 解析和差异比较，限制文件/解压大小，校验表头、单位、明确数值和完整规则，失败保留生效版本。预览后显式发布，写入与升级均核对预期版本，尚未生效日期拒绝发布。
- 核价 `ShippingModule`、Yandex 发布仓运输模式和 `draft_pricing._pricing_environment` 使用同一份有效费率来源；单次核价固定快照，更新后旧预览须重算。发布校验会比较已保存报价的运费版本与当前有效版本，单次 SKU 批次由 `PublishValidationSession` 固定版本读取，历史报价保留展示。当前导入支持 Yandex FBS 与 Ozon OGL，Mercado 按个人店铺授权调用远端试算。
- GET `/api/shipping/tariffs/rules?platform=yandex|ozon&scope=effective|shared` 提供已导入有效规则。读取范围由会话空间及共享 owner 决定，不接收 owner、路径或任意版本；响应只含文件名和公开规则。按计算器口径展示原币固定费／每克费、重量、货值、包装及准入规则，轻小件限制从同一政策快照读取。前端 `TariffRulesDrawer.vue` 支持当前表、管理员默认表、搜索和分页。
- 前端入口为系统设置 `settings_section=managed`（仅管理员）和 `settings_section=tariffs`（所有账号）；`TariffUpdateNotice` 在核价页提示个人表可升级。禁售名单所有账号只读，原导入入口单独校验管理员角色。
- 真实双账号权限、共享读取、凭据换回仍失效、导入校验和升级冲突见 `tests/test_managed_settings.py`。

### 1688 应用与账号授权

`ConfigStore.alibaba_credentials/alibaba_application/update_alibaba_credentials` 管理应用凭据、买家授权和一次性登录请求，
首次读取保留并迁移旧 `1688_api` 凭据；通用配置和采集入参不能覆盖凭据或网关。
`services/alibaba_authorization_service.py` 独占官方登录链接、授权码交换、买家身份核验和 Token 刷新；
采购、订单物流及 API 采集经 `alibaba_runtime_config` 取用。公共状态不发起远程请求。
`facades/alibaba_authorization_facade.py` / `http_route_units/alibaba_authorization_routes.py` 提供
GET `/api/alibaba/authorization`、POST `/api/alibaba/authorization/start`
及 GET `/auth/alibaba/callback`；仅该回调的顶层 GET 导航允许跨站，仍必须校验服务端单次 state。
`AlibabaSettingsPanel.vue` 打开浏览器授权，在等待期间查询服务器状态，并显示完成授权的账号名称。
管理员可将完整回调地址 POST 到 `/api/alibaba/authorization/complete`。该接口绑定登录管理员的业务空间，复用同一组单次 state 和授权码交换校验。
新采购归属按应用与官方买家身份固定，刷新 Token 不改变历史范围；旧 Token 的范围摘要原样保留。
商品查询、采购和物流共用当前客户的跨境 ERP 授权（生产 AppKey 4848173）。旧客户端的跨境授权路径映射到同一服务。
已退役的企业自用凭据仅归档，旧采购归属不会被新的应用接管。商品详情通过 `product.search.queryProductDetail` 读取。
部署、迁移限制、协议依据及验证见 [1688 授权](alibaba-authorization.md)。

### 桌面与统一业务后端

网页和 Electron 共用 Vue 业务界面与统一后端；前端程序资源跟随代码目录，`ERP_APP_DIR` 仅选择业务数据/配置目录。`front/desktop/main.cjs` 只提供前端代理、
本机执行器生命周期和当前桌面连接；`runtime.cjs` / `connection.cjs` 分别持有进程管道和后端等待通道。
`desktop_runtime/__main__.py` 不导入 `erp_web`、数据库或后台业务服务。
浏览器/CDP、Gemini 登录和 CLI 实现在 `desktop_runtime/`；无副作用契约、站点识别和页面提取在 `erp_shared/`。

`http_route_units/desktop_routes.py` → `facades/desktop_facade.py` →
`services/desktop_connection_service.py` 只做进程内调用关联。普通调用不创建领域 Job；
断连、取消和期限终止等待，已发送操作不重放。HTTP 的当前桌面绑定由 Electron 代理注入；
图片批次沿用原 `copy_context`，原生工具审批/Deferred 的 metadata 只补充任务执行需要的连接引用，
现有 Job 和 Agent 生命周期仍由原 owner / Pydantic AI 负责。

当前端可用性由 `services/operation_availability.py` 按实际模型绑定和当前请求的桌面连接计算。
`GET /api/operation-availability` 返回限制原因及需要桌面的模型用例。网页通过
`utils/desktopHandoff.ts`、`desktopHandoffDirective.ts` 接管原功能入口并直接唤起桌面，配置问题在原地展示。
新增本机动作继续经 `call_desktop` 投递。业务工具需要按端筛选时，在应用装配的
`AiToolBinding.unavailable_reason` 声明依赖；原生工具发现和运行时复核共用该判断。
混合工具按参数检查，例如链接采集仅在浏览器模式需要桌面。后台长任务在创建前检查，
按已存在的请求 ID 查询或恢复原结果仍可用。

网页到桌面的页面交接由 `desktop_routes.py` → `desktop_facade.py` →
`desktop_handoff_service.py` 管理，POST `/api/desktop-handoff/{create,status,resolve,acknowledge,installers}`
全部经过账号与请求体验证。短期请求属于来源账号与网页会话，领取和确认绑定实际桌面连接，
只恢复页面、编辑内容及对话输入。`front/desktop/deep-links.cjs` 验证 `champion-erp://continue`
所属站点与配置一致，主进程处理冷启动、macOS `open-url` 和 Windows `second-instance`，preload 仅让主页面接收编号。
`DesktopHandoffNotice.vue` 在登录后接收，所有页面内容恢复完成后才确认，未确认时显示重试与安装入口。
安装包列表复用 `desktop_version_policy.RELEASES` 下发布清单，文件由现有 Nginx 发行目录提供。

每条聊天输入的设备引用保存在服务端收件箱。主 Agent 在模型请求边界应用新输入，
通过原生消息的输入 ID 恢复来源；同批混合来源不授予本机能力。审批和领域 Job 只用原调用
的 Deferred metadata，审批窗口、后台扫描和微信连接的启动端都不能换设备。

`source_collect_workflows.collect_browser_url` 调用本机 `collect.read`，后端才读目标商品、
规范化、登记图片并经 `ProductStore` 保存。商品采集仅接受浏览器与 API 两种方式。浏览器模式支持单链接和批量队列。API 采集通过 `/api/collect-1688` 提交商品链接及 `mode=api`，后端从链接解析商品编号并使用当前账号授权。
`/api/collect-verification` 检查原页面验证，`/api/collect-resume` 在验证完成后沿用原桌面连接读取该商品页。
采集素材逐张交接，`desktop_asset_service` 复用图片服务生成后端引用。
Browser/CLI Provider 与后端 Canvas 适配器只调用当前桌面；API Model / Direct / Factory 不变。
`desktop_model_config_service` 维护业务连接引用和指纹，具体命令、资料、代理由本机配置 owner 管理；
配置写入只通过来源受限的 preload IPC，后端调用通道不能修改本机命令。

`scripts/build_desktop_runtime.py` 和 `scripts/desktop_runtime.spec` 构建本机执行器。
`requirements-desktop-build.txt` 引用运行依赖 `requirements-desktop.txt` 并指定打包工具。
桌面构建和服务器部署共用 `scripts/install_release_dependencies.py` 安装各自的 Python 依赖并复用本机 wheel 缓存。
`scripts/migrate_desktop_business.py` 提供先备份的离线一次性迁移与关联检查，浏览器资料留在本机。
统一发布等待由 `scripts/release.py` → `deploy/release_server.py` 协调，服务端入口为
`erp_web/release_guard.py`。新增能启动业务工作的入口须在领取前检查准入；已接工作的异步分支须在提交到线程或队列前登记，
直到真实工作返回。排队和线程交接期间同样受保护。可恢复的外部确认由原领域服务处理，平台待确认状态本身不占用发布等待。
运行细节与首次接入步骤见[统一编译与发布](server-deployment.md#统一编译与发布)。

统一发布默认分发可选桌面更新，构建用 Mac 同样通过客户端升级。`--server-only` 跳过桌面打包和分发。
桌面版本发布由 `scripts/release.py` 和 `deploy/publish_desktop.py` 写入同一套平台/架构清单，Nginx 独立提供安装包。
`front/desktop/updates.cjs` 负责启动检查与下载，`services/desktop_version_policy.py` 在已上报版本的桌面新提交上执行最低版本规则。
桌面回执和网页继续使用原入口。发布与撤回见[桌面更新发布服务](server-deployment.md#桌面更新发布服务)。

部署、配置、迁移门禁和隔离验收见 [桌面与统一后端](desktop-packaging.md)；当前服务器、访问通道及备份位置见 [服务器部署记录](server-deployment.md)。

### 微信 ClawBot 对话窗口

`services/chat_conversation_service.py` 是网页和微信共用的输入接收、归属、取消和原生运行投递入口；
`vercel_ai_ui_service.py` 只保留网页协议适配，领域 Job 恢复不再依赖 Vercel 编码。
`services/wechat_channel_service.py` 通过固定版本 `wechatbot-sdk` 转发文字，不提供业务工具或独立 Agent。
`stores/wechat_channel_store.py` 用独立库保存渠道收件、游标与回复投递，原生消息仍是唯一对话历史。
`AgentRunStorage` 复用原生 `before_node_run`，在工具节点前保存已完整生成的中途正文，
`finish` 将最终回答标记随历史同次保存；原生消息仍是唯一事实源，不观察逐字片段。
历史提交和运行结束后的进程内通知唤醒渠道，按设置生成文字投影，失败只重试投递。
五类转发选择由 `schemas/wechat_channel.py` 定义，保存于 `app_config.system_settings.wechat_forward_types`；
关闭某类停发该类排队内容，不影响输入、工具执行、审批或 `/new`，重新勾选不补发。
绑定接口位于 `http_route_units/wechat_routes.py`，只供受信设置界面使用，不作为 AI Capability。
扫码确认后自动创建空普通会话，可信归属保存在渠道库；不要求选择旧会话，也不伪造消息或运行。
微信独立 `/new` 直接新建并切换绑定，保留接收游标和后续收件，旧历史及已提交任务继续由原 owner 管理。
范围、SDK 适配缺口、恢复语义和验收见 [微信对话窗口](wechat-channel.md)。

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
  本机 Chrome 的版本检查、标签页查询/创建及采集验证轮询统一经 `desktop_runtime/browser_debug_http.py`；
  只访问固定回环地址的 CDP 接口，禁用代理与重定向，保留任务取消、期限及响应大小限制。
  这些本地控制请求不读写外部请求审计或阻断；已有历史记录保留，但不再影响浏览器连接。
  远端网页和图片下载仍经外部请求管理器，不按 IP 对业务请求作通用豁免。
  AI 推理经 `ai_provider_catalog.py` 使用原生 Provider/SDK 客户端，不接入业务请求排队、限流、熔断或写入未知阻断。
  SDK 使用原生有限重试，模型超时、Agent 总任务期限与取消仍有效；AI 调用的业务工具仍经过业务请求管理器。
  专项传输诊断已退役，保留常规运行错误与原生消息历史；既有请求审计和诊断历史仍可读取。
  AI HTTP 402 按 `*_PAYMENT_REQUIRED` 记录余额或计费问题，不自动重试。
  Pydantic AI 仍独占模型/Agent 生命周期，不新增模型请求旁路。
- `services/platform_request_policy.py` 按平台响应信封与具体接口纯解析 HTTP 和业务拒绝，不通用推断任意 JSON 的 `success` / `status`。
  1688 物流查询 HTTP 200 的 `500_2` 为正常空物流；请求级业务拒绝只保护原请求，不累计接口故障熔断。
  未声明的业务协议由原适配器解释，传输审计成功不代表业务成功；HTTP 限流、权限与服务故障仍统一处理。
  `stores/external_request_store.py` 用独立
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

- 采集箱推到草稿统一从 `/api/claim-products` → `collect_facade.claim_products_payload` → `collect_helpers.claim_products_to_platforms`，请求必须显式携带 `product_ids` 与 `targets`（平台、销售市场、注册语言）。每个商品按语言创建一份独立草稿，市场只包含该语言下勾选的项目；美客多销售市场按授权物流操作归入 CBT 的 `sites_to_sell`。响应的 `claimed_count` 统计成功商品数，`draft_count` 统计实际创建草稿数。
- 顶部批量与单行操作共用 `claimProductsToDrafts`，只传各自商品 ID 和顶部市场选择，不使用当前平台或整库兜底。AI 认领与市场准备任务仍使用其显式平台参数，复用同一个商品复制、草稿持久化循环。

- `source_sites.py` 的采集质量门槛与核价/发布条件分开：1688 有标题和图片且未被验证拦截即可入库。缺失包装长宽高、重量通过 `collect_helpers.py` 的 `missing_fields` / `next_action` 提示在 SKU 页补齐，不触发采集失败；有规格时逐 SKU 判断包装完整性，不用首项资料代表整组。
- `front/src/views/workflow/CollectView.vue` 组织浏览器采集（URL 批量）和 1688 API 采集，`CollectBatchManager.vue` 负责 URL 增删改、状态筛选和失败重试。网页在提交 URL 批量采集时交接链接、队列与操作范围，桌面恢复并确认接收后直接执行，页面查看和编辑不触发交接。
- 批量列表状态为 `pending`（未开始）、`running`（正在采集）、`waiting_verification`（等待验证）、`success`（完成）、`failed`（失败）。部分结果保留资料提示并显示失败。
- `front/src/stores/workflow/actions/collection.ts` 逐条提交 `/api/collect-batch`。遇到登录或验证码，后端在解析、下载、商品写入之前返回 `waiting_verification`；其 `verification` 契约由 `schemas/collection.py` 定义，仅含标签 ID、原商品 URL、平台。
- `/api/collect-verification` 由 `collect_routes` → `collect_facade` → `source_collect_verification.inspect_collection_verification` 只读检查原标签。前端每两秒检查一次，不导航、不刷新；验证完成后用原标签 ID 和 URL 调用 `/api/collect-resume`，再次验证页面身份并采集，然后继续后续链接。标签被关闭、离开商品或无法读取时返回可操作提示，不改采集目标。
- 用户可以取消等待，未开始的链接保持原状。`collectQueue.ts` 本地保留等待项的标签 ID 和 URL，刷新应用或取消后点击开始采集可恢复；采集应用关闭后不承诺后台执行。不保存凭据或整份商品。正在实际采集时刷新仍标记结果未确认，避免假报成功。删除列表行不删除入库商品。
- 浏览器标签页选择与独立保存 HTML/截图的操作已移除；URL 采集仅使用指定商品页，验证码页不会作为部分商品写入。
- 上述等待是 ERP 浏览器采集的领域交互，不创建 Agent run、审批或消息协议，不涉及 Pydantic AI 生命周期。
- `desktop_runtime.collection.target_for_url` 复用相同 URL 或同一 1688 offer 路径的页面，后者允许验证后查询参数变化；其他商品仍通过 Chrome DevTools 的 `PUT /json/new` 创建目标页。打开操作直接返回目标句柄，采集不重复查找或强制刷新，保留人工登录和验证后的页面；协议依据：[Chrome DevTools HTTP endpoints](https://chromedevtools.github.io/devtools-protocol/)。
- 1688 采集与浏览器 AI 共用 `desktop_runtime.browser_debug_service.ensure_browser_debug_session`：检测连接、查找本机 Chrome/Edge、使用指定资料目录启动并等待调试端口。调用方分别提供目录和端口；Gemini 默认使用 `browser_profile/ai/gemini/<profile>`，不共用 1688 登录资料，也不依赖 BitBrowser。
  同一调试端口的任务与收尾串行。采集读取完成后关闭已消费的任务标签；还有其他网页时保留实例，人工验证及正在操作的登录窗口保留原页。最后一个任务页完成后退出专用浏览器。托管网页 AI 在整次本机调用结束时关闭，人工登录例外；退出桌面执行器回收本次持有的实例。关闭前核对 Profile 的实例标识，确认退出后只清网页、代码和图形缓存，保留登录、站点存储与扩展。外部浏览器的生命周期由用户管理。
  `browser_connection_config` 纯校验代理地址；浏览器 AI 的可选 `browser_proxy` 只在专用浏览器启动时生效，修改后需关闭专用窗口再重开，不更改采集或后端网络配置。

## 草稿描述

- 商品采集和主档只保存标题、属性、SKU、图片等事实资料，不采集或维护描述与独立卖点；采集诊断不再统计描述。新建草稿的描述为空，不从来源继承，也不拼接模板描述。
- `services/copy_service.py` 根据标题、商品/来源属性和 SKU 规格生成平台草稿 `description`，允许在草稿中人工编辑；发布与生图读取草稿文案。缺少事实依据的优势、用途、认证等不得编造。
- `stores/product_description_migration.py` 在数据库读取边界移除历史商品/来源描述及卖点，保留各草稿的描述，历史草稿卖点并入各自描述。保存后仅写入当前契约；不改变 SQLite schema，也不改写发布历史。
- 文案生成继续使用 Pydantic AI 原生 `PromptedOutput` 和输出校验，没有新增 Agent 基础设施。

## SQLite 数据库版本边界

- `erp_web/db.py` 是 schema 与版本门禁的唯一 owner，当前 `SCHEMA_VERSION=17`。
  真正空库才自动建表；已有库只接受完整当前结构，运行时不修补、不升级。
- 完整 v15 库使用 `scripts/migrate_online_products.py <数据库>` 显式升级到当前版本。
  脚本先创建权限 0600 的 SQLite 一致性备份，再在事务内校验原结构、添加在线商品两表及索引。
  商品、草稿、授权、发布及 Agent 持久数据全部保留。其他版本或残缺结构在写入前拒绝。
- 完整 v16 库使用 `scripts/migrate_online_operations.py <数据库>` 升级到 v17：锁定写入并创建权限
  0600 的一致性备份后，仅将在线商品唯一占用索引缩小到 `queued/running`。不修改商品、历史任务、回执或幂等键。
- `upc_pool.json` 是已购买 UPC 的显式资产导入，不属于 schema 迁移。
- 2026-09-28 请求管理开发中间版本曾将 `external_*` 审计表写入 v16 主库。
  `scripts/migrate_external_request_audit.py <主库>` 仅识别这套完整遗留结构：先保存权限 0600 的
  一致性备份，再迁到独立审计库；目标提交成功后才移除主库审计表，并升级在线占用索引到 v17；业务数据不变。
  运行时不会自动迁移；结构拒绝信息列出多余、缺失或定义不一致的对象，不再把同版本结构差异误报为版本过旧。

## 统一任务队列

- `/publish`（原导航键保留）展示「任务队列」，首期包含商品发布、店铺商品同步和在线商品修改。`TaskQueuePanel.vue` 每五秒读取本地投影，支持来源、页面/AI、状态、平台、店铺、操作、日期和任务/商品搜索；不由页面触发平台轮询。
- `http_route_units/task_queue_routes.py` → `facades/task_queue_facade.py` → `services/task_queue_service.py` 为统一入口。`stores/task_queue_reader.py` 直接读取原领域结果；`schemas/task_queue.py` 定义任务视图和查询策略。
- 原发布/修改任务在外发前保存，原任务号继续作为统一请求 `operation_id`，发起方式同时冻结。确认计划和已用预算单独保存在 `task-confirmations.sqlite3`，不复制业务回执或创建第二套业务终态。
- `TaskQueueService` 在启动时运行后台有限确认；自动和人工确认共用账号占位与 30 秒冷却，自动计划有效时共享剩余轮数、真实 HTTP 请求数及截止时间。自动确认停止后，显式人工核验使用受限的单轮请求额度与期限，不清零累计用量或重启自动计划；近期回读证据仍由原领域服务校验。`request_operation.before_send` 在每笔请求发出前持久扣减预算，继承单轮期限与取消约束；统一请求管理器继续负责平台配额及阻断。
- 同步成功直接完成，只有 Ozon 发布、Yandex 发布/价格/内容、Mercado 真实异步任务及可定位的未知结果进入确认。价格隔离立即转需要处理；确认到期/耗尽只停止查询，不改写为业务失败，不自动重放或解除未知结果保护。
- 查询结果回写原业务任务；发布终态回填暂时失败时后台仅恢复本地持久化，不重新请求平台。旧记录全部进入列表，旧确认标记不启动新的自动预算。自动查询中断后可重新领取过期租约，已停止的计划不会因人工查询或重启而恢复。过期任务可显式查询或在平台后台核对，在线修改人工结束继续使用原证据与版本校验。
- 旧发布列表/详情/分页 Store、页面专用 API 封装、展示类型及旧测试已移除；导航和入队不再额外加载旧发布队列。公开确认入口统一经过任务队列，不保留缺少队列时直接查询的分支。领域层只保留业务互斥、版本和证据校验，不再另设查询冷却或查询占位；历史提交状态、查询字段和回执继续读取。
- 在线商品页旧「操作记录」列表、详情及专用读取封装已移除，提交、同步进度和冲突提示通过 `open-task` 进入统一队列，`task_id` / `source` 查询参数定位原任务。同步进度仅读原任务，不进入确认调度；失败项重试和人工核对复用原领域接口。已有较新修改的旧失败任务不显示重试入口。
- 发布提交重启后仅标记 `interrupted`，不自动继续写入。`server.py` → `publish_adapter.mark_interrupted_publish_jobs` → `PublishingBus.mark_interrupted_jobs` 只更新本地中断状态和已有回填；已完整提交的任务继续原只读确认。任务页「重试未完成提交」通过 `POST /api/publish-bus/retry` → `publish_facade` / `publish_workflows` → `PublishingBus.retry` 显式继续原批准内容，累计执行次数并保留每次重试的日志；查询次数与执行次数分开。
- `SkuGroupPublishingAdapter` 逐 SKU 保存后续写步骤的发送边界和结果；中断时从真实 owner 的最新记录重建任务进度，结果未知项不重发。单项异常保留原计划、检查点及其他 SKU 结果；已受理项可独立只读核验，剩余明确未完成项等待用户重试。重试重新校验原批准绑定，不重置已有结果确认预算或截止时间。业务回归见 `tests/test_publish_manual_retry.py`。
- 查询策略数值与回归入口见 [任务队列](task-queue.md)。Ozon 在线写入、采购/支付/履约及未开放的 Mercado 商品族编辑不在本次接入范围。

## 在线商品管理

- 价格列的店名提示通过 `GET /api/online-products/yandex-shop-names` 按当前账号读取 Yandex 店铺目录；商品列表、库存仓名和历史快照均可独立使用。

- Yandex 在线属性编辑通过 `GET /api/online-products/attributes` 按刊登身份和版本读取类目字段、当前值与分页候选；
  AI 的 `online_products_attributes` 复用同一服务，从真实刊登解析类目，不接受模型指定或猜测类目。
  `OnlineProductService.attribute_options` 从真实快照解析类目，复用注入的 `CategoryCatalog`，不接受客户端指定类目。
  `OnlineYandexAttributes.vue` / `OnlineAttributeField.vue` 展示缺失必填项，支持枚举搜索、单/多值、自由文本及单位。
  `services/online_product_attributes.py` 在入队、AI 审批与实际发送前核对字段、候选 ID/原文、类型、数量、单位和约束；
  只读及分组字段不开放。枚举编号在页面及新提交中使用字符串，平台发送时转换为整数，历史数字编号继续读取。
  `product_model/online_attribute_model.py` 持有纯形状校验与属性合并，网络查询由服务负责。
  修改按属性替换完整值集合，保留未编辑属性；多值回读逐组核对。定义和候选不写回商品或草稿。
  现阶段只扩展 Yandex，其他平台继续按其已实现的编辑契约执行。
- 离线模板导出：`POST /api/online-products/ozon-export/preview` / `download` →
  `online_product_facade.export_ozon` → `OnlineOzonExportService`；只读取当前 Yandex 店铺快照，
  不调用平台 API、不修改在线商品或草稿、不进入发布队列。
  `ozon_category_template.py` 解析官方 XLSX 的隐藏元数据并替换商品数据区，保留字典、校验和样式；
  当前仅支持已核对的中文圣诞装饰品 CNY 模板，其他类目或必填契约变化显式拒绝。
  生成时重建预览并检查 `preview_fingerprint`，编辑只作用于本次请求的导出覆盖值。
  无品牌值取自模板元数据；中文型号只在整个已同步分组的俄文标题一致且无跨组重名时自动复用标题，
  不以选中子集决定型号。中文型号、完整字典中的无效品牌阻止导出；品牌字典不完整时提示到平台核对。
  前端 `OzonExportDialog.vue` 复用 `WorkspaceDialog`；列表仅增加选择列和筛选栏的“操作”菜单。
  字段与输出契约见 `schemas/ozon_template_export.py`，操作与验证说明见 [Ozon 模板导出](ozon-template-export.md)。
- `http_route_units/online_product_routes.py` 是列表/详情及同步、单件状态刷新、修改、回读、人工结束、失败重试的唯一 HTTP 入口。
  `facades/online_product_facade.py` 负责请求转换；`facades/online_product_factory.py` 显式装配三个平台适配器。
- `POST /api/online-products/change-batch/preview` / `change-batch` 经 facade → `OnlineProductService` →
  `services/online_product_batch.py` 处理 Yandex 所选商品的停售、恢复销售、归档和恢复归档。
  预览只读本地：父节点展开当前目录全部 SKU，不受关键词和销售状态筛选影响；单件选择只包含该 SKU，重叠去重。
  预览返回实际商品数、SKU、版本及不可执行原因；提交固定这份目标清单，不在执行时扩大范围。
  同账号批量校验与 `OnlineProductStore.enqueue_batch` 短事务原子入队，仍使用逐 SKU 的 `online_jobs`、版本、冲突及幂等约束。
  批次标识保存在任务 JSON，不新增数据库结构；批量重试键绑定完整目标、版本与变更，HTTP 回执只返回精简任务引用。
  原有 worker 按批次领取，Yandex 停售/恢复最多 500 SKU、归档/恢复归档最多 200 SKU，可跨商品合并，超量拆批。
  `YandexOnlineAdapter.read_many` 复用按目标目录及批量详情读取；写前再次逐项校验，单件冲突不发送该项，其他有效成员继续合批。
  `dispatch_batch` 在一个短事务中保存全批写前日志和停售意图，网络等待在事务外；逐项平台回执保存回原任务。
  逐项拒绝、超时和中断保持对应失败/未知语义，已经发出的整批不自动拆成单件重发；回读与明确失败重试继续使用原入口。
- `services/online_product_service.py` 编排 ERP 领域任务，通过注入访问适配器，不反向导入 runtime。
  `POST /api/online-products/refresh-detail` → facade → `refresh_detail` 只读取指定刊登详情；本地列表与 GET 详情不外发。
  页面打开 Yandex 商品时显式调用一次；AI 的 `online_products_read(id=...)` 自动取得当前完整详情，含最新内容，再投影所需字段。
  `services/online_product_detail.py::OnlineProductDetailReader` 为自动读取与主动刷新共用的 owner；两者统一调用适配器 `read`。
  同一原生 Task/attempt、账号和 SKU 复用成功快照，并发查询共用正在执行的请求；快照变化、提交修改或新执行重新读取。
  标记仅在原执行 deadline 内保留；列表及 HTTP GET 不外发，不因字段为空隐式同步店铺，不增加自动重试或后台调度。
  服务在网络前后核对当前账号；`OnlineProductStore.update_detail` 在短事务核对版本、基础与详情时间、状态时间及活动任务，拒绝迟到响应覆盖新记录。
  `refresh_status` 直接查询单件状态；`POST /api/online-products/refresh-status` 不入同步队列，返回更新后的公开商品。
  `stores/online_product_store.py::update_status` 校验并发版本后合并状态、平台反馈及 `status_checked_at`，保留价格、库存、内容和完整同步时间。
  `services/online_product_sync.py` 消费 `OnlineSyncBatch`、保存目录记录与详情进度，详情失败保留旧业务快照。
  Yandex 的 `basic` 批次通过 `OnlineProductStore.save_basic` 保存基础资料、销售状态及卡片/店铺错误警告，保留旧价格、库存及其读取时间。
  `basic_synced_at` 标识基础资料读取时间，`synced_at` 标识完整详情读取时间；新商品为 `details_state=unloaded`，不伪造零库存。
  旧快照缺少新时间字段仍可读取，完整快照原时间保持含义；同步只读基础接口，不增加自动重试或失败回退查询。
  同步请求与持久任务保存 `catalog=current/archived`，默认普通商品；Yandex 每次只发现该目录，页面确认、最新进度和失败重试保持同一范围。
  指定 SKU 重试不混传平台禁止的归档过滤条件，读取目标目录身份后排除已移出原目录的商品；单件修改前读取与批量写入校验仍按明确 SKU 读取，不受页面目录限制。
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
  `catalog=current/archived/all` 在分组和统计前限定普通商品、归档商品或全目录，默认普通商品；
  `groups.feedback_summary` 汇总当前目录内整个组合的受影响 SKU 数、平台错误和警告条数，覆盖其他筛选隐藏的 SKU；
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
  `runtime_units/online_yandex_read.py` 负责目录分页、隐藏清单分页和每批 100 个 SKU 的基础状态/反馈读取，最多 3 个接口并发；
  逐页消费后即保存基础结果，不累积全店目录后再读取价格和库存。`online_yandex_snapshot.py` 只做响应投影。
  Business 库存模式按 v3 仓库 `models[].campaignId` 关联实际店铺：卡片及基础价按 Business 合批，
  店铺状态按 Campaign 合批、库存按仓库合批，保留各仓库独立的数量、状态与操作范围。
  `product_model/online_listing_summary.py` 是同步、回读及列表共用的状态汇总 owner；
  在售优先，其次为审核/建卡、待处理状态、停售和缺货；旧记录缺少其他仓库状态时显示待核实，
  不将默认店铺缺货代表全部仓库，也不依据正库存推断在售。列表按当前仓库范围重新汇总后再筛选和统计。
  库存列只显示当前仓库筛选范围内的数量合计，不嵌套仓库表；部分数量未知时展示已知合计及简短说明。
  单仓筛选仅做查询投影，不改写刊登身份、保存的库存范围或版本；修改库存仍明确选择目标仓库。
  `OnlineReadRequest.warehouse_id` 与页面仓库筛选共用此投影；页面去掉原店铺名称控件。
  `product_model/online_scope_model.py` 校验 `campaign:<id>` 只指向已核验的关联店铺；停售和独立价
  按该 Campaign 写入及回读，单仓停售不写全局停售意图。库存操作仍使用真实仓库 ID。
  已持久化的 `global` / `campaign` 旧任务继续指向原授权店铺，不重写历史任务的目标。
  单件详情与修改前核验统一使用 `read`，批量写入使用 `read_many`；写前核验独立执行，不复用 AI 取数跳过并发检查。
  Yandex 属性名称通过统一 `CategoryCatalog.attribute_definitions` 按类目关联，适配器一次运行内共用名称查询；
  名称查询失败保留属性值及平台反馈，并公开 `content.attribute_names_error`，不让整件详情同步失败。
  `online_yandex_snapshot.py::card_issues` 将卡片 `errors` / `warnings` 投影为具名 `PlatformIssue`，保留平台原文及补充说明；
  基础同步、完整读取、单件状态刷新及内容回读共用此投影，店铺错误以 `source=campaign` 保留。
  缺少目标卡片响应时查询失败，避免误清除旧反馈；单件详情成功不清除旧基础读取错误或更新基础状态的时间。
  `runtime_units/online_change_confirmation.py` 按修改字段和范围回读，不重复下载完整聚合。
  `runtime_units/online_product_status.py` 是三个平台的单件状态读取入口，限定目标身份，不扫描全店，不调用独立价格或库存接口。
  `online_mercadolibre_read.py` 先完整扫描目录，再用最多 3 件的并发窗口读取父商品及关联站点，保留 User Products mapping 校验；
  `online_ozon_read.py` 先扫描 ALL 与 ARCHIVED 目录，再每批最多 100 件读取详情与价格，`online_ozon_snapshot.py` 只做投影。
  三个平台统一产出同步批次，`marketplaces/online_sync.py` 仅提供目录占位和授权/限流中断规则，旧通用串行读取器已移除。
- Yandex 停售仍使用 campaign 级 `hidden-offers`；独立 `archive` 操作通过 business 级
  `offer-mappings/archive` / `unarchive` 归档或恢复，影响该商家全部店铺，保留库存与隐藏设置。
  `changes.archived` 必须为布尔值、`scope_id=business`；归档商品须先恢复归档才能调整销售状态。
  平台成功回执只列失败 SKU，单件无失败时直接 `confirmed` 并更新各店铺同 SKU 的本地目录归属；
  HTTP 200 中的逐项失败记为失败，未知回执/超时保留未知结果，手动核验只读取目标 `offer-mappings`，不重发写入。
  `OnlineProductStore.archive_state` 在短事务内校验租约、更新归档字段和版本，不改库存或完整同步时间；
  归档与同 business 同 SKU 的其他店铺执行互斥，未知归档只保护归档、库存及销售状态等相关修改。
  `OnlineListing` 从旧快照 `offer.archived` 读取归档事实并派生当前归档能力，无需数据库迁移或强制全店同步；
  单件状态刷新也读取目标归档状态，恢复归档不推断立即在售。协议见
  [归档](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/addOffersToArchive)和
  [恢复归档](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/deleteOffersFromArchive)。
- `schemas/online_products.py` 定义平台刊登、市场、具名价格/库存范围、平台错误/警告及变更契约；
  `platform_issues` 与 ERP 同步错误 `errors` 分开，旧快照缺字段时默认空列表；平台反馈不改变销售状态。
  属性展示名称、名称读取提示及平台反馈不参与业务内容版本；编号、值和单位仍参与并发校验。
  `stores/online_product_store.py` 独占 `online_listings` / `online_jobs`。远端商品不要求有本地商品或草稿，
  不伪造 publication，不修改 `ProductStore` 的归属。
- `marketplaces/online_buyer_links.py` 纯函数提取平台买家链接，规范化为 `BuyerLink`，随列表/详情返回。
  Yandex 使用 B2C `showcaseUrls`，Mercado 使用站点子刊登 `permalink`；不猜测缺失地址。
  `scripts/backfill_online_buyer_links.py` 补齐当前账号存量链接，只更新导航元数据，保留业务版本及同步时间。
- `product_model/online_operation_model.py` 是回执语义与冲突范围的纯逻辑 owner，Service 和 Store 共用。
  Yandex 库存/隐藏接口的 `OK`、Mercado 同步成功回执记为 `confirmed`（本次操作已完成），不占用商品；
  Mercado 传统 PUT 允许空对象，User Products 按目标身份和逐项 `success/errors` 判断。Yandex 价格/内容审核及 Mercado `task_id`
  记为 `waiting_confirmation`；价格隔离转 `needs_attention`，不可识别回执及写入超时保留 `outcome_unknown`。
  历史 `submitted` 按保存的回执投影，缺少回执不能视为受理；读取不改写原任务或发起平台请求。
- 任务以提交键防重，`queued/running` 执行阶段保留同目标互斥；真实异步或未知结果只阻止相关修改。
  同类修改仍互斥；Mercado 库存与销售状态相互关联；Yandex 隐藏不受旧库存任务阻塞，但恢复销售需排除未知库存修改。
  全店同步只与该账号排队或执行中的任务互斥。同步仅更新商品快照，不确认旧修改或解除其防重发保护；
  同步和同商品执行期间不能并发查询修改结果，避免快照互相覆盖。写前核对当前店铺及平台业务版本，
  写前日志区分已发送与未发送；未知结果只回读、不自动重放。每次领取生成独立租约，过期执行者不可提交结果。
  工作线程在应用启动时恢复队列，前端轮询仅负责展示；部分同步失败保留旧快照，不推断删除。
  结果查询由统一任务队列有限调度或用户显式发起，连续查询间隔 30 秒；失败也保留冷却。同步成功不要求回读后才能继续修改，
  后续查询失败、查询进程中断或展示尚未更新不撤销已持久化的受理事实。历史自动确认标记不参与任务领取。
  局部确认更新版本与对应字段，保留完整同步时间；旧任务核验不能覆盖更新的销售意图。
- 修改或同步被已有任务拦截时，`OnlineConflictResponse.conflict_job_id` 返回实际阻塞任务编号；前端错误提示可直接跳到任务队列中的对应任务。
  AI 工具错误的 `details.conflict_job_id` 同样保留该编号。
  `GET /api/online-products?job_id=...` 经 facade → `OnlineProductService.job_detail` 校验当前店铺归属后读取任务，不受最近 100 条列表限制，不执行平台查询或重放修改。
- `POST /api/online-products/close` → `OnlineProductService.close_operation` → `OnlineProductStore.close_operation`
  允许人工核对后结束仍未确认的本地修改，终态为 `closed_unconfirmed`，不判定原写入成功或失败、不撤销平台请求、不自动重放。
  `CloseOnlineOperationRequest` 要求明确确认及所见 `job.updated_at`；短事务校验五分钟内成功回读的 `evidence.checked_at/observed`、商品版本和同目标执行互斥；平台仍处理中、查询失败、缺失当前值或证据过期均拒绝。
  每次开始回读先使旧证据失效，`checked_at` 与保留的完整同步时间 `synced_at` 分开；旧历史必须重新查询才能获得结束资格。
  人工决定与证据保存于原任务 `result.resolution`，清除租约防止旧执行者覆盖；旧幂等键和外部请求阻断保持有效，只允许重新读取商品后提交新的操作。
  `OnlineOperationRecovery.vue` 在任务队列提供核对当前值、原目标值及明确结束的共享弹窗；AI 的 `online_products_close` 复用原生审批快照和同一领域入口，绑定任务与商品版本。
- Yandex 在线内容编辑支持 `content.guarantee_period`（时长、单位、可选说明），从官方 `offer.guaranteePeriod` 读取。草稿与在线编辑共用 `YandexGuaranteePeriodFields.vue`，后端共用纯领域 `product_model/yandex_warranty_model.py` 校验编译。只有勾选的字段进入现有 content 修改任务，按 offerId 局部更新账号商品，不重新发布或改写草稿。空对象不作为清除命令；完整填写后可新增或修改。
  保修字段纳入快照版本及写前并发检查；历史无保修字段的快照与空值保持相同版本，已有原始字段可读取。回读 `getOfferMappings` 中的实际期限与目标逐项比较，缺失字段不沿用旧值；内容审核和未知结果继续使用原任务流程。页面预览显示原值与目标值，接口受理不等于修改已生效。测试见 `tests/test_online_yandex_warranty.py`。
- `runtime_units/online_product_capabilities.py` 将现有读取、单件状态刷新、统一修改、同步、回读、重试接口直接装配进主 Agent。
  `online_products_changes_apply` 按明确清单和固定范围复用逐项修改，整组提交校验成员完整性；
  `online_products_job_read` 仅读取本地任务。任务回执由 `services/online_product_tool_results.py` 精简投影，完整数据留在原领域任务。
  同步保留进度和有界失败样例；`OnlineProductJobReader` 保留 `partial` 终态，不能解释为全部失败。
  目录完整时继续处理仅重试失败项，目录发现中断才重新全量发现；多次尝试不能覆盖已保存的成功结果。
  `services/online_product_completion.py` 在主对话的原生输出校验中检查本轮实际尝试的可恢复遗漏，
  不把只读目标变为写入授权、不保存计划或增加执行循环；契约、退出条件及验证见 [在线商品 AI](online-product-ai.md)。
  `schemas/online_product_capabilities.py` 只声明现有接口形状；公共 `OnlineProduct` 与 `OnlineChange` 契约从持久快照/HTTP 请求中复用。
  商品数量、SKU 分析等由读取返回值与已有 Code Mode 组合完成，不新增场景工具、查询 DSL 或平台写入旁路。
  列表和单件均可通过 `fields` 读取所需业务字段及固定身份、版本；已有足够字段时不再读取完整详情。
  父子字段重叠在参数校验阶段拒绝，避免完整内容与属性重复返回；常用路径说明及分页边界见 [在线商品 AI](online-product-ai.md)。
- 审批和持久调用直接使用现有 Tool Bridge 的 Pydantic Deferred 机制；修改和重试返回 `OnlineSubmissionResult`，任务入队即完成 AI 工具调用，批量提交继续处理剩余目标，不等待远端终态。
  平台写入由后台处理，确认由统一任务队列管理；`online_product_job_reader.py` 为同步及已持久化引用读取领域回执。
  历史 `submitted` 只完成提交职责，新同步成功及最终确认写入 `confirmed`；部分完成、未知结果和等待人工确认保留原状态并结束本次工具等待，不自动重放。
  协议依据：[Yandex 隐藏](https://yandex.ru/dev/market/partner-api/doc/ru/reference/hidden-offers/addHiddenOffers)、
  [Yandex 库存](https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/updateStocksOnPartnerWarehouses)、
  [Mercado 普通修改](https://global-selling.mercadolibre.com/devsite/sync-and-modify-listings-gs)、
  [Mercado 异步任务](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt)。
  改库存、调价、内容、停售与归档共用一个 `online_products_change`，所需权限、审批和提交身份由原有 Runtime 处理。
- 前端复用工作台的 `/online-products` 导航及 `OnlineProductsPanel.vue` / `OnlineContentEditor.vue`。
  在线图片编辑使用内嵌源草稿多选区，图集第一张为主图；排序和移除按钮提供禁用原因及操作提示。
  `OnlinePicturePreview.vue` 提供失败占位与重试；进入预览时保留编辑组件，返回继续编辑。
  `OnlineBuyerLinks.vue` 在列表和详情提供单链接直达、多站点选择及缺失提示；点击不调用后端或 AI。
  `OnlineProductDetails.vue` 在详情直接展示属性名称、编号、值和平台错误/警告，保留换行及原文；
  `onlineAttributeDisplay.ts` 统一详情及编辑表单的展示。列表显示反馈数量，需关注统计包含平台反馈，
  Yandex 卡片处理中、修改未被接受与销售状态分别展示；刷新状态会更新反馈，属性名称由完整同步补齐。
  Yandex 提供普通/归档目录；操作菜单统一提供所选商品的停售/恢复销售、归档/恢复归档，确认弹窗复用 `WorkspaceDialog`。
  勾选父行在这些修改操作中选择整件商品，单独取消成员后退回明确选中的 SKU；导出仍使用当前筛选选中的成员。
  前端一次调用批量入口，连接失败保留完整原请求及幂等键，受理后逐 SKU 结果在任务队列查看。
  页面背景传递当前平台、目录 `catalog`、最近点击或键盘聚焦的 `group_id` / `listing_id`，区分组合与单件；转入聊天保留定位，切换平台、目录、筛选、页码、离开在线商品页或目标消失时清理。无焦点不能推断首项，不混用本地商品或草稿 ID，也不作为授权。
  旧本地 publication 列表、旧独立暂停 HTTP/AI 工具及其前端已删除；持久化 publication 的读取迁移保留。
- AI 接入边界、平台限制及验收方式见 [在线商品 AI](online-product-ai.md)。

## AI Provider 与 AI Work

### 当前统一边界

最终目标不是只统一 Agent loop，而是统一全部 `connection_type=api` 的 AI 推理请求：需要工具
循环的用例使用 Pydantic Agent，不需要 Agent 的普通 chat/JSON/stream/typed output 使用
Pydantic Direct Model Requests，图片等能力使用锁定版本提供的 Pydantic capability/native tool。两类调用都
必须复用 `erp_web/services/ai_model_factory.py` 创建的 Model/Provider。

OpenAI 预设连接自定义 Responses 地址时，Factory 通过原生 profile 关闭 `additional_tools`。
Pydantic AI 2.44.0 的 OpenAIProvider 将该能力用于官方服务，自定义网关可能把历史中的
命名空间和函数名拼接为重名。此设置让本地工具搜索揭示的函数通过普通工具列表声明，
原生消息、失败调用和执行回执均保留。SDK 能按实际端点识别该能力后，可移除覆盖。

Responses 流经 `services/ai_responses_stream.py` 校验 SDK 已解码的错误与结束事件，
随后原样交给原生 `OpenAIResponsesModel`。这是 Pydantic AI 2.44.0 对顶层
`ResponseErrorEvent` 只警告、前台 EOF 可被记为完成的适配缺口；不解析正文、
不编码 SSE、不重试或重新执行工具，保留原生后台暂停与取消。上游支持这些错误后，
通过 `tests/test_ai_responses_stream.py` 的实际 SDK 回放再删除适配。
依据为安装源码及 [官方模型实现](https://github.com/pydantic/pydantic-ai/blob/v2.44.0/pydantic_ai_slim/pydantic_ai/models/openai.py)。

聊天流式展示由 `front/src/utils/aiChatMessages.ts` 从 SDK 消息派生浅快照，历史消息和
工具回执保留引用，禁止逐 token 深拷贝全历史。折叠过程及详情在展开后挂载，
正文只在显示时移除尾部空白，原始消息不变；滚动依赖尺寸观察，不深遍历消息。
回归覆盖 392 条历史回执、4090 次空白更新、SDK 原地 part 更新、取消和错误恢复。

阶段 6 已完成：普通 chat/JSON/stream 统一由 Pydantic Direct Model Requests 发送；图片请求
也先进入 Pydantic Direct Model，再由集中 Factory 创建的 focused Images Model 执行。CLI 与
浏览器 AI 不属于 API Provider，继续使用独立适配器。
Gemini Canvas 图片编辑经 `gemini_canvas_runtime.GeminiCanvasProvider` 独立执行；
复用浏览器连接和页面原生模型转发，不进入 API Direct Model，也不创建 Agent loop。
`desktop_runtime/gemini_browser_session` 仅编排托管 Chrome 的隐藏执行、交互登录与请求前恢复；
浏览器进程切换由共享调试服务核对实例归属后完成，沿用同一 Profile，不读取或复制凭据。
登录等待期间图片领域任务保持执行中，未通过登录检查不发送图片；不另建 Deferred 或任务恢复协议。
首次图像编辑能力测试（尚未启用该能力）直接显示专用 Google 登录入口，不先等待隐藏 Canvas 页面；
登录完成后恢复隐藏连接并测试，新增模型本身不打开浏览器。
浏览器目标已由查找或创建标签页入口确定，连接准备只等待加载，不重复导航打断 Google 登录重定向。
“添加网页模型”的预置目录由 `ai_model_config.browser_model_presets` 经公共 AI 配置的
`browser_presets` 提供，形状见 `schemas/config.py::AiBrowserPreset`，不混入 API Provider Catalog。
预置独立于已保存模型，删除后可以重新添加；不携带用户代理、登录资料或已验证的能力回执，能力仍需实测启用。
网页服务商下拉由该目录中的稳定 `model.browser_provider` ID 生成，显示名称不参与适配器选择；
界面保留已保存但未在目录中开放新增的服务商值，新增连接须明确选择，切换时清除旧能力回执。
自有 Canvas 的最小单文件源码在 `erp_web/assets/gemini_canvas_host.html`；维护者创建并分享应用，
部署时浏览器模型绑定公开 `/share/<分享 ID>` 页面，使用者各自登录 Google。
私有 `/app/<对话 ID>` 仅作维护入口，不作为跨账号分发地址；不依赖第三方工作台。
连接设置、已验证范围及限制见 [Gemini Canvas 修图](gemini-canvas-image-edit.md)。

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
  未接入能力不得静默跳过，临时网络错误不得记为不支持。API、CLI 与 Browser 的测试消息、
  图片提示词和素材全部由系统提供，请求只选择待测能力；设置页展示固定测试说明并等待确认。
- `erp_web/services/ai_model_probe_service.py`：API 模型能力探测 adapter；chat、JSON、联网、
  Function Call、图片理解、图片生成和图片编辑全部使用独立 probe binding。图片理解用两张随机图形的原生二进制输入核验识别结果及顺序。探测不读取待测 capability
  声明，Function Call 必须完成 tool call → tool result → final response 的完整往返。
- `erp_web/services/ai_model_config.py`：保存带 `configuration_fingerprint` 的 v2 能力证明；Provider、
  Base URL、模型名、API style、模型 Thinking 开关、transport 配置或受控 `extra` 改变后，规范化阶段会移除失效证明及
  对应 capability，旧版无指纹配置仍可读取并通过重新探测升级。
- `erp_web/services/ai_model_discovery.py`：与推理解耦的远端模型目录发现；按 Catalog 的可选发现
  策略复用 Pydantic Provider 持有的 client。设置页打开模型选择框时才读取目录，单项能力测试直接请求所选模型。
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
- Responses 全部使用原生 `OpenAIResponsesModel`。曾按 PinCC/Grok 正文 `<think>` 标签
  重分类并拒绝未闭合输出的适配已删除：真实响应会把业务答复接在未闭合标签之后，
  重分类会丢失答复并将已完成任务误报为失败。不能从普通文本标签推导执行状态。
  已核对 Pydantic AI 2.44.0 与[原生 Thinking 文档](https://pydantic.dev/docs/ai/capabilities/thinking/)；
  原生 reasoning、工具调用、用量及历史仍按真实协议保存，旧原生历史保持可读，不作改写。
  `front/src/utils/aiThinkingPresentation.ts` 只做显示投影：开头闭合标签中的内容折叠，
  未闭合内容整段折叠保留（包含可能混入的答案），不猜语言/标题分界；正文复制排除折叠内容。
  `tests/test_ai_responses_text.py` 验证流式/非流式均不因文本标签报错或额外请求，工具只执行一次。
- `erp_web/services/ai_agent_factory.py`：唯一 Pydantic Agent 装配与运行入口。`ai_model_errors.py` 统一保留嵌套 Direct Model 错误的 HTTP 状态、可重试性和安全原因，供 Agent 与业务工具边界使用。
  原生 `UsageLimits` 限制模型请求和工具调用；`PrepareTools` 在预算耗尽后隐藏业务工具，
  不预留可执行的额外额度。`Hooks.before_node_run` 在 `UserPromptNode` 和
  `ModelRequestNode` 开始前更新页面背景，通过 `RunContext.enqueue` 接收用户更新；
  消息注入和事件编码由原生队列负责，不按用户文本生成或重置工具权限。
  主 Agent 使用 `str | DeferredToolRequests`，focused Agent 保留其类型化输出与独立领域能力。
- `erp_web/services/copy_service.py`：`copy.generate` 通过同一工厂运行独立文案 Agent。
  使用原生 `PromptedOutput` 校验输出结构，生成后不再额外调用 AI 复核语言、事实或营销措辞。
  主标题及每个备选标题的字符上限由平台注册表注入输出 Schema，超长与格式错误均通过原生反馈改写；
  CBT 根英文标题同样由输出 Schema 校验。字段校验通过后直接交给领域层保存，原生重试
  最多两次，整个生成过程最多三次模型请求，并受总计 240 秒的 deadline 约束。
  事实摘要保留重量的 kg 单位，不复制主 Agent 的全部消息。已核对安装的 Pydantic AI 2.43.0
  与[原生结构化输出文档](https://ai.pydantic.dev/output/#prompted-output)：直接复用原生格式
  校验及重试，不增加业务 Agent loop。文案生成使用集中 Agent 工厂支持的 API 模型连接。
- `erp_web/services/ai_model_context_projection.py`：原生模型请求 Hook 的纯输入投影；旧大工具结果只在发送副本中形成摘要，完整原生 run 历史与持久化不裁剪。`ai_tool_result_projection.py` 在普通业务结果的模型输入副本中将重复 JSON 集合转换为共享引用，可完整展开；脚本读取、执行回执及审计仍保留原值。Tool Search 等原生协议结果不摘要、不重编码，确保后续回合与审批恢复能重建工具可见性。保留工具配对及 Provider 需要的思考信息，不维护第二套历史或恢复协议。
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
  全局对话与草稿箱批量准备共享 `front/src/stores/aiChat.ts`。
  网页聊天通过原生 Deferred 工具 `request_user_input` 等待填写或选择。人工答复按运行范围和调用 ID 与真实用户消息同事务关联。人工提问不投递给后台任务执行器。工作台与浮窗的 `ChatPendingActions.vue` 合并主任务和即时回复的未决提问与审批。
  运行期间网页输入框以 `queue_if_busy` 提交排队消息，前一任务及其审批结束后自动投递。排队行支持逐条删除及立即发送。其他渠道的原生队列将输入加入模型上下文后，`before_model_request` 同事务提交完整原生历史与收件箱状态；输入框上方只展示仍在等待的消息。后台只发送历史版本变更通知，
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

- 完整草稿准备的图片内容入口为 `draft_images_prepare` → `runtime_units/draft_image_prepare.py`，
  先在图片批次库登记准备任务，返回 `queued` 和批次编号；后台用提交时的原图与 SKU 执行规划、生成、采用。
  多草稿先提交，主对话继续做文案、属性和核价。商品事实读图默认仅接受来源原图。
  无关资料更新可与规划并行；逐页入池时，内容相同的草稿保留版本，整套采用前复核素材、目标语言和 SKU，再合入最新草稿。规划和逐页生成共用图片请求并发上限。使用 Gemini Canvas 的批次在规划前检查通道，失败即返回原因。
  独立工作完成后，`draft_images_wait` 经 Pydantic AI 原生 Deferred 和领域 Job 读取器等待实际采用结果。
  当前安装的 Harness BackgroundTools 在 run 因审批或补问暂停时会取消未完成任务，因此任务必须由领域库持有；
  若原生后台工具支持跨暂停恢复已登记领域任务，可重新评估此适配。服务重启结算为待核对，不重放生成。
  `auto` 对未加工图片生成共用套图及已选 SKU 独立图；人工修改或历史加工素材返回待确认，
  等待确认期间其他资料可继续准备。共用参考优先使用当前草稿，草稿尚无共用图时，
  从本商品适用于目标平台的非 SKU 素材中选取。自动规划最多读取 12 张可用参考，
  优先主图与不同用途，再按原顺序补足。
  明确保留使用 `keep`，明确要求套图或确认重做使用 `regenerate`；
  两者绑定当前图片内容指纹，确认期间图片变化则拒绝旧决定。
  HTTP 草稿保存以共用图集合及 SKU 配图变化记录 `images_manually_modified_at`，
  排序、角色调整和 SKU 勾选不算人工内容修改；手动修图结果在资产 `raw.image_content_actor` 记录来源。
  `image_preparation` 是 Store 维护的当前版本决定，普通保存不能伪造或清除。
  缺少历史操作者证据时仅保护已加工素材，不推断由谁完成。
  全批成功后一次采用，原图留在素材池；部分失败或结果未知保留原引用，按原批次核对，
  不自动重放计费。当前已生成或已保留版本重复准备直接复用；新图片使旧决定失效。
  内容状态与上传、水印、托管链接独立，发布资源仍经 `product_publish_prepare` 检查和准备。
  `draft_prepare_for_market` 的图片步骤只整理现有引用，不能替代套图内容准备。
  回归见 `tests/test_draft_image_prepare.py`。
- 草稿图片工作台提供独立的「生成套图」入口：`POST /api/draft-images/plan-set` 经
  `image_facade` → `image_set_service.plan_image_set`，用 `image.plan` 绑定的识图 API 模型
  和 `AiAgentFactory` 原生 `BinaryContent` / `PromptedOutput` 读取整组素材、生成可编辑方案。
  此功能要求文本、JSON 和图片理解（`image_understanding`）三项能力。已有绑定保留模型 ID，曾仅通过文本和 JSON 测试的模型需补测图片理解。
  设计提示词由 `image.plan` 独立配置，不加载文案或图片翻译 Prompt；事实约束由 focused service 注入。
  方案是待生成图片的领域设计内容，
  不包含下一步工具计划。生成前后复核草稿版本，模型等待和参考图片文件预检均在商品锁外。
  `POST /api/draft-images/generate-set` 注册既有 `ImageBatchStore` 批次，每页作为独立输出项，
  经 `image_translate_service` / `ai_gateway` 生成；`combine_references` 将同页多张参考图放进
  一次原生多模态请求，API 与显式 Canvas 连接都不逐张套用相同提示词。结果只合入商品素材池，
  不自动改变草稿引用。`schemas/image_sets.py` 定义方案，批次保存设计内容与真实结果资产 ID，
  重新打开可恢复预览；单页重做引用原批次，保留未重做页面的实际状态和结果，不重新执行或计费。
  套图范围支持全部生成、只生成共用主图、只生成 SKU 图。
  `plan-set` 按实际目标接收共用参考图或启用的 `sku_ids`，也可同时接收两组。
  只选 SKU 时从已绑定原图直接组成可编辑的规格图方案，无需共用图参考素材或共用图规划模型。
  `sku_image_targets` 按真实当前图片关联合并目标，
  共用方案完成后为每组添加沿用统一风格的 SKU 主图页面，不允许模型猜测规格绑定。
  `ImageSetPage.scope/sku_ids` 区分共用与独立规格用途；旧页面缺省为共用图。
  SKU 页面只能引用其绑定规格的一张当前原图，生成输入只包含绑定规格的事实，生成与重做均复核范围和来源；采用后的原素材关系
  由真实生成资产的 `raw.image_set` 保留，用于原方案单页重做。无独立图片的规格继续使用共用套图。
  采用由 `useDraftImageWorkbench` 同次保存共用引用和选中 SKU 的图片覆盖，SKU 结果不进入共用图集，
  未采用或失败项保留当前图片；共用图可排序，第一张成为共用主图。整次采用复用本次打开的撤销边界，
  撤销/重做一起恢复共用与 SKU 引用，不执行模型。旧图片批次缺省新增字段后继续读取，不重放历史任务。
  已核对 Pydantic AI 2.44.0 的[多模态输入](https://pydantic.dev/docs/ai/core-concepts/input/)与
  [结构化输出](https://pydantic.dev/docs/ai/core-concepts/output/)；沿用已有 Images Model 适配，
  不增加 SDK 推理旁路或 Agent loop。回归见 `tests/test_image_sets.py` 及前端工作台/套图组件测试。
- 草稿图片 V2 由 `DraftImageWorkbench.vue` 展示共用主图与区域内去重后的当前 SKU 图片；同一资产可在两区展示，标明关联 SKU 及共用用途，勾选跨区同步，总数和处理提交按资产 ID 去重。`ProductImageEditorPanel.vue` 保留商品素材池入口。页面不做版本分组或 SKU 配图，历史错误加入的共用引用照实展示，不自动清理。
  `useDraftImageWorkbench.ts` 固定提交目标、清空提交选择；移出、添加、排序和撤销/重做经既有草稿保存入口立即持久化，只改图片引用，保留 SKU 独立引用、价格和源文件。上传在短锁内合入最新素材池，回执 `uploaded_image_ids` 只标识本次上传，不能按素材池增量猜测范围而混入另一草稿的结果。
  撤销栈限定本次打开的草稿工作台，切换工作台内标签保留，关闭或刷新后建立新边界；重新加载或外部更新改变图片引用时旧历史失效。一次批次只记录成功项形成的前后引用差异，重做不再生成或计费。保存回执丢失先重读真实状态，不假报撤销成功。
  `POST /api/draft-images/process` 经 `image_facade` 注册既有 `ImageBatchStore` 批次后异步执行；`POST /api/draft-images/state` 只读查询当前草稿与原批次。批次携带草稿 ID，同草稿只准一批；进度快照必须在草稿读取前固定，避免终态配旧引用。前端断线保留请求 ID，重新打开按草稿恢复查询，不重发模型请求；结果未知保持只读，服务重启仍沿用已有的人工核对边界。
  `ProductStore.save_draft_content` 通过上下文显式注入的图片任务 guard 阻止冲突图片写入，页面保存入口另在短锁内检查任务；网络与模型调用不占商品锁。处理期间引用被其他路径改动时保留生成资产并拒绝套用到变化后的范围。
  `image_pool_core._pool_display_item` 保留 `derived_from_id`、`source_asset_id`、语言和供应商字段；历史仅有 `source_asset_id` 的前端读取映射继续保留。
  `replace_selected` 只替换当前草稿原有共用位置与 SKU 引用；SKU 专属结果不能追加到共用图集。显式输入 ID 不再受素材池旧勾选或平台标签二次过滤。
  `image_translate_service.py` 每次生成独立资产 ID 与文件路径，直接来源为实际输入图；旧资产和文件继续读取，曾被历史实现覆盖的文件不能凭关联恢复。发布仍由 `skuPublishImages` / `sku_publish_projection` 按 SKU 专属图在前、共用顺序跟随并去重，不新增平台图片限制。
- 图片工作台的 `POST /api/image-edit`、`/api/image-translate` 经 `image_facade` →
  `image_batch_service` 按原图建立批次，进程内所有批次共享三路执行上限；每张仍经
  `image_translate_service` → `ai_gateway`；API 进入 `ai_direct_request_service` → Pydantic Direct Model，
  显式选择的 Gemini Canvas 浏览器连接进入独立图片执行器，两者失败均不自动切换。
  `image_batch_store.py` 在独立 `data/image-batches.sqlite3` 保存排队、执行、成功、失败及结果文件引用；
  不复制模型消息，不改 ERP 主库结构。每张结果在商品短锁内重读最新内容并写回，保存成功才结算成功；
  `replace_all` 仅首张成功时清空目标图集，后续追加，避免并发完成时互相覆盖。
- `ai_direct_history.capture_direct_history` 只收集真实原生请求和响应；批次内单写者经
  `PydanticMessageStore` 保存全部原生消息及逐项关联 metadata。图片批次的 UI 投影只展示初始用户说明，
  逐张进度读取 `GET /api/v1/ai-work/conversations/{id}/image-batch`，不把并发响应混入单个 SSE 流。
  `AiImageBatchProgress.vue` 在浮窗与 AI Work 共用，按原图编号显示，未结束时轮询只读快照；
  切换和卸载作废旧读取，重新打开继续读取，终态停止轮询。普通对话的 `run_active` 仍只表示 Agent run。
- 旧同步图片端点随原业务请求收尾；草稿 V2 在同一领域批次登记后后台执行，浏览器重新读取不执行模型。进程重启后保留成功项，将正在执行项标为结果待核对、
  尚未执行项标为中断，不自动重发。旧对话没有批次记录时沿用原生历史展示，不推测缺失的处理结果。
- 核对依据：安装版本 Pydantic AI 2.44.0 的 `direct.model_request` 和 `ModelMessage.metadata`，
  官方 [Direct Model Requests](https://pydantic.dev/docs/ai/core-concepts/direct/) 与
  [消息历史](https://pydantic.dev/docs/ai/core-concepts/message-history/)。框架负责单次模型请求和消息，
  商品素材落库、原图编号及批次排队属于 ERP 领域适配；不引入 Agent loop 或自定义事件编码。
  如果原生图片批次能力未来覆盖这些业务关联，可替换执行调度，保留领域保存及持久记录读取能力。

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
  主对话的大型目录在组合根设置 `AiToolSet.defer_loading`，Bridge 映射为原生 `Tool.defer_loading`；小型 focused 工具集直接提供。工具发现不改变 Runtime allowlist、目标范围或审批权限。
- `erp_web/services/ai_tool_discovery.py`：原生 `ToolSearch` 的匹配策略，完整工具名优先匹配，同样支持用空格分隔名称各词（如 `product image read`），关键词支持中文；发现、Provider 适配及历史恢复均由 Pydantic AI 管理。已核对安装的 Pydantic AI 2.44.0 / Harness 0.34.0 和[官方工具搜索文档](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/tool-search.md)。原生关键词算法只识别 ASCII，且完整工具名也召回引用该名称的其他工具，因此仅适配目录匹配；原生算法支持这两点时可移除该策略。CodeMode 对按需目录使用原生 `dynamic_catalog`，仅为已发现的同步非审批能力生成 Python 类型与签名，不再预注入全部函数；保留直接调用入口，执行仍归官方 CodeMode。
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
- `erp_web/services/global_agent_chat_service.py`：主对话服务；主模型依据完整对话选择实际提供的工具，并按原生执行回执回答先前操作结果。`GLOBAL_RESPONSE_INSTRUCTIONS` 统一约束所有全局回答，装配在业务提示词和长期记忆之后。新任务先用一句原生流式正文说明目标和下一步，再调用工具或继续作答；最终汇报结果优先，只保留必要依据与限制；批量汇报覆盖及异常范围，合并同类失败。取数先在当前目标所属业务域完成，只有目标需要或必要事实有可信跨域关联时才扩大范围；搜索未命中不能当作其他字段无数据。额度不足时仍围绕当前问题回答，不强制纯查询或解释汇报写入。不增加回答压缩模型或工具授权推断层。
- `erp_web/runtime_units/collect_helpers.py::claim_products_to_markets`：AI 认领和采集箱市场选择共享按语言分组逻辑；全部市场从真实店铺绑定及平台注册表解析。
- `erp_web/services/ai_tool_bridge.py`：机械参数校验、原生并发、审批快照与 Deferred 转接；不选择下一步。
- `erp_web/services/tool_approval.py`：业务审批内容 digest，绑定工具名/版本、operation key、原生 call ID 和审批版本；执行前重核。
- `erp_web/schemas/ai_approval.py`、`erp_web/services/ai_approval_policy.py`：工具审批偏好 `ask/full` 与原生 `HandleDeferredToolCalls` 接法。完全授权只产生带原快照的 `DeferredToolResults.approvals`，不修改工具权限、不跳过执行校验，也不接管 Agent 循环。
- `erp_web/facades/ai_approval_facade.py`：`POST /api/ai/approval-mode` 的受信 UI 入口，要求 `X-Approval-Token`；模式存入 `app_config.ai_tool_approval_mode`，只读值随 `/api/state` 返回。普通设置保存保留最新授权偏好，模型没有修改模式的工具。
- `front/src/components/ai-work/AiApprovalModeSelect.vue`：主对话与浮动对话共用的“询问审批 / 完全授权”选择器，全局保存。完全授权覆盖后续调用和未决定的审批，既有批准/拒绝不改写；普通未返回工具只显示“等待工具结果”，审批标签以服务端 `pending_tool_calls` 为准。
- `erp_web/services/agent_run_storage.py`：原生 hook 到消息 CAS、输入收件箱与副作用检查点的适配。
- ERP 主 Agent 的固定规则归 `erp_web/assets/systemAgents.md` 和 `services/agent_instructions.py`，功能模板归 `erp_web/assets/prompts/` 和 `services/ai_prompt_templates.py`。文件只从当前发布包加载，不接受用户路径，不回退到实例目录的旧 prompt/agents.md；缺失或无效时明确报错。发布直接更新系统规则，不再向账号复制功能 prompt。
- 用户补充偏好唯一归 `config/userAgents.md`，`services/user_agents.py` 提供固定路径、UTF-8、32 KiB、原子写入和内容版本校验。`GlobalAgentChatService.instructions()` 在每次新运行及 Deferred 恢复时，沿 `AiAgentFactory` 的原生 instructions 加载系统规则和当前账号偏好；不增加 Agent loop。已核对 Pydantic AI 2.44.0 与[原生指令文档](https://ai.pydantic.dev/agents/#instructions)。用户偏好不替代系统权限、业务校验或本轮明确要求，注释大纲不注入模型。
- `GET/POST /api/system-settings/user-agents` 经 `auth_config_facade` → `ConfigStore` → `user_agents` 读写当前账号文件；POST 必须携带 expected_revision，冲突返回 409 并保留双方内容。前端「系统设置 → AI 自定义指令」只展示编辑此文件，功能绑定页仅维护模型及生成参数。没有 Agent 自主写入偏好工具，开发过程不得追加业务规则、记忆或任务总结。
- `assets/account-defaults/userAgents.md` 只有注释过的编辑大纲，账号首次初始化以排他创建方式复制；存在的文件（包括空文件）不覆盖。实际 `config/userAgents.md` 不入 Git、不随发布写入。旧 app_config 的 ai_use_case_prompts 字段可读后丢弃，旧 prompt/agents.md 文件保留但不再执行；旧内容若需保留为偏好，由用户自行整理到新文件，不自动混入系统或用户指令。回归见 `tests/test_agent_instructions.py`、`test_user_agents.py`、账号 HTTP 及前端指令设置测试。
- `erp_web/schemas/ai_page_context.py`：主对话页面背景的有界契约与中文指令渲染，只接受页面枚举和资源 ID。`vercel_ai_ui_service.py` 校验请求的 `page_context` 后写入原生消息 metadata，缺省表示本条消息不携带背景；客户端消息 metadata 仍被丢弃。`AgentRunStorage` 随用户输入更新背景，Factory 使用 Pydantic AI 原生动态 `instructions` 注入模型系统上下文，不创建独立系统消息历史或新 Agent loop；Deferred 恢复复用已保存快照，后续关闭开关会清除当前背景。
- `front/src/stores/aiPageContext.ts`、`useAiPageContext.ts`：可见页面及编辑区域提供定位信息，输入框眼睛开关默认开启并在本机记住选择；普通发送和运行中追加消息均在发送瞬间复制背景。关闭编辑器、切换页面或 KeepAlive 停用时撤销该区域背景，页面背景不作为写权限，也不携带表单未保存值。
  草稿箱通过 `selected_draft_ids` 传递发送时的勾选 ID（最多 1000 个），空数组明确表示未勾选；不读取商品正文，也不改写显式任务范围 `target_draft_ids`。编辑中的 `draft_id` 优先于列表勾选背景，关闭后恢复列表快照。`drafts_query.selected_items` 是查询位置选择，不能用作界面勾选状态。
- `erp_web/stores/agent_call_store.py`：原生 Deferred 请求/结果序列化、收件箱和领域执行回执；不存步骤计划、Agent 状态或事件副本。
- `erp_web/runtime_units/publish_capabilities.py::product_publish_request`：原生审批通过后同步提交 PublishingBus，返回 `ProductPublishRequestResult` 即结束 AI 的发布操作；不使用 `CallDeferred`、`JobReferenceResult` 或发布 Job Reader 等待平台终态。平台发布及结果确认继续由发布领域负责，用户可在发布任务界面查看。
- `erp_web/services/agent_job_service.py`：固定大小线程池领取和对账需要结果的领域 Job；scanner 不执行模型。选品研究、在线商品等后台工具通过 `DeferredToolResults` 返回结果后恢复原生 Agent。
- `erp_web/runtime_units/domain_job_readers.py`：选品研究 Job 的只读终态和有界活动证据。
- `erp_web/schemas/domain_jobs.py`：Job Reader 的有界生命周期/活动证据，供模型了解真实终态。
- `erp_web/stores/product_mutation.py`：商品聚合的短期互斥；`ProductStore` 的局部读改写保护、草稿旧快照冲突检测。锁不跨模型调用或人工等待。
- `erp_web/runtime_units/conversation_fact_capabilities.py`：按草稿、会话归属查询真实用户消息；引用不能由模型自报可信标志替代。

原生 Deferred 要求当前批次全部调用结果/审批齐备才恢复模型。部分结果先落盘，独立领域 Job 可并发。
发布仅使用原生审批和普通工具返回：已核对当前安装的 Pydantic AI 2.44.0 及[官方审批文档](https://ai.pydantic.dev/deferred-tools/)，原生能力满足需求，无需自建完成协议或等待状态机。提交回执表示队列接收成功，不表示平台发布成功；模型不得主动轮询、等待或因后台失败重新发布，用户另行请求查看或处理时才继续。
直接追加的用户消息在下一个原生模型边界生效。网页显式排队的输入在原任务结束后生效。取消不会撤销已经发出的平台操作；未开始的调用先返回用户要求已更新，等待模型重新决定。

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

- `POST /api/v1/ai-chat/fork` → `ai_chat_facade.fork_chat_conversation` → `VercelAiUiService.fork_conversation`：验证普通对话归属及已闭合的回复边界，复制原生历史前缀到新会话；不启动 Agent 或复制待办、Job、写权限。分支归属和来源保存在原生应用 metadata，首次发送后的归属仍由 claim 校验。`/ui-messages` 从既有 claim 投影每轮时间到 `metadata.ai_turn`；聊天隐藏推理正文，原始消息不变。展示及适配边界见 [AI Work](aiworkpage.md)。

- `POST /api/v1/ai-chat/runs`：新用户消息、关联待答问题的用户回复（`answer_to`）或官方 approval-responded 消息。用户消息 ID 幂等领取与输入同事务保存；仅服务端历史参与模型调用。
- 对话输入框支持粘贴和选择 JPEG、PNG、WebP 图片。服务端按全局 AI 实际绑定的 API 模型校验 `image_understanding`，验证原图后交由 Vercel Adapter 转为原生消息。图片随收件箱和对话历史保存。模型输入投影附上内容 ID，切换文本模型后仅提供附件引用。
- `runtime_units/image_capabilities.py` 的 `product_image_read` 按商品列出素材，由商品 SKU 的 `image_asset_id` 反查每张图关联的全部 SKU，并通过 `ai_direct_request_service.image_to_text` 用同一模型读取指定原图。`chat_images_save_to_product` 从当前会话的用户消息取指定附件，以短锁合入最新商品图片池。两条入口分别使用商品读权限和图片写权限。
- `POST /api/v1/ai-chat/queue` 按会话与消息 ID 处理仍在排队的输入。删除清除正文并保留幂等身份。立即发送由 `chat_queue_service.py` 将新消息和活动任务交给全局模型判断是否取代旧要求，提交前重核目标轮次。撤下过期审批、交接新输入和保护其他排队项使用同一事务。进度查询和独立任务使用自己的原生执行范围、审批及回执，在原对话按提问时间展示。独立即时回合携带主对话和近期即时回合的事实摘要，即使分流时旧任务已经结束也保留主对话背景。`AgentRunStorage` 将服务端保存的 `immediate_context` 交给原生动态 instructions，恢复运行时沿用；摘要只提供上下文，不复制开放工具协议或扩大授权。回归见 `tests/test_chat_queue_actions.py` 中等待回答时补发截图及旧任务结束后的即时分流场景。
  已核对 Pydantic AI 2.44.0 的原生队列、并发运行及取消令牌。ERP 负责用户收件箱和业务意图分流，Agent 循环和原生事件继续由框架负责。框架原生支持并发处理可撤回输入并按意图取消时，可移除对应适配。[Pydantic AI 运行与取消](https://pydantic.dev/docs/ai/core-concepts/agent/#cancelling-a-run)
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

气泡与 AI Work 默认入口统一由 `AiChatStore.restoreConversation` 恢复上次选中的普通对话。
`/resume` 在聊天窗口分页选择账号内的全局对话，复用原生历史和事件订阅。AI Work 左侧历史对话列表提供图钉、改名和删除入口。置顶在分页前排序；`POST /api/v1/ai-chat/manage` 处理置顶、手动命名与删除，手动名称优先于后台命名。删除覆盖同一对话的并行回复，保留业务回执。
`services/chat_titles.py` 使用首条用户输入作为展示标题，后台按会话领取一次中文命名任务，结果写入账号独立的标题索引；列表读取旧历史首句不会触发模型。
浏览器只记住会话 ID，首次发送或选中历史时更新；消息、运行状态和审批仍读取服务端历史并重新订阅。
恢复中禁止发送，失败展示重试，不自动创建新会话或选择最近的任务记录；明确历史链接优先于默认恢复。
新对话按钮与 `/new` 清除旧选择，未发送的空白对话不生成历史深链；迟到的恢复响应不能覆盖新的选择。
`AiWorkFloatingButton` 始终显示普通聊天，业务 presentation 通过独立进度卡和详情入口展示，
开始、失败及结束均不替换聊天面板或聊天入口目标。`aiWorkDisplay` 仅维护任务展示与占用，不再持有聊天/任务切换模式。

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
  `schemas/category.py` 统一校验每行的“属性名:属性值”。公共摘要投影 `text_format` 和
  `format_hint`，供主 Agent 生成属性与前端多行编辑使用，不暴露内部 constraints。
  属性写工具、草稿发布预检和 payload 编译均执行同一规则；该选填字段只保留没有专用
  平台字段的来源事实，不重复材质等已映射属性，不把整组 SKU 的概括描述套给各规格。
- `schemas/category_constraints.py` 是属性长度和数值范围的纯校验入口；规范键为
  `max_length/min_value/max_value`。公共摘要显式提供这三个字段，缺失为 null，数值边界以
  字符串保留精度；不把内部任意 constraints 暴露到界面或 Agent。Yandex 草稿预检、发布
  编译、Agent 属性写入及在线修改共用逐值检查，枚举自定义值与普通文本遵守同一长度规则。
  前端 `utils/categoryConstraints.ts` 提供填写时反馈，后端为最终校验；草稿允许保存待完善值，
  不静默截断，已知违规禁止发布。`fetchCategoryAttrs` 完整读取属性分页，缺页、重复或身份变化
  报错，不展示不完整定义。规则缺失不推定统一上限。故障证据及回归见
  [草稿属性限制验收](draft-attribute-limits.md)。
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
- `erp_web/assets/prompts/text_translate.json`：唯一“翻译” Prompt。调用方负责注入目标语言和具体文本；
  类目候选与平台属性在前端各自独立组装内容并触发，不共享领域 payload。

已退役的类目结果翻译、类目属性翻译端点、用例、Prompt 和 runtime unit 不提供兼容路径。

## 类目匹配 Capability

- `POST /api/category-attrs` 返回平台类目属性定义；Ozon 字典字段保留
  `dictionary_id/is_dictionary/is_collection/max_value_count/category_dependent`，不把大字典内联到类目响应。
  带单位的平台属性（Yandex）通过共享的 `unit_options/default_unit` 暴露可选单位，
  属性值以 `{value, unit}` 提交；`dictionary_value_id` 按字符串传输与校验，
  不做数值化（Yandex 字典 ID 超出安全整数范围）。
- `POST /api/category-attribute-values` 是草稿类目枚举值的公开读取入口；在线商品通过上述刊登绑定接口复用相同 Catalog；
  `erp_web/runtime_units/category_store.py` 通过 `CategoryProvider.attribute_values` 分派，
  Ozon 由 `erp_web/runtime_units/ozon_category_api.py` 调用独立的
  `description-category/attribute/values` 接口分页，并把非空检索交给平台的
  `description-category/attribute/values/search` 大字典搜索接口；结果短时缓存。品牌空查询首屏
  会用当前类目的实时搜索结果置顶官方“无品牌”候选；`无品牌/其他/Generic/no brand` 等查询别名
  只转换为平台原文检索词，枚举 ID 不做跨类目硬编码。
  Yandex 发布时从草稿或商品品牌读取商品级字段 `vendor`。类目候选只接受当前类目定义的属性 ID，属性不存在会返回参数错误。
- `erp_web/schemas/category_brand.py`：平台品牌身份及无品牌查询词；仅采用平台实际返回的候选。
- `erp_web/product_model/category_model.py`：类目选择、属性有效性与发布必填项判断。
- `erp_web/runtime_units/product_capabilities.py`：属性任务优先使用 `draft_attributes_read(scope=common)`，一次返回 `product` 商品/来源事实、`targets` 全部目标的类目及完整已填公共属性和 SKU 数量，不携带图片或 SKU 明细。指定 `scope=sku` 和明确平台/站点后，一次读取全部已选启用 SKU 的有效事实和差异属性；大量数据可用 `limit` / `next_offset` 分段。
- `category_attributes_query` 默认 `scope=common`，SKU 任务使用 `scope=sku`，检查全部定义可用 `scope=all`；`write_scope` 标明可写范围，排除字段通过 `excluded_attributes` 的精简记录说明。过滤沿用原始分页游标，空页仍按 `has_more` 继续；不丢弃必填或可选属性。`category_attribute_values_query` 查询真实候选，小字典优先空查询，独立目标/候选并行，同一目标集中写入。主对话负责事实判断、语义匹配、翻译和缺资料时询问用户；不设前 20 个可选属性的限制。
- 主对话按 SKU 的来源规格拆分独立事实，仅写目标类目允许的差异字段。整组填写后用只读 `draft_sku_variants_check` 核对当前已保存组合；与发布共用属性投影及组合判定，不触发核价或完整预检。Yandex 同卡组合缺少来源规格对应的差异字段时，传入 `yandex_variant_field_ids` 实时核对该店铺类目，返回适用的后台设置步骤。商家保存后用相同字段重新核对，并让后续读写使用更新的定义。
- `product_attributes_update` / `draft_sku_attributes_update` 分别保存公共属性和指定 SKU 的差异属性。`erp_web/runtime_units/category_attribute_updates.py` 只执行确定性校验：服务端重读类目定义，核对作用域、只读字段、值类型/数量/单位以及平台枚举 ID 与原文。网络校验后在商品锁内重读当前目标，再局部合并本次字段，拒绝变化后的类目、停用或未选 SKU。
- `erp_web/runtime_units/category_attribute_access.py` 是查询与写入共用的纯作用域规则：公共、SKU、托管及只读字段一致判定。托管字段在枚举查询前拒绝；Ozon 单字符枚举值用字典分页按 ID 和原文精确核对，不走至少两字符的搜索端点，不跳过枚举真实性校验。本地查询参数错误不可重试，网络故障保留可重试属性。
- 属性填写只有主对话这一条 AI 路径。页面入口、专用属性 Agent/复核模型、局部 HTTP 调用和复合草稿准备里的隐式属性步骤均已删除。`draft_prepare_for_market` 返回的完成步骤只包含目标、文案、图片、类目和定价；主对话随后按需直接填写属性。
- `erp_web/services/global_agent_chat_service.py` 与 `erp_web/assets/systemAgents.md` 规定事实复用、公共/SKU 边界、无品牌优先及缺口汇报。继续使用已安装的 Pydantic AI 2.44.0 原生工具调用、消息历史和指令装配；此次不需要新增 Agent loop 或生命周期。
- `front/src/composables/useAiDraftSync.ts` 在原生对话历史提交、流结束及返回草稿工作台时读取最新业务数据，统一同步属性、包装、库存、核价参数、SKU 售价和保存版本。无未保存编辑时自动更新；有编辑时保留页面并提示重新加载，覆盖前需确认。重新加载是只读操作，不重放 AI 工具或提交表单；过期读取响应不能覆盖切换后的草稿。`DraftWorkspacePanel` 在工作台内持续显示错误与“重新加载最新数据”入口。眼睛开关继续控制发送时的页面背景。
- `front/src/components/domain/CategoryAttributesPanel.vue` 对字典字段只保存平台选项的
  `dictionary_value_id + value`（ID 原样按字符串存取，不做数值化），搜索输入不进入草稿；
  实时候选按 `next_cursor/has_more` 追加并按 ID 去重，大品牌字典通过“加载更多”继续读取；
  带 `unit_options` 的属性通过数值输入 + 单位下拉生成 `{value, unit}`；
  发布预检拒绝自由文本字典值。

- `erp_web/runtime_units/category_tools.py`：`category.search` 只读 ToolSet。绑定对象实现
  `CategoryNavigator` 时只暴露 `browse_categories(parent_ids)`；否则只暴露
  `search_categories`（v5）。先在 `product_identity` 摘出原始规格中的实物结构，再填写固定语言的
  `product_type / alternative_names / keywords`，三组词在一次工具调用中合并查询。
  不额外启动规划 Agent；工具 schema 与执行器均没有 platform/site 参数。
- `erp_web/schemas/category_search_language.py`：根据注册平台/站点确定唯一检索语言，独立于商品
  原文和草稿语言。Yandex/Ozon 俄语，MLB 葡语，其余美客多本地站西语，CBT 预测接口固定英语。
  CBT 覆盖定义在 `marketplace_registry.py`，不改变刊登语言。执行前整批拒绝中文或错误文字体系，
  被拒的批次不调用搜索器；拉丁字母短词的英/西/葡语语义仍需要模型遵循契约，不能将文字体系
  检查宣称为完整语言识别。
- `erp_web/runtime_units/category_keyword_search.py`：关键词批量 I/O owner；每个匹配任务内
  去重并复用成功查询，最多 3 个并发查询，沿用入口绑定的 provider 超时和任务 deadline。
  Yandex/Ozon 本地树搜索取回每词全部命中，交错合并并按类目 ID 去重，不再按 8/24/50 条裁剪自动检索。
  人工查询显式传入的 `limit` 继续有效；美客多远端预测仍遵守平台接口限制，数量不代表全库覆盖。
  focused 工具仅返回 ID、名称、完整路径和命中词；已见类目用 `repeated_candidate_ids` 引用，仍可最终选择。
  单次回执沿用 Execution Profile 的 128 KiB 预算，仅超过字节预算时分页；这是工具输出边界，不是对模型剩余上下文的估算。
  `total_candidate_count / returned_candidate_count / remaining_candidate_count` 分别为本组去重命中总量、
  本页交付量（含历史引用）与后续页数量；`query_candidate_counts` 统计每词完整取回数。
  `has_more / next_offset` 明确续页，保持全部搜索参数不变并传入 `offset`；`truncated` 与 `has_more` 同义。
  focused 续页复用本次查询缓存；只有实际交付的候选登记到账本，缓存中的未交付类目不可选择。
  主对话 `category_search` 同样默认完整检索并按字节分页，支持显式 `limit / offset`，保留 Ozon ID 配对且计入预算；
  该通用只读工具每次重新查询，跨调用分页依赖平台目录和查询结果保持稳定。
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
- `erp_web/assets/prompts/category_product_match.json`：`category.product_match`
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

- `services/pricing_return_loss.py` 按退货率将单次净损失分摊到最终成交件数，使用精确分数参与
  求解器、取整后的利润校验及手动售价利润计算，不额外查询平台。`schemas/draft_pricing.py`
  定义选填参数，市场共用、SKU 覆盖、费用模板、预览绑定和发布失效校验均沿用同一字段。
  佣金按全退、发货物流与包装按不退估算；退回再售完整回收采购成本，弃货计入采购成本。
  其他固定成本及支付/其他平台费用不自动追加到退货损失，额外不退费用由用户填写；
  结果标为本地预估，不属于官方费用核验。旧数据缺省退货率为 0，不改变其原成本口径。

- `PricingPanel.vue` 按 Penpot「核价」原型组合范围/汇率栏、费用模板侧栏及连续 SKU 结果。
  `PricingExchangeRates` 保留每个汇率的手动覆盖与 API 参考；`PricingTemplates` 管理费用模板；
  `PricingSkuResults` 对完整结果执行搜索和市场/异常筛选，按 SKU 合并成本包装，无分页。
  进入页面、切换页签、修改输入或刷新汇率均不触发核价；只有手动计算/重试才请求预览。
  运费不提供币种切换，共用和 SKU 手动运费均按人民币录入，结果表使用核价快照中的人民币成本，原币报价保留在明细。
  已有外币手动运费按当前有效汇率展示；首次编辑时将该市场共用运费和所有 SKU 运费覆盖一起转换为人民币，未编辑不改写旧数据。
  Yandex 运费下拉项复用仓库配置接口的 `pricing_options`（只读本地同步快照），默认全部比价，也可指定发布仓库与渠道。
  `shipping_selection` 随草稿参数和费用模板保存、进入核价依据；显式 null 恢复比价。计算时重新核验可用方案，严格过滤仓库和渠道，不可用或 SKU 不符合条件则报错，不自动改选。
  发布预检同时比较草稿当前物流选择、SKU 已应用的核价选择与实际仓库方案，修改选择后不能继续使用旧报价发布。
  进行中、失败或参数不匹配时停用旧结果，应用仍由后端重新校验。
  草稿弹窗固定标题与页签，由页签内容区统一纵向滚动；费用区和 SKU 结果不再限制高度，表格仅保留横向溢出。
  `PricingScopeDialog` 的市场选择仅限定本次核价，不删除草稿市场及未选市场的费用规则。
  物流继续使用各平台现有真实仓库/渠道配置，没有独立的统一上架仓库目录。
- `http_route_units/pricing_workspace_routes.py` → `facades/pricing_workspace_facade.py`
  提供人工费用模板和独立汇率查询。模板由 `schemas/pricing_templates.py` 限定形状，
  `ConfigStore.mutate_pricing_template` 在配置短锁内保存到 `app_config.pricing_templates`。
  默认模板由 `draft_pricing.price_draft` 统一装配，页面、AI 与市场准备均适用：仅当草稿没有已保存的
  公共/市场核价参数、模板来源、核价时间和 SKU 报价，且请求没有显式模板来源（含 null 清除）时初始化。
  本次明确参数（包括零值）和 SKU 覆盖优先于模板；已有草稿沿用自己的参数快照。
  核价页可将当前选中的模板设为个人默认。普通草稿保存和发布前保存仅在费用、规则或模板来源有改动时写入表单参数，避免仅编辑商品资料就跳过首次默认模板。
  页面在首次显示时预填默认模板，传入完整参数和来源；保存过参数但尚无报价的草稿也不得重新套默认模板。
  预览不持久化模板，应用售价才保存参数与来源；自动采用的默认模板在计算期间变化会拒绝结果，
  预览后有效参数或来源变化则要求重新核价，不能把旧报价应用到新模板参数上。
  只保存手动固定费用、市场费率、定价和运费偏好；手动汇率必须显式选中，
  商品事实、单 SKU 覆盖、官方佣金和自动运费回执不进入模板。
  `PricingCommonPatch.exchange_rate_overrides` 按汇率对覆盖同次 API 快照，空对象恢复实时，
  三对全部覆盖时不依赖 API。已有 `exchange_rate_mode=manual` 仍可读取和计算。
- 页面入口为 `/api/draft-pricing/preview`、`/api/draft-pricing/restore` 与 `/api/draft-pricing/apply`，经
  `facades/draft_pricing_facade.py` 调用 `runtime_units/draft_pricing.py::price_draft`。
  AI 的 `draft_pricing_preview` / `draft_pricing_apply` 及市场准备也使用此业务入口。
  请求为草稿 ID、目标范围与本次费用修改；省略参数复用草稿保存配置。系统读取已勾选且启用
  SKU 的采购成本、包装资料和逐 SKU 费用覆盖，绝不以商品主档成本替代缺失规格成本。
  `common.domestic_freight_cny` 仅表示国内物流，目标的 `shipping_amount` 仅表示国际运费。
  页面通过类型化 `sku_updates` 携带未保存的费用覆盖和 SKU 选择，通过 `target_selections`
  携带当前 Mercado 销售条件；后端验证成员范围。其他页面编辑保留在前端。
- 预览不保存草稿或已应用售价；成功预览返回 `preview_id`，服务端缓存十分钟（最多 32 份）。
  返回核价页时，`restore` 按草稿查找最近一轮仍有效的服务端预览，仅恢复匹配当前输入的报价，不计算、不保存、不延长有效期。
  应用必须携带此标识和相同核价参数，比较逐 SKU 事实及费用输入、类目、销售目标、店铺配置、物流配置及费率版本；
  普通保存、文案或预检状态变化不使报价失效；请求仍携带当前草稿版本，本次操作内继续使用完整商品/草稿快照检查并发变化。
  仅保存服务端已核验结果，不再运行计算器或请求物流/佣金/汇率。缓存失效或参数改变明确要求重新预览，
  不自动重算。页面、AI 和市场准备统一遵循先预览后应用；成功应用后消费标识。
  预览与规则缓存由 `AppContext.pricing_cache` 持有，不持久化，重启后重新计算。
  验证全部 SKU × 市场结果，
  成功后通过 Store 一次保存公共参数模板和 `sku_items[].pricing`。发布只使用逐 SKU 结果。
  页面保存草稿（含预检前保存）或应用售价时同时保存 `pricing.template_source`（模板 ID 与名称）及当前费用参数；普通保存不应用预览售价，重新打开只恢复来源显示，
  费用仍读取草稿参数快照，模板变更或删除不自动覆盖。旧草稿没有来源时不猜测匹配模板。
  计算前后比较商品、草稿和店铺配置，网络报价在商品锁外进行，保存时在商品锁内重读，
  冲突返回 `DRAFT_CHANGED`。公共模板不保存首个 SKU 的结果冒充所有 SKU 的售价。
  缺失信息返回 SKU、市场和字段；AI 只补问实际缺失项，正常核价无需先读取成本。
  显式查看成本可用 `draft_attributes_read(scope=sku)` 的 `cost_cny` / `cost_source`。
- 内部批量引擎 `pricing_batch.calculate_sku_prices` 使用 `pricing_runtime.PricingSession`
  和 `services/pricing_shipping.py` → `international_shipping/ShippingModule.quote()`。
  `items: [{sku_id, input}]` 仅是内部引擎契约；HTTP 和 Agent 不再自行拼装。
  一批共享店铺、汇率、Mercado token、Ozon OGL 运价快照、Yandex 可用物流方案；每个 SKU 独立计算成本与运费。
  批次返回逐 SKU 结果和 `metrics`；`erp.pricing` 记录每 25 个 SKU 的进度与耗时。
  模块负责 Ozon/Yandex 费率版本和三平台物流计算，返回全部候选；ERP 使用既有汇率
  换成物流字段 CNY/USD。Yandex 按最低运费选择；OGL 按完整费用反推后的最低达标售价选择。模块通过回调使用当前售价算法校验货值，
  不反向导入 ERP。Mercado 的内置运费表已删除，多销售国家分别请求报价并计算售价。
  凭据由项目装配，Mercado token 仍通过 `get_mercadolibre_access_token()` 取得。
  证据进入既有核价依据和指纹；逐 SKU 核价固定模板版本。无新增 tariff HTTP 端点，
  模板导入/预览/发布支持系统设置界面，模块 CLI 继续可用于离线维护，详见 `erp_web/international_shipping/README.md`。
- Ozon 统一使用 `international_shipping/ogl.py` 的单 SKU、数量 1、单独成包 OGL 预估。
  随应用分发 `ogl_20260824.json`，由官方 2026-08-24 Excel 导入并校验内容哈希；后续仍用原 CLI
  导入完整 OGL 表并显式指定生效日期。旧 `current.json` 指向第三方表时，新核价使用内置 OGL
  版本，历史文件不删除；显式指定旧版报价则报错要求重新核价。没有双轨计费或失败回退。
  `tariff_store` 负责版本和读取，`ogl.py` 只负责纯运费、准入及服务费分段，不调用网络或 Store。
  自送与揽收共用国际运价，不再查询或按 XY/Ural 仓库名称匹配评分组。国内费用由原有固定成本承担。
  物流货值档与电子服务费保底/比例/封顶区间分别求解；`pricing_service` 在每段复用原有利润求解，
  `PricingCommissions` 从真实 Ozon 佣金表提供全部价格档，在最终发布币种的分价上取得回执并检查利润。
  电子服务费单独展示，纳入总成本、加价基数和指纹，不混入销售佣金、支付费或国际运输费。
  旧模板与 SKU 手填运费不参与 OGL；新模板保存自动模式。旧报价保留展示，但前端应用及发布校验
  要求 OGL 与服务费依据，须重新核价。公共 `ozon_discovery_ms` 响应字段保留为 0 供既有读取方使用。
  官方页面未能读取，运输费以官方 Excel 验证；电子服务费及结算汇率细则按任务参考规则暂行，
  不声称已核实最新公告或实际结算。货值按 CNY 栏及本轮汇率预估，见 [OGL 核价说明](ozon-ogl-pricing.md)。
- Yandex 自动运费仅使用远端发布仓指定物流商与运输模式对应的运费标准。`AppContext.yandex_logistics`
  装配 `services/yandex_logistics_service.py`；`schemas/yandex_warehouses.py` 定义独立契约，
  `ConfigStore` 在 `app_config.yandex_warehouses` 中按 Business/Campaign 保存仓库 ID、物流商、
  运输模式和配置版本。本地报单仓、收货地址与跨境巴士授权不参与核价候选或失效判断。
  系统设置的「Yandex 发布仓库」提供同步、单选运输模式、停用及解除关联入口。模式从当前
  运费表按物流商和服务生成，Extra Small 等独立服务单独选择；同一仓库不跨 Economy/Express 比价。
  同一模式的适用运价由包装与货值筛选，价格直接读取当前运费表，不另存一份。
  已持久化的旧 `route_ids` 保留读取：全部有效且仅属于一种模式时解析为该模式；跨模式或失效
  则提示重新选择并禁止参与核价。用户保存后写入 `shipping_mode`，不再写运价行列表。
  `pricing_workspace_routes.py` / `pricing_workspace_facade.py` 提供 GET `/api/yandex/warehouses`、
  POST `/api/yandex/warehouses/refresh` 与 POST `/api/yandex/warehouses/logistics`。
  保存检查店铺归属、仓库可用性、物流商与运输模式的一致性，并在配置短锁内校验版本；
  通用应用配置保存不能覆盖此独立配置，远端请求不持有配置锁或商品锁。
  平台仓库列表来自官方 v3 warehouses（FBS 且 API 可用），不按名称猜测物流商；
  当前仅支持 `stock_update_mode=business`，仓库组不能指定组内单仓，故阻止参与此流程。
  无有效映射时停止核价；手填 Yandex 运费无法确定发布仓，须改为自动核价。
  每批先刷新平台仓，再按包装/货值筛选各仓指定模式的运价；同步失败不沿用旧快照。
  `services/yandex_pricing_selection.py` 对同一草稿全部已选 SKU 取共同可用仓库，按每个 SKU
  一件的运费合计最低选仓；无共同仓库时整批不得应用售价，错误逐 SKU 返回，不拆仓核价。
  仓库实际 Campaign 来自 `models[].campaignId`，独立保留授权 Campaign；不得把授权探测店铺
  当作全部仓库的发布目标。各渠道货值试算与最终售价均按其实际 Campaign 查询佣金，费用缓存
  及原生批量请求按 Campaign 隔离；统一选仓后的必要重算复用本轮汇率、运价和费用回执。
  选中方案保存在逐 SKU `calculation_basis.shipping_evidence.fulfillment_plan`，纳入报价指纹。
  应用时复核配置及方案；发布准备刷新仓库，每次发布写入前复核方案版本，库存仅写该
  `publish_warehouse_id`，不再使用授权探测时选出的最小仓库。旧报价可读取，但须重新核价。
  系统设置与核价明细展示关联；关联是用户提供的运输配置声明，不声称已通过官方接口核实
  陆运/陆空联运服务，不按仓库名称推测，也不修改平台侧物流配置。
- 店铺授权配置（`store_auth.auth_detail_json`）中的 `listing_currency` 是核价与发布
  的唯一币种事实源。注册表、国家、站点、草稿历史值和前端 option 都不是发布币种
  来源，也不得作为 fallback。
- 上架前佣金由 `services/pricing_commissions.py::PricingCommissions` 查询，`PricingSession`
  与自动运费的售价回调共用同一实例。纯核价引擎按最终舍入售价校准官方费用，回执进入
  `calculation_basis.commission_evidence` 和指纹；手动运费、手动售价也必须取得有效回执。
  物流预检只校验本地输入；Yandex 普通渠道没有货值门槛，不调用售价回调。
  轻小件与 Ozon 仍按最终舍入售价校验货值门槛，同一 SKU 的相同运费条件共用回调结果；
  选中渠道后的最终售价继续查询官方佣金，不以其他 SKU 的费率或零佣金替代。
  官方费用按分舍入时，已查询售价满足定价及物流下限且舍入余量不超过原币一分即可采用，
  避免费率反推造成价位振荡；依据仍绑定该售价的真实佣金回执。
  已删除手填佣金请求字段与 16%／20% 默认值；旧配置和报价继续可读，缺少上架前官方依据的
  历史报价不能用于发布。Mercado 多国家即使使用手动运费，也分别查询和计算。
  Yandex 使用所选仓库对应 Campaign 的 `/v2/tariffs/calculate`，只取 `FEE`；按店铺发布币种
  传售价并核对回执币种（`RUR` 与 `RUB` 等价），不把 CNY 售价预先换成卢布。
  `PricingSession.calculate_many` 汇集同轮缺少的费用，由 `services/yandex_commission_batch.py`
  按当前账号、币种和完整商品参数去重，每次最多 200 项；已经完成的 SKU 不再执行。
  `CommissionBatchPending` 仅在这次同步核价的内存调用栈中收集待查价格，不落库、不创建后台任务。
  完整且能复现实际收费的 `value/valueType`、价格区间、保底/封顶规则进入
  `schemas/commissions.py::CommissionRule`；`pricing_fee_rules.py` 负责规则求值和缓存命中验证，
  `pricing_solver.py::PricingGoal` 把利润率与成本加价统一为售价、佣金和目标成本的不等式，
  分别解析固定、比例、保底、封顶区间，仅在半分费用舍入误差限定的区间校正分价。
  求价与利润目标检查使用精确分数，避免浮点和汇率除法误差；不再循环 64 次反推有效费率。
  最终利润按实际发布的分价和该价格的官方费用计算；自动定价必须真实达标，不能用
  已显示为 30.00% 的舍入值代替校验。手动售价不为满足利润目标而自动改写。
  本地估算明确标记 `local_calculation`，不能作为官方佣金依据；候选售价始终再次批量获取
  真实费用回执后才能返回成功。缺失规则、跨档或边界项按实际候选价补查，最多 16 轮，
  失败停止受影响项，不能把未核验结果留作可应用售价。回执条数和顺序必须与请求完全对应。
  日志逐批记录费用请求次数、报价项数与耗时。官方回执只在本轮复用；已解析规则缓存一小时
  （最多 4096 项），按店铺/授权、币种、类目和除售价外的完整报价参数隔离，键中仅保留摘要。
  新一轮命中规则后本地求价，最后仍批量核验；规则失效或费用变化时修正规则并重新核验。
  `campaignId` 不能与 `currency` 同传；参数错误正文中的 `Forbidden` 不代表授权失败。
  核价保留脱敏后的平台错误说明，HTTP 400 只阻止相同错误请求，不阻断整个费用接口。
  此只读费用查询显式设置最多 3 次的共享重试预算，临时 HTTP 故障与连接中断共用预算；
  参数与权限拒绝不重试，其他接口保持各自原有预算，外部写入仍不得重放。
  Mercado 区域类目使用 `/sites/{site}/listing_prices` 的销售费用总额，不重复加固定收费；
  CBT 由 `services/mercado_commission_tariffs.py` 按官方类目 API 的 Domain 读取官方类目费率页
  自用数据，并从当前 Pricing 页读取低价固定收费；这是官方网页数据源，不是公开 Seller API。
  缺少完整国家规则（如阿根廷）及哥伦比亚低价商品豁免未确认时阻断，不套用其他国家费率。
  Ozon 由 `services/ozon_commission_tariffs.py` 从当前官方说明发现 ChinaHK 费率表，按已有
  类目 owner 的真实类型 ID→俄文类型与所属类目精确匹配，再按实际品牌及卢布价格档计算
  rFBS 基础佣金。保存表格哈希、生效日与命中单元格；品牌变化使报价失效。该表不覆盖
  其他卖家地区／履约模式、地区及推广系数，不使用 `/v5/product/info/prices` 等已有商品查询。
  公开资料统一通过 `services/commission_sources.py` 的受管 HTTP 读取；结构变化或查询失败
  返回明确错误，不使用旧表或默认比例。当前覆盖范围外的费率仍需另行核实。
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

- 草稿箱的「已发布」表示该草稿已成功提交发布任务：索引按 `publish_jobs` 中同商品、同草稿的持久记录归类，入队后立即生效，失败或后续编辑不撤销该归类；未入队的预览不计入。该展示状态不改写草稿 `publish_status`、SKU 回执或任务结果，不能据此判断平台已上架。界面加载及修改响应均返回全部草稿，由现有分区筛选显示。

- 主 Agent 将“做好发布准备／准备发布／完成草稿”理解为资料完成目标：按目标站点补齐文案、销售图片、类目、公共及 SKU 属性、选品与库存，核对事实并计算应用有效售价，再准备发布素材与最终校验。`draft_prepare_for_market` 不包含属性填写，必须继续处理；单项任务保持原范围，准备不构成发布授权。说明同时由 `GlobalAgentChatService.NATIVE_INSTRUCTIONS` 和 `erp_web/assets/prompts/global_chat.json` 提供。
- `prepare_product_publish` 使用 `prepare_and_evaluate_publish_validation` 的阶段结果：基础资料失败就返回并保存该失败，不再追加最终图片校验；准备成功仍使用该流程的持久化后回读与最终校验。
- 页面流程为基础资料预检 → 准备素材与预览 → 最终发布校验 → 确认发布。`precheck_publish_payload` 对 Yandex/Ozon 使用 `image_stage="source"`，检查图片来源与选择，不要求已上传；预览准备后、AI `product_publish_validate` 和入队准入仍执行最终校验并验证 digest。基础预检通过不能直接入队。
- 核价指纹由 `pricing_service.pricing_calculation_fingerprint` 生成，v2 对 JSON 数字统一整数表示，避免页面往返将 `18.0` 写成 `18` 导致失效。`pricing_calculation_fingerprint_matches` 保留旧摘要读取，并仅为历史 Yandex 费用请求恢复明确的 float 字段类型；金额、尺寸或其他事实变化仍须重新核价。
- 条码资料按目标市场和类目定义显示，共享资料不再提供 UPC 豁免开关。Mercado 的 `EMPTY_GTIN_REASON` 是目标 `attributes` 中的可编辑类目字段，`DraftBarcodeFields.vue` 在类目与发布预检区域展示平台候选原因；预填仅使用平台返回的默认候选，已有值和明确清空均不覆盖。`mercadolibre_attribute_contract.py` 在逐 SKU 编译时只为缺条码的 SKU 提交原因，有实际条码时提交 GTIN。历史 `allow_gtin_exemption` 仅在 Mercado 且尚无显式原因字段时保留读取支持，变更不会撤销其他市场预检；新编辑使用目标属性。
- `DraftWarrantyFields.vue` 在 Mercado 与 Yandex 发布区域展示各自的保修设置。Yandex 由 `YandexWarrantyFields.vue` 编辑草稿 `guarantee_period`（时长、单位、可选说明），保存后由 `product_model/yandex_warranty_model.py` 校验，经 `publish_yandex.py` 写入官方 `catalog.offer.guaranteePeriod`；不补默认期限，未填写给出提醒，填写不完整或无效则阻止发布。修改仅使 Yandex 预检与发布预览失效，不更改已发布商品。官方契约见 [updateOfferMappings](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/updateOfferMappings)。
- Mercado 保修仍使用 `DraftWarrantyFields.vue`。未配置时界面将“无保修”写入草稿 `sale_terms`，随预检前保存提交；已有保修类型、时长和其他销售条款保留。该字段继续使用既有持久化格式，但前后端均只使 Mercado 的预检失效。后端仍校验实际提交的条款，不在 payload 构造阶段静默补值。

- `erp_web/product_model/sku_model.py`：商品实际 SKU、来源快照、草稿选品与每行卖家编码的唯一契约。商品保存全部实际规格；草稿通过 `sku_id` 引用，卖家编码绑定 `draft_id + sku_id`。已发送的编码和远端关联不能由普通保存覆盖。
- `erp_web/runtime_units/publish_context.py`：一次发布评估共享类目定义及类目币种查询结果。币种结果仅在当前上下文及其 SKU 投影间复用（包括空结果），按类目与授权隔离，不跨请求缓存或持久化。
- `erp_web/runtime_units/sku_publish_projection.py`：逐 SKU 合并草稿覆盖值、目标属性和独立核价结果，并校验平台组合条件。先缩小输入再复制：临时单品视图只带当前 SKU、当前平台草稿和图片池，不带整组选品、来源规格或历史预检结果，也不写回主档。组内校验只保留 `SkuGroupingMember` 的身份与属性，不保留所有商品投影；重复组合逐组返回有界说明，受影响身份保存在 `PublishValidationIssue.affected_sku_ids`，不复制规格名称与逐 SKU 错误。
- `erp_web/product_model/precheck_model.py` 是预检错误合并的纯领域 owner。按原因、字段、级别及修复建议合并，图片 ID 归入 `affected_asset_ids`，同类 SKU 问题只附去重 `affected_sku_ids`；不同属性或不同原因不能合并。SKU 组校验逐项汇入累加器，不再生成含完整预检的 `sku_results`。后者仅在发布执行回执中继续存在，不属于预检。
  草稿根及各站点的预检在 `normalize_platform_draft` / `normalize_draft_target_site` 读取和保存边界归一化；历史逐 SKU 副本先合并独有错误再移除，下次写入仅保存新格式，不触及原生对话审计或远端发布回执。界面显示合并问题和影响数量，AI 与 HTTP 使用同一合并结构，不能只在模型输入阶段隐藏冗余。
- `erp_web/runtime_units/sku_precheck.py`：平台无关的纯预检问题汇总。以错误码、类目差异字段和受影响 SKU 集合关联必填缺失与整组空值检查；其它组合约束独立阻断。以 `PublishValidationIssue.affected_sku_ids` 和 `PublishRelatedIssue` 保留规格身份与关联校验，并按类目定义定位填写入口。
- `runtime_units/publish_validation_session.py` 只承载单次校验的共享读取：店铺配置、相同仓库方案的验证结果和图片检查缓存。`SkuGroupPublishingAdapter.validate_draft` 每次创建新作用域，经 `PreparedPublishContext.with_product` 传给叶子；基础与最终校验不共用缓存，不持久化、不跨请求保留。
  Yandex 在组入口规范化一次，叶子检查已准备的 SKU 投影；直接调用 `validate_yandex_draft` 仍在入口规范化。`yandex_attribute_checks` 合并属性检查，必填判断复用 `product_model/category_model.py::unresolved_required_attributes_in_draft`。
  组入口检查成本、尺寸及库存，叶子继续检查核价指纹与店铺币种身份，避免重复报告同一变化。
- 图片基础预检只检查引用、可读取性及大小，完整解码在素材准备时执行；最终校验继续检查文件内容摘要与交付记录一致。同一次校验内相同资产只检查一次，资产资料不同不得复用；下一次校验重新读取文件和托管配置。
  素材准备通过 `ImageDeliveryService.prepare_product` 最多四路并发传输，全进程共享上限；每个线程独立创建存储客户端并继承请求审计上下文，失败后停止派发新图片，等待在途请求结束。保留按内容对象先检查再上传、源文件及配置变化校验，网络完成后仍经原短锁持久化交付记录。
  `schemas/publish.py::PublishPreparationProgress` 是实时进度契约，observer 从 HTTP facade 经预览用例、SKU/平台适配器显式传入图片交付服务；图片按唯一资产计数，完成包含直链确认、已上传素材复用和本次上传，不能解释为已发送字节或新增上传数。
  `/api/publish-payload-preview` 在请求 `Accept: application/x-ndjson` 时逐条推送进度，最后返回带业务状态的 `result` 回执；普通 JSON 调用不变。HTTP 路由只编码与刷新响应，不编排上传；客户端断开后停止推送，服务继续完成原持久化边界。前端 `publishPreparationStream.ts` 使用公共 Axios 客户端读取增量回执，各目标编辑器独立保存进度，上传 100% 之后仍显示最终校验/预览阶段，仅最终成功回执可产生确认指纹。
  前端 `previewPublishPayload` 仅对该长请求设置 `timeout: 0`，等待真实结果；普通 API 超时不变，S3 单次请求仍受后端 30 秒超时控制。浏览器关闭或断网不表示后台操作已撤销。
  Yandex 数值约束在定义转换时保留零值，兼容读取空上下限；预检复用属性编译规则，在上传前报告越界、非有限数字或无效规则，不能等到预览构造时才暴露 `Decimal` 异常。
- Yandex 型号按具体类目要求判断，不设通用“型号必填”阻断。无类目参数时省略 `parameterValues`，显式提供时必须为非空数组；类目必填、枚举、单位、文本格式校验仍保留。依据：[官方商品写入接口](https://www.yandex.ru/dev/market/partner-api/doc/en/reference/business-offer-mappings/updateOfferMappings)、[官方类目参数规则](https://yandex.ru/dev/market/partner-api/doc/en/reference/content/getCategoryContentParameters)。
- `erp_web/runtime_units/sku_publish_adapter.py`：注册表唯一发布入口，委托 `sku_precheck.py` 汇总 SKU 预检错误和提醒；编译带每项身份的冻结 SKU 清单；平台叶子适配器保留原生单品 I/O。每项写前落盘、写后保存响应，成功项按内容指纹跳过，未知结果禁止再次创建，异步确认仅推进原任务。
  Yandex 经 `runtime_units/yandex_publish_batch.py` 按同一实际 Business / Campaign 及接口上限合批
  商品资料、上架条件、价格、库存和确认读取；发送前及响应后仍逐 SKU 持久化原检查点。
  发布计划与库存证据保存选中仓库及其实际 Campaign，确认查询使用该 Campaign。
  合批不改变发布成功判定、手动重试与未知写入规则，不增加重启后续发。
- `erp_web/facades/agent_tool_visibility.py` 在原生工具集的 `prepared` 阶段检查目标草稿已选、启用的 SKU。缺包装边长时，当前允许的商品读取、原图识别、运费区间试算与估算确认工具直接可见；无页面目标时从本轮真实的草稿读取或核价缺项回执定位。工具可见性不产生写入或授权。
- 核价页或主对话可以发起包装外尺寸估算。`package_estimate_capabilities.py` 使用草稿已选 SKU 和商品版本，复用核价批处理比较区间端点，保留整组 SKU 共同选仓语义。端点报价只供估算比较。确认建议使用 Pydantic AI 原生审批，服务端快照的 `requires_user_confirmation` 标记让通用自动批准策略跳过需要人工核对的建议。确认后经 ProductStore 保存建议值和逐边依据，商品编辑及重采集按当前值保留适用标记。
- 前端 `ProductSkuEditor.vue` 维护商品事实，`DraftSkuPanel.vue` 负责草稿选品和覆盖，`actions/pricing.ts` 按 SKU × 目标调用现有核价引擎。包装资料或费用改变后必须重新应用售价。
- `erp_web/product_model/sku_image_model.py`：SKU 图片资产引用与原图地址迁移的纯函数 owner。采集统一下载规格图并按来源去重；内部 `image_asset_id` 是唯一关联，草稿以同名覆盖字段单独选图。旧持久化 `image` 只在读取边界迁移，发布不再按 URL/路径匹配。
- 前端 `SkuImagePicker.vue` 从素材池选图；商品素材池“关联 SKU”复用商品保存入口；草稿 V2 不提供配图入口。`replace_selected` 处理结果只替换当前草稿的相应 SKU 引用，换图不使核价失效。无调用方的 `set_sku` 素材标记 action 已删除。
- 商品 schema 当前为 4；保留本地商品及草稿的 SKU 图片地址迁移能力，不恢复旧的按下标选品格式。详见 `docs/sku-workflow.md`。
- `erp_web/http_route_units/publish_routes.py`：发布预检、payload 预览、非 Mercado 平台同步发布、
  发布队列及 `POST /api/publish-bus/reconcile`。reconcile 只查询已持久化任务，不重放发布修改。
  Mercado Libre 只允许预览、确认与 PublishingBus 持久队列。
- `erp_web/http_route_units/get_routes.py`：发布任务列表与指定 Job 详情。
  店铺商品发现、查询和销售状态管理由在线商品模块负责。
- `erp_web/facades/publish_facade.py`：HTTP 层唯一发布 facade；业务编排进入
  `erp_web/runtime_units/publish_workflows.py`。
- `erp_web/runtime_units/publish_result_confirmation.py`：发布单次确认与回写的唯一 owner。
  提交回执进入 `pending_confirmation`；统一任务队列安排有限只读查询，人工入口共用预算与冷却。
  确认成功、失败和部分成功回写原任务及草稿；查询失败保留受理事实和原回执。价格隔离转 `needs_attention`，停止普通自动查询。
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
  重启后提交停止，手动重试跳过已完成及结果未知的写步骤。价格写入按已验证 `only_default_price` 分流
  Business/Campaign 级接口，价格进入隔离区时阻断自动确认。
  新编译的目录商品不携带 `basicPrice`，价格统一由上述独立步骤写入；已持久化的批准
  payload 与 checkpoint 保留原内容和恢复能力。图片按最终 URL 保序去重后再取前 30 张，
  不改变首图、草稿素材引用或其他 SKU 的图片。标题、描述仍使用草稿文案，不由发布器推测改写。
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
- `services/watermark_service.py` 使用 Pillow 和随应用分发的 Noto CJK 字体合成斜向平铺的文字／自定义图片水印（两者可同时使用），
  预览与发布调用同一实现。`watermark_assets.publication_image` 在仓库外临时目录生成副本，
  远程原图复用受管安全下载；不覆盖原图，不处理物流报单图片。
  水印在显式发布准备时覆盖共用图和已选 SKU 图，有文字或图片内容即生效，全部清空则不添加；原始内容哈希与交付内容哈希分别保存，
  水印配置指纹变化使旧交付失效。远程素材保留原 URL，重新准备不得下载已加水印的副本再叠加。
  Mercado 独立图片上传也使用同一合成服务，在 `platform_uploads.mercadolibre` 保存水印指纹与原图哈希；
  配置变化必须重新上传。在线商品新选入的源图片同样处理，已有远端图集仅排序／移除不重加水印。
  队列沿用已确认的冻结图片，不追改已发布或已入队任务。回归见 `tests/test_watermark.py`。
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
- `POST /api/v1/product-research/keywords/search` 经上述 route/facade 调用 `services/product_research_keywords.py`，只在用户点击时查询 Amazon US 相关热搜词第一页（最多 100 条）。`stores/research_keyword_store.py` 在独立 `data/research-keywords.sqlite3` 保存按凭据及查询条件隔离的 24 小时缓存，并通过短事务占位阻止并发重复扣费；网络请求不持有事务。显式重新获取绕过缓存，失败不自动重试，缓存命中不复述旧回执为本次扣量。`ProductResearchKeywords.vue` 只在本地筛选、排序、翻批和回填关键词，不自动查询商品。该入口为界面专用，不新增 Agent 工具。
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
- `product_sku_facts_update` 是 SKU 来源资料写入口，声明在 `runtime_units/product_write_capabilities.py`，契约在 `schemas/product_facts.py`，写入由 `ProductStore.update_product_sku_facts` 持锁执行。请求明确商品 ID、商品版本及逐 SKU 差异；`product_read` 返回对应版本和 SKU 事实。整批校验后更新商品，所有引用草稿读取同一事实。未知 SKU、重复 SKU、过期版本及非法数值均在保存前拒绝。旧 `draft_sku_package_update` 已删除。
- `product_model/source_facts.py` 定义来源归属、Schema 4 → 5 迁移与受影响草稿失效；`db.py` 在商品保存事务内更新全部引用草稿（包括非最新副本）。`scripts/migrate_product_source_facts.py` 提供只读预览和带完整备份的事务清理。商品非空值优先，空值只接受一致草稿值，冲突副本删除；旧商品级采购成本保留为待确认参考，不分配到 SKU。历史报价依据、发布及订单快照不重写。
- `ProductSkuEditor.vue` 承担来源资料及批量编辑；草稿 SKU 和核价缺资料入口定位商品编辑器。刷新同步商品上下文，来源变化使旧报价失效；只发生来源与校验元数据变化时保留未保存的草稿内容和费用。回归见 `tests/test_product_source_facts.py`、`tests/test_product_facts_native.py` 及前端相关组件测试。
- `draft_sku_attributes_update` 仅写平台类目属性，不能写包装字段。通用 `draft_save` 仍为 internal，不为包装编辑重新开放整对象保存。

### 属性事实边界

主对话不能将混合 SKU 的汇总描述套给每个规格，不能从图片比例猜测尺寸、重量、品牌或认证。读工具提供来源文字与结构化 SKU 事实。用户要求从图中取证时可按需读取对应商品图，信息无法辨认时在主对话说明。

草稿根级 `brand`、`model`、`stock`、`sku`、`upc`、`package_dimensions` 和 `publication` 已退役，`draft_stock_update` 已删除。`draft_read` 按所选 SKU 返回库存、编码和逐目标售价摘要；`draft_attributes_read(scope=sku)` 返回 SKU 的有效条码、成本、包装和差异属性。公共费用/定价规则、图片、属性和整组发布状态继续保留。单 SKU 平台请求使用 `sku_model.single_sku_publish_draft` 临时派生销售资料，不持久化第二份草稿销售字段。UPC 的 `ProductStore.assign_upcs_to_product` 在商品锁内重读后，通过数据库事务为缺条码的启用 SKU 各分配唯一号码；HTTP `/api/assign-upc` 与 AI `upc_assign` 均要求明确商品，允许限定 SKU，保留已有条码，号码不足整批回滚。

## 订单通知与订单快照

- 订单中心 AI 接入复用主 Agent 的 `@ai_tool` → 显式 Catalog/allowlist → Binding Scope →
  `AiToolRuntime` → 原生 Pydantic Tool 链路，不新增订单 Agent、模型循环或审批协议。
  `runtime_units/order_capabilities.py` 提供详情、摘要、显式同步、通知处理、来源确认、采购登记/作废、
  采购查询/同步和本地地址备注；`platform_orders_query` 读取同一份快照，支持状态、关键词和批量货号检索。
  返回的 `items.id` 是其他订单工具的 `order_id`，不是平台订单号。
- 订单检索由 `order_notification_facade.query_orders` → `services/order_query_service.py` 编排，
  `OrderProcurementStore.iter_query_facts` 在同一读事务按批次读取当前账号订单、发布店铺绑定、已确认来源和有效采购记录。
  `services/order_source_resolution.py` 与订单详情共用纯来源解析；远端身份、订单行签名、重复行及歧义检查不省略。
  不读取当前草稿、不解析 SKU 编码、不调用远端；来源字段匹配在分页前完成，避免只过滤第一页。
  `article_numbers` 按编号边界匹配任一货号，`q` 为关键词模式，两者互斥；平台留空覆盖所有已配置账号。
  `items[].items[].matches` 保留来源类型和引用；已确认来源、冻结发布来源、采购记录各自独立，歧义候选不能当作确定命中。
  `coverage` 记录本地扫描范围、缺失/歧义、未配置平台、分页和快照时间，不承诺平台实时或全历史覆盖。
  `queried_at` 仅为查询时间，数据时效看各订单 `checked_at` 与平台 `sync_status.last_success_at`。
  工具只返回订单及行摘要，完整采购、物流、金额明细按需读取详情；通知仅在 `include_notifications=true` 时读取，且按所查平台账号隔离。
  HTTP 订单中心列表继续使用 `read_orders` 的页面契约。回归：`tests/test_order_query_service.py`，覆盖批量货号、跨批次分页、来源变更/作废、账号隔离和原生直接工具/CodeMode。
- `runtime_units/order_purchase_capabilities.py` 提供 1688 单条/批量候选、地址、预览、创建、
  签约状态、支付、原请求核验和官方收银台链接；预览不会下单，创建不会付款，支付单独审批。
  `AlibabaSelfPurchaseService.approval_facts` 只读冻结业务事实并检查账号归属；创建/支付审批绑定
  完整商品规格与数量、金额、渠道、地址、账号及原请求，执行时重核。过长审批拒绝截断，要求缩小采购范围。
  原有请求号、采购数量占位、实时价格复核及未知回执防重继续由采购领域维护。
- `runtime_units/order_fulfillment_capabilities.py` 提供收货仓读取/确认、履约详情、合作服务查询，
  以及面单、包裹、方案、提交、核验、关联、重试、取消等命令。履约修改与收货仓确认统一审批，
  取消履约不影响有效收货绑定。已取消订单通过 `order_fulfillment_update` 的 `plan` 设置本次方案，`FulfillmentService.command` 先只读核实原单已作废，再由 `FulfillmentStore.change` 在同一短事务中复核版本、订单状态、账号、收货绑定和采购占位，调用 `archive_cancelled` 归档旧单并保存新方案；保存后须显式 `submit`，自动规则不能直接重报。`ReceivingWarehouseService.save` 只保存绑定，不归档或重启履约；旧请求的 `fulfillment_revision` 保留读取但不再用于绑定校验。网络查询不持有事务锁，不提供单独重新安排命令。
  重新确认收货绑定、保存服务并显式 submit 后才允许重报，`attempt_history` 保留原报单，`awaiting_resubmission` 阻止已有自动规则越过人工确认。
  绑定当前版本、账号和业务事实；保存资料可能推进既有自动履约，不得把本地提交回执当作远端完成。
  物流凭据、全局承接规则配置及本地面单文件上传继续走可信界面；AI 可以获取平台面单或填写已知公开面单 URL。
- `runtime_units/order_capability_support.py` 持有订单领域 Scope、错误转换和审批支撑；
  `agent_capability_facade.py` 以当前 AppContext 显式绑定。HTTP 与 AI 共用原服务及采购后的同步逻辑，
  facade 的可选 `context` 只用于可信装配，不向模型开放。工具网络仍继承 Runtime 的 deadline 和取消上下文；嵌套领域请求只能收紧期限，面单子操作不得覆盖上层停止检查。
  `schemas/order_capabilities.py` 仅补充调用形状，业务数据复用现有 Schema；`OrderDataResult.data`
  包装复用的领域结果，采购预览/提交保留具名 `record` / `records` 回执。
- `OrderDetailPanel.vue` 将当前 ERP `order_id` 纳入已有页面背景；切换、关闭和停用沿用背景生命周期，
  不发送表单内容或产生额外写权限。新增能力和明确保留的界面入口均登记在 Endpoint Coverage Manifest。
- 约束适配核对：本次使用已安装 Pydantic 2.13.4 / Pydantic AI 2.44.0；原生字段已支持
  [数值及字符串约束](https://docs.pydantic.dev/latest/concepts/fields/)，项目旧编译白名单缺少
  `exclusiveMinimum` / `exclusiveMaximum` / `pattern`，现无损传递并在既有轻量校验器补齐对应断言。
  适配范围只限现有工具 Schema 边界，Pydantic TypeAdapter 仍执行精确参数及业务模型校验；
  若项目移除轻量 Schema 校验边界，这些断言随该边界一同移除，不引入新的校验或执行协议。
  回归入口：`tests/test_order_ai_capabilities.py`，覆盖真实领域服务、原生审批允许/拒绝/完全授权、
  直接工具/CodeMode、身份与审批篡改、未知创建、支付防重及履约版本冲突；网络全部使用替身。

- HTTP 唯一入口：`erp_web/http_route_units/order_routes.py`，覆盖 Mercado Libre/Ozon/Yandex 回调和 `/api/orders` 本地查询及明确用户命令。
- 领域装配：`erp_web/facades/order_notification_facade.py`。平台差异集中在 `runtime_units/order_notifications.py` 与 `runtime_units/orders_{mercadolibre,ozon,yandex}.py`。
- 后台处理：`erp_web/services/order_notification_service.py`，领域任务领取、平台隔离和回调重试；不参与 Agent 生命周期。导航进入订单中心时检查自动同步；`useOrderCenterEntrySync` 只监听实际导航进入，初次挂载、焦点恢复、详情参数变化和本地轮询不触发。后端订单库 `settings` 按平台账号原子保存自动同步尝试时间，间隔由系统设置指定（默认及最低 5 小时），失败也计入且跨窗口/重启生效；手动同步不受该间隔限制。主动同步失败不自动重试，历史重试和过期执行记录转为失败；后台不再创建定期对账任务，旧 `sync_schedule` 表不再读取。
- 持久化：`erp_web/stores/order_notification_store.py`，独立订单库中的收件箱、快照、租约和未读提醒；`order_notification_migration.py` 仅执行主库历史通知的幂等导入。
- 共享契约：`erp_web/schemas/orders.py`。AI `platform_orders_query` 与界面读取同一份本地快照，不触发同步远端查询，不获取回调凭据。
- 金额：`orders_yandex.py::normalize_yandex_amount` 负责订单及 SKU 行的十进制金额归一化，`OrderAmountBreakdown` 保留付款、补贴和积分抵扣。`OrderAmountDetails.vue` 明确商品金额与旧付款快照的口径，不将其标成净到账；明细按平台行小计展示，不再乘数量。
- 发货日期：Yandex 保留 `delivery.shipment` 的日期/时间精度，Mercado Libre 读取卖家发货 SLA，Ozon 使用 `shipment_date`；`orderPresentation.ts::deadline` 仅对带时区时间计算倒计时。
- 前端：`front/src/stores/orderNotifications.ts`、`OrderSummaryCard.vue`、`OrderCenterPanel.vue`、`OrderDetailPanel.vue` 和 `OrderAlertBanner.vue`；原发布 store 中的单平台订单状态已移除。
- 订单中心页面查询由 `read_orders` → `services/order_center_query_service.py` 编排；筛选契约见 `schemas/order_center.py`，前端控件为 `OrderCenterFilters.vue`。通过采购、履约和收货绑定 Store 批量读取当前账号的本地事实，在分页前完成单号／来源／备注检索及阶段、物流、仓库、异常和截止日期的组合筛选；状态页签数量沿用同一筛选范围。仅日期或无时区的截止信息不计算小时级超时，明确时区的时间按请求时区换算。查询不创建履约记录、不调用远端、不触发同步；已归档单号可检索，当前阶段不使用历史报单推断。AI 货号证据查询继续使用原有 `OrderQueryService`。
- 原 `/api/mercadolibre/orders` 和 `mercadolibre_orders.py` 已退役；接入、状态映射、持久化与验证范围见 [订单通知说明](order-notifications.md)。

### 订单买家聊天

- Yandex 订单详情的「联系买家」打开 `OrderChatDialog.vue`；仅点击「获取最新消息」调用
  `POST /api/orders/chat/history`，按平台分页读取完整历史。打开、发送成功、订单同步及本地轮询均不拉取消息。
- `POST /api/orders/chat/send` 创建或获取订单会话后发送文字，使用官方 `context` 参数；
  路由 → `facades/order_chat_facade.py` → `services/order_chat_service.py`，复用 Yandex HTTP 和外部请求管理器。
  聊天查询为只读，创建和发送为写入，无自动重试；账号与返回会话归属必须匹配本地订单。
- `runtime_units/order_chat_capabilities.py` 将 `order_buyer_chat_read` / `order_buyer_chat_send` 显式接入主 Agent，
  通过 `OrderChatCapabilityScope` 绑定 `order_chat_facade.create_service`，共用 HTTP 的账号与会话归属校验。
  读取只按需执行，不创建会话；发送使用原生审批，冻结当前 business/campaign、销售订单、订单状态与完整正文，执行时重核。
  正文不改写、不截断；超过摘要长度时在原生审批的完整 `message` 参数中展示，不扩展审批协议。
  发送未知回执携带 `outcome_unknown`，复用原生 Agent 已有写调用回执防重；不自动重发，不把成功回执解释为已读。
  拟稿/翻译由主 Agent 完成，不为其新增发送旁路；买家内容不是操作授权，附件不自动下载。
  买家正文语言按 `erp_web/assets/systemAgents.md` 的长期约定选择：用户指定优先，其次买家沟通语言，再次订单目标市场语言；Yandex 无其他依据时默认俄语。已批准或明确要求原文发送的正文不自动改写。
- `schemas/order_chat.py` 定义生成到前端的响应契约。页面仍只在请求及弹窗内存中保存消息、附件链接和 chat ID；
  AI 工具参数、回执与审批按既有原生会话历史持久化。不另建消息表、缓存、浏览器存储或后台聊天通知；外部请求管理继续保留脱敏请求审计。
- 回归入口：`tests/test_order_chat.py`、`tests/test_order_chat_ai_capabilities.py`、`OrderChatDialog.test.ts`；覆盖分页完整性、归属校验、手动获取边界、原生审批与未知回执防重、发送不回读及遮罩手势。

### 订单采购

- 销售订单支持多个商品及其 SKU 明细；`OrderDetail.lines` 只负责来源确认与采购建议数量，`OrderDetail.purchases` 保存订单级采购单及其多商品明细。商品分组仅使用可信发布关联的商品 ID，不能按标题猜测。
- `record-purchase` 仅要求销售订单、采购平台、采购单号与幂等请求号；商品关联和实际收货地址快照均选填。不校验采购总数与销售数的相等关系，追加采购无需作废已有采购。同一销售订单内的平台与采购单号唯一；`order_purchase_requests` 独立保存请求到采购单的映射。
- `cancel-purchase` 只作废整笔本地采购登记，保留历史，不取消远端采购、不删除包裹、不修改仓库报单。采购同步写回须重核账号、有效采购与代次；自动采购回执重放不能复活已作废登记。
- `DomesticParcel` 直接归属于销售订单，一条记录代表一个规范化国内运单。履约不读取采购数量或 SKU 分配；件数、货物说明、图片、采购平台/单号是包裹自己的资料，来源仅作说明。补寄直接新增包裹，原包裹不减量。
- `FulfillmentParcelDialog.vue` 分为保存待完善与保存并确认清单。件数未知可保存草稿；确认须有参与报单的包裹且件数为正整数。移出报单保存 `included=false`；请求省略已有包裹时也保留为移出记录。版本冲突保留输入并停止提交，重新打开核对最新清单后再保存。
- `FulfillmentService.merge_purchase_parcels` 只加入首次观察到的运单，手工编辑、排除及已移除运单由 `observed_waybills` 保护；新增运单清除 `parcels_confirmed`，不会自动更新远端。手工及自动报单统一要求确认当前清单。
- `services/fulfillment_packages.py::parcel_payloads` 只验证包裹自身完整性和运单唯一性，并生成 `package_of=1` 的 `package_list`；`num` 是包裹实际商品件数。`delivery_packages` 处理主图托管。采购来源选填，多来源可写入说明，不拼造单值采购单号。
- 多个参与报单的运单仍须在所选真实仓库服务中指定 `consolidation_service_id`；仓库打包锁定、账号归属、创建未知结果与远端防重边界保留。
- `facades/order_procurement_facade.py` 负责装配；`services/order_procurement_service.py` 负责唯一来源匹配、人工确认和详情契约；`schemas/order_procurement.py` 与 `stores/order_procurement_store.py` 管理订单级采购单、来源版本和请求幂等。自动采购仍精确校验供应商 SKU、冻结金额与地址，并阻止未决请求重复下单。
- 运行时只读写新结构。`scripts/import_order_center_data.py` 为一次性离线导入：按销售订单、平台与采购单号合并旧记录，按运单合并旧包裹，重映射自动采购回执；保留源库和核对报告。参见 [履约说明](crossborder-fulfillment.md) 的导入步骤。回归入口：`test_order_center_import.py`、`test_fulfillment_packages.py`、`OrderParcelModel.test.ts`。

- `runtime_units/order_source_bindings.py` 从冻结发布任务提取关联；`publish_bus.py` 在发布终态持久保存，采购服务装配时幂等回填历史任务。禁止按 SKU 编码反解、按标题猜配或以当前草稿替代发布时事实。
- `OrderCenterPanel.vue` 负责订单页面编排与筛选，`OrderListTable.vue` 承载紧凑列表与分页；`OrderDetailPanel.vue` 为可直达的订单详情抽屉，`OrderNotificationsDrawer.vue` 承载通知、同步异常与重试。
Yandex 交货信息由 `runtime_units/orders_yandex.py` 在订单同步及通知读取时调用 `services/order_handover_service.py`，按订单页批量查询并严格验证批次归属；`schemas/order_handover.py` 的结果随 `OrderSnapshot.handover` 一起持久化，由普通订单详情返回。`OrderHandoverPanel.vue` 只展示本地快照，不独立外发或刷新。批次分页失败走订单任务的失败与重试，保留旧快照；旧数据缺少该字段仍可读取。外部管理器仅把确切的批次搜索 PUT 归为只读，订单请求范围包含其 campaign 配额。
地址备注由 `facades/order_address_note_facade.py` → `stores/order_address_note_store.py` 在订单领域库独立持久化；契约见 `schemas/order_address_notes.py`，GET/POST `/api/orders/address-note` 只读写本地。按平台、店铺和原地址匹配，平台同步不覆盖；保存校验订单地址与备注版本。`OrderAddressNote.vue` 提供点击展开、关闭自动保存的气泡，复用完整遮罩指针手势并向外层订单传播锁定状态。

- Yandex 订单列表、详情和跨境报单统一使用已同步在线商品的图片。`order_procurement_facade` 按当前授权的 business + campaign 装配图片读取，`OnlineProductStore.thumbnails` 批量读取对应远端 SKU 的 HTTPS 缩略图；`OrderProcurementService.present_orders` 校验订单账号与 SKU 身份后补充图片，不发起远端请求、不改写原始订单快照。未同步、身份不符或图片缺失时显示占位，不回退到本地发布图片或 1688 采购图片。包裹编辑可从这些商品带入说明及图片，保存后为独立资料；已创建远端报单不因页面读取而自动更新。
- 其他平台仍由 `order_source_bindings.py::published_sku_image` 从冻结 SKU 图片覆盖/事实引用或公共主图解析缩略图。历史采购绑定继续保留读取与补齐能力，图片不参与绑定身份，也不影响已确认采购来源。
- `OrderThumbnail.vue` 统一商品缩略图及加载失败占位；`OrderProcurementLine.vue` 按 SKU 卡片承载来源确认与采购操作，`OrderPurchaseRecords.vue` 展示可展开的采购历史，`OrderSourceDialog.vue` 和 `OrderPurchaseDialog.vue` 分别负责来源编辑、采购登记。所有弹窗、抽屉共用 `WorkspaceDialog.vue` 与完整遮罩手势校验。
- `OrderIntegrationSettings.vue` 承载设置页回调接入。未验证的平台规格链接只能作为商品链接展示，修改来源规格或链接后必须重新核验直达能力。

### 1688 采购状态与物流

- 采购查询使用当前采购账号的服务端授权，账号连接入口见上文「1688 应用与账号授权」。
- POST `/api/orders/purchase-query` → `order_procurement_facade.query_purchase` → `services/alibaba_purchase_query_service.py`。请求只接受销售订单 ID、采购记录 ID 和查询种类；服务端读取冻结采购单号，网络前后校验店铺归属和记录未作废，不跨网络持有事务。请求与返回形状见 `schemas/alibaba_orders.py`。
- `order_notification_facade.py` 向 `OrderNotificationService` 显式注入 `services/order_progress_sync_service.py`：导航进入订单中心且满足 5 小时自动同步间隔、点击同步订单及平台通知保存快照后，按最新平台状态和当前报单阶段查询。平台已发货或已送达时，不再查询 1688 采购状态/国内物流和仓库报单；平台尚未发货且当前报单已有远端编号时，只刷新仓库状态；当前报单尚未确认成功时，刷新有效采购及创建待核实的仓库单。仅配置方案、提交中、失败、结果待核实和已归档的旧报单不算当前报单成功。单轮同订单去重，每笔网络请求前续租并验证账号与停止状态；查询期间不持数据库锁。单笔失败继续其他查询，各领域保留旧快照和错误，订单任务汇总部分失败且不自动重试附属查询。单笔人工查询入口仍可按需刷新。
- 列表、详情及履约页签定时刷新只读取数据库；详情编辑和提交期间暂停替换内容，旧响应不得覆盖较新的手动结果。采购与仓库单笔刷新入口保留。
- `services/alibaba_api_client.py` 固定 1688 HTTPS AOP 网关及三个买家只读接口，以 HMAC-SHA1 签名，通过 `managed_urlopen` 发送，显式声明 read 语义和 token 指纹，禁止重定向，不自动刷新 Token。物流先查运单，再按订单一次查询轨迹并按物流 ID 匹配；轨迹失败保留运单并返回警告。
- POST `/api/orders/purchase-sync` → `order_procurement_facade.sync_purchase` → `alibaba_purchase_sync_service.py`。登记成功后自动同步一次，幂等登记不重复查询；失败保留采购登记和最近成功快照。`purchase_progress_store.py` 持久化同步快照、代次与短期占位，网络不持锁，写入前重新核对有效采购、账号与代次。
- 手动刷新按整笔采购单执行一次订单、物流及轨迹查询；多商品结果以 `products` 保存。批量采购产生多个采购单时通过 `PurchaseProgressStore.begin_many` 在短事务中统一占位，查询不持锁，失败保留原快照。成功查询可补齐采购单实际商品，已有可信销售关联按供应商商品及 SKU 精确保留。
- 订单状态与物流分别记录更新时间。物流接口的 `500_2` 按官方定义表示未发货，归一为空物流；其他物流失败只返回警告，仍保存成功读取的订单状态，保留旧运单及其原始更新时间，不触发包裹合并。授权变化与采购作废仍拒绝写入。
- `OrderProcurementStore.tracking_summaries` 为当前列表与详情批量读取有效采购的本地状态摘要，排除作废采购单；不额外查询远端。摘要 `packages` 按国内运单去重，无运单的采购单保留待发货/待查询条目；使用各自最近成功的订单或物流查询时间，失败尝试不推进时间。国内包裹状态不覆盖 ERP 处理进度，逐包裹状态与最近成功查询时间保留在提示和详情中。列表 `OrderProgressHint.vue` 悬浮或键盘聚焦时由 `orderProgressHint.ts` 按主状态的显式来源生成提示：1688 展示采购包裹明细，跨境巴士展示仓库状态与最后成功同步时间，平台展示平台快照，ERP 优先展示当前步骤与已知异常，并附带明确标注来源的国内包裹明细，不发起网络请求。`FulfillmentStore.summaries` 批量补充本地目录的分区/仓库名称、去重运单、所选服务和指定合单服务；目录按履约账号匹配，运单和服务在提示中标为 ERP 登记资料，不推断仓库签收或服务完成，同步时间也不当作作业发生时间。单包裹和多包裹使用同一规则：采购完成但无国内运单时展示采购订单状态，有远端或人工登记运单后展示“待报单”；提交中、结果待核实及报单异常优先显示。“待报单”不代表资料齐全，提交仍须通过原有校验。已有预报始终优先显示仓库进度，物流签收不等同仓库发货。
- `alibaba_purchase_assignment.py` 按实际运单生成独立包裹；已知物流商品件数可相加，数量或归属证据缺失时件数留空，不从销售数量推算。采购单跨销售订单共用时只保存采购快照，包裹由用户按实际归属登记。
- `OrderPurchaseTracking.vue` 在订单级采购历史中展示整笔采购商品、物流和同步结果；打开页面只读本地快照。包裹资料由独立的 `FulfillmentParcelDialog.vue` 管理，`purchaseLogistics.ts` 只校验运单和件数，不分配 SKU。

## 跨境巴士履约 V1

`http_route_units/fulfillment_routes.py` → `facades/fulfillment_facade.py` →
`services/fulfillment_service.py` 是唯一履约入口。`AppContext.fulfillment` 装配并持有后台任务，
服务只接收当前平台账号和采购详情提供函数，不反向导入 runtime unit。
仓库状态及创建/取消结果核实随订单同步或单笔同步按钮触发，打开“跨境履约”页签仅读取本地快照；
`POST /api/orders/fulfillment/sync` 在请求内完成单轮只读核实并返回最新快照，不排队或失败重试。
后台只执行自动预报、创建后的单次 Yandex 平台关联、已请求的资料更新和取消传播，不轮询已关联订单的状态。
`schemas/fulfillment.py` 定义内部配送及公开履约契约；`stores/fulfillment_store.py` 共用订单 SQLite，
负责唯一订单、版本 CAS、创建/取消占位、编辑租约和重启后的未知状态保留。
`services/crossborderbus_client.py` 适配官方协议和企业 Token 刷新，全部请求经过统一外部请求管理。
`services/platform_label_service.py` 负责 Yandex 单箱平台 PDF 与箱条码读取，复用现有店铺授权，不自动分箱或推进平台发货状态。`POST /api/orders/fulfillment/fetch-label` 为带版本校验的人工获取入口；默认自动预报在资料齐备后通过同一占位流程获取面单，失败独立记录，页面提示用户手动重新获取，不自动重试。

`services/fulfillment_label_service.py` 使用现有 `S3ImageStorage` SDK 边界交付 PDF 面单并核验公开内容。
`services/fulfillment_image_service.py` 在 Yandex 报单创建和包裹更新前，将已同步的平台图片字节原样托管并核验跨站公开读取，防止源站 Referer 防盗链。通过 facade 注入 `FulfillmentService.image_delivery`，不在订单 GET 中上传，不替换平台图片来源；复用安全下载、图片内容校验及唯一 S3 SDK 边界，临时源文件在仓库外自动清理。下载或托管失败不会发送报单写入；准备后复核版本及取消状态。
原有平台订单身份及状态不变；列表另行读取履约摘要，详情承载履约操作及异常处理。
授权和默认方案在已有授权模块中配置；Yandex FBS 在采购前确定收货仓，报单仅沿用该仓选择服务，不依赖全局默认方案。
`GET/POST /api/orders/receiving-warehouse` 经 `fulfillment_routes.py` 和 `fulfillment_facade.py` 委托给 `ReceivingWarehouseService`。`schemas/receiving_warehouse.py::delivery_carrier` 仅从归属已校验且可用的 Yandex IMPORT 交货点名称前缀识别承运商，保留 GUOO(GTA) 与 GBS(GUOO) 的区分；缺失、未知或冲突时返回待确认，不使用 `deliveryPartnerType`、`Самовывоз`、卖家仓或地址备注猜测。名称及识别依据返回收货仓界面；物流来源指纹包含识别结果，不包含官方交货点 ID 或地址。`stores/receiving_warehouse_store.py` 与采购共用订单数据库，保存本单地址及规则快照；统一验证配置版本、启用状态及承接范围，已有采购不再豁免。配置变化使旧绑定失效，但不改写采购记录中的历史地址。

`GET /api/logistics/settings`、`POST /api/logistics/refresh`、`POST /api/logistics/address` 经同一薄路由和 facade 委托 `services/logistics_settings_service.py`。`schemas/logistics.py` 定义 Yandex 平台物流商枚举，地址规则只按平台和物流商匹配，默认地址仅影响排序。Express/Economy 及交货节点范围已退役；持久化旧规则可读，只保留已确认的平台和物流商用于地址匹配，原采购快照不重写。`stores/logistics_store.py` 按跨境巴士授权身份及合作仓保存多地址，用版本校验阻止覆盖；旧 `receiving_warehouse_profiles` 只迁移地址、不迁移自由文本承接能力，迁移标记防止重复导入。前端入口为「平台授权 → 跨境巴士 → 默认履约」内的 `components/settings/LogisticsSettingsPanel.vue`，由 `CrossborderBusSettingsPanel.vue` 展示；默认方案卡片及专属编辑弹窗已移除，既有持久化规则和后端匹配、自动预报行为保留。`/auth?auth_section=crossborderbus&bus_section=rules` 直达；旧物流设置深链跳转到此处。采购弹窗只提交地址 ID 和配置版本，平台物流商由服务端从订单解析，不接受物流商、运输方式或自由地址覆盖；识别不到物流商时阻止选仓并提示同步平台交货信息。
收货仓读取的 `binding_stale` 仅按所引用的物流配置判断已有绑定失效，无绑定不标失效，取消履约及历史 `confirmation_required` 标记均不使有效绑定失效；收货仓与履约详情复用 `ReceivingWarehouseStore.binding_stale`。`needs_rebind` 才是待发货订单的重绑待办，平台取消、已发货和已送达不计入；需要重绑不等于允许立即操作，仍检查 `can_confirm`、锁和阻断原因。AI 查询沿用该领域结果，不从履约取消或采购记录推断待办和锁定。

AI 保存报单方案时，`order_fulfillment_update` 先复核审批快照，再从同一快照中的收货绑定注入 `receiving_revision`，调用与页面相同的 `FulfillmentService.command`。模型无需填写收货仓版本；旧工具参数保留读取，但不作为版本依据。审批后的绑定或配置变化由快照校验拒绝，核验到写入之间的变化继续由业务版本和事务校验拒绝，不在执行时重读最新版本补齐。

`POST /api/logistics/parse-address` 只接收地址文本，经同一 facade 复用 `services/alibaba_purchase_address.py::parse_address`；不依赖订单、商品行、1688 授权或数据库。物流地址编辑器填充同一地址形状并展示缺失字段提示，解析不自动保存地址或改变支持的物流商。
`OrderReceivingWarehouse.vue` / `ReceivingWarehouseDialog.vue` 从物流设置筛选已合作 Yandex 仓的具体收货地址，只选择匹配本单平台物流商的地址。设置允许修改已有地址的所属仓及收件资料；`ReceivingWarehouseStore.configuration_changed` 统一判断配置版本、启用状态、仓库及地址，`binding_stale` 向界面和 AI 暴露旧绑定失效。配置未变化的有效采购继续锁定；配置变化后即使已有采购也可重新选择仓库及地址，须核对包裹收货安排并确认，保存新绑定不改写采购快照。reserved 采购占位及已创建/结果未知的履约单仍禁止重绑，均在同一短事务校验。历史采购缺少仓库快照时必须补录并确认实际寄往地址；未知采购期间不能补录。原 `fulfillment_warehouse_links` 历史数据保留，但新流程不读取或写入。

绑定失效统一阻止保存服务、手动提交、重试及自动报单；「跨境履约」通过 `receiving_binding_stale` 展示重新绑定入口。重绑递增收货仓版本，使原服务方案失效，须重新保存；旧服务不作为新方案回显。`FulfillmentStore.claim` 在创建占位事务内复核绑定，`LogisticsStore.save` 同事务检查引用该配置的创建占位／未知结果，防止配置修改与发送竞态。已创建远端报单继续保留原方案和单号，不因配置编辑覆盖。AI 收货仓工具复用同一服务及审批快照。
`services/fulfillment_routing.py` 提取官方交货地址供展示和备注；地址本身不参与仓库匹配，物流商由可用的 IMPORT 交货信息识别。`FulfillmentPlanDialog.vue` 固定采购收货仓，`FulfillmentPlanFields.vue` 实时读取基础单选、附加多选及费用；保存及提交复核收货仓版本和物流方案，官方地址变化不使方案失效，平台物流商缺失或变化则须重新核对。
目的国按官方创建接口为可选字段，没有时不发送、不阻止报单。创建后原报单仓及服务保持锁定；没有自动规则时须点击“立即提交”。
本单可选报单备注由 `FulfillmentPlanDialog.vue` 手工录入，随 `plan` 命令持久化 `remark`，沿用编辑租约、CAS 及报单前锁定边界。`createOrder.order_data[].introduce` 仅在备注非空时发送，不再生成平台订单标识；地址备注和默认履约规则不提供默认值。历史记录缺少备注字段仍可读取，页面读取不更新已创建远端订单。
Yandex 首次报单以真实平台订单号写入 `order_number`，历史已创建及创建待确认的预报号不变。`POST /api/orders/fulfillment/associate` 通过官方关联接口读取跨境巴士已同步的店铺订单；`FulfillmentService.associate` 复用占位和版本校验，预先核实远端 ID、仓库、渠道和待打包状态。`platform_link_state/error` 独立于仓库状态；失败不重建报单，未知结果不重放，GET 与只读同步不触发关联。官方查询不返回店铺订单栏全部明细，成功回执不等于该栏逐项验收。
协议、产品闭环、核实限制和验收见 [跨境履约说明](crossborder-fulfillment.md)。

### 平台请求中断与恢复

- `http_route_units/external_request_routes.py` → `facades/external_request_facade.py` → `services/external_request_control_service.py` 为系统设置统一中断查询与恢复入口；契约在 `schemas/external_request_control.py`。恢复只改变外部请求 Store 的放行条件，不执行业务请求。
  `POST /api/external-requests/history/clear` 在用户确认后清理历史中断；使用页面快照的时间上界，Store 在短事务内重查当前保护范围。
  保留当前阻断的已发送依据、最近十分钟控制窗口、未完成请求、尚未确认恢复的未知写入、重试父记录、成功审计及恢复记录；旧的重复本地拦截可清理，阻断计数不变。人工恢复按范围和时间关联历史，已确认恢复的未知写入也可清理；后续新阻断不得重新保护在其创建前已确认处理的旧记录，本轮故障依据仍保留。系统检查记录不视为人工确认。审计库 v2 保留 v1 迁移能力。
  请求级明确拒绝与未知写入分开展示：前者只限制原操作中的原请求，填写处理原因可解除；后者须核对业务回执。服务与 Store 共用 `schemas/external_requests.py` 的明确拒绝判定，存储事务校验故障及创建时间，旧快照不能解除重新产生的同类故障。
  系统设置内同类明确拒绝按平台、真实账号、审计关联接口与完整拒绝状态分组；组身份绑定所有成员快照。聚合只影响展示与明确恢复选择，不扩大实际阻断范围，恢复在同一事务内逐条留审计。
- `stores/external_request_store.py` 在短事务中持久化冷却、唯一探测租约、退避及恢复记录；只读临时故障可自愈，明确平台限制与未知写入不得隐式解除。旧永久阻断仅凭完整只读临时失败审计迁入冷却。
- `http_handler.py` 与 `external_request_context.py` 仅收集真实请求拒绝和领域操作关联；前端 `ExternalRequestNotice.vue` 提示用户发起的操作受阻并直达「系统设置 → 中断与恢复」，`components/settings/ExternalRequestControlPanel.vue` 负责统一倒计时、记录及人工恢复。提示不替代业务结果，不创建第二套任务协议。
- 订单同步状态从完整历史和当前账号的真实外发范围汇总，不受最近 50 条通知限制。Yandex 订单归属 campaign，外部请求阻断归属 business；不得混用。详情见 [外部请求管理](external-request-management.md)。


### 1688 订单采购

- Yandex FBS 的订单级「1688 批量采购」使用 `GET /api/orders/alibaba-purchase/batch` 和 `POST /api/orders/alibaba-purchase/batch-preview`，与单条采购共用 create/pay/reconcile/cashier 生命周期。`AlibabaBatchPurchaseDialog.vue` 支持勾选最多 50 个商品行、修改数量、逐组确认支付渠道与一次确认后顺序提交；分组独立持久化，部分成功不回滚已创建订单，前端请求中断暂停后续组。
- `services/alibaba_purchase_preview.py` 按 1688 `orderPreviewResuslt` 的真实分组匹配完整 offer/SKU/spec 集合；供应商展示名不作为合单身份，同组同一采购规格汇总数量。所有组使用本单冻结收货仓地址，创建前复核分组、金额、渠道、店铺和仓库。商品缺失、重复或不一致时拒绝创建；未知回执按外部请求号查询，再校验整组商品和数量。
- 每个批量组在采购请求 payload 保存 items/targets。新预览使交叠行的旧预览失效；未决请求跨授权阻止涉及行再次提交。`OrderProcurementStore.reserve_purchases/complete_purchases` 在同一短事务占位和回填，整组只生成一笔采购单，多个 SKU 是该单的 items；回填失败保留远端回执供幂等恢复。
- 批量创建后通过 `alibaba_purchase_sync_service.sync_purchases` 对每笔采购单同步一次，失败查询不因 SKU 数量重复执行；刷新只增补新运单，不覆盖人工包裹，不执行仓库写入。

- 入口：订单详情的商品采购区域 →「1688 采购」。`order_routes.py` 的 `/api/orders/alibaba-purchase` 及 `/preview`、`/create`、`/reconcile`、`/cashier` 经 `facades/alibaba_self_purchase_facade.py` 装配，不再从在线商品发起独立采购。
- `services/alibaba_self_purchase_service.py` 读取销售订单已确认来源和剩余数量；只有待发货订单可新增采购。预览绑定订单行、来源版本、数量、地址、采购收货仓版本、人民币分金额和凭据摘要，有效期十分钟。下单参数仅取采集并冻结到上架记录的商品编号、SKU 和 specId，再使用已选且核对后的地址进行实时预览；缺失、歧义或不一致时返回 blocked_reason 并禁止预览/创建，不借用历史订单或临时商品查询补猜。起订限制以 1688 返回为准。
- `services/alibaba_purchase_address.py` 归一化全部 1688 保存地址，并从订单交货快照和 `OrderAddressNoteStore` 读取揽收／交货地址及本地备注。GET `/api/orders/alibaba-purchase/addresses` 提供候选，POST `/parse-address` 只在本地提取地址文本中的有用字段；优先明确标注的收货联系人和电话，同类多个号码取优先级最高的首个，手机和座机分别填入，忽略备用资料及备注，缺失字段提示补全，用户修改并确认后才能预览，不改写原始地址或备注。保存地址使用内容指纹选择，选择后到预览前发生变更须重选；预览之后按已确认快照提交，不静默改为账号最新默认地址。
- Yandex 的收货地址统一由物流设置维护，`AlibabaSelfPurchaseDialog.vue` 直接展示已确定仓库的地址，预览请求只传收货仓版本，不能传其他地址覆盖。其他平台的 `AlibabaPurchaseAddressPicker.vue` 保留地址选择、粘贴解析和字段核对。`AlibabaSelfPurchaseDialog.vue` 单独展示订单预览与支付方式。地址或数量变化使旧预览失效；支付渠道只使用本次预览返回的可用值。
- `stores/alibaba_self_purchase_store.py` 由 AppContext 持有，未提交预览仅在内存中短期保留（十分钟、最多 256 条），不写数据库、不出现在采购历史中；关闭弹窗或刷新页面后重新预览，服务重启后预览失效。用户提交时才在独立的 `data/alibaba-self-purchases.sqlite3` 原子保存请求，随后允许调用创建接口。版本 2 将旧 listing_id 列迁移为 target_key，历史数据保留读取能力，旧持久化预览不展示也不能提交。未知请求阻止同订单行重复采购，换授权不能绕过防重。
- 创建接口使用跨境 ERP 对接方案中的 `alibaba.trade.fastCreateOrder`，不调用分销方案的 `alibaba.trade.fenxiaoOrder.create`。只发送该接口声明的参数，预选支付渠道不是付款操作。预览、网关参数校验或替身测试成功，都不能表述为真实下单已成功；验收须以实际创建回执和订单查询为准。
- `OrderProcurementStore` 在订单库以短事务为自动采购占位，阻止同一商品存在未决请求时再下单或修改来源；人工登记已发生的采购不受数量占位限制。占位冻结收货仓、来源及数量，创建前再次核对价格与渠道；不限制采购量小于销售量，建议剩余量仅用于默认填写。回执不明只查原请求，不重发。
- 1688 创建回执先持久化，再幂等回填订单级采购单和明细；独立请求保存采购单 ID，批量多 SKU 指向同一个 ID。关联失败可查询原订单重试。远端取消经查询确认后作废整笔登记；本地作废不会取消远端订单或退款。
- 支付渠道支持 alipay / shegou（先采后付）。两种渠道均由用户确认创建并支付后，前端依次调用 create 和 POST `/api/orders/alibaba-purchase/pay`，不在 GET、后台同步或查询恢复中发起支付。POST `/payment-status` 必填 pay_channel，只读查询当前授权的对应渠道免密签约，必须同时 signedStatus、bindingStatus 为 true。支付宝明确提示直接扣款；批量混合渠道逐项检查签约。`AlibabaPaymentAgreement.vue` 展示官方签约链接与主动刷新，`AlibabaPurchasePayment.vue` 为已创建且未发起支付的订单提供单独确认入口。
- `alibaba_purchase_payment.py` 解析官方签约与支付回执。pay 在网络前核对销售订单归属、冻结金额、完整商品与数量、1688 待付款状态及应付金额（元转分）；显式发送创建记录保存的 payChannel（alipay / shegou）、payAmount 和持久化 opRequestId，不省略或自动切换渠道；/pay 不接受客户端覆盖渠道。采购库 v3 新增独立 payment JSON 列，保留 v1/v2 数据迁移；短事务占位后才调用官方 preparePay，创建结果更新不会覆盖支付回执。
- 支付超时、异常、paySuccess=false 或渠道不符均保留待核验，重启和重复提交不重发；明确接口拒绝单独展示。reconcile 只读查询，订单进入发货／收货阶段后标记 settled，不据此猜测实际支付渠道，也不因仍待付款而认定原支付失败。批量采购逐组创建和支付，支付待核验暂停后续组。接口依据及验收范围见 [采购免密支付接入](alibaba-purchase-payment.md)。

- 1688 API 与网页采集均保留原始 `skus[].offer_id/spec_id`，`product_model/sku_model.py` 将其保留为 `source_offer_id/source_spec_id` 及来源快照。重新采集更新这些只读来源标识，销售内容覆盖不能覆盖它们。
- `product_model/alibaba_purchase_model.py` 在冻结发布关联时核对原始采集 SKU、规格标识和实际销售规格；不一致记入 `purchase_block_reason`。自动采购还须命中当前店铺销售 SKU 的已上架关联，人工填写的来源不能作为自动采购依据。旧资料仍可读取和人工采购，缺少完整证据的旧预览不能再提交。

### 系统设置

- 界面语言设置与 `vue-i18n` 已退役；前端文案、路由标题及业务 HTTP 的语言头固定为中文，不读取历史设备语言偏好。刊登语言与图片翻译目标语言仍由各自业务契约维护。

- 「禁售类目」（`settings_section=forbidden_categories`）支持导入 Yandex 的 XLSX 名单，列为 `category`、`name`，可含 `int64` / `string` 类型说明行。GET `/api/system-settings/forbidden-categories` 和 POST `/api/system-settings/forbidden-categories/import` 经 `auth_config_facade`、`forbidden_category_service` 与根 `ConfigStore` 完整校验并替换共享 `app_config.forbidden_categories`；导入仅管理员开放，所有个人空间的预检读取同一名单。空名单、错误行和同 ID 异名均拒绝整次导入，失败保留旧配置。通用配置保存不能覆盖该区段。
  匹配仅按 Yandex 类目 ID，不按名称推测或自动扩大到父子类目。`category_facade` 类目预检与 `publish_validation` 基础/最终发布校验共同执行；单次 SKU 批次由 `PublishValidationSession` 固定一次名单快照，下一次校验重新读取，旧预检通过不能绕过最终校验。名单更新不改写历史预检或已经提交的发布任务。回归见 `tests/test_forbidden_categories.py`、`tests/test_yandex_publish.py`。

- “关于”页提供应用版本、英文第三方声明，以及独立的中文《软件许可与服务协议》和《隐私政策》。版本与 `front/package.json` 同源。第三方声明从运行依赖生成并校验指纹，`front/build/third-party-notices.ts` 按 Web 实际构建模块或桌面交付范围筛选，排除纯开发与测试工具；许可证及声明生成为 `licenses/` 下的可读文件，桌面版随安装包放在 ASAR 外，“关于”打开该目录，Web 仅提供 TXT 下载；不在弹窗加载或展开全文。组件版权和 NOTICE 保留，来源与发行边界见 [第三方声明来源](../scripts/legal-notices/README.md)。法律文本分别维护于 `front/src/legal/user-agreement.ts` 与 `privacy-policy.ts`，硬件信息期限不适用于自行部署的业务数据库。查看不代表同意，不启用设备上报或自动升级。

- 左侧栏底部 `AppSidebar.vue` → `/?tab=systemSettings` → `SystemSettingsPanel.vue` 在主区域提供订单自动同步间隔设置；复用工作台导航，支持刷新及浏览器前进、后退。
- GET/POST `/api/system-settings` → `auth_config_facade` → `ConfigStore.system_settings/save_system_settings`；单项更新保存在 `app_config.system_settings`，通用授权配置合并忽略该区段，避免旧页面覆盖。字段契约由 `schemas/config.py` 定义，前端类型自动生成。旧配置缺少区段时默认 5 小时。
- `WatermarkSettingsPanel.vue` 是系统设置的「图片水印」页签（`settings_section=watermark`），
  提供文字／TXT 导入、图片上传、大小、疏密与透明度，不设开关和类型互斥。水印图片以有大小限制的 data URL
  随应用配置持久化，支持静态 PNG/JPEG/WebP；实际解码校验后才保存。
  POST `/api/system-settings/watermark-preview` 经 `watermark_preview_service` 自动从现有草稿选取可用本地原图（公共图优先，其次已选 SKU 图），
  前端防抖自动更新并忽略旧请求回执；无图显示空状态。预览不保存设置、不修改草稿、不上传图床。
  系统设置按提交的顶层项合并，订单同步间隔与完整水印设置分别保存，旧页面不清空新增设置。旧水印配置读取时保留文字和图片，移除开关、互斥类型与位置字段；后续只写平铺格式。
- `OrderNotificationService` 通过显式 provider 在每次导航自动同步时读取最新间隔；修改设置不发起外部同步，也不重置既有同步尝试时间。

### 工作台导航

- 发布日志 Tab、仪表盘日志摘要及其前端加载链路已退役。后端 `publish_logs` 表、发布产物、日志写入、`GET /api/publish-logs` 和 AI `publish_logs_query` 继续保留；`schemas/ai_page_context.py` 继续读取历史 `logs` 页面快照。

- 待处理 Tab 及仪表盘的待处理统计、待办队列已退役，不再按商品索引状态生成待办。采集箱、草稿箱及各领域任务继续使用原有索引和业务状态。
- 前端页面背景只包含现有页面；`schemas/ai_page_context.py` 保留历史 `pending` 页面快照的读取与校验，不作为当前导航入口。

账号业务上下文通过 `AccountRuntime.context_for` 在原有个人模型目录中加入两条 New API 系统模型，保留个人连接及功能绑定。系统模型复用模型管理、测试和推理入口，服务端校验其 ID 与连接信息；运行时只有这两条记录从显式绑定的用户上下文取得托管令牌。


业务问答验收脚本 `scripts/business_query_acceptance.py` 从普通页面建立事实基准，
检查模型的结构化答案及原生 usage。`GlobalAgentChatService.instruction_sections`
仅分项计量执行规则、业务提示、系统规则、用户偏好和回答风格；完整持久化历史不裁剪。
