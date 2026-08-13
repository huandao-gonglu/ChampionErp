# champion-Erp 设计审查与重构记录

日期：2026-07-26。范围：erp_web（2.67 万行）+ front/src（1.78 万行）+ 根目录数据文件。方法：四路并行深扫 + 关键结论逐条抽查复核（本文所有 file:line 均已验证）。

## 总评

核心结构病一句话：**兼容永久化 + 有状态服务全部退化为"模块+全局"**。

- runtime.py 的全量快照注入机制是多数怪象的根因：它导致全库零 `global` 语句、催生 3 处"dict 容器躲注入"workaround，并让模块状态可被静默回滚。
- 真正的状态持有者约 6-7 个（DB、商店、总线、缓存、注册表、日志），全部写成模块级函数+全局变量；而 PublishingBus、Provider 类证明团队会写类——只是有状态的地方恰好都没用。
- 平台行为散在 19 个文件 68 处 `platform ==` 分支；marketplace_registry 只是站点数据表，不绑定行为。新增一个平台要改 ~15 文件 35+ 处。
- SQLite 表建了不接线：store_auth、publish_logs 均 0 行，凭据和日志仍走明文 JSON；category_cache 表 344 行但全库零引用（死数据）。
- "迁移做一半、旧世界永不下线"：四代字段回捞、11 条假路由、模板 auth 残留、根目录 4 具死目录，同一种决策模式。

---

## 一、应该设计成类但没有

判定标准：模块级可变全局 + 围绕它的函数群 = 事实上的类；一组函数反复传同一批参数 = 应封装为对象。

| # | 严重度 | 位置 | 现状 → 建议类 |
|---|--------|------|----------------|
| 1 | 高 | runtime.py:79-115 | `_sync_runtime_units` 每次调用把聚合器命名空间整包拷入 23 个模块，是手工模拟的隐式上帝对象 → **AppContext/RuntimeContext**（启动时构建一次，持有 paths/db/stores/bus），runtime.py 改 PEP 562 `__getattr__` 惰性转发，删快照拷贝。这是根因，最先动 |
| 2 | 高 | runtime_units/publish_adapter.py:54-82 | `_BUS_STATE = {"bus": None}` dict 容器 + 双重检查锁存单例，注释自认"躲注入" → 单例归 `AppContext.publishing_bus` |
| 3 | 高 | db.py:28-1004 | 12 个公开函数全以 `app_dir` 开头、8 个内部函数以 `conn` 开头，每操作即连即关 → **ErpDatabase(app_dir)** 类：连接/事务/WAL 集中一处，`initialize_database` 变构造期动作 |
| 4 | 高 | services/product_research_service.py:45-47 | `_RUNS`/`_RUN_ORDER`/`_RUNS_LOCK` 手写带淘汰上限的线程安全注册表 → **ProductResearchRunRegistry** |
| 5 | 中 | runtime_units/runtime_common.py:51-95 | 40+ 路径/端口"常量"实为可变配置（测试会 repoint APP_DIR）→ frozen dataclass **AppPaths/AppEnvironment** |
| 6 | 中 | runtime_units/category_store.py:44-65 | `_SQLITE_INIT_STATE` 按 app_dir 分 key 的 init-once 容器（躲注入第二例）→ 并入 ErpDatabase 构造即消失 |
| 7 | 中 | runtime_common.py:95 + pricing_runtime.py:60-91 | `EXCHANGE_RATE_CACHE` 定义在 A 模块、B 模块读写的无锁 TTL 缓存 → **ExchangeRateService** |
| 8 | 中 | services/ai_work_service.py:22-28 | `_CONVERSATION_LOCKS` per-会话 Condition 表，只增不清 → **AiWorkJournal(app_dir)**（含清理策略） |
| 9 | 中 | services/ai_gateway.py:2306-2378 | `(app_dir, app_config, use_case_id)` 参数隧道穿 8+ 函数 → 解析后构造 **AiProviderClient**（api_style 差异用子类） |
| 10 | 中 | runtime_units/product_store.py（1129 行） | 全部 CRUD 隐式依赖全局 APP_DIR、混杂两套配置读写 → 拆 **ProductStore(db)** 与 **ConfigStore(paths)** |
| 11 | 低 | runtime_units/publish_bus.py:29-59 | 锁+三函数围着 publish_logs.json 读改写截断 → **PublishLogStore**（并留换 SQLite 后端接缝，见第四部分） |
| 12 | 低 | runtime_units/ozon_category_api.py:20 | `_tree_cache` 模块级 TTL 缓存 → **OzonCategoryClient** 或通用 TtlCache 与 #7 共用 |

另：http_route_units 有 91 个函数以 `handler` 为首参——本质是从 handler 类拆下的方法，AppContext 落地后可一并收口。

收敛路径：#1 做完，#2/#6/#7 三个 workaround 自动可删；#3 做完 #10 顺势完成。

---

## 二、应该抽象但没有

核心度量：**新增一个平台 = 改 ~15 文件 35+ 处；新增一个采集源 = 3 文件 10+ 处**。按收益排序：

1. **发布链路无 PlatformPublisher 接口**（最大头）。同一平台分支重复 5 处：publish_validation.py:67-293 三个 `validate_*_draft` 近似复制 ~70 行/个；publish_helpers.py:66-124 build/validate payload 各一套 if/elif；runtime_api.py:112-166；publish_adapter.py:26-38、:74-78——三个平台注册的是**同一个** ProjectPublishingAdapter（壳存在但没按平台拆）。→ 定义 `PlatformPublisher` 协议（validate/build_payload/publish/resolve_category），每平台一实现注册进 registry。

2. **registry 缺平台能力声明**。marketplace_registry.py 只有 label/site/币种；can_publish/can_preview 等能力判断散在 ≥5 文件硬编码字符串比较（category_store.py:95、publish_facade.py:76、runtime_api.py:158-163、image_pool_core.py:245、product_store.py:1019）。→ registry 加 capability 描述符，通用层查表。

3. **采集源无 Collector/Parser 接口**。source_collect_workflows.py 里 `platform_detected ==` 分支 20+ 处、解析派发三元式复制 4 处、登录/验证码诊断整段重复两份；parsers 是 parse_1688/amazon/generic 三个并列自由函数；collect_helpers.py 另有 4 组每源分支。→ `SourceSite` 描述符注册表（detect/parse/登录检测/error 前缀/browser profile）。

4. **AI 能力探测三族平行实现**（ai_gateway 2521 行的主要水分）。probe_model_capabilities:1414 / probe_cli:1663 / probe_browser:1827 三个同构循环 + 12 个 `_probe_{http|cli|browser}_{能力}` 平行函数，绕开了已存在的 `AI_PROVIDER_REGISTRY`（:2277，全库唯一真注册表）。→ probe 下沉为 Provider 接口方法。注：纯 OpenAI 兼容 HTTP 新供应商已是 0 代码接入，这部分设计达标。

5. **chat/responses 双协议泄漏**。Provider 类已拆（:1961/:2046），但 `_model_api_style(...)==RESPONSES` 仍散在 ~8 处共享 helper（:446、:478、:538、:1012 等）。→ body 构造与流解析归 Provider。

6. **类目 API 每平台一套自由函数**。category_refresh.py（全是 mercadolibre_* 却叫通用名）与 ozon_category_api.py 平行；category_store.py:178/219/261 手工派发；marketplaces/category_services.py 再养第三套。→ `CategoryProvider` 接口（record 字段已统一，缺的只是入口）。

7. **AI 用例曾存在重复请求编排**。多个领域用例的请求流程逐行同构（load config → load prompt pair → 渲染 → chat_json → normalize）。现统一通过 `run_ai_use_case(use_case_id, payload, normalizer)` 执行，新用例只保留领域 normalizer。

8. **店铺授权每平台 if/elif**。auth_runtime.py:271-318、product_store.py:983-988/:1026-1052、auth_config_routes.py:126/168。→ 平台描述符声明 credential 字段清单 + test_auth 回调。

9. **平台字段映射分支多份拷贝**。product_store.py:814-835 的 category_id 三选一与 publish_adapter.resolve_category 是同一逻辑两份；copy_generation.py:26-32 ozon 悄悄映射 yandex 预设；copy_service.py:73 `60 if mercadolibre else 120` 魔数。→ registry 加字段映射（category_field/preset_key/title_limit/language）。

10. **facade 层职责不一致 + wildberries 僵尸**。product_facade.py:24-29 纯转发；collect_facade.py:63-115 反而漏进 50 行业务逻辑；wildberries 不在注册表却残留 payloads/category_services/static_routes 三处。→ facade 统一为薄适配；wildberries 删除或正式注册。

优先做 1+2（同一次 registry 改造），平台新增即收敛为"一个适配器文件 + 一行注册"。

---

## 三、设计错误

不是 bug，是结构性错误决策。按严重度排序：

1. **runtime.py 全量快照注入**（runtime.py:79-115）。每次调用任一 runtime 函数，把聚合器 globals 整包覆写进 23 个 unit 模块；ThreadingHTTPServer 多线程下无锁覆写。受害者自证：publish_adapter.py:48-54 注释明说被迫改 dict 容器。AGENTS.md:47-48 自禁却仍被 http_routes.py:8 等使用，auth_config_routes.py:80-127 甚至调聚合器私有函数。→ 删聚合器，显式 import + context 对象。

2. **前后端无契约层，双向兜底**。merge_model.py 每次读写回捞并**继续写出**全部旧投影：images 四代并存（:25-36、:320-323）、price 三代、upc/gtin/barcode 三别名（:108-110）、类目 ID 三处存储；前端 normalizers.ts 1081 行做 `materials ?? source_material ?? source.materials ?? source.material` 式四连兜底（:605-612）+ 全字段 snake/camel 双读；新代码仍在双写旧字段（collect_facade.py:70-98）。别名永不淘汰。→ 版本化 schema + 一次性迁移（读旧写新），前端类型由 schema 生成。

3. **四层分层是装饰性的**。三条链路三种走法：publish 链 facade 装真控制器逻辑（publish_facade.py:52-119）；copy 链没有 facade，业务直接写在路由层（copy_routes.py:38-84，违反 AGENTS.md:41）；层间依赖双向——runtime_units 13 处 import services，而 browser_ai_runtime.py:176,182 反向 import runtime_units、auth_runtime.py:110 反向 import facades（函数内 import 掩盖环）。→ 收敛两层 handler→domain service，依赖方向用架构测试强制。

4. **明文凭据整包下发 + 本地 API 零鉴权**。GET /api/state 原样返回 load_app_config()+storeConfig（get_routes.py:84-104，实测含明文 sk- 密钥与 ML access_token）；掩码函数存在却只覆盖 /api/ai-config（:151-153）；前端整包塞 Pinia（workflow.ts:808-809）。→ 凭据只留后端出站注入；状态接口只回摘要（summarize_store_auth_states 已存在，却不是唯一出口）。

5. **schemas/ 是文档摆设**。全部 `TypedDict(total=False)` 零运行时校验；publish.py/image.py/config.py 全库 0 处 import；入口直接消费 read_body() 裸 dict（publish_facade.py:122-131、copy_routes.py:38-63）。→ 入口按 schema 校验并拒绝，或至少 mypy 强检。

6. **前端上帝 store**。workflow.ts 2700 行、74 个 ref、185 个函数，承载 ≥9 个不相关领域；`loadState()` 一次吞 /api/state god-endpoint 整包，与后端 #4 互为因果；api/workflow.ts 1383 行 67 函数同病。→ 按领域拆 5-6 个 store + 拆 /api/state。

7. **多平台发布是波将金矩阵**。三平台注册同一 adapter（publish_adapter.py:72-78）；yandex 分支硬编码"请先完成接入"（runtime_api.py:158-159）；非 ML 预览返回 **ok:true + "payload 待真实接口完善"**（publish_facade.py:76-89）。假成功语义污染上层状态机。→ 未实现能力显式 supported:false/501，绝不 ok:true 占位。

8. **认证体系是模板残留空壳 + "auth"一词撞名**。client.ts:17-23 每请求附 Bearer localStorage.accessToken，但全库无任何代码写入该 token、无登录页；requiresAuth 声明了无 guard 消费；后端从不读 Authorization。而 auth_* 文件实指店铺 OAuth。→ 明确"本机单用户免认证"删光 token 管道，店铺授权改名 store_credentials。

9. **11/13 条路由指向同一组件**（router/index.ts:19-85，已验证恰好 11 条）。真导航是 WorkflowView 内部 tab 切换；路由器退化为标题更新器，无代码分割、无路由守卫/keep-alive。→ children 子路由 + 懒加载，或只留 `?tab=`。

10. **恒真校验当准入门**。publish_adapter.py:41-42 `validate_required_attributes` 恒 `return []`，publishing_bus_core.py:154 却拿它做发布队列准入。校验步骤存在但恒真最危险。→ 接通或从流程移除（待拍板项，接通=行为变更）。

11. **设置接口 mass-assignment**。auth_config_routes.py:160-163 `app_cfg.update(body["appConfig"])` 任意键直接持久化，无白名单（同文件 merge_store_config_fields 的白名单做法却没用到 appConfig）。

12. **根目录 4 具死目录**。routes/、services/、product_model_units/、marketplace_publish_units/ 仅剩 __pycache__，全库 0 引用；与 erp_web 同名概念双份，root 在 sys.path 时有遮蔽风险。→ 删除并加架构测试防再生。

---

## 四、应该存数据库但没有存

前置事实（已实测）：根目录 erp.sqlite3 是唯一在用库；data/erp.sqlite 为 0 字节死文件。8 张表中 **store_auth、publish_logs 均 0 行**（建了没接线），category_cache 344 行但全库零代码引用。通用 `write_json()`（category_store.py:36-38）**非原子**（无 tmp+rename），以下所有 JSON 落盘共担崩溃损坏风险。

### A. 该进库没进（按风险降序）

1. **UPC 池**（根目录 upc_pool.json，5000 枚/已用 24）。publish_helpers.py:28-52 全程无锁、非原子写、先 save_product 后标记 used——并发两请求会把同一 UPC 发给两个商品（平台端 GTIN 冲突）。→ `upc_pool(upc PK, status, product_id, assigned_at)`，单事务 `UPDATE ... WHERE status='free'` 领取。**风险最高，消耗性资产。**

2. **ML 订单 webhook 通知**（data/logs/mercadolibre_order_notifications.json）。mercadolibre_orders.py:15-29,187-189，多线程 webhook 并发 read-modify-write 无锁 + 硬截 200 条：并发丢通知、超量订单静默丢弃。钱相关。→ `order_notifications` 追加式插入。

3. **店铺凭据与授权运行态**（config/store_config.json）。明文 access_token/refresh_token/app_secret，混入 auth_status/code_verifier 等动态状态；保存是整文件重写（config_http.py:75），token 刷新与配置保存并发 = 丢 token。**store_auth 表就是为此设计的，0 行闲置。**→ 迁入 store_auth，config 只留静态项。

4. **发布日志**（data/logs/publish_logs.json，现 83 条，硬截 200）。publish_bus.py:32-43 整文件重写；无法按商品/平台/时间查询，超 200 条永久丢。**publish_logs 表 schema 已够用，直接接线。**

5. **发布任务队列状态**（data/logs/publishing_jobs/*.json，24 个）。写入本身原子可恢复，但 state 里 deepcopy 了完整 config——**已验证文件含明文 APP_USR- token**——无清理、无法按状态查询。→ `publish_jobs` 表；凭据发布时现取，不落盘。

6. **选品运行结果**（内存 _RUNS + data/cache/product_research/runs/，7 天 TTL）。重启把进行中 run 标 failed（product_research_service.py:341-348）；已完成候选商品 7 天自动删——这是业务成果不是缓存。→ `research_runs` + `research_candidates`，TTL 只清中间态。

7. **AI 对话事件流**（data/logs/ai_work/<日>/<会话>.jsonl）。追加式 journal 合理，但会话列表靠全目录 glob、seq/锁在内存。→ 会话元数据入表，事件体留 JSONL。

8. **app_config.json 混装凭据与业务状态**。明文 AI api_key（6 个模型实测非空）、alibaba_cookie（1688 登录态）；非原子整文件重写，设置页并发保存互相覆盖。→ cookie/token 类运行态入库，配置项留文件。

9. **汇率缓存仅内存**（runtime_common.py:95）。重启即失、核价依据无留痕。→ `exchange_rates(base, quote, rate, fetched_at)` 顺手落一行。

（留文件合理的：presets/platforms.json、prompts/*.json、ai_config.snapshot.json（已脱敏）；config/local-backup/ 为空死目录可删。）

### B. 存了但设计不当

1. **发布日志四轨并存、无一完整**：publish_logs 表(0 行) ｜ 全局 JSON(截 200) ｜ 每草稿 draft_json 内嵌 publish_logs[:20]（publish_validation.py:314-324、draft_publish_context.py:207-222）｜ 166 个 artifacts 文件按路径引用（截断后成孤儿）。→ 表存日志行，artifacts 只存大报文。

2. **草稿三重写入 + products 宽表 JSON 化**：同一草稿存于 products.product_json.drafts、platform_drafts.draft_json 全量、platform_drafts 拆解列三处，靠手写同步（db.py:359-377、:767-790），漏一条即漂移；products 真列极少，product_json 是事实单一真源，SQL 只能当 KV 用。

3. **draft_id_aliases 是 ID 设计错误的补丁**：草稿 ID 曾把"首个平台"编码进主键，换平台后失真，于是加别名表 + 读路径循环解析别名链（db.py:330-411）。→ 无语义 ID，迁移完删别名表（当前 0 行，正是动手好时机）。

4. **类目缓存三轨全为死代码/死数据**：category_cache 表 344 行零引用；data/category_cache/*.json 零引用；现行实现是实时 API + 15 分钟内存 TTL。→ 接回表或删表删目录，别留着误导。

5. **双库文件**：data/erp.sqlite（0 字节）与 erp.sqlite3 并存，备份/迁移易选错。→ 删死文件，立"建表即接线"规矩。

---

## 建议动刀顺序

1. **删注入机制 → AppContext**（一§1、三§1）：根因，做完自动消解 3 个 workaround；此前任何单例/状态改造都会被它反噬（既有记忆：修单例时实测被陈旧快照覆盖过两次）。
2. **ErpDatabase 类 + 接线 store_auth/publish_logs 两张空转表**（一§3、四A§3/4）：把凭据、发布日志从明文 JSON 迁入，顺带治 UPC 池（四A§1，可先单独加锁止血）。
3. **registry 能力化 + PlatformPublisher**（二§1/2）：平台新增从 35+ 处收敛到 1 文件 1 注册；同时消灭波将金假成功（三§7）。
4. **契约冻结**（三§2）：版本化 schema、一次性迁移、前端类型生成，normalizers.ts 与 merge_model 回捞同批退役。
5. **前端拆 store + 真路由**（三§6/9）：依赖第 4 步的接口拆分。

其中 1、2 可与既有"未修 P0"清单（workflow store 拆分、ai_gateway 拆分、契约冻结）合并立项，不冲突。

---

## 五、接管执行记录（2026-07-29）

本报告提出的五步主重构已经完成。执行基于用户当前工作区直接进行，没有另建或声称使用不存在的分支，也没有覆盖工作区中原有的未提交改动。

1. **状态所有权与生命周期**
   - 引入冻结的 `AppPaths` 和唯一装配入口 `AppContext`，数据库、产品/配置 store、发布总线、选品注册表、AI journal 和汇率服务均由上下文持有。
   - 删除运行时全量快照注入及其 dict-container workaround；`erp_web/runtime.py` 仅保留惰性、显式的旧导入兼容面，不再持有应用状态。
   - `AppContext.close()`、临时上下文嵌套恢复和发布 executor 所有权均有生命周期测试。

2. **数据库、迁移与秘密**
   - `ErpDatabase` 接管 SQLite schema、事务和连接策略；schema v5 迁移具备原子回滚，未来 schema 在任何 WAL、sidecar、权限或数据变更前通过只读预检拒绝。
   - 产品/草稿、店铺授权、运行时秘密、UPC、发布日志、发布任务、订单通知、选品结果、AI 会话元数据和汇率均接入 SQLite；发布大报文仍以私有 artifact 文件保存。
   - `ConfigStore` 将静态文件与数据库秘密分离，保存与失败回滚处于同一个实例锁临界区。snake_case、camelCase、kebab-case 敏感字段统一识别；1688/YunExpress 的空值或掩码请求会安全回落到数据库真值。
   - 真实工作区迁移后，`app_config.json` 与 `store_config.json` 的非空敏感值均为 0；数据库、配置、日志、AI journal、发布 artifact、选品缓存和采集调试文件已收紧到 `0600/0700`。

3. **平台、来源与发布抽象**
   - 平台能力、凭据字段和字段映射集中到 marketplace registry；发布器、类目 Provider 和采集来源使用 registry/Protocol 派发。
   - 未接入发布能力的平台明确返回 `supported: false`/`unsupported`，不会创建任务或伪造成功。
   - 发布任务补上“执行已完成但终态回调尚未持久化”的崩溃恢复窗口；重试按 `job_id + platform` 复用 artifact，并保证结构化日志幂等。

4. **契约、HTTP 与 AI 边界**
   - 产品、平台草稿、目标站点、定价和图片结构采用 `schema_version: 1` 的“读旧写新”迁移；前端 wire type 由后端 schema 生成，前端与数据库写边界都会丢弃旧别名和未知字段。
   - `/api/state` 缩为启动摘要，凭据只返回掩码或授权摘要；Mercado Libre 的公开 App/Client ID 是 OAuth 所需的唯一上下文例外，secret、token 和 PKCE 不下发。
   - 所有 GET/POST 共享回环 Host/Origin/Sec-Fetch 边界；GET 默认纯读，访问与异常日志只记录 path，`/file` 仅允许指定图片根目录和扩展名。
   - AI Gateway 收敛为稳定小门面，HTTP、CLI、浏览器 Provider、解析、提示构造和能力探测分成 focused modules；业务 AI 用例经统一入口执行。

5. **前端领域拆分**
   - 工作台收敛为单一 `/` 路由加 `?tab=`，旧路径只做重定向；删除从未生效的应用用户 Bearer token/auth 模板。
   - activity、catalog、collection、publishing、settings 五个 store 是领域状态 owner；`WorkflowView` 直接读取 owner store，`workflow.ts` 仅保留组合、hydration 与跨域派生兼容面。
   - collection/catalog/pricing/publishing action factory 使用显式窄端口，新增依赖未声明时由 TypeScript 直接报错。
   - `CollectForm`/Pinia 不再包含 1688 Cookie、App Key、App Secret 或 Access Token；凭据只存在于组件局部态或瞬时请求对象，并在提交/测试后清空。发布进度严格按当前 platform、draft 和 target 隔离。

6. **可检索性与防回退**
   - 新增并持续更新 `docs/ai-context-map.md`，标明 HTTP 入口、领域边界、状态所有者、数据形状和验证入口。
   - `tests/test_ai_context_architecture.py` 防止 wildcard runtime 依赖、状态聚合器回流、巨型 AI Gateway、明文状态响应、重复工作台路由、假认证管道、完整 runtime action 参数和旧别名写入。
   - 根目录空壳 `routes/`、`services/`、`product_model_units/`、`marketplace_publish_units/` 及死库 `data/erp.sqlite` 已移除。

最终验证不是只依赖 `tests/refactor_acceptance`：

- 后端全量：`444 passed, 27 subtests passed`
- 前端类型契约生成检查：通过
- 前端类型检查：通过
- 前端测试：`14` 个测试文件、`62 passed`
- 前端 ESLint：通过
- 前端生产构建：Vite build 通过
- `python3 tests/refactor_acceptance/run_acceptance.py`：全部门禁通过
- 真实配置迁移检查：两个 JSON 明文敏感值为 0，公共店铺配置可重放敏感值为 0
