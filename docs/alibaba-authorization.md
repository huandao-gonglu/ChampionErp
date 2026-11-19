# 1688 授权与服务商接入

## 目标与当前边界

授权设置分别显示企业自用、跨境 ERP、万邦三张授权卡。每位用户在每套方案下的授权独立保存。管理员切换业务供应商时，只改变商品、采购、支付和物流使用的方案，其他授权和已发起的分方案授权不受影响。页面标出当前用于业务的方案。

两套官方应用分别发起 1688 OAuth 和续期，可以提前授权尚未启用的方案。状态、发起和手动完成接口接受 `provider`；官方浏览器回调通过 state 定位发起的用户及方案，并在该方案的记录中校验一次性 state、回调和应用指纹。未指定方案的入口按当前选中方案发起请求，新授权都绑定发起时的方案；升级前已发起的旧 state 仍按原方式验证。

企业自用应用还支持管理员在该卡片粘贴 1688 开放平台「授权配置」提供的已授权 Token。服务端使用该应用的签名和提交的 Token 调用买家只读接口，验证成功后才替换当前用户在此应用的凭据。浏览器回调或续期返回非正有效期时，企业自用应用也通过该接口验证回执中的 Token；有效时保留平台返回的买家身份，到期时间留空。验证失败保留现有授权。

## 万邦自助授权

在「平台授权 → 1688 授权 → 万邦」点击「连接 1688 账号」。在浏览器登录自己的 1688 并确认授权后，立即复制地址栏中的完整地址，粘贴到万邦卡并点击「完成授权」。回跳页可能空白。ERP 显示核验通过的买家和授权到期时间，到期后可从同一卡片重新授权。

POST `/api/alibaba/authorization/start` 通过 `provider: onebound` 生成万邦授权链接并保存当前用户的待完成记录。万邦使用企业 Key 作为回跳 `state`。POST `/api/alibaba/authorization/onebound/complete` 接收 `callback_url`，验证登录会话和 CSRF Token，以会话确定账号，并核对该用户的待完成记录、万邦回跳地址和应用配置。

后端用固定接口 `/1688global/buyer_token_create/` 一次性交换授权码，从 `items` 读取 `session`、`nick` 和 `exp_time`。随后用该 Token 作为外层 `access_token` 调用 `alibaba.account.basic`。只有 1688 返回的登录名与换码昵称相符，且提供会员 ID，才保存到当前用户。失败保留原授权，成功响应只包含状态、买家和到期时间。已核验的同一买家重新授权时沿用采购归属。换码回执提供到期时间，当前使用重新授权续期。 [万邦买家 Token 文档](https://open.onebound.cn/help/api/1688global.buyer_token_create.html)

## 管理员配置与个人凭据

「系统设置 → 管理员共享设置 → 1688 API」维护两套官方 AppKey、AppSecret、回调地址及万邦 Key、Secret，并选择业务接入。企业凭据归根库 `enterprise_applications / active`，设置由管理员维护，Secret 留在服务端。密钥留空沿用原值，配置 revision 防止覆盖并发更新。更换企业凭据使对应旧授权失效。

尚未保存时可读取 `ERP_ALIBABA_*`、`ERP_ALIBABA_CROSSBORDER_*`、`ERP_ONEBOUND_API_KEY`、`ERP_ONEBOUND_API_SECRET`。个人 `credentials:alibaba` 的 `state`、`crossborder`、`onebound` 保留原通道凭据，不借其他用户或其他应用的 Token 补齐当前通道。

`AlibabaAuthorizationService` 负责公开状态、官方 OAuth、万邦自助授权和官方续期。GET 状态只读本地。商品、采购和物流通过 `alibaba_runtime_config` 获取当前通道的个人凭据。万邦运行时要求个人授权，有效期内才可使用。

## 万邦买家 Token 的传递

万邦业务请求使用管理员保存的企业 Key、Secret 和当前用户的万邦买家凭据。买家 Token 统一放在 URL 查询参数 `access_token`，目录或旧 Session 放在外层 `session`。`_o_args` 只包含业务参数。

买家身份、订单列表、收货地址和支付签约状态均已核对外层 Token 传法。支付签约查询在 `_o_args` 中传无效 Token 时曾返回与有效 Token 相同的数据，改走外层后无效 Token 返回错误。

管理员 GET `/api/admin/alibaba/onebound/shop-bindings` 通过企业 `key`、`secret` 调用万邦授权店铺列表，可传 `nick` 和 `page`。返回绑定编号、昵称、状态、到期时间及分页信息。它只读企业目录，买家凭据的归属仍从 ERP 个人授权记录读取。[万邦 shop_bind_list 文档](https://open.onebound.cn/help/api/sys.shop_bind_list.html)

管理员可在「用户管理」中，通过管理员或普通用户行的「万邦」下拉选择有效的 1688 授权，选择后即保存。GET/POST `/api/admin/users/onebound` 读取和保存该用户的绑定。保存时后端按页及绑定编号重新取目录 Session，核对 `alibaba.account.basic` 返回的登录名和买家 ID，结果与目录相符才启用。身份不匹配时保留绑定为待核验，运行时拒绝使用其 Session。启用后供应商每次建立业务客户端时核对买家 ID。目录和绑定响应只返回展示字段。

管理员专用 POST `/api/alibaba/authorization/onebound` 接受 `{"access_token":"<买家 Token>"}`，保存到当前账号的万邦凭据记录。旧请求仍可传 `session`；未标注类型的旧凭据沿用原有的顶层 Session 协议。企业 Key、Secret 仍从管理员设置读取。绑定时只读检查收货地址接口，不能据此证明多买家隔离。

## 万邦下单与免密支付

两项操作均经万邦 `GET /1688/custom` 转发。外层传企业 `key`、`secret`、已核验的目录 `session` 和 `method`，`_o_args` 传业务参数的 JSON。ERP 在使用目录授权执行交易前核对买家 ID。自助授权换得的买家 Token 用于收货地址、订单列表等已支持的查询。 [万邦 custom 文档](https://open.onebound.cn/help/api/1688.custom.html) · [万邦技术示例](https://segmentfault.com/a/1190000047904773)

- 创建采购订单使用 `com.alibaba.trade/alibaba.trade.fastCreateOrder`。业务参数包含 `addressParam`、`cargoParamList`、`flow`、`tradeType`、`preSelectPayChannel` 和用于核验的 `outOrderId`。创建回执须返回唯一订单号。
- 免密支付使用 `com.alibaba.trade/alibaba.trade.pay.protocolPay.preparePay`。`tradeWithholdPreparePayParam` 包含 `orderId`、`payChannel`、`payAmount`（分）和持久化的 `opRequestId`。ERP 先核对所选渠道签约、订单内容和金额，收到渠道一致的成功回执后记录支付成功。 [采购免密支付接入](alibaba-purchase-payment.md)

两项接口均按写入处理。回执不明确时保留待核验状态，通过原订单核对结果。请求参数和回执由本地替身验证，线上权限和真实交易回执仍待联调。

## 万邦协议验证

联调已用外层 `access_token` 核对买家基本信息、订单列表和收货地址。买家基本信息接口的无效 Token 对照请求返回拒绝。 [万邦 custom 文档](https://open.onebound.cn/help/api/1688.custom.html)

项目原始 Word 的「1.2.1 认证授权」写的是登录万邦控制台、点击 1688 授权、到服务市场订购后返回万邦完成授权；采购调用示例只传企业 Key、Secret 和业务参数。内嵌截图的授权管理表有独立的 `session(token)`、`Key` 两列，表明后台保存了授权记录，但正文和截图均未交代 ERP 回调或多买家凭据选择协议。不能由该文档推出万邦仅支持一个买家，也不能推出可沿用现有官方 OAuth。

2026-11-13 的只读连通测试中，万邦 Key 不传 Session 即可读取后台默认买家，其身份与当时管理员的官方授权一致。该记录测试的是顶层 Session。后续用户补充了顶层 `access_token` 的买家身份与订单列表实测请求。补充对照测试对同一个买家基本信息接口分别使用不传 Session、无效 Session、现有官方 Token，三者均返回同一买家。因此，调用成功不足以证明传入凭据被使用，更不能证明跨用户授权成立。线上授权、配置和接入选择均未修改，未创建订单或付款。

ERP 的双账号回归模拟不同买家换码，验证会话隔离、过期处理、回执核对和授权失败保留原凭据。两个真实买家分别通过正式 ERP 页面完成授权并对照只读结果的联调仍待完成。自动续期、撤销和自助授权的交易能力需各自的接口验证。

## 历史接口与数据

已发布的管理员专用 POST `/api/alibaba/authorization/onebound` 保留兼容，仍要求显式传入既有凭据。已保存的旧凭据和原采购归属继续读取，默认 Key 绑定不视为个人授权。移除未发布的 `/authorization/verify` 及自动跨通道归并流程。

采购库保留 v4 别名表以及 v1/v2/v3 读取迁移能力，兼容可能已写入的本地数据；当前授权流程不再自动创建归并。旧归属仍可保存网络中请求的回执，原订单号、支付意图和未知写入不删除或重放。个人授权库中其他字段原样保留。

`onebound_api_transport.py` 保留固定 HTTPS 网关、关闭缓存与重定向、错误脱敏和已验证的回执归一。网关用 GET 下单或付款时仍按写请求处理，不能自动重试或隐式切回官方。官方订单列表不含 `success` 时，仅接受完整的 `result`/`totalRecord` 形状。

`test_accounts.py` 覆盖双账号 HTTP 授权和权限，`test_onebound_user_authorization.py` 覆盖回执失败、过期与并发保护，`test_onebound_api.py` 覆盖协议、错误和读写语义。授权、采购和支付测试验证各应用凭据、旧库读取和未知请求防重。
