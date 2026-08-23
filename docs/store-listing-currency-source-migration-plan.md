# 店铺发布币种配置单一事实源迁移方案

状态：已实施（2026-08-23 完成，全部验收项落地，详见文末实施记录）
日期：2026-08-23
范围：Mercado Libre、Yandex、Ozon 的店铺授权、发布币种配置、核价和发布链路

## 1. 决策结论

同意把“店铺授权配置”改为发布币种的唯一事实源，并作如下约束：

1. 授权成功后，后端立即通过平台能力接口读取发布币种或可选币种，并把结果持久化到该店铺的 `store_auth.auth_detail_json`。
2. 远端返回单一币种时自动填入，界面只读展示。
3. 远端返回多个币种时保存允许集，界面使用下拉框要求用户明确选择。
4. 平台明确不提供可查询能力时保持空值，界面要求用户手动填写；没有填写前不得核价、预检或发布。
5. 平台本应支持读取但本次请求失败时，不得降级成人工填写，也不得继续使用代码默认值；应标记读取失败并允许重试。
6. 核价和发布只能读取持久化店铺配置中的 `listing_currency`。注册表、国家、站点、草稿历史值和前端 option 都不得作为 fallback。
7. 草稿仍保存本次核价使用的币种快照和指纹，用于复盘及失效判断，但草稿不是币种事实源。
8. 删除发布工作流中的 `market_currency`：当前它只被层层复制，没有真实展示或计算调用。将来若调研、订单或报表需要市场展示币种，应由对应领域显式定义，不能挂在发布目标上。
9. Yandex `RUB -> RUR` 协议字段转换必须保留；这是最终 HTTP payload 的 wire 编码，不是币种来源。店铺配置为 CNY 时不会触发该转换。

本方案不引入 feature flag、双读、双写、legacy fallback 或按平台静态兜底。迁移完成后，旧路径和只验证旧行为的测试必须删除。

## 2. 当前状态与问题

### 2.1 当前持久化状态

店铺凭据和动态授权信息按平台保存在 SQLite `store_auth` 表；非敏感静态配置保存在 `config/store_config.json`，运行时由 `ConfigStore` 合并。

当前数据表现为：

| 平台 | 授权状态 | 当前币种状态 | 问题 |
|---|---|---|---|
| Ozon | 测试成功 | 已保存 `CNY` | 路径最接近目标方案，但仍使用 `contract_currency` fallback，且核价会现场补取并写配置 |
| Yandex | 测试成功 | 币种字段存在但值为空 | 已请求 Business settings，却只保存 `onlyDefaultPrice`，遗漏 `settings.currency` |
| Mercado Libre | 测试失败 | 未保存发布币种 | 当前由站点注册表静态推断；静态 `site_id=MLM` 与远端遗留 `account_site_id=CBT` 语义混杂 |

### 2.2 现有错误来源

- `erp_web/marketplace_registry.py` 同时维护 `market_currency` 和静态 `listing_currency`，Mercado Libre 与 Yandex 会把注册表值当发布真值。
- `market_currency` 当前只在注册表、草稿 schema 和前端 target state 之间传递，没有参与核价、payload 或实际展示，属于无独立产品价值的冗余字段。
- `erp_web/services/listing_currency_service.py` 按平台分支，Mercado Libre 读取站点静态值，Yandex 读取 campaign 静态规则，只有 Ozon 读取店铺授权数据。
- `erp_web/product_model/defaults.py`、`erp_web/stores/product_store.py` 和前端站点 option 会在草稿初始化时复制静态 `listing_currency`。
- 前端存在多处 `target.listingCurrency || site.listingCurrency` fallback，旧静态值可重新进入核价和发布上下文。
- `erp_web/runtime_units/pricing_runtime.py` 在 Ozon 币种缺失时现场调用远端并写配置，使“授权”和“核价”共同拥有币种生命周期。
- 发布 payload 主要读取草稿快照；发布预检只验证草稿内部币种一致性，没有强制与当前店铺币种配置及其指纹相等。
- 前端保存授权后仅为 Yandex 自动复测，三个平台行为不一致。
- `/api/save-settings` 当前请求契约过宽，客户端理论上可以提交 `currency_source`、`allowed_currencies` 等应由后端派生的可信字段。

## 3. 目标与非目标

### 3.1 目标

- 店铺授权配置中的已确认 `listing_currency` 是核价和发布的唯一币种来源。
- 授权验证、币种发现、人工选择、持久化、核价失效和发布校验形成一条确定性链路。
- 授权状态与发布币种就绪状态分离，错误提示能够明确指出是凭据失败还是币种未配置。
- 平台差异只保留在远端能力适配器；共享核价、草稿和发布代码不出现平台币种分支。
- 旧静态币种来源及其前后端 fallback 被完整删除。

### 3.2 非目标

- 不把买家前台展示币种、订单交易币种或最终结算/提现币种等同于发布币种。
- 不在本次方案中支持同一平台绑定多个卖家账户；当前仍是一平台一条 `store_auth` 记录。
- 不允许商品草稿单独覆盖店铺发布币种。
- 不在平台远端读取失败时自动使用国家币种、市场币种、上次草稿币种或代码常量。

## 4. 术语与强制不变量

### 4.1 术语

| 字段 | 含义 | 是否可作为发布真值 |
|---|---|---|
| `listing_currency` | 当前授权店铺已确认的发布报价币种 | 是 |
| `allowed_currencies` | 远端在店铺级返回的可选发布币种集合 | 仅用于约束用户选择 |
| `currency_mode` | 币种是远端锁定、远端可选、人工配置还是未解析 | 是 |
| `currency_status` | 币种配置是否已经可以用于核价和发布 | 是 |
| 草稿 `listing_currency` | 某次核价使用的店铺币种快照 | 否，只用于一致性校验 |

### 4.2 不变量

1. 只有 `currency_status == "ready"` 且 `listing_currency` 非空时，店铺才具备核价和发布能力。
2. `currency_mode == "locked"` 时必须满足 `allowed_currencies == [listing_currency]`。
3. `currency_mode == "selectable"` 时，`listing_currency` 必须属于 `allowed_currencies`；未选择时状态必须是 `selection_required`。
4. `currency_mode == "manual"` 时，`allowed_currencies` 必须为空，币种只能通过受控人工配置接口写入。
5. `currency_status == "refresh_failed"` 时，即使仍保留上次展示值，也不得用于核价和发布。
6. 远端派生字段不得由通用配置保存接口直接写入。
7. 发布前必须重新加载当前店铺配置，并比较当前币种指纹与核价快照指纹。

## 5. 目标数据契约

在 `erp_web/schemas/currency.py` 中把平台特定 mode 改为通用店铺币种契约：

```python
CurrencyMode = Literal[
    "locked",
    "selectable",
    "manual",
    "unresolved",
]

CurrencyStatus = Literal[
    "ready",
    "selection_required",
    "manual_required",
    "refresh_failed",
    "unresolved",
]

class StoreListingCurrency(TypedDict):
    listing_currency: str
    allowed_currencies: list[str]
    currency_mode: CurrencyMode
    currency_status: CurrencyStatus
    currency_source: str
    currency_verified_at: str
    currency_fingerprint: str
    currency_error_code: str
    currency_error_message: str
```

持久化示例：

```json
{
  "listing_currency": "CNY",
  "allowed_currencies": ["CNY"],
  "currency_mode": "locked",
  "currency_status": "ready",
  "currency_source": "account_api",
  "currency_verified_at": "2026-08-23T12:00:00Z",
  "currency_fingerprint": "sha256:...",
  "currency_error_code": "",
  "currency_error_message": ""
}
```

`currency_fingerprint` 由以下规范化数据计算，不包含时间戳：

```text
platform
+ 稳定店铺身份
+ listing_currency
+ 排序后的 allowed_currencies
+ currency_mode
+ currency_source
```

同一配置重新验证不会让旧核价无故失效；店铺身份、发布币种、允许集或模式变化时，指纹一定变化。

### 5.1 删除 `contract_currency`

`contract_currency` 当前只作为 Ozon `listing_currency` 的同值副本和 fallback，语义又容易与结算币种混淆。迁移时将有效值写入规范化 `listing_currency` 后，删除该字段、读取分支、测试和文档。

## 6. 币种发现状态机

成功验证凭据后，平台 tester 返回统一的发现结果：

```text
授权成功
  |
  +-- 远端返回 1 个币种
  |      -> mode=locked
  |      -> 自动保存该币种
  |      -> status=ready
  |
  +-- 远端返回多个币种
  |      -> mode=selectable
  |      -> 旧选择仍在允许集则保留，否则清空
  |      -> 有选择时 ready，无选择时 selection_required
  |
  +-- 平台明确不支持店铺币种查询
  |      -> mode=manual
  |      -> 保持 listing_currency 为空
  |      -> status=manual_required
  |
  +-- 平台声明支持，但请求失败或响应无效
         -> 不转 manual，不使用默认值
         -> status=refresh_failed
         -> 阻断核价和发布，允许用户重试
```

授权失败时将币种状态重置为 `unresolved`。凭据、Campaign ID 或稳定店铺身份变化时，也必须在同一次保存中清除旧币种可信状态。

## 7. 各平台发现策略

| 平台 | 授权完成后的读取方式 | 规范化处理 | 失败策略 |
|---|---|---|---|
| Yandex | `POST /v2/businesses/{businessId}/settings` 的 `settings.currency` | 将 wire `RUR` 规范化为内部 `RUB`；其他 ISO 代码保持大写 | 接口失败为 `refresh_failed`，不再使用注册表 RUB |
| Ozon | `POST /v1/seller/info` 的 `company.currency` | 单值保存为 `locked`，来源 `account_api` | 接口失败为 `refresh_failed`，核价不再现场补取 |
| Mercado Libre | 先用 `/users/me` 确认 `account_site_id`，再读取远端 site/category currency metadata | 店铺级返回单值则锁定；返回多值则待选；仅有类目允许集时作为发布前约束 | 无法从店铺级接口得到币种时进入 `manual_required`，不得从本地站点注册表推断 |

补充约束：

- Mercado Libre CBT/Global Selling 即使官方发布契约固定 USD，也必须先把发现结果持久化到店铺配置；核价和发布不得直接读取代码常量。若运行时接口没有返回可确认的币种，则遵循本方案进入人工配置。
- Mercado Libre 类目 `settings.currencies` 是类目级约束。它可以在类目确认和发布预检时验证店铺配置，但不能静默修改全局店铺币种。
- “多个币种下拉选择”只用于店铺级发现确实返回多个值的情况；商品类目级多币种不会自动变成草稿 override。

## 8. 授权页交互方案

在 `front/src/components/auth/AuthSettingsPanel.vue` 的每个平台授权卡片中增加统一的“发布货币”区块，放在凭据输入之后、授权操作结果之前。

### 8.1 展示字段

- 当前发布货币
- 配置状态：已就绪、请选择、需人工填写、读取失败、未验证
- 来源：平台账户、Business、站点 API、人工配置
- 最近验证时间
- 允许币种列表（存在时）
- 读取失败原因和“重新验证授权并读取币种”按钮

### 8.2 控件规则

| 状态 | 控件 |
|---|---|
| `locked + ready` | 只读输入框或值标签，不允许编辑 |
| `selectable + selection_required` | 必填下拉框，option 仅来自 `allowed_currencies` |
| `selectable + ready` | 下拉框，可切换到允许集内其他币种 |
| `manual + manual_required` | 空白文本框，提示输入 ISO 4217 三位代码 |
| `manual + ready` | 可编辑文本框，修改后提示旧核价将失效 |
| `refresh_failed` | 显示上次值仅供参考，禁用保存和核价，提供重试 |
| `unresolved` | 禁用币种输入，先完成或重新验证授权 |

手工值由后端规范化为大写并使用 ISO 4217 校验器验证。人工填写只代表“用户已配置”，界面不得文案化为“平台已验证”。

### 8.3 授权操作统一

- 将按钮统一为“测试授权并读取发布货币”。
- Mercado Libre OAuth code 交换成功后自动执行用户信息和币种发现，不再要求用户额外点击 `07D 用户信息` 才能补齐发布能力。
- 保存凭据本身不伪造成功态；只有持久化配置上的在线测试结果可以写入可信币种状态。
- 未保存凭据的 preview 测试可以返回币种预览，但不得落库，界面必须标注“预览，尚未保存”。
- 成功测试响应必须返回最新公开 `storeConfig`、`storeAuthSummary` 和 `currencyConfiguration`，前端立即刷新卡片，不依赖整页重载。
- 删除前端“仅 Yandex 保存后自动复测”的特殊分支，改为所有平台共享同一授权完成流程。

## 9. HTTP 与信任边界

### 9.1 授权测试响应

扩展 `POST /api/test-store-auth`：

```json
{
  "ok": true,
  "platform": "ozon",
  "auth_status": "测试成功",
  "publish_ready": true,
  "currency_configuration": {
    "listing_currency": "CNY",
    "allowed_currencies": ["CNY"],
    "currency_mode": "locked",
    "currency_status": "ready",
    "currency_source": "account_api",
    "currency_verified_at": "2026-08-23T12:00:00Z"
  },
  "storeConfig": {},
  "storeAuthSummary": {}
}
```

`ok` 表示授权请求本身成功，`publish_ready` 表示发布币种也已就绪，两者不得混为一个状态。

### 9.2 人工选择接口

新增薄路由：

```text
POST /api/store-auth/currency
{
  "platform": "mercadolibre",
  "listing_currency": "USD"
}
```

后端规则：

- `locked`：拒绝修改。
- `selectable`：必须属于当前持久化允许集。
- `manual`：必须通过 ISO 4217 格式和代码校验。
- `unresolved`、`refresh_failed`：拒绝保存。
- 保存后重新计算 `currency_fingerprint` 并返回最新公共店铺配置。

路由放在 `erp_web/http_route_units/auth_config_routes.py`，编排放在 `erp_web/facades/auth_config_facade.py`，请求契约同步加入 `erp_web/schemas/requests.py::REQUEST_CONTRACTS`。路由不得直接依赖 runtime unit。

### 9.3 禁止客户端写派生字段

`/api/save-settings` 只允许保存平台注册表声明的凭据字段和非敏感静态字段。以下字段必须拒绝或剥离客户端输入，只能由后端授权/币种服务写入：

- `allowed_currencies`
- `currency_mode`
- `currency_status`
- `currency_source`
- `currency_verified_at`
- `currency_fingerprint`
- `currency_error_code`
- `currency_error_message`

`listing_currency` 也不再通过通用保存接口写入，只能经受控人工选择接口更新。

## 10. 后端职责拆分

### 10.1 `store_credentials.py`

- 继续作为授权 tester 注册入口。
- 每个平台 tester 在凭据校验成功后调用对应平台远端能力，并返回统一 `StoreListingCurrencyDiscovery`。
- 共享编排负责把发现结果归一化并持久化；tester 不各自拼接状态字典。
- 授权失败、身份变化和清除授权统一清除币种 ready 状态。

### 10.2 `listing_currency_service.py`

保留为纯服务，但删除所有平台分支和 `marketplace_site()` 依赖。目标职责只有：

- 规范化远端发现结果。
- 应用单值、多值和人工选择状态机。
- 从店铺授权配置构造只读 `StoreListingCurrency`。
- 校验 ready 状态、允许集、币种格式和指纹。
- 为核价、草稿投影和发布提供同一解析结果。

不得执行网络请求、持久化或按国家/站点推断币种。

### 10.3 平台 HTTP 边界

- Yandex：复用 `fetch_yandex_business_settings()` 返回的完整 `settings`，新增显式 currency 解析和 wire/ISO 转换测试。
- Ozon：复用 `fetch_ozon_seller_info()`，删除 Ozon 专属 `contract_currency` 模型。
- Mercado Libre：集中 `/users/me`、site currency metadata 和可选类目约束读取；授权、token refresh 和 publish preflight 不再各自复制用户信息更新逻辑。

### 10.4 持久化

- 新币种字段继续存 `store_auth.auth_detail_json`，不进入 `config/store_config.json`。
- `ConfigStore` 的 auth detail allowlist 加入新字段并删除 `contract_currency`。
- `update_store_auth()` 的普通业务写仍可 merge；版本迁移必须使用显式 replace/rewrite，确保退役 key 从 JSON 中物理删除。
- `account_site_id` 属于远端授权身份，应移入 `auth_detail_json`；静态 `site_id` 不得再承担账户身份或币种来源语义。

## 11. 核价链路

1. `pricing_runtime.calculate_price()` 加载当前店铺授权配置。
2. 通过无平台分支的 `require_listing_currency()` 获取 ready 配置。
3. 若未就绪，直接返回结构化错误：
   - `STORE_CURRENCY_UNRESOLVED`
   - `STORE_CURRENCY_SELECTION_REQUIRED`
   - `STORE_CURRENCY_MANUAL_REQUIRED`
   - `STORE_CURRENCY_REFRESH_FAILED`
4. 将 `listing_currency` 和 `currency_fingerprint` 注入每个目标的核价输入。
5. `pricing_service` 将两者写入 `calculation_basis`，原有 SHA-256 核价指纹继续覆盖该 basis。
6. 草稿只保存结果快照，不得覆盖店铺配置。

删除 Ozon 在核价时调用 `refresh_ozon_currency_capability()` 的特殊路径。缺币种时必须回到授权配置完成，不允许核价层产生远端副作用。

## 12. 发布链路

发布前构造不可变 `StorePublishContext`：

```python
class StorePublishContext(TypedDict):
    platform: str
    site: str
    store_identity: str
    listing_currency: str
    currency_fingerprint: str
```

强制检查：

1. 当前店铺币种状态必须是 `ready`。
2. 当前店铺 `listing_currency` 必须等于草稿目标币种快照。
3. 当前 `currency_fingerprint` 必须等于核价 basis 中的指纹。
4. `applied_price.currency` 必须等于当前店铺 `listing_currency`。
5. 类目返回允许币种时，当前店铺币种必须属于该类目允许集。
6. payload builder 从 `StorePublishContext.listing_currency` 写平台字段，不从注册表或草稿猜值。

任一不一致都返回 `PRICING_STALE` 或 `STORE_CURRENCY_CHANGED`，要求重新核价；不得自动换币或只改 payload 币种。

平台 payload 仍使用各自协议字段：

- Mercado Libre：`currency_id`
- Ozon：`currency_code`
- Yandex：`currencyId`，内部 `RUB` 在最后一层转换为 wire `RUR`

## 13. 数据迁移

使用版本化、一次性的数据库迁移，不保留运行时旧格式 fallback。

### 13.1 `store_auth` 迁移

1. Ozon：若现有 `contract_currency/listing_currency`、`currency_source=account_api` 和允许集一致，则迁移为新 `locked + ready`；随后物理删除 `contract_currency`。
2. Yandex：现有静态 RUB 或空币种均不视为可信。迁移为 `unresolved`，要求重新测试授权，从 Business settings 获取真实值。
3. Mercado Libre：不把注册表 `site_id -> currency` 映射迁移成可信值。现有授权失败或无凭据时保持 `unresolved`；下次授权成功后重新发现。
4. 将可信 `account_site_id` 从静态 JSON 移入 auth detail；授权失败记录中的遗留值直接丢弃。
5. 删除所有 auth detail 中的旧 mode/source：`account_locked`、`site_locked`、`campaign_locked`、`site_rule`、`campaign_rule`、`account_api_required`。

### 13.2 静态配置迁移

- 从 `config/store_config.json` 删除任何遗留 `listing_currency`、`contract_currency` 和派生 currency 字段。
- 从 marketplace site option、商品草稿 target、数据库 draft fallback 和前端 workflow state 中删除 `market_currency/marketCurrency`。
- 选品调研、订单或报表若已有独立 `currency` 字段可继续保留；它们属于各自领域，不复用发布目标字段。
- 若保留 Mercado Libre 默认目标站点设置，应将含义明确命名为 `default_target_site`；不得继续把 `site_id` 同时解释为账户站点和草稿目标站点。

### 13.3 草稿与核价迁移

- 旧草稿中的 `listing_currency` 可保留为历史快照，但不能通过新 publish precheck。
- 清除旧 `currency_resolution` 中的静态 source/mode。
- 旧核价 basis 没有 `currency_fingerprint`，统一标记 stale，要求重新核价。
- 不根据旧草稿、站点代码或 payload 自动回填新店铺币种。

## 14. 退役代码删除清单

以下内容在新路径切换后直接删除，不保留备用实现。

### 14.1 后端静态来源

- 删除 `erp_web/marketplace_registry.py` 中所有 site 的 `listing_currency` 和 `market_currency`；注册表只保留平台/站点身份、标签、语言和能力元数据。
- 删除 `erp_web/services/listing_currency_service.py` 中 Mercado Libre/Yandex/Ozon 平台分支、`marketplace_site()` 读取和静态 source 字符串。
- 删除 `erp_web/product_model/defaults.py` 从站点注册表复制 `listing_currency` 的逻辑，默认草稿币种改为空。
- 删除 `erp_web/stores/product_store.py::_normalized_target_payload` 的站点 `listing_currency/market_currency` fallback。
- 删除 `erp_web/schemas/product.py`、`erp_web/product_model/merge_model.py`、`erp_web/runtime_units/draft_publish_context.py`、`erp_web/db.py` 中草稿目标 `market_currency` 的 schema、归一化、快照和 fallback。
- 删除 `erp_web/runtime_units/pricing_runtime.py` 的 Ozon 现场币种刷新和 `contract_currency` fallback。
- 删除 `erp_web/runtime_units/store_credentials.py::refresh_ozon_currency_capability` 专属状态拼装；由共享发现状态机替代。
- 删除 `_STORE_AUTH_DETAIL_FIELDS` 中的 `contract_currency` 及所有读写。

### 14.2 前端静态来源

- 删除平台 option wire shape 和 TypeScript 类型中的 `MarketplaceSiteOption.listingCurrency/marketCurrency`。
- 删除 `MarketplaceTargetSite.marketCurrency` 及其序列化字段；发布目标只保留平台、站点、语言和店铺币种快照。
- 删除 `front/src/api/workflow/normalizers/core.ts` 对 site `listing_currency/market_currency` 的读取。
- 删除 `front/src/utils/draftTargetOptions.ts` 从 site option 生成 `listingCurrency/marketCurrency` 的逻辑。
- 删除 `front/src/stores/workflow/orchestration/runtime.ts` 中全部 `site.listingCurrency/site.marketCurrency` fallback。
- 删除 `CategoryAttributesPanel.vue`、`PublishPrecheckPanel.vue` 中 `site?.listingCurrency` 展示 fallback；未就绪统一显示“店铺发布货币待配置”。
- 删除前端只为 Yandex 自动复测的特殊分支，统一使用授权完成响应。

### 14.3 发布边界旧读取

- Mercado Libre、Ozon、Yandex payload builder 不再自行从 draft/store/registry 多处寻找币种，只接受 `StorePublishContext`。
- 删除任何 `draft.listing_currency or registry_currency`、`store.contract_currency or store.listing_currency` 形式的 fallback。
- 保留 Yandex `_YANDEX_WIRE_CURRENCY_MAP`，但只允许对已经确认的店铺币种执行协议转换。

### 14.4 重复授权路径

- Mercado Libre `/users/me`、token refresh 后的店铺身份更新与币种发现收敛到一个服务。
- 删除 `_07d_user_info` 和 `ensure_mercadolibre_auth_ready` 中重复的身份/币种状态拼装；发布前只调用统一授权服务返回的只读上下文。
- `07D category_attrs`、`payload_generate` 若保留，必须明确作为诊断工具，禁止写授权或币种状态；若没有独立产品入口则连同 UI 按钮、路由、日志和测试一起删除。

### 14.5 旧测试和文档

- 删除 `tests/test_listing_currency_service.py` 中“Mercado 按站点锁定”和旧 mode/source 断言，改写为店铺配置状态机测试。
- 删除 `tests/test_marketplace_registry.py` 中 site `listing_currency` 断言。
- 删除所有以 `contract_currency` 构造 Ozon fixture 的测试数据。
- 重写依赖 registry 自动注入 MXN/BRL/RUB/USD 的核价、草稿和发布 fixture，使其显式创建 ready 店铺授权配置。
- 更新 `docs/ai-context-map.md`，删除“注册表维护站点锁定 listing_currency”和“Ozon 核价现场补取”的旧说明。
- 重新生成 `front/src/types/workflow.generated.ts` 和前端静态构建产物，不手工维护旧字段。

## 15. 实施顺序

### Phase 1：契约与纯状态机

- 定义新 schema、状态枚举、规范化规则和指纹。
- 把 `listing_currency_service.py` 改为无平台分支的纯服务。
- 增加状态机、人工选择和指纹单元测试。

### Phase 2：远端发现与持久化

- 接入 Yandex `settings.currency`。
- 将 Ozon seller info 接入共享状态机。
- 收敛 Mercado Libre 店铺身份与远端币种发现。
- 扩展 auth detail allowlist、身份变化失效和授权测试响应。
- 新增受控人工币种保存接口。

### Phase 3：授权页

- 增加统一发布货币区块。
- 实现单值只读、多值下拉、无能力人工输入、失败重试。
- 删除 Yandex 特殊复测分支和 site option 币种 fallback。

### Phase 4：核价与发布切换

- 核价只读 ready 店铺配置并保存币种指纹。
- 发布构造 `StorePublishContext`，强制与核价快照交叉校验。
- payload builders 改为显式接收已确认币种。
- 删除 Ozon 核价现场刷新和全部注册表 fallback。

### Phase 5：数据迁移与退役清理

- 执行版本化 SQLite/JSON/草稿迁移。
- 物理删除退役 JSON key、旧符号、旧请求字段、旧测试和旧文档。
- 使用代码检索和架构测试确认旧 source/mode/field 不再存在。

各阶段是代码提交顺序，不是运行时双轨方案；最终合入前必须完成 Phase 5，不允许带旧 fallback 上线。

## 16. 测试方案

### 16.1 后端单元测试

- 单值远端结果自动成为 `locked + ready`。
- 多值结果无选择时为 `selection_required`，合法旧选择可保留，非法旧选择被清空。
- 明确不支持查询时进入 `manual_required`。
- 已声明支持但远端失败时进入 `refresh_failed`，不转 manual。
- 手工币种大小写规范化、非法 ISO 拒绝、locked 模式拒绝编辑。
- 凭据或稳定店铺身份变化清空旧 ready 状态。
- 指纹对时间戳稳定，对店铺、币种、允许集和模式变化敏感。

### 16.2 平台测试

- Yandex Business settings 的 `CNY` 被保存为 CNY，`RUR` 被规范化为内部 RUB。
- Ozon `company.currency=CNY` 被保存为唯一允许币种。
- Mercado Libre 单值、多值、无店铺级币种和 category 不允许当前币种四种结果。
- 远端 401/403、限流、超时和响应缺字段均产生正确状态，不泄露凭据。

### 16.3 核价与发布测试

- 未就绪店铺不能核价。
- 店铺币种变更后旧核价必定 stale。
- draft 快照被篡改不能绕过当前店铺配置。
- payload 中币种严格等于 `StorePublishContext.listing_currency`。
- Yandex 仅在 wire 边界把 RUB 转为 RUR；CNY 保持 CNY。
- 类目允许集不包含店铺币种时发布被阻断。

### 16.4 前端测试

- 授权成功后立即显示读取到的币种、来源和时间。
- 单值只读、多值下拉、manual 空输入和 refresh_failed 重试状态正确。
- 未保存 preview 不更新持久化展示。
- 修改人工/可选币种时提示旧核价失效。
- 前端不再从平台 site option 获得发布币种。

### 16.5 架构守卫

在 `tests/architecture/test_platform_contracts.py` 增加：

- `MARKETPLACE_SPECS[*].sites` 不允许出现 `listing_currency`。
- `MARKETPLACE_SPECS[*].sites` 和发布草稿 target 不允许出现 `market_currency`。
- `listing_currency_service.py` 不允许导入 marketplace registry 或平台 HTTP/runtime 模块。
- `pricing_runtime.py` 不允许调用任何平台币种发现函数。
- 前端平台 option 类型和 normalizer 不允许包含 `listingCurrency`。
- 退役字符串 `site_rule`、`campaign_rule`、`account_api_required`、`contract_currency` 在项目代码中不存在。

执行：

```bash
.venv/bin/python -m pytest tests -q
cd front && pnpm types:generate && pnpm types:check
cd front && pnpm lint:check && pnpm test:run && pnpm build
```

## 17. 验收标准

- [x] 授权页每个平台都展示发布货币状态、当前值、来源和验证时间。
- [x] Yandex 授权完成后自动保存远端 Business currency。
- [x] Ozon 授权完成后自动保存 `company.currency`。
- [x] Mercado Libre 能获取时自动保存；返回多值时必须选择；无法获取时保持空白并要求人工填写。
- [x] 任一平台币种未 ready 时，核价和发布都被确定性阻断。
- [x] 核价、预检和 payload 使用同一店铺配置币种及指纹。
- [x] 店铺币种或店铺身份变化后，所有旧核价失效。
- [x] 注册表和前端 site option 中不再存在 `listing_currency` 或 `market_currency`。
- [x] `contract_currency` 及旧 mode/source 已从代码、持久化数据、fixture 和文档中删除。
- [x] 不存在国家币种、市场币种、草稿历史币种或代码常量 fallback。
- [x] 不存在 feature flag、双路径、legacy endpoint 或仅为旧行为保留的测试。

## 18. 实施记录（2026-08-23）

按 Phase 1–5 完成，关键落点如下：

- **契约与状态机**：`erp_web/schemas/currency.py` 定义 `CurrencyMode` / `CurrencyStatus` /
  `StoreListingCurrency` / `StoreListingCurrencyDiscovery` / `StorePublishContext`。
  `erp_web/services/listing_currency_service.py` 重写为无平台分支的纯状态机服务
  （发现归一化、人工选择、ISO 4217 校验、指纹计算），不导入注册表/平台 HTTP/runtime。
- **远端发现与持久化**：三个平台 tester 在凭据校验成功后返回统一发现结果，由共享
  状态机写入 `store_auth.auth_detail_json`；新增 `erp_web/runtime_units/mercadolibre_auth.py`
  统一 `/users/me` 身份同步与站点币种发现；`/api/test-store-auth` 响应分离 `ok` 与
  `publish_ready`，并返回 `currency_configuration` / 最新 `storeConfig`；新增受控接口
  `/api/store-auth/currency`；`/api/save-settings` 只接受注册表凭据与静态字段，派生
  币种字段一律剥离。
- **授权页**：`AuthSettingsPanel.vue` 每平台新增统一“发布货币”区块（单值只读 / 多值
  下拉 / 人工填写 / 失败重试 / preview 标注）；删除“仅 Yandex 保存后自动复测”分支与
  `07D 用户信息` 写授权入口。
- **核价与发布**：核价只读 ready 店铺配置并注入 `currency_fingerprint`，删除 Ozon 现场
  补取；发布预检重新加载店铺配置，交叉校验草稿快照、币种指纹、Money 币种与核价指纹，
  变化即 `STORE_CURRENCY_CHANGED` / `PRICING_STALE`。
- **迁移与退役**：新增幂等一次性迁移 `erp_web/stores/store_currency_migration.py`
  （ConfigStore 初始化时执行）；物理删除注册表与草稿链路中的 `market_currency` /
  `listing_currency` 静态值、`contract_currency` 与旧 mode/source；`tests/architecture/
  test_platform_contracts.py` 增加守卫，禁止上述静态币种来源复活。

验证：后端 `pytest tests` 全绿（1238 通过），前端 `types:check` / `lint:check` /
`test:run`（283 通过）/ `build` 全绿。

## 19. 官方能力参考

- [Yandex Cabinet Settings：返回 `settings.currency`](https://yandex.com/dev/market/partner-api/doc/en/reference/businesses/getBusinessSettings)
- [Ozon Seller Info：`/v1/seller/info`](https://docs.ozon.ru/api/seller/#operation/SellerAPI_SellerInfo)
- [Ozon Product Import：`currency_code`](https://docs.ozon.ru/api/seller/#operation/ProductAPI_ImportProductsV3)
- [Mercado Libre 站点与默认币种](https://developers.mercadolibre.com.mx/en_us/categories-and-listings)
- [Mercado Libre 类目 `settings.currencies`](https://developers.mercadolivre.com.br/en_us/category-prediction-resource/categories-and-attributes)
- [Mercado Libre Global Listing](https://global-selling.mercadolibre.com/devsite/en_us/create-application/global-listing)
