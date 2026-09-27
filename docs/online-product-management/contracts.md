# 在线商品管理实现契约

核验日期：2026-09-27。原始调研保留在 [api-reference.md](api-reference.md)，当前实现以本文件及代码为准。

## 数据和 HTTP

`online_listings` 以平台 + 当前账号 + 远端主身份唯一索引，保留原始响应、规范化价格/库存范围、原始状态、能力与同步时间。Mercado 主身份为 CBT Item，User Products 的 Siteless ID 与本地市场 Item 分别保存。Yandex 账号范围是 business + campaign，主身份为 offerId。Ozon 账号为 Client ID，主身份为 product_id。标识符在内部统一为字符串，仅在平台明确要求时转整数。

快照不要求本地商品或草稿；可选关联 ID 为空代表尚未关联。同步不会创建草稿、改写已有 publication 或推断未出现的商品已删除。

| HTTP | 参数 / 返回 |
| --- | --- |
| `GET /api/online-products` | `platform,q,status,market,page`；每页 25，返回商品、原始状态筛选项、摘要、最近同步和最近 100 条操作记录 |
| `GET /api/online-products?id=...` | 当前店铺单件详情；不下发平台原始响应 |
| `POST /api/online-products/sync` | `platform,idempotency_key`；返回已持久化 Job |
| `POST /api/online-products/change` | `listing_id,version,operation,scope_id,changes,idempotency_key` |
| `POST /api/online-products/reconcile` | `job_id`；只读平台和任务终态 |
| `POST /api/online-products/retry` | `job_id,idempotency_key`；同步只重试明确失败的商品；修改只允许确定失败，混合结果拒绝整体重试 |

变更类型：`price={amount,currency}`、`stock={quantity}`、`content={选定字段}`、`sale_state={state:paused|active}`。金额按 Decimal 校验，币种和 scope 必须匹配当前快照；数量是非负整数的绝对值。内容只允许当前能力声明字段；身份、品牌、型号、条码、枚举和复杂属性不由通用文本框修改。

## 平台矩阵

| 能力 | Mercado 传统 Global Item | Mercado User Products | Yandex | Ozon |
| --- | --- | --- | --- | --- |
| 发现 | 当前父账号 `/marketplace/users/{id}/items/search`，scan/scroll_id | 同一 CBT 发现，再读取实际 UP 身份和 mapping | business `offer-mappings`，未归档 + 归档、pageToken | `/v3/product/list` ALL + ARCHIVED，last_id；当前账号 403 阻断 |
| 详情 | 父/本地 `/marketplace/items/{id}`，校验卖家/父子归属 | mapping 顶层单元素闭包 + `/user-products/{id}` v2，再查本地 Item | mapping、campaign offers、offer-cards、hidden-offers、offer-prices、仓库库存 | `/v3/product/info/list` + `/v5/product/info/prices`；尚未实店闭环 |
| 买家页面 | 本地市场 Item 的 `permalink` | 已校验归属的本地市场 Item 的 `permalink` | offer-mappings 的 B2C `showcaseUrls`，保留卖家参数 | 暂未取得已验证地址，显示缺失提示 |
| 调价 | 全局基础价；有权威 net_proceeds 时用净收入，否则本地售价 | 仅已读到净收入的具体 listing_sites | business 基础价；onlyDefaultPrice=false 才开放 campaign 独立价 | 禁用，完整当前更新 Schema 未核验 |
| 库存 | Remote 共享数量；变体更新保留其他变体数量；FBO 不写 | 仅已验证 cross_docking 共享数量；其他模式缺少 Stock Locations 闭环，禁用 | 当前配置指定 business 卖家仓或 campaign 仓库组；FBY 不直接改数 | 禁用，逐仓读取/写入契约未闭环 |
| 内容 | 无销量时标题；已有图片排序/删除；已有自由文本属性 | 已有图片、自由文本属性；族名影响其他 UP，因此不在单商品表单修改 | 标题、描述、HTTPS 图集、自由文本参数；参数与 marketCategoryId 同传 | 禁用 |
| 停售/恢复 | 全局 `status`，回读父及关联市场 | UP 根 `status`，回读全部关联市场 | 当前 campaign hidden-offers 添加/删除；恢复隐藏状态不代表通过平台审核 | 禁用，归档/恢复限制未核验 |

### Mercado

- [商品发现](https://global-selling.mercadolibre.com/devsite/items-and-searches-global-selling)：每页 50，去重，游标缺失/循环报错，不换 offset 掩盖不完整。身份漂移立即停止该商品读取；没有草稿也可发现。
- [传统商品更新](https://global-selling.mercadolibre.com/devsite/en_us/sync-and-modify-listings-gs/sync-and-modify-listings-gs)：`PUT /global/items/{id}` 只携带本次字段；全局基础价影响无独立价的市场。父库存不按市场重复分配；变体请求保留所有已有变体 ID 和未选数量。
- `net_proceeds` 真实响应是 `{amount,currency_id,...}`，不把对象转成金额，不把当地售价币种套到 USD 净收入。全局基础价、本地售价、净收入分别标明。
- `paused_by_seller` 的补货会保持暂停；`out_of_stock` 补货可自动恢复。本地有主动停售意图时，传统商品补货必须先确认平台 `paused_by_seller`，否则提示重新提交停售；回读继续核对停售。UP 尚无等价证据时拒绝此补货路径。
- [User Products 更新与异步任务](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt)：具体市场净收入通过 `listing_sites[{listing_id,net_proceeds}]`；不因错误换传统接口。异步 task_id 可在根或逐市场出现；查询 `/user-products-families/tasks/{id}`，根 finished 仍须检查每项 succeeded/failed。
- 图片发送完整已有 ID 列表；未实现新图片上传。描述未形成读写闭环，当前不开放。不得用发布全 payload 覆盖在线商品。

### Yandex

依据 [官方 OpenAPI 固定核验版本](https://github.com/yandex-market/yandex-market-partner-api/tree/321c272cfe218c21fd1644242cef205b6c9b8dbe) 中的 `UpdateOfferDTO`、`ParameterValueDTO`、`OfferCardStatusType` 和相关 paths；复用项目 `yandex_http.py`。

- business/campaign 绑定在适配器初始化时验证。账号设置决定是否允许店铺独立价格。
- 默认价专门从 `offer-prices` 读取，更新保留 `discountBase` 和 `minimumForBestseller`；店铺价保留 vat，不将账号价和店铺价同时提交。确认时按同一范围回读价格隔离区；隔离中的价格保持待确认，不自动批准。
- 仓库发现读取 models/apiAvailability；business 库存指定一个 partnerWarehouseId。AVAILABLE 缺失时，仅 FIT 和 FREEZE 均存在才计算 FIT − FREEZE，不把 FIT 直接称为可售数。
- 隐藏查询用 `offer_id` 参数。停售只修改当前 campaign 的隐藏清单，库存更新不删除隐藏记录。
- `parameterValues` 是局部更新，图片是完整替换；其余未传字段不变。通用内容编辑不传 SKU、类别变更或价格字段。
- offer-mappings/update 的 HTTP 200 可能含 `status=ERROR/results.errors`；错误使整次修改失败，warnings 保留展示。回读内容还检查卡片审核状态和错误，审核中不记已生效。

### Ozon 外部阻碍

当前实店 `/v3/product/list` 返回 HTTP 403、code 7、`Api access disabled, please contact support`。官方文档站及 swagger 入口在本环境循环跳转，原参考材料对完整更新 Schema 的评级仍为 B。未用第三方客户端或自造成功响应代替核验。

目前只有只读适配入口及错误记录；价格、库存、内容、归档恢复均明确禁用。2026-09-27 再次读取商品仍返回相同 403，买家链接也尚未实店验证，不根据未经核验的 SKU 拼接地址。需恢复账号 API 权限并取得可访问的完整官方契约后，才能完成和验证这些功能。本次未宣称三平台全部完成。

## 买家页面入口

- 列表与详情共享 `buyer_links=[{label,url,site_id}]`。单个链接直接在新窗口打开；多个链接按站点与刊登 ID 展示选择；空数组显示“暂无买家链接”。链接存在不代表当前有库存、审核通过或一定可购买。
- Yandex 只取 `showcaseType=B2C` 的 `showcaseUrl`，保留 `businessId` 等卖家定位参数。Mercado 从已读取的站点子刊登提取 `permalink`，不使用 CBT 父商品地址；同站点不同刊登也保留独立入口。
- 仅接受不含登录凭据的 HTTP/HTTPS 绝对地址，忽略无效地址并按 URL 去重。同步沿用已有平台请求，点击使用普通链接，不新增平台接口或模型调用。
- 链接保存在商品快照，但不参与库存、价格等业务版本计算；旧持久化快照缺失该字段时读作空数组。没有新增 HTTP 端点或数据库表。
- 存量补齐命令：`.venv/bin/python scripts/backfill_online_buyer_links.py`。只处理当前配置账号；Mercado 使用已存原始响应，Yandex 分批只读 offer-mappings。通过版本条件只更新 `buyer_links`，保留其他快照字段、业务版本及原同步时间；并发变更或平台未返回的商品记录为跳过。

## 任务与一致性

- 状态：queued、running、submitted、waiting_confirmation、confirmed、partial、failed、outcome_unknown。
- 提交键绑定完整请求与店铺，重复请求返回原 Job；同商品存在活动/未知任务时禁止第二个修改。全店同步与当前账号修改互斥。
- worker 写前重读真实平台数据并比较业务版本。修改过程中若店铺切换或快照变化，停止提交，提示重新确认。
- 网络写入前持久化 dispatched 标记。崩溃前未发送的任务可重新领取；已发送的超时/崩溃统一结果未知，不自动重放。回读阶段 403 也不能证明之前没写入。
- 自动确认最多 20 次、间隔至少 15 秒，此后仍保留待确认锁，允许人工只读查询。每次领取有独立租约；过期 worker 无法覆盖快照或任务结果。
- 部分失败保留逐项错误。同步只重试已识别失败 ID，不重做成功集合；发现阶段失败需要重新全量同步。平台混合修改回执无法证明完整失败集合时，不提供整体自动重试。
- 这是 ERP 平台任务，不是 Agent 生命周期；没有新增模型调用、审批、Deferred 或消息协议。
