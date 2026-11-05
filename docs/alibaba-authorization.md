# 1688 应用配置与买家授权

## 配置与使用

服务商的应用凭据由受控后端配置：

```dotenv
ERP_ALIBABA_APP_KEY=你的应用编号
ERP_ALIBABA_APP_SECRET=你的应用密钥
ERP_ALIBABA_REDIRECT_URI=开发者后台登记的完整回调地址
```

AppKey 与 AppSecret 成对设置，不与历史配置拼接。回调必须是 HTTPS，或本机调试的
`http://127.0.0.1:<端口>/auth/alibaba/callback` / localhost；实际能否登记该回调由 1688 平台决定。
配置文件可使用后端 `config/.env`，不要使用 `VITE_` 变量或将真实凭据提交到仓库。
环境注入的密钥不会复制到数据库。环境缺省时保留已迁移的本机应用凭据。

客户在「授权配置 → 1688 授权」点击连接，在官方页面登录自己的采购账号并同意授权。
不填写 AppKey、AppSecret 或 Token。回调到本后端的 `/auth/alibaba/callback` 时自动完成；
若开发者使用独立回调落地页，客户可将完整回调地址粘贴回 ERP 完成授权，必须同时包含
本次生成的 code 和 state。ERP 不接受任意授权码、手填 Token 或前端传入的应用密钥。

授权状态读取不发送远程请求；后续 API 调用在到期前五分钟刷新 Token。授权码只交换一次，
失败保留原账号，需重新发起登录。刷新回执必须属于原买家；网络期间配置或授权改变时，迟到
回执不能覆盖新状态。请求统一经过外部请求管理器，不自动重放结果未知的请求。

## 存储与业务归属

`ConfigStore` 在主库 `runtime_secrets` 的 `credentials:alibaba / state` 保存结构化授权状态：
应用凭据 `application`、当前买家 `authorization`、短期授权请求 `pending` 各自独立。
应用权限属于 AppKey；买家 Token 绑定签发应用与账号，公司的买家 Token 不能跨客户共享。
同应用密钥配置变更后，旧授权保留但不再使用，需要重新授权。

每个数据目录的采购应用和跨境商品查询应用各有一个活动买家授权。采购授权保存在原有的
`credentials:alibaba / state`，跨境授权使用同一命名空间下独立的 `crossborder` 记录。
跨境应用使用后端变量 `ERP_ALIBABA_CROSSBORDER_APP_KEY`、`ERP_ALIBABA_CROSSBORDER_APP_SECRET`
和 `ERP_ALIBABA_CROSSBORDER_REDIRECT_URI`。可登记同一个回调 URL，服务端按一次性 state
选择应用并核验。用户在「1688 跨境商品查询授权」用原采购账号再次登录后，可以输入商品编号
验证多语言商品详情接口返回的 SKU 数和含 specId 的数量。

开发版跨境应用实测使用 `http://localhost:5050/auth/alibaba/callback` 可以进入官方授权页。
同端口和路径使用 `127.0.0.1` 时平台显示“非法请求”。配置时按应用实际接受的主机名填写。
新授权的采购范围由 AppKey 和官方返回的 memberId 确定。刷新 Token 或同账号重新授权不改变
历史归属；切换账号不能处理另一个账号的采购请求。原有跨授权未决采购防重继续生效。

首次读取授权时，将旧 `app_config / 1688_api` 的 AppKey、AppSecret、Access Token 写入
上述独立记录，成功后清空旧凭据。先落新记录再清理旧字段，异常中断可续迁；静态采集选项保留。
历史采购范围摘要原样保留，迁移不猜测旧 Token 的账号身份。旧 Token 没有 memberId 和刷新凭据，
继续按原有范围使用，到期需重新授权；新授权不能自动接管无法证明归属的旧请求，历史记录不删除。

公共配置不再下发 1688 凭据或掩码；通用配置保存及瞬态采集参数均不能覆盖应用密钥、买家授权、
请求网关。采购、物流、订单测试与 API 采集统一从授权服务取得服务端凭据。

## 桌面交付与平台权限

公司 AppSecret 不得打包到分发给客户的桌面程序。此实现支持由可信管理员管理密钥的后端部署；
若要把同一个公司应用提供给所有桌面客户，需要另建由公司控制的授权和 API 服务，按客户隔离，
桌面端连接该服务。当前修改没有创建或部署这一云端服务，也不把客户本机环境变量视为密钥保密方案。
桌面版使用随机端口，不能假定固定回环回调可以直接使用；现阶段可通过已登记的落地页手动完成回调。

应用仍须实际开通服务商方案和具体接口权限。账号授权成功不代表商品详情、下单或免密支付接口可用，
不改变平台额度规则，也不解除已有外部请求阻断。

## 协议依据与验证

核对日期：2026-11-04。

- [1688 官方 getToken 定义](https://open.1688.com/api/data/getApiDetail.json?namespace=system.oauth2&name=getToken&version=1&_input_charset=UTF-8)
- [1688 官方测试工具](https://open.1688.com/api/apiTool.htm)及其
  [授权实现](https://g.alicdn.com/b2b-open-platform/ocean-apifactory-assets/1.8.7/gw_api_tool.js?v=1)：
  签名授权链接 `https://auth.1688.com/auth/authorize.htm`，site=china；
  换取和刷新使用 `/openapi/http/1/system.oauth2/getToken/{AppKey}`，表单提交，刷新使用
  grant_type=refresh_token。读取官方响应的 memberId、access_token、refresh_token、expires_in。

回归覆盖迁移、应用更换、回调 state/期限/单次使用、刷新与账号一致性、迟到回执、公共配置及界面凭据边界。
测试使用隔离数据库与替身响应；未执行真实客户授权、创建订单或支付。上线仍需配置真实回调并由客户完成登录验证。
