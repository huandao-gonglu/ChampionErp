# 三平台订单通知

订单通知由统一的本地收件箱、平台读取适配器和订单快照构成，支持 Mercado Libre、Ozon 和 Yandex。工作台提供订单筛选、分页、处理记录、手动重试、未读提醒与用户主动开启的桌面提醒。

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

## HTTP 契约

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/mercadolibre/notifications` | 接收 `orders_v2`、`shipments` |
| `POST /api/ozon/notifications` | 握手、FBS/FBO 发运单及订单变更；订单级事件触发列表核对 |
| `POST /api/yandex/notifications` | `PING` 与订单创建、更新、取消等通知 |
| `GET /api/orders` | 本地快照，支持 `platform`、`state`、`offset`、`limit`（最多 100） |
| `GET /api/orders/integrations` | 本机可信界面的平台接入状态与带凭据回调地址 |
| `POST /api/orders/configure` | `public_url`：公网 HTTPS 源地址 |
| `POST /api/orders/sync` | 可选 `platform`；入队后台对账，合并重复请求 |
| `POST /api/orders/retry` | `event_id`：重新处理当前账号的失败事件 |
| `POST /api/orders/acknowledge` | `through_id`：标记该游标以内的当前账号提醒已读 |

订单列表返回 `items`、`total`、`pagination`；`counts` 为当前全部已配置账号的本地状态总数，与列表分页和筛选无关。`notifications` 为最近 50 条处理记录；`alerts` 为最近 50 条未读提醒，`unread` 为完整未读总数，`latest_alert_id` 用于有界确认。公开输出不含平台原始响应、买家个人资料或店铺密钥。

旧 `GET /api/mercadolibre/orders`、专用 runtime/facade、前端旧类型与刷新路径已删除。主库历史 `order_notifications` 表仅作为持久化导入源保留，不再写入；有明确账号归属的历史事件单次幂等导入，原库不删除，无可靠账号身份的历史行不猜测归属。

## 平台接入

1. 在店铺授权设置中配置账号。Yandex 需有效的 Business ID、Campaign ID 及订单读取权限；Ozon 需 Client ID 和 API Key；Mercado Libre 需已授权 seller/user ID 和 App ID。
2. 在工作台“订单通知 → 平台回调接入”填写公网 HTTPS 源地址。
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

2026-10-05 工作分支验证结果：

- 后端全量：2215 项通过、47 个子测试通过，1 项原有测试失败。`tests/test_online_buyer_links.py::test_yandex_sync_carries_the_platform_url` 的夹具返回空 `offerCards`，现有商品读取逻辑因此拒绝不完整结果；该测试及商品实现均未被本分支修改。
- 前端全量：65 个测试文件、539 项测试通过；格式整理后订单与导航相关 7 项再次通过。
- Python 编译、新增 Python 文件 Ruff 检查、前端类型检查、相关文件 ESLint 与 Vite 生产构建通过。构建保留项目已有的大包体积提示。
- 此分支未合并主工作区正在进行的在线商品修改；实际平台权限、通知订阅、HTTPS 代理及公网可达性仍需在部署环境验证。
