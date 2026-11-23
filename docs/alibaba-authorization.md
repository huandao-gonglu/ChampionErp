# 1688 授权与服务商接入

## 目标与当前边界

1688 接入方案按登录账号空间解析：管理员（根空间）使用在共享设置中保存的方案（企业自用、跨境 ERP 或万邦），普通用户（个人空间）在任何前台、后台任务与 AI 工具路径下固定使用万邦，无法发起或读取企业自用与跨境 ERP 授权（访问企业应用授权接口返回 403）。授权设置对管理员显示企业自用、跨境 ERP、万邦三张授权卡，对普通用户只显示万邦卡。每位用户在每套方案下的授权独立保存；普通用户历史上曾保存的企业自用或跨境凭据保留不删，但不再读取。管理员切换方案只影响管理员本人，保留各方案已有授权。

两套官方应用分别发起 1688 OAuth 和续期，可以提前授权尚未启用的方案。状态、发起和手动完成接口接受 `provider`；官方浏览器回调通过 state 定位发起的用户及方案，并在该方案的记录中校验一次性 state、回调和应用指纹。未指定方案的入口按当前选中方案发起请求，新授权都绑定发起时的方案；升级前已发起的旧 state 仍按原方式验证。

企业自用应用还支持管理员在该卡片粘贴 1688 开放平台「授权配置」提供的已授权 Token。服务端使用该应用的签名和提交的 Token 调用买家只读接口，验证成功后才替换当前用户在此应用的凭据。浏览器回调或续期返回非正有效期时，企业自用应用也通过该接口验证回执中的 Token；有效时保留平台返回的买家身份，到期时间留空。验证失败保留现有授权。

## 万邦自助授权

在「平台授权 → 1688 授权 → 万邦」点击「连接 1688 账号」。在浏览器登录自己的 1688 并确认授权后，立即复制地址栏中的完整地址，粘贴到万邦卡并点击「完成授权」。回跳页可能空白。ERP 显示核验通过的买家和授权到期时间，到期后可从同一卡片重新授权。

POST `/api/alibaba/authorization/start` 通过 `provider: onebound` 生成万邦授权链接并保存当前用户的待完成记录。万邦使用企业 Key 作为回跳 `state`。POST `/api/alibaba/authorization/onebound/complete` 接收 `callback_url`，验证登录会话和 CSRF Token，以会话确定账号，并核对该用户的待完成记录、万邦回跳地址和应用配置。

后端用固定接口 `/1688global/buyer_token_create/` 一次性交换授权码，从 `items` 读取 `session`、`nick` 和 `exp_time`。随后用该 Token 作为外层 `access_token` 调用 `alibaba.account.basic`。只有 1688 返回的登录名与换码昵称相符，且提供会员 ID，才保存到当前用户。失败保留原授权，成功响应只包含状态、买家和到期时间。已核验的同一买家重新授权时沿用采购归属。换码回执提供到期时间，当前使用重新授权续期。 [万邦买家 Token 文档](https://open.onebound.cn/help/api/1688global.buyer_token_create.html)

## 管理员配置与个人凭据

「系统设置 → 管理员共享设置 → 1688 API」维护两套官方 AppKey、AppSecret、回调地址及万邦 Key、Secret、新买家基础额度（默认 200 次），并选择管理员本人使用的 1688 方案。企业凭据归根库 `enterprise_applications / active`，设置由管理员维护，Secret 留在服务端。密钥留空沿用原值，配置 revision 防止覆盖并发更新。更换企业凭据使对应旧授权失效；仅调整万邦新买家基础额度不会使现有买家授权失效。

## 万邦额度记账、分级放行与 Key 恢复

企业共享额度库位于根空间 `data/onebound-quota.sqlite3`（WAL、`0600`，由 `stores/onebound_quota_store.py` 与 `services/onebound_quota_service.py` 维护），分两层治理：

- **平台层（企业万邦 Key 指纹）**：响应体 `api_info` 按白名单解析 `today`、`max`、`expires` 更新 `key_snapshots`；结合快照之后本地发出的次数估算 `today_estimated`，每日保留额度为 `max(50, max × 5%)`（`trade` 与 `auth` 可使用保留额度，`collect` 达到 `max - reserve` 即本地拦截）；`expires` 在 7 天内提示管理员，过期则整 Key 拒绝。
- **用户层（企业 Key 指纹 + 1688 买家 `member_id`）**：同一买家被多个 ERP 用户授权时共用一份余额。新买家首次获得一次默认基础额度（`grant`）；经万邦自采购下单并确认付款后（免密支付成功回执或对账确认已付款状态）按 `floor(实付元) × 2` 幂等入账（`earn`，按订单号去重，取消/退款不扣回）；管理员可在「用户管理」页查看各账号绑定的万邦买家与当前余额，并按目标余额调整（`POST /api/admin/users/onebound-quota`，带预期余额并发校验，差值记为 `adjust` 流水及备注）；所有万邦请求在 `RequestContext.before_send` 原子先扣后发（`spend`，发出即计消耗）。
- **调用分级**：交易接口（`trade`）与授权接口（`auth`）在买家余额不足时仍照常放行并记账（余额可为负），避免无法下单导致额度死锁；`shop_bind_list` 对同一买家 10 分钟内最多触发 1 次；商详采集及未列出的接口按 `collect` 处理，余额不足时本地拒绝并提示用户可改用浏览器采集（不自动切换）。
- **错误码与跨重启恢复**：`platform_request_policy.py` 识别万邦 HTTP 200 的 `error_code`：4013 进入北京时间次日 00:00 冷却并持久化 `exhausted_until`；4016（余额不足）/ 4007（账户停用）/ 4005（Key 或签名无效）内存熔断并持久写入 `key_state`，管理员充值或修正后通过 `POST /api/alibaba/onebound/key/restore`（不发探测请求）人工恢复；4008 走纯内存退避；4006 / 4012 单接口 60 秒防重撞；4009 维护中对写入保留结果未知语义；网关明确拒绝（4013/4016/4007/4005/4008/4006/4012/4003 等）仍转换为 `OneBoundApiRejected`，保证下单占位可安全释放。
- **统一只读查询**：`BusinessQuery` `alibaba.onebound_quota`（`/api/business-queries/alibaba.onebound_quota`）供万邦授权卡、采集面板预估和 AI 共用。普通用户仅见自己授权买家的余额、构成与流水；管理员额外可见 Key 用量、到期日、停用状态和全部买家，并可按 `entries` 分页读取流水。
- **独立 1688 万邦直购**：工作台左侧独立 Tab「1688 直购」（所有登录用户可见，`/api/alibaba/direct-purchase/*`）通过 `alibaba_onebound_runtime_config` 强制走万邦企业 Key 与当前用户已核验买家身份的万邦授权（兼容自助授权 `access_token` 及历史已核验 `session`，即便管理员 ERP 主业务设为企业自用或跨境应用），支持粘贴链接/短链解析、复选多 SKU 设数量、选取 1688 地址或手动输入/解析地址、预览下单、打开 1688 收银台付款或可选免密支付。直购记录保存在独立库 `data/alibaba-direct-purchases.sqlite3`，不进入采集箱、草稿箱、销售订单、采购记录或 AI 数据目录；刷新订单状态或免密支付确认付款后同样按 `floor(实付元) × 2` 幂等入账。
- **1688 只读缓存与调用节约**：`services/alibaba_read_cache.py`（挂载于 `AppContext.alibaba_read_cache`，地址缓存持久化在当前空间的 `data/alibaba-read-cache.sqlite3`）按 `account_scope` 隔离三类只读缓存：① 1688 收货地址列表（`receiveAddress.get`）持久化缓存 24 小时，直购与 ERP 自采购地址加载及下单预览优先复用，页面手动「刷新地址」透传 `refresh=1` 强制拉取；② 免密支付签约状态（`protocolPay.isopen`）仅在 `ready=True` 时内存缓存 6 小时（未签约不缓存），免密支付遭遇远端明确拒绝时即时失效，手动刷新签约透传 `refresh=true` 穿透；③ 商详接口（`queryProductDetail`）内存缓存 10 分钟，跨 1688 API 采集、SKU 货源绑定解析与 1688 直购共享（直购支持 `refresh=true` 穿透）。此外，下单提交距本次预览 ≤ 60 秒时跳过重复的二次预览（本地校验、占位与超时重验保留）；采购物流同步在订单处于未付款/已取消/已关闭（`waitbuyerpay` / `cancel` / `terminated`）或首次自动同步（`once=True`）仍为 `waitsellersend` 且无承运商信息时跳过 `getLogisticsInfos` / 轨迹查询。

尚未保存时可读取 `ERP_ALIBABA_*`、`ERP_ALIBABA_CROSSBORDER_*`、`ERP_ONEBOUND_API_KEY`、`ERP_ONEBOUND_API_SECRET`。个人 `credentials:alibaba` 的 `state`、`crossborder`、`onebound` 保留原通道凭据，不借其他用户或其他应用的 Token 补齐当前通道。

`AlibabaAuthorizationService` 负责公开状态、官方 OAuth、万邦自助授权和官方续期。GET 状态只读本地。商品、采购、支付和物流通过 `alibaba_runtime_config` 获取当前通道的个人凭据。万邦只在完成自助授权时查询买家身份，业务请求直接使用有效的个人授权。采集每件商品只请求一次 `product.search.queryProductDetail`。

## 万邦买家 Token 的传递

万邦业务请求使用管理员保存的企业 Key、Secret 和当前用户的万邦买家凭据。买家 Token 放在 URL 查询参数 `access_token`，已保存的 Session 使用外层 `session`。`_o_args` 只包含业务参数。

买家身份、订单列表、收货地址和支付签约状态均已核对外层 Token 传法。支付签约查询在 `_o_args` 中传无效 Token 时曾返回与有效 Token 相同的数据，改走外层后无效 Token 返回错误。

## 万邦下单与免密支付

两项操作均经万邦 `GET /1688/custom` 转发。外层传企业 `key`、`secret`、当前已核验买家身份的 `access_token`（或历史已核验 `session`）和 `method`，`_o_args` 传业务参数的 JSON。交易资格由 `onebound_trade_ready` 统一校验（要求具备已核验的 `member_id`，自助授权 `access_token` 与历史已核验 `session` 均放行，未核验凭据禁止写入）。 [万邦 custom 文档](https://open.onebound.cn/help/api/1688.custom.html) · [万邦技术示例](https://segmentfault.com/a/1190000047904773)

- 创建采购订单使用 `com.alibaba.trade/alibaba.trade.fastCreateOrder`。业务参数包含 `addressParam`、`cargoParamList`、`flow`、`tradeType`、`preSelectPayChannel` 和用于核验的 `outOrderId`。创建回执须返回唯一订单号。
- 免密支付使用 `com.alibaba.trade/alibaba.trade.pay.protocolPay.preparePay`。`tradeWithholdPreparePayParam` 包含 `orderId`、`payChannel`、`payAmount`（分）和持久化的 `opRequestId`。ERP 先核对所选渠道签约、订单内容和金额，收到渠道一致的成功回执后记录支付成功。 [采购免密支付接入](alibaba-purchase-payment.md)

两项接口均按写入处理。回执不明确时保留待核验状态，通过原订单核对结果。请求参数和回执由本地替身验证，线上权限和真实交易回执仍待联调。

## 万邦协议验证

联调已用外层 `access_token` 核对买家基本信息、订单列表和收货地址。买家基本信息接口的无效 Token 对照请求返回拒绝。 [万邦 custom 文档](https://open.onebound.cn/help/api/1688.custom.html)

项目原始 Word 的「1.2.1 认证授权」写的是登录万邦控制台、点击 1688 授权、到服务市场订购后返回万邦完成授权；采购调用示例只传企业 Key、Secret 和业务参数。内嵌截图的授权管理表有独立的 `session(token)`、`Key` 两列，表明后台保存了授权记录，但正文和截图均未交代 ERP 回调或多买家凭据选择协议。不能由该文档推出万邦仅支持一个买家，也不能推出可沿用现有官方 OAuth。

2026-11-13 的只读连通测试中，万邦 Key 不传 Session 即可读取后台默认买家，其身份与当时管理员的官方授权一致。该记录测试的是顶层 Session。后续用户补充了顶层 `access_token` 的买家身份与订单列表实测请求。补充对照测试对同一个买家基本信息接口分别使用不传 Session、无效 Session、现有官方 Token，三者均返回同一买家。因此，调用成功不足以证明传入凭据被使用，更不能证明跨用户授权成立。线上授权、配置和接入选择均未修改，未创建订单或付款。

ERP 的双账号回归模拟不同买家换码，验证会话隔离、过期处理、回执核对和授权失败保留原凭据。两个真实买家分别通过正式 ERP 页面完成授权并对照只读结果的联调仍待完成。自动续期、撤销和自助授权的交易能力需各自的接口验证。

## 历史授权与采购数据

已有万邦个人凭据和原采购归属继续读取。未标注凭据类型的历史记录沿用外层 `session`。历史目录绑定的核验结果仍有效，核验未通过的账号需要自助重新授权。新授权统一从万邦授权卡发起。

采购库保留 v4 别名表以及 v1/v2/v3 读取迁移能力，兼容可能已写入的本地数据；当前授权流程不再自动创建归并。旧归属仍可保存网络中请求的回执，原订单号、支付意图和未知写入不删除或重放。个人授权库中其他字段原样保留。

`onebound_api_transport.py` 保留固定 HTTPS 网关、关闭缓存与重定向、错误脱敏和已验证的回执归一。网关用 GET 下单或付款时仍按写请求处理，不能自动重试或隐式切回官方。官方订单列表不含 `success` 时，仅接受完整的 `result`/`totalRecord` 形状。

`test_accounts.py` 覆盖双账号 HTTP 授权和权限，`test_onebound_user_authorization.py` 覆盖回执失败、过期与并发保护，`test_onebound_api.py` 覆盖协议、错误和读写语义。授权、采购和支付测试验证各应用凭据、旧库读取和未知请求防重。
