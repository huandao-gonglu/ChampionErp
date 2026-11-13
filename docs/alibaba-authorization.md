# 1688 应用切换与账号授权

系统支持企业自用应用（原 AppKey 1938727）、跨境 ERP 服务商应用（原 AppKey 4848173）和万邦 1688 API，管理员统一选择当前使用哪套方案。未设置选择时默认恢复企业自用应用。商品采集、采购、订单、支付和物流共用所选方案，三套买家授权分别保存在自己的 ERP 空间。

跨境方案提供的接口、消息类型及 ERP 已接入的业务接口见 [1688 API 与消息目录](alibaba-api-catalog.md)。两套应用可调用的能力仍以各自在 1688 获得的权限为准，不因本地切换而获得新权限。

## 管理员配置与客户使用

管理员在「系统设置 → 管理员共享设置 → 1688 API」选择当前方案并保存，在下方分别维护两套官方 AppKey、AppSecret 和回调地址，以及万邦 API Key、API Secret。密钥留空沿用已保存值；切换保留各方授权，更换应用凭据后该方案需要重新授权。保存后对所有账号生效，账号授权页显示当前方案。

首次保存前，企业自用应用读取原根库凭据或 `ERP_ALIBABA_APP_KEY`、`ERP_ALIBABA_APP_SECRET`、`ERP_ALIBABA_REDIRECT_URI`；跨境应用读取 `ERP_ALIBABA_CROSSBORDER_APP_KEY`、`ERP_ALIBABA_CROSSBORDER_APP_SECRET`、`ERP_ALIBABA_CROSSBORDER_REDIRECT_URI`。密钥仅保存在服务端。仅有旧 Token 而没有回调地址时，可继续使用匹配的原授权，但须补全回调地址才能重新发起授权。

选择跨境应用时，按跨境 ERP 方案完成资质、订购和上架审核，使用平台认可的聚石塔应用回调地址。将审核通过的服务订购地址提供给客户。客户先订购，再到「授权配置 → 1688 授权」打开浏览器，登录自己的 1688 账号；企业自用应用直接连接该应用下的采购账号。ERP 服务端收到回调后校验一次性 state、交换授权码，并在发起授权的客户空间保存 Token。页面自动显示授权账号。

管理员本机调试时，可先在 ERP 发起授权，登录 1688 后从浏览器地址栏复制完整回调 URL（包含 `code` 和 `state`），粘回「1688 授权」并点击「完成授权」。即使 localhost 页面无法连接也可复制。服务端仅解析该 URL，校验回调地址与管理员自己的待完成授权并向 1688 换码。普通账号使用自动回调。

## 万邦接口接入与授权边界

万邦企业配置只向管理员开放，首次保存前可读取服务端环境变量 `ERP_ONEBOUND_API_KEY` 和 `ERP_ONEBOUND_API_SECRET`。Key/Secret 保存至根库 `enterprise_applications / active / onebound`，Session 保存至当前账号库 `credentials:alibaba / onebound`，不进入共享企业配置。所有公开状态均不返回 Secret 或 Session。

截至 2026-11-13，提供的流程文档与[万邦 custom 文档](https://open.onebound.cn/help/api/1688.custom.html)只说明控制台授权、Session 及 API 转发，未提供面向 ERP 用户的买家授权链接签发、回调和 Session 换取协议。按本次确认先完成管理员配置和接口适配；普通用户不显示万邦控制台、企业凭据或 Session 输入，万邦方案暂不开放在线授权。官方两套应用继续复用现有 1688 授权入口，官方应用 Token 不代替万邦 Session。

管理员可在「1688 授权」为自己的账号填写控制台取得的 Session，经 POST `/api/alibaba/authorization/onebound` 只读读取收货地址后绑定。接口仅允许管理员、绑定当前会话空间，不能指定其他账号或覆盖企业凭据。核验不创建订单或发起支付，也不代表已核实真实买家身份；公开名称为“万邦采购账号”。现有资料没有稳定买家 ID 或 Session 续期协议，采购范围按 Key + Session 隔离，更换 Session 后原采购须恢复原授权处理。配置轮换仍校验代次，迟到回执不能覆盖另一方案或新授权。

`services/onebound_api_transport.py` 固定请求 HTTPS 万邦 `1688/custom`，使用 `method`、`_o_args` 和独立 Session，关闭缓存和重定向，将 `response` 内的官方回执交回原业务解析器。商品、地址、预览、创建、订单、免密支付和物流复用现有业务服务；万邦收银台按提供文档调用 `alibaba.alipay.url.get(orderIdList)`。即使网关使用 GET，下单与支付仍明确按写操作处理，不自动重试、不切回官方网关。超时、上游网络错误和不完整回执保留结果待核验。

回归入口为 `tests/test_onebound_api.py` 与 `tests/test_alibaba_purchase_payment.py`，覆盖网关参数、权限、独立归属、轮换、错误脱敏和未知支付防重。接口契约以文档及替身验证，尚未使用真实万邦凭据完成商品、下单或支付验收；收到买家授权协议后再补自动授权闭环。

## 已有客户与采购记录

共享应用沿用根库 `enterprise_applications / active` 下的 `alibaba` 和 `alibaba_crossborder`，万邦新增 `onebound` 段，选择保存在同条记录的 `alibaba_api_application`。客户自用授权沿用 `credentials:alibaba / state`，跨境授权沿用 `/ crossborder`，万邦授权独立为 `/ onebound`；归档自用凭据和旧 `1688_api` 凭据保留恢复及迁移能力。切回对应应用后，指纹仍匹配的原授权可以继续使用。共享设置及授权响应不下发密钥或 Token。

切换不重写采购记录归属；另一应用的记录不能借用当前授权查询或支付，切回原应用及买家后再处理。原请求号、未知结果防重及本地数量占位继续有效。切换期间收到的授权、刷新回执必须重新校验当前应用和授权快照，不能写入另一套凭据。修改密钥后再改回原值，也不能恢复旧代次授权。

历史桌面客户端的两组授权接口都连接管理员当前所选应用。当前版本只显示一个授权入口。旧客户端保存共享设置时省略应用选择或各方案配置，不改变当前选择及已保存配置。查询账号状态不发送远程请求。官方方案的业务调用在到期前五分钟刷新 Token，刷新结果校验买家身份；万邦不复用该刷新协议。

企业自用商品详情经 `com.alibaba.product / alibaba.product.get` 读取，发送 `productID`、`webSite=1688` 并解析 `productInfo`；跨境应用经多语言商详接口 `product.search.queryProductDetail` 读取。两者都核对返回商品编号，并保留原文标题和 SKU 规格，不在接口拒绝时切换应用或接口。请求固定发送到 1688 官方网关并使用服务端签名。[1688 官方跨境商详定义](https://open.1688.com/api/data/getApiDetail.json?namespace=com.alibaba.fenxiao.crossborder&name=product.search.queryProductDetail&version=1) · [阿里云官方 API 参考（含企业自用商详契约）](https://alibaba-pdf.oss-ap-southeast-1.aliyuncs.com/1688%20Cloud%20Hub%20API%20Reference-0723.pdf)

回归入口：`tests/test_managed_settings.py` 覆盖管理员切换、权限、共享生效、双授权保留及凭据轮换；`tests/test_alibaba_authorization.py` 覆盖旧授权恢复、迁移中断及切换期间回执；`tests/test_1688_api_collect.py` 覆盖两种商详请求与商品身份核验。

## 协议依据与验证

核对日期：2026-11-04。

- [1688 官方 getToken 定义](https://open.1688.com/api/data/getApiDetail.json?namespace=system.oauth2&name=getToken&version=1&_input_charset=UTF-8)
- [1688 官方测试工具](https://open.1688.com/api/apiTool.htm)及其
  [授权实现](https://g.alicdn.com/b2b-open-platform/ocean-apifactory-assets/1.8.7/gw_api_tool.js?v=1)：
  签名授权链接 `https://auth.1688.com/auth/authorize.htm`，site=china；
  换取和刷新使用 `/openapi/http/1/system.oauth2/getToken/{AppKey}`，表单提交，刷新使用
  grant_type=refresh_token。读取官方响应的 memberId、access_token、refresh_token、expires_in。
