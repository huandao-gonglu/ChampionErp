# 1688 跨境 ERP 授权

生产环境使用跨境 ERP 服务商应用（AppKey 4848173）。商品采集、采购、订单、支付和物流共用同一应用，每位客户的买家授权保存在自己的 ERP 空间。企业自用应用 1938727 已退出 ERP 业务。

## 管理员配置与客户使用

管理员在「系统设置 → 管理员共享设置 → 1688 跨境 ERP 服务商应用」维护 AppKey、AppSecret 和回调地址。首次保存前可通过后端环境变量 `ERP_ALIBABA_CROSSBORDER_APP_KEY`、`ERP_ALIBABA_CROSSBORDER_APP_SECRET` 和 `ERP_ALIBABA_CROSSBORDER_REDIRECT_URI` 提供初始值。密钥仅保存在服务端。

按跨境 ERP 方案完成资质、订购和上架审核，使用平台认可的聚石塔应用回调地址。将审核通过的服务订购地址提供给客户。客户先订购，再到「授权配置 → 1688 授权」打开浏览器，登录自己的 1688 账号。ERP 服务端收到回调后校验一次性 state、交换授权码，并在发起授权的客户空间保存 Token。页面自动显示授权账号。

## 已有客户与采购记录

共享应用沿用根库 `enterprise_applications / active` 下的 `alibaba_crossborder`，客户授权沿用各自数据库的 `credentials:alibaba / crossborder`。已有跨境授权、配置代次和当前授权请求继续有效。只连接过企业自用应用的客户需要订购并重新授权。旧的数据库授权和采购记录保留原归属，自用凭据不会参与发起授权、换码、查询或支付。

历史桌面客户端的两组授权接口都连接同一个服务商应用。当前版本只显示一个授权入口。查询账号状态不发送远程请求。业务调用在到期前五分钟刷新 Token，刷新结果校验买家身份。

商品详情经多语言商详接口读取，保留原文标题和 SKU 规格。请求固定发送到 1688 官方网关并使用服务端签名。[1688 官方商详接口定义](https://open.1688.com/api/data/getApiDetail.json?namespace=com.alibaba.fenxiao.crossborder&name=product.search.queryProductDetail&version=1)

## 协议依据与验证

核对日期：2026-11-04。

- [1688 官方 getToken 定义](https://open.1688.com/api/data/getApiDetail.json?namespace=system.oauth2&name=getToken&version=1&_input_charset=UTF-8)
- [1688 官方测试工具](https://open.1688.com/api/apiTool.htm)及其
  [授权实现](https://g.alicdn.com/b2b-open-platform/ocean-apifactory-assets/1.8.7/gw_api_tool.js?v=1)：
  签名授权链接 `https://auth.1688.com/auth/authorize.htm`，site=china；
  换取和刷新使用 `/openapi/http/1/system.oauth2/getToken/{AppKey}`，表单提交，刷新使用
  grant_type=refresh_token。读取官方响应的 memberId、access_token、refresh_token、expires_in。
