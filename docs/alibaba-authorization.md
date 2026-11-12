# 1688 应用切换与账号授权

系统支持企业自用应用（原 AppKey 1938727）与跨境 ERP 服务商应用（原 AppKey 4848173），管理员统一选择当前使用哪套 API。未设置选择时默认恢复企业自用应用。商品采集、采购、订单、支付和物流共用所选应用，每位客户的两套买家授权分别保存在自己的 ERP 空间。

跨境方案提供的接口、消息类型及 ERP 已接入的业务接口见 [1688 API 与消息目录](alibaba-api-catalog.md)。两套应用可调用的能力仍以各自在 1688 获得的权限为准，不因本地切换而获得新权限。

## 管理员配置与客户使用

管理员在「系统设置 → 管理员共享设置 → 1688 API」选择当前应用并保存，在下方分别维护两套 AppKey、AppSecret 和回调地址。密钥留空沿用已保存值；切换保留双方授权，更换应用凭据后该应用需要重新授权。保存后对所有账号生效，账号授权页显示当前应用类型。

首次保存前，企业自用应用读取原根库凭据或 `ERP_ALIBABA_APP_KEY`、`ERP_ALIBABA_APP_SECRET`、`ERP_ALIBABA_REDIRECT_URI`；跨境应用读取 `ERP_ALIBABA_CROSSBORDER_APP_KEY`、`ERP_ALIBABA_CROSSBORDER_APP_SECRET`、`ERP_ALIBABA_CROSSBORDER_REDIRECT_URI`。密钥仅保存在服务端。仅有旧 Token 而没有回调地址时，可继续使用匹配的原授权，但须补全回调地址才能重新发起授权。

选择跨境应用时，按跨境 ERP 方案完成资质、订购和上架审核，使用平台认可的聚石塔应用回调地址。将审核通过的服务订购地址提供给客户。客户先订购，再到「授权配置 → 1688 授权」打开浏览器，登录自己的 1688 账号；企业自用应用直接连接该应用下的采购账号。ERP 服务端收到回调后校验一次性 state、交换授权码，并在发起授权的客户空间保存 Token。页面自动显示授权账号。

管理员本机调试时，可先在 ERP 发起授权，登录 1688 后从浏览器地址栏复制完整回调 URL（包含 `code` 和 `state`），粘回「1688 授权」并点击「完成授权」。即使 localhost 页面无法连接也可复制。服务端仅解析该 URL，校验回调地址与管理员自己的待完成授权并向 1688 换码。普通账号使用自动回调。

## 已有客户与采购记录

共享应用沿用根库 `enterprise_applications / active` 下的 `alibaba` 和 `alibaba_crossborder`，选择保存在同条记录的 `alibaba_api_application`。客户自用授权沿用 `credentials:alibaba / state`，跨境授权沿用 `/ crossborder`；归档自用凭据和旧 `1688_api` 凭据保留恢复及迁移能力。切回对应应用后，指纹仍匹配的原授权可以继续使用。共享设置及授权响应不下发密钥或 Token。

切换不重写采购记录归属；另一应用的记录不能借用当前授权查询或支付，切回原应用及买家后再处理。原请求号、未知结果防重及本地数量占位继续有效。切换期间收到的授权、刷新回执必须重新校验当前应用和授权快照，不能写入另一套凭据。修改密钥后再改回原值，也不能恢复旧代次授权。

历史桌面客户端的两组授权接口都连接管理员当前所选应用。当前版本只显示一个授权入口。旧客户端保存共享设置时省略应用选择或自用配置，不改变当前选择及已保存的自用配置。查询账号状态不发送远程请求。业务调用在到期前五分钟刷新 Token，刷新结果校验买家身份。

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
