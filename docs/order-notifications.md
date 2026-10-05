# 三平台订单通知

订单通知由统一的本地收件箱、平台读取适配器和订单快照构成，支持 Mercado Libre、Ozon 和 Yandex。仪表盘仅提供通知摘要与订单入口；订单中心提供筛选、分页、同步记录与手动重试，订单详情承载金额、采购来源和采购记录，平台回调配置位于设置页。

## 边界与所有权

- `erp_web/http_route_units/order_routes.py`：请求校验与响应转换，禁止在回调内读取平台订单。
- `erp_web/facades/order_notification_facade.py`：显式装配三平台适配器及 HTTP/AI 共用查询。
- `erp_web/schemas/orders.py`：事件、订单、状态、提醒及页面返回结构。
- `erp_web/runtime_units/order_notifications.py`：平台回调解析、资源白名单、账号匹配和握手。
- `erp_web/runtime_units/orders_{mercadolibre,ozon,yandex}.py`：平台协议及状态归一化。复用项目已有 HTTP 和外部请求管理器，Ozon 查询 POST 明确归类为只读。
- `erp_web/services/order_notification_service.py`：后台领取、重试和周期对账，各平台独立 worker，避免某个平台故障阻塞其他平台。
- `erp_web/stores/order_notification_store.py`：独立领域库 `data/order-notifications.sqlite3` 的唯一写入入口；SQLite 事务不跨网络等待。
- `front/src/stores/orderNotifications.ts`：订单前端状态；发布 store 不再持有订单数据。

这是平台订单领域任务，不涉及模型调用、Agent 暂停/恢复、审批或消息历史，因此不适用 Pydantic AI Deferred Tools。没有新增 Agent loop 或自研 AI 协议。AI 的 `platform_orders_query` 直接查询同一份本地订单快照，支持三个平台；回调接入凭据不进入模型输出。

## 接收与处理

1. 平台 POST 到固定回调路径，携带订单中心生成的专用 `token` 查询参数。校验平台账号、Mercado Libre 应用 ID 和资源路径；未知但合法的事件类型忽略，不生成订单。
2. 事件去重和持久化完成后才返回 HTTP 200。回调写锁等待最多 200 毫秒，保存失败返回 503，允许平台重投；请求体上限 256 KiB。
3. 后台领取任务，按平台与账号串行执行。租约过期后可重领，旧 claim 不得提交订单状态。一个平台的失败不阻止另一平台领取任务。
4. 平台详情成功读取后更新唯一订单快照。带平台更新时间的数据拒绝旧版本覆盖；Ozon 没有状态更新时间时依赖串行实时回读，不把创建时间当作状态版本。
5. 可重试故障使用指数退避，最多 8 次；授权、权限、确定性协议错误进入失败记录。用户修复原因后可重新处理；不会绕过外部请求管理器的限流或账号阻断。
6. 新进入“待发货”的订单生成一条持久提醒，同状态重复通知不重复提醒。离开待发货状态后自动清除对应未读提醒。已读操作只确认用户已读取的提醒游标。

每 5 分钟对账平台订单。Ozon 使用 `/v4/posting/fbs/list` 和 `/v3/posting/fbo/list`，按不透明 `cursor` 翻页，要求根级 `postings` 和 `has_next`；不使用已退役的 offset 列表端点。Ozon/Yandex 初次列表为最近 30 天，Mercado Libre 使用平台 recent 查询；本地已知的窗口外未完成订单继续逐单核对。没有声称导入平台全部历史订单。分页失败保留已核实订单，同时将任务标为失败或等待重试；列表中缺席不等于取消。

待发货依据明确状态：

- Mercado Libre：订单 `paid`、物流 `ready_to_ship`，排除平台 fulfillment 和风险订单。读取物流详情，不从订单中缺失的 shipping status 推测。
- Ozon：FBS/rFBS 的 `awaiting_packaging`、`awaiting_deliver`；FBO 不计作卖家待发货。
- Yandex：FBS/DBS/EXPRESS 的 `PROCESSING` 且阶段为 `STARTED`、`READY_TO_SHIP` 或 `PACKAGING`。FBY 不计作卖家待发货。
- 未知或缺失状态显示“状态待确认”，不计入待发货。

页面每 5 秒读取本地数据，无需等待平台授权或远端查询。保留上次成功结果并显示读取错误。桌面通知须用户点击开启并由浏览器授权；首次加载不重复弹出历史提醒。页面未读提醒可跨重启保留。

## 商品金额口径

Yandex 的订单和 SKU 行主金额 `amount` 为同币种 `payment + subsidy + cashback`，展示为“商品金额（含平台补贴）”，不含 `prices.delivery`，也不代表扣除佣金、物流等费用后的净到账。`amount_breakdown` 分别保存付款、平台补贴和积分抵扣，币种沿用平台返回值。金额使用 Decimal 精确加总；SKU 行接口已按数量汇总，不得再乘数量。

订单可展开查看每个 SKU 的数量及小计。缺失付款信息时显示“金额待确认”；零金额正常展示，金额非法或币种不一致时停止本次更新。旧持久快照继续可读，缺少 `amount_breakdown` 的 Yandex 金额明确标为“付款金额（待同步明细）”，经平台重新读取后转为当前口径，不从旧付款值猜测补贴。其他平台保留其现有金额口径。

## HTTP 契约

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/mercadolibre/notifications` | 接收 `orders_v2`、`shipments` |
| `POST /api/ozon/notifications` | 握手、FBS/FBO 发运单及订单变更；订单级事件触发列表核对 |
| `POST /api/yandex/notifications` | `PING` 与订单创建、更新、取消等通知 |
| `GET /api/orders` | 本地快照，支持 `platform`、`state`、`q`、`offset`、`limit`（最多 100） |
| `GET /api/orders/summary` | 仪表盘摘要，独立于订单列表筛选；异常计数覆盖完整收件箱 |
| `GET /api/orders/detail` | `order_id` 为本地订单身份；返回 SKU 来源、候选与采购记录 |
| `POST /api/orders/select-source` | 人工确认发布来源候选或录入采购来源，带来源版本 |
| `POST /api/orders/record-purchase` | 记录采购单号及数量，携带幂等请求 ID |
| `POST /api/orders/cancel-purchase` | 作废本地采购记录，保留原记录，不取消外部订单 |
| `GET /api/orders/integrations` | 本机可信界面的平台接入状态与带凭据回调地址 |
| `POST /api/orders/configure` | `public_url`：公网 HTTPS 源地址 |
| `POST /api/orders/sync` | 可选 `platform`；入队后台对账，合并重复请求 |
| `POST /api/orders/retry` | `event_id`：重新处理当前账号的失败事件 |
| `POST /api/orders/acknowledge` | `through_id`：标记该游标以内的当前账号提醒已读 |

订单列表返回 `items`、`total`、`pagination`；`counts` 为当前全部已配置账号的本地状态总数，与列表分页和筛选无关。`notifications` 为最近 50 条处理记录；`alerts` 为最近 50 条未读提醒，`unread` 为完整未读总数，`latest_alert_id` 用于有界确认。公开输出不含平台原始响应、买家个人资料或店铺密钥。

旧 `GET /api/mercadolibre/orders`、专用 runtime/facade、前端旧类型与刷新路径已删除。主库历史 `order_notifications` 表仅作为持久化导入源保留，不再写入；有明确账号归属的历史事件单次幂等导入，原库不删除，无可靠账号身份的历史行不猜测归属。

## 平台接入

1. 在店铺授权设置中配置账号。Yandex 需有效的 Business ID、Campaign ID 及订单读取权限；Ozon 需 Client ID 和 API Key；Mercado Libre 需已授权 seller/user ID 和 App ID。
2. 在“设置 → 订单通知接入 → 平台回调接入”填写公网 HTTPS 源地址。
3. 复制对应平台地址，在卖家后台配置通知。Mercado Libre 订阅 `orders_v2` 和 `shipments`；Yandex 配置订单相关 API 通知；Ozon 配置所使用履约模型的订单/发运单通知。
4. 公网代理仅暴露三个回调路径，改写 `Host` 为本机回环地址，不公开 ERP 其他接口。后台仍只监听回环地址，不放宽全局 Host/Origin 检查。

Nginx 路径示例（HTTPS 证书在部署环境配置）：

```nginx
location ~ ^/api/(mercadolibre|ozon|yandex)/notifications$ {
    if ($request_method != POST) { return 405; }
    client_max_body_size 256k;
    proxy_set_header Host 127.0.0.1;
    proxy_pass http://127.0.0.1:5000;
    # token 属于接入凭据；公网代理不得把查询参数写入访问日志。
    access_log off;
}
location / { return 404; }
```

回调地址含专用凭据，不能公开分享。配置 URL 本身不意味着平台已经订阅，也不意味着公网代理已部署；“最近回调”可核实实际接收时间。

## 协议依据与验证范围

核对日期：2026-10-05。

- [Mercado Libre 通知](https://developers.mercadolibre.com.ar/en_us/products-receive-notifications)：快速 HTTP 200、重复投递、订单与物流主题。
- [Yandex API 通知](https://yandex.ru/dev/market/partner-api/doc/en/push-notifications/reference/sendNotification)、[官方握手示例](https://yandex.ru/dev/market/partner-api/doc/en/push-notifications/concepts/quick-start-notifications-node-express)、[Business Orders](https://yandex.ru/dev/market/partner-api/doc/en/reference/orders/getBusinessOrders)：快速应答、事件类型、`name/version/time` 和当前订单分页契约；另核对官方 OpenAPI 仓库。
- [Ozon Seller API](https://docs.ozon.ru/api/seller/) 与[官方通知变更公告](https://t.me/s/OzonSellerAPI?before=695)：覆盖 FBS/FBO/订单级主题。文档站本次返回反机器人验证页；读取了官方 Swagger 的[公开存档副本](https://github.com/MissiaL/ozon-api/blob/main/references/ozon-seller-openapi.json)，并与官方退役公告交叉核对端点版本。存档内容包含当前列表游标、握手、FBO/订单通知示例；仍需真实账号联调确认，不能视为当前文档站逐字段核验通过。

测试覆盖原子去重、状态乱序、未知状态、完整计数、账号隔离、重启重领和迟到提交、重试退避、历史导入、三平台协议样例与分页、实际本机 HTTP 接收/查询隔离，以及页面筛选、轮询、错误保留和提醒去重。模拟接口测试不能证明平台权限、真实回调配置或公网可达性；此次未向真实店铺写入设置，也未部署公网代理。

2026-10-05 订单中心与采购关联验证结果：

- 后端全量：2253 项测试、47 个子测试通过。补充架构检查与采购测试 62 项通过；最终发布链路与采购回归 82 项通过。
- 前端全量：67 个测试文件、550 项测试通过，覆盖摘要跳转、详情金额、来源确认、链接标识、重复提交与采购历史。
- Python 编译、新增 Python 文件 Ruff 检查、前端类型检查、相关文件 ESLint 与 Vite 生产构建通过。构建保留项目已有的大包体积提示。
- 只读检查历史发布记录，订单 `62668010304` 的两个销售 SKU 均可追溯到来源 SKU 与规格；未向真实业务库创建采购记录。实际平台权限、通知订阅、HTTPS 代理及公网可达性仍需在部署环境验证。

## 订单处理与采购来源

- 仪表盘 `OrderSummaryCard` 只展示完整待发货数、未读提醒、同步异常数和最近五笔订单。全局轮询读取 `/api/orders/summary`；仅进入订单中心时才轮询订单列表。
- 订单中心为工作台独立导航 `/?tab=orders`。订单详情通过 `/?tab=orders&order=<本地订单身份>` 直接定位，保留平台、店铺和履约身份。采购进度与平台状态分开；平台未提供发货期限时明确显示未提供，不推算截止时间。
- `runtime_units/order_source_bindings.py` 仅从成功 SKU 的发布结果、发布任务冻结商品和受信 `store_identity` 建立关联；不读取当前可编辑草稿推测历史，也不拆解销售 SKU 编码。匹配按平台、店铺身份和销售 SKU 限定，远端商品身份可进一步消歧。
- 发布终态写入 `sales_sku_bindings`；订单采购服务首次装配时从持久发布任务幂等补齐，因此历史已发布商品及终态写入中断均可恢复。没有成功发布证据或存在多个来源时，页面要求人工关联或选择，不按商品标题相似度自动匹配。
- 来源快照保存采购商品 URL、来源 SKU 编号和原始规格。未验证的来源只显示“打开采购商品”和可复制规格，不声称已预选 SKU。允许用户提供同站点规格链接，并明确勾选已打开确认该 SKU；没有为 1688 等平台拼造未经验证的深链接规则。链接使用 HTTP(S)，拒绝带登录凭据的 URL；服务端不抓取采购 URL。
- 人工来源只作用于该订单行的后续采购，不修改商品原始采集事实或已经确认的发布映射。采购记录冻结当次来源；改来源后旧记录保持原供应商、SKU 和规格。缺失唯一行身份、跨店铺访问、过期版本和超量采购均拒绝。
- `OrderProcurementStore` 与订单快照共用领域 SQLite，事务内校验订单现状、来源版本、幂等键和剩余数量；网络调用不进入事务。作废记录保留审计事实，可以重新采购，不向第三方发送取消或下单请求。
- 新持久表为增量扩展，不删除原订单、发布、来源或采购数据。通知已读不代表采购完成，打开采购链接不生成采购记录，内部采购进度不自动推进平台发货状态。

订单采购是领域数据与人工操作，不涉及模型运行、Deferred Tools 或 Agent 等待恢复，不新增 AI 执行循环。人工采购端点被明确标记为界面内部入口。
