# Yandex Market 授权、商品编辑与发布闭环实施方案

> 状态：待实施
>
> 编写日期：2026-08-16
>
> 目标平台：Yandex Market Seller API
>
> 目标站点：俄罗斯（`yandex:global`，刊登币种 `RUB`）

## 1. 结论

当前项目没有完成 Yandex Market 的商品编辑发布闭环。

已经具备的部分只有：

- Yandex 通用草稿数据形状；
- 标题、描述、SKU、库存、图片、重量尺寸和核价结果的确定性预检雏形；
- 目标站点、俄语和 RUB 的基础注册信息；
- API Token 的保存和脱敏展示。

尚未具备的关键能力包括：

- Campaign ID 输入、API-Key 在线校验及权限范围校验；
- `businessId`、店铺名称、店铺模型和 API 可用状态解析；
- Yandex 实时类目树、类目属性和枚举值接入；
- Yandex 商品 payload 预览与平台级字段校验；
- 新增商品和按 `offerId` 更新商品；
- 价格、上架条件和库存写入；
- 平台异步处理状态轮询、失败字段映射和发布终态回写；
- 已发布 ERP 草稿再次编辑并更新同一 Yandex 商品的稳定身份约束。

因此，本次不应只修授权页。实施范围应覆盖：

```text
填写 API-Key + Campaign ID
  → 未保存凭证在线测试
  → 保存授权与已验证店铺元数据
  → 编辑本地 Yandex 草稿
  → 读取真实类目与类目属性
  → 选择图片、核价、填写库存和包装信息
  → 发布预检与 payload 人工确认
  → 创建或更新商品
  → 写入上架条件、价格和库存
  → 轮询平台状态
  → 回写成功、处理中或结构化失败
```

## 2. 已确定的产品与技术决策

### 2.1 授权方式

新实现只使用 Yandex 当前推荐的 API-Key，不新增 OAuth 或 Bearer 兼容分支。

```http
Api-Key: <token>
```

请求主机固定为：

```text
https://api.partner.market.yandex.ru
```

Yandex 官方已经将 OAuth 标记为过时方案，API-Key 由卖家后台创建并可配置细粒度权限。参考：

- [Yandex Market 授权说明](https://yandex.ru/dev/market/partner-api/doc/en/concepts/authorization)
- [API-Key 创建和请求头格式](https://yandex.ru/dev/market/partner-api/doc/en/concepts/api-key)

现有配置键继续使用 `api_token`，但界面文案改为“API-Key Token”。不再增加并行的 `api_key` 配置键，避免形成两套 Yandex 凭据来源。`api_token` 在本项目中只是存储字段名，请求语义始终是 `Api-Key`。

### 2.2 店铺标识

授权页由用户直接填写：

- API-Key Token；
- Campaign ID。

不做“先读取店铺列表再选择 ID”的额外步骤。后端使用用户输入的 Campaign ID 请求：

```http
GET /v2/campaigns/{campaignId}
```

成功后解析并保存：

- `campaign.id` → `campaign_id`；
- `campaign.business.id` → `business_id`；
- `campaign.business.name` → `business_name`；
- `campaign.domain` → `shop_name`；
- `campaign.placementType` → `placement_type`；
- `campaign.apiAvailability` → `api_availability`。

只有 `apiAvailability == AVAILABLE` 才算店铺可用。HTTP 200 但状态为 `DISABLED_*` 或 `MANUALLY_DISABLED` 时必须测试失败，并给出恢复动作。参考：

- [获取店铺信息](https://yandex.ru/dev/market/partner-api/doc/en/reference/campaigns/getCampaign)
- [Yandex API 可用状态](https://yandex.ru/dev/market/partner-api/doc/en/concepts/api-access)

### 2.3 凭证权限

授权测试不只验证 Campaign ID 可读，还要调用：

```http
POST /v2/auth/token
```

读取 `result.apiKey.name` 和 `result.apiKey.authScopes`。完整商品编辑发布至少需要：

- `OFFERS_AND_CARDS_MANAGEMENT`，或 `ALL_METHODS`；
- `PRICING`，或 `ALL_METHODS`。

只读 scope 不能通过“可发布授权”测试。权限不足时返回缺失 scope 和对应中文操作提示。参考：[获取 API-Key 信息和权限范围](https://yandex.ru/dev/market/partner-api/doc/ru/reference/auth/getAuthTokenInfo)。

### 2.4 创建与编辑使用同一条写入路径

首次创建和后续编辑都使用：

```http
POST /v2/businesses/{businessId}/offer-mappings/update
```

本地 `draft.sku` 映射为 Yandex `offerId`。`offerId` 是远端商品稳定身份：

- 首次发布前可以编辑；
- 首次发布成功后锁定，不允许把同一 ERP 草稿改成另一个 `offerId`；
- 后续编辑只发送有变化的字段，但始终携带同一个 `offerId`；
- 发布成功后同时写入 `last_publish_task.offer_id` 和 `external_id`。

Yandex 官方说明 `offerId` 必须唯一，已经使用过的 SKU 不能释放后再给另一件商品使用。参考：[添加商品和修改商品信息](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/updateOfferMappings)。

### 2.5 发布成功必须有平台回读证据

`offer-mappings/update` 返回 HTTP 200 只代表请求被接收，不代表商品已经在售。发布总线不能据此写成成功。

提交后使用以下接口回读：

```http
POST /v2/businesses/{businessId}/offer-mappings
POST /v2/campaigns/{campaignId}/offers
```

终态原则：

- `PUBLISHED`：发布成功；
- `CHECKING`、`CREATING_CARD`：继续轮询；
- `NO_STOCKS`：库存步骤尚未完成或库存为零，不得假成功；
- `REJECTED_BY_MARKET`、`DISABLED_AUTOMATICALLY`、`NO_CARD`：结构化失败；
- 平台仍在处理中：返回 `publish_pending_confirmation`；现有 PublishingBus 在每次 poll 前持久化
  pending result、在当前 worker 内继续轮询，并可在进程重启后从该 result 恢复，不把 pending 当成功。

状态来源参考：

- [读取业务商品、卡片状态和店铺状态](https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/getOfferMappings)
- [读取指定店铺中的商品](https://yandex.ru/dev/market/partner-api/doc/en/reference/offers/getCampaignOffers)

### 2.6 发布确认继续使用唯一公共 owner

Yandex 不实现第二套人工确认或 digest 校验。当前唯一入口保持为：

```text
erp_web/runtime_units/publish_capabilities.py
  → 共享发布评估：预检、最终 payload、店铺 binding、validation digest
  → validate_product_publish()
  → request_product_publish()
      → PublishingBus.enqueue(..., approved_publications=...)
```

`erp_web/runtime_units/publish_confirmation.py` 继续拥有 canonical digest 和店铺 binding 算法，
PublishingBus 继续负责在 worker 外发前复核冻结 payload、digest 和当前店铺身份。
`YandexPublishingAdapter` 只处理平台 payload 与远端 I/O，不读取、生成或校验确认 digest。

全局任务已经通过 `product.publish.validate` / `product.publish.request` 使用这条链路。人工页面也要
复用同一个共享发布评估结果：预览接口返回 payload、summary 和 `validation_digest`；用户确认后，
通用 enqueue facade 用服务端生成的 `manual:<uuid>`、服务端确认时间和客户端回传的 digest 构造
`ProductPublishRequest`，再调用 `request_product_publish()`。不得把当前“单独预览 + 无 digest 直接入队”
当作可信人工确认。

### 2.7 动态店铺能力和稳定发布身份

配置存储按当前 `ConfigStore` 边界拆分：

- `campaign_id`：用户输入的非敏感静态配置，保存在 store config 静态部分；
- `api_token`：秘密凭据，保存在 SQLite `store_auth.credentials_json`；
- `business_id`、店铺名称、投放模型、API 可用状态、token 名称、scopes、价格模式、仓库与库存路径：
  在线派生的动态授权/店铺能力，保存在 SQLite `store_auth.auth_detail_json`；
- 静态 `store_config.json` 不保存非空动态验证结果。

`erp_web/stores/config_store.py` 必须扩展 DB-owned auth detail 白名单，覆盖上述派生字段；
`config_http.py` 只补静态 `campaign_id` 默认值，不把动态元数据定义为文件 owner。

发布确认的 Yandex 店铺身份固定绑定 `business_id + campaign_id`。在
`MarketplaceSpec` 增加声明式的 store binding 字段组，`resolve_publish_store_binding()` 从注册表读取并
要求 Yandex 两个字段同时存在；不能用可变化的 `shop_name`、脱敏 token 或单独 `business_id`
代替稳定 campaign 身份。

## 3. 当前代码能力与缺口

| 环节 | 当前状态 | 缺口 |
|---|---|---|
| 店铺授权 UI | 仅有 `api_token` 密码框 | 缺 Campaign ID、测试按钮、权限结果和店铺元数据 |
| 在线授权 | Yandex `test_auth=""` | 无 HTTP 客户端、无校验器 |
| 授权状态 | `api_token` 未计入凭证存在判断 | 已保存 token 仍显示“未配置” |
| 通用草稿编辑 | 已有标题、描述、卖点、站点和语言 | 品牌、型号、SKU、库存等分散在其他步骤，尚无 Yandex 字段语义说明 |
| 类目与属性 UI | 通用组件已经存在 | Yandex 无 `CategoryProvider`，不能加载真实类目和属性 |
| 图片池 | 已有通用图片选择与平台图片引用 | 没有检查 Yandex 可访问 URL 和图片规则 |
| 核价 | 已有 `yandex:global` 的 RUB 核价目标 | 无 Yandex 价格 payload 和价格策略选择 |
| 发布预检 | 已有 `validate_yandex_draft` 雏形 | 未校验真实属性 schema、API scope、图片 URL、稳定 `offerId` 和上架模型 |
| payload 预览 | 无 | Yandex 不具备 `CAP_PREVIEW_PAYLOAD` |
| 真实发布 | 无 | Yandex 不具备 `CAP_PUBLISH`，无发布适配器 |
| 发布终态 | 通用 PublishingBus 已支持待确认轮询 | 无 Yandex poller 和状态映射 |
| 发布结果回写 | 通用终态回写已存在 | Yandex 还没有可供回写的远端身份和平台结果 |

涉及的现有主要入口：

- `front/src/components/auth/AuthSettingsPanel.vue`
- `front/src/components/domain/DraftEditorPanel.vue`
- `front/src/components/domain/CategoryAttributesPanel.vue`
- `front/src/api/workflow/publishing.ts`
- `front/src/stores/workflow/actions/publishing.ts`
- `erp_web/marketplace_registry.py`
- `erp_web/runtime_units/store_credentials.py`
- `erp_web/runtime_units/category_providers.py`
- `erp_web/runtime_units/publish_validation.py`
- `erp_web/runtime_units/publish_adapter.py`
- `erp_web/runtime_units/publish_workflows.py`
- `erp_web/runtime_units/publishing_bus_core.py`
- `erp_web/runtime_units/publish_bus.py`

## 4. 目标用户流程

### 4.1 授权设置

1. 用户选择 Yandex。
2. 输入 API-Key Token 和 Campaign ID。
3. 点击“测试授权”。
4. 前端通过现有 preview 语义把未保存表单值提交给 `/api/test-store-auth`。
5. 后端校验 token、Campaign ID、API 可用状态和发布所需 scopes，但不保存未确认的表单值。
6. 页面显示店铺名、Business ID、投放模型、API 状态、token 名称和权限检查结果。
7. 用户点击“保存当前平台授权”。
8. 保存成功后，对已保存配置执行一次在线校验并持久化可信测试结果，避免状态退回“已保存，未测试”。

第 8 步由前端在保存成功后自动调用现有 `testAuth('yandex')` 完成，不让客户端直接提交伪造的 `auth_status`、`business_id` 或 scopes。
在线校验器只更新当前服务端配置对象，最终由 `ConfigStore.save_store_config()` 把 token 路由到
`store_auth.credentials_json`、把派生元数据路由到 `store_auth.auth_detail_json`。preview 测试使用
合并后的内存副本，不得持久化 token、Campaign ID 或任何派生能力。

保存边界必须比较服务端真实 `api_token` 与 `campaign_id` 是否变化。任一身份字段变化时，要在同一次
保存中清除旧 `business_id`、scopes、价格/库存能力和旧成功态，并将状态置为“已保存，未测试”；
随后前端自动测试才能产生新的可信成功态。即使保存后、自动测试前进程退出，也不能让新 Campaign ID
继承旧 campaign 的授权证明。

### 4.2 商品编辑

本期的“编辑”定义为：

- ERP 本地 Yandex 草稿首次发布；
- 已由 ERP 发布成功的 Yandex 草稿再次打开，修改内容并更新同一个远端 `offerId`。

本期不包含“扫描 Yandex 全量存量商品并自动导入为 ERP 草稿”。该能力可在发布闭环稳定后单独实施，不能成为首次发布的前置条件。

编辑页继续复用现有通用页面，但对 Yandex 明确展示并校验：

- `title` → `name`；
- `description` → `description`；
- `brand` → `vendor`；
- `sku` → `offerId`；
- `category_id` → `marketCategoryId`；
- `attributes` → `parameterValues`；
- 草稿图片引用 → `pictures`；
- 包装重量尺寸 → `weightDimensions`；
- 核价结果 → `basicPrice` 或单独价格更新请求；
- 店铺上架条件 → `/offers/update`；
- 库存 → 对应库存更新接口。

首次新增商品时，官方要求至少具备 `offerId`、`name`、`marketCategoryId`、`pictures`、`vendor` 和 `description`。平台属性还要按真实类目定义校验类型、枚举值、单位和多值数量。

### 4.3 类目与属性

新增 Yandex CategoryProvider，接入：

```http
POST /v2/categories/tree
POST /v2/category/{categoryId}/parameters?businessId={businessId}
```

实现约束：

- 只允许选择叶子类目；
- 类目树按语言和更新时间缓存，避免触发每小时限额；
- 授权测试和用户主动刷新必须绕过缓存；
- Yandex Provider 在平台边界把 `parameterId` 转换成通用属性 `id`，把类型、必填、多值和单位约束
  转换成共享 `CategoryAttributeDefinition`；Yandex wire 字段只允许保留在定义的 `raw` 中；
- `ENUM` 属性转换为 `value_mode=strict_enum`，`valueId` 规范化为字符串
  `dictionary_value_id`，草稿仍使用共享 `{values: [{dictionary_value_id, value}]}` shape；
- 前端共享 `dictionaryValueId` 类型统一为字符串；读取旧数字 ID 时在 API 边界规范化为字符串，
  新写入只使用规范字符串；
- 若属性需要选择单位，扩展共享 schema 的 `unit_options/default_unit` 和共享属性值 shape，
  同步更新后端有效性判断、生成类型和通用面板；不得给 `CategoryAttributesPanel.vue` 增加
  Yandex 专用 `valueId` 或单位分支；
- 切换类目时清理不属于新类目的属性值；
- `businessId` 从已验证授权元数据读取，不允许用户手填。

官方接口：

- [Yandex 类目树](https://yandex.ru/dev/market/partner-api/doc/ru/reference/categories/getCategoriesTree)
- [类目商品属性](https://yandex.ru/dev/market/partner-api/doc/ru/reference/content/getCategoryContentParameters)

### 4.4 图片

Yandex 商品写入的 `pictures` 是 URL 列表，因此 Yandex 适配器要复用现有 `image_delivery`，在构造 payload 前确保每张选中图片已经转换为平台可访问的 HTTPS URL。

预检必须阻止：

- 本地文件路径；
- `data:` URL；
- 过期签名 URL；
- 空 URL；
- 图片投递失败但仍残留在草稿中的引用。

`prepare_product()` 负责投递图片，`build_payload()` 只消费已准备好的远端 URL，不得在 payload builder 中执行网络写入。

### 4.5 价格

价格写入不能固定选择 Campaign 级或 Business 级接口。授权成功后读取：

```http
POST /v2/businesses/{businessId}/settings
```

根据 `onlyDefaultPrice` 决定：

- `true`：使用 `POST /v2/businesses/{businessId}/offer-prices/updates`；
- `false`：使用 `POST /v2/campaigns/{campaignId}/offer-prices/updates`。

首次商品写入可携带 `basicPrice`，但发布编排仍要把价格同步作为独立、可观测步骤，便于重试和定位价格隔离/锁定错误。

发布确认前还要查询价格隔离区。若价格进入 quarantine，不得自动确认异常价格；任务返回需要人工检查的失败/阻塞结果。参考：[Yandex 修改价格流程](https://yandex.ru/dev/market/partner-api/doc/ru/step-by-step/assortment-change-prices)。

### 4.6 上架条件与库存

商品目录写入后，调用：

```http
POST /v2/campaigns/{campaignId}/offers/update
```

写入最小订单量、销售倍数、VAT，以及 DBS 模型需要的配送参数。

库存根据店铺模型和仓库配置选择路径：

- FBS、Express、DBS：由 ERP 推送库存；
- FBY：库存由 Yandex 履约侧管理，本流程不调用 FBS 库存接口；
- 有仓库组：`PUT /v2/campaigns/{campaignId}/offers/stocks`；
- 无仓库组：使用 `POST /v3/businesses/{businessId}/offers/stocks/update`。

仓库配置通过 `POST /v2/businesses/{businessId}/warehouses` 探测并保存为已验证店铺能力，不在代码中按猜测写死。

Campaign 级库存接口及适用条件参考：[传递库存](https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/updateStocks)。

### 4.7 发布编排顺序

发布确认与 Yandex 远端状态机按下列顺序执行：

1. 共享发布评估解析已保存且测试成功的店铺授权；
2. `prepare_product()` 投递图片并生成可访问 URL；
3. 共享类目规则校验真实 schema 和所有必填属性；
4. Yandex builder 构造一个确定性的复合 payload，按“目录商品 / 上架条件 / 价格 / 库存”分组；
5. `publish_capabilities` 生成 summary、店铺 binding 和 `validation_digest`，供页面预览与人工确认；
6. 用户确认后，`request_product_publish()` 重新执行共享评估、常量时间比较 digest，并把冻结 payload
   写入 PublishingBus 的 `approved_publications`；
7. worker 复核当前 `business_id + campaign_id` binding 和冻结 payload 后，调用
   `YandexPublishingAdapter.publish_payload()`；
8. Yandex 状态机执行 `offer-mappings/update`，立即返回带 checkpoint 的
   `publish_pending_confirmation`；
9. PublishingBus 持久化 checkpoint 后，poller 执行 `/offers/update`，再次返回 pending checkpoint；
10. 下一次 poll 执行价格写入并保存 checkpoint；
11. 下一次 poll 按店铺模型写入库存并保存 checkpoint；
12. 后续 poll 只回读 Business 商品映射和 Campaign 商品状态，不重新执行已完成写步骤；
13. 成功时回写 `offer_id`、`external_id`、远端状态和检查时间；失败时回写平台错误码、字段错误、
    原始响应 artifact 和下一步动作。

每个远端写步骤一次只处理一个确定性 mutation；只有该调用返回后 PublishingBus 才能持久化其
checkpoint，因此不得在单次 `publish_yandex_payload()` 内连续执行步骤 8 至 11。checkpoint 至少保存
`phase`、已完成步骤、各请求的幂等事实/远端证据、最后响应摘要和下一次允许轮询时间，且绝不包含凭据。

当前 PublishingBus 会在 worker 内持续轮询 pending 结果，并不具备“单次 worker 等待超时后释放、
未来再调度”的机制。本期按现有模式使用有界退避轮询和进程重启恢复，不在文档中宣称 worker 会释放；
若后续需要长时间审核任务释放线程，应作为通用 PublishingBus 延迟调度能力单独设计，不能只为
Yandex 添加旁路。

## 5. 后端实施设计

### 5.1 新增 Yandex HTTP 边界

新增：

```text
erp_web/marketplaces/yandex_http.py
```

职责：

- 固定官方主机和 `Api-Key` 请求头；
- 统一 JSON 编解码、超时和脱敏错误；
- 同时处理 HTTP 错误与 HTTP 200 中的 `status: ERROR`；
- 解析 Yandex `errors[]` 和 `warnings[]`；
- 暴露无业务状态写入的低层请求函数。

不要把新代码继续堆入 `marketplaces/config_http.py`。Yandex 商品、价格和库存请求会快速增长，单独 HTTP 边界更符合当前 focused module 约束。

首批函数：

```text
request_yandex_json(...)
fetch_yandex_token_info(...)
fetch_yandex_campaign(...)
fetch_yandex_business_settings(...)
fetch_yandex_warehouses(...)
fetch_yandex_category_tree(...)
fetch_yandex_category_parameters(...)
update_yandex_offer_mapping(...)
update_yandex_campaign_offer(...)
update_yandex_price(...)
update_yandex_stock(...)
fetch_yandex_offer_mapping(...)
fetch_yandex_campaign_offer(...)
```

### 5.2 新增共享 schema

新增：

```text
erp_web/schemas/yandex.py
```

定义并校验：

- `YandexCampaignInfo`；
- `YandexTokenInfo`；
- `YandexCategoryRecord`；
- `YandexCategoryParameter`；
- `YandexOfferMappingPayload`；
- `YandexPublishCheckpoint`；
- `YandexPublishResult`。

`YandexPublishCheckpoint` 必须是可持久化、无凭据的显式状态机 shape，至少包含当前 phase、
已完成 mutation、远端身份/任务证据、最后响应摘要、重试次数和下一次允许轮询时间。

`schemas/yandex.py` 只定义 Yandex wire/状态机 shape。平台响应在 HTTP 边界转换为这些 shape；
进入草稿与通用类目 UI 前，再由 Provider 转换为 `erp_web/schemas/category.py` 和
`erp_web/schemas/product.py` 的共享 shape。不得把 `parameterId/valueId` 作为新的通用草稿字段。

### 5.3 授权校验器

在 `erp_web/runtime_units/store_credentials.py` 新增：

```text
_test_yandex_auth(config, scope)
```

行为：

1. 缺 `api_token` 或 `campaign_id` 立即报错；
2. 调 `/v2/auth/token`，验证 API-Key 和 scopes；
3. 调 `/v2/campaigns/{campaignId}`，验证店铺归属和 `apiAvailability`；
4. 读取 Business settings 与仓库能力，解析价格和库存路径；
5. 把 `business_id`、店铺信息、token 名称、scopes、价格/库存能力及验证时间写入当前配置对象；
6. 调用统一 `_store_auth_result_fields(...)` 生成授权状态；
7. 不在日志、异常和响应中回显 token。

tester 不自行判断或写数据库。`test_store_auth()` 在非 preview 情况下调用
`ConfigStore.save_store_config()` 完成持久化；preview 使用配置副本，因此上述 mutation 不会落盘。

在 `erp_web/marketplace_registry.py` 注册：

```python
test_auth="erp_web.runtime_units.store_credentials:_test_yandex_auth"
```

同时修复：

- 让 `summarize_store_auth(platform, store)` 根据 `MarketplaceSpec.credential_fields` 判断是否存在凭证，
  不再向 `_auth_status_label()` 的硬编码凭证列表追加 `api_token`；
- `config_http.py` 的 Yandex 静态默认配置只补齐 `campaign_id`；
- `ConfigStore._STORE_AUTH_DETAIL_FIELDS` 补齐 `business_id/business_name/placement_type/api_availability`、
  `api_key_name/auth_scopes/only_default_price/stock_update_mode/warehouse_ids/capabilities_verified_at`；
- 保存 facade 在真实 token 或 Campaign ID 改变时原子清除旧 Yandex auth detail 和成功状态，
  不能依赖前端后续测试来弥补身份切换窗口；
- `validate_yandex_draft()` 将“已保存，未测试”视为发布阻塞；
- Yandex HTTP 420 映射为“被限流”，不能只识别 429。

### 5.4 类目适配器

新增：

```text
erp_web/runtime_units/yandex_category_api.py
```

并在 `category_providers.py` 增加 `YandexCategoryProvider`。Provider 负责平台 shape 到通用
CategoryProvider shape 的机械转换，缓存和 API 访问留在 `yandex_category_api.py`。

转换规则必须复用共享类目 schema：

- `parameterId` → `id`；
- ENUM → `value_mode=strict_enum`，并提供稳定 `dictionary_id`；
- `valueId` → 字符串 `dictionary_value_id`；
- repeatable/max values → `is_collection/max_value_count`；
- 单位约束 → 共享 `unit/unit_options/default_unit`。

必填属性是否解决仍由 `product_model/category_model.py::unresolved_required_category_attributes()`
唯一裁定，Yandex Provider 和发布模块不得复制该规则。

注册表为 Yandex 增加：

```text
CAP_CATEGORY_SEARCH
CAP_CATEGORY_ATTRIBUTES
```

由于官方接口提供树而不是文本搜索，通用“搜索”使用本地缓存树上的规范化匹配，不为每次输入发远端请求。

### 5.5 发布适配器

新增：

```text
erp_web/runtime_units/publish_yandex.py
```

职责：

- `build_yandex_publish_payload()`；
- `validate_yandex_publish_payload()`；
- `publish_yandex_payload()`；
- `poll_yandex_publish_status()`；
- `map_yandex_publish_error()`；
- 把已经通过共享校验的规范属性编译为 `parameterValues`；
- 目录商品、上架条件、价格、库存和终态回读的显式状态机 transition；
- 每个 transition 完成后返回 `publish_pending_confirmation` 和下一阶段 checkpoint。

`publish_yandex_payload()` 只执行第一个尚未完成的远端 mutation；
`poll_yandex_publish_status()` 根据已持久化 checkpoint 执行下一个 mutation 或只读状态确认。
两者都不得直接访问 PublishingBus 数据库、确认 digest 或 facade。

在 `publish_adapter.py` 新增 `YandexPublishingAdapter`，注册到 `_PUBLISHERS`，并让 Yandex 获得：

```text
CAP_PREVIEW_PAYLOAD
CAP_PUBLISH
```

不新增 Yandex 专用 HTTP 端点。通用 route、facade 和 PublishingBus 保持平台无关，但人工页面的
通用 preview/enqueue 必须改为调用共享发布评估与 `request_product_publish()`，不能继续走无
`approved_publications` 的直接 enqueue。

### 5.6 发布确认和 PublishingBus 的最小通用修改

本次需要以下平台无关修改，不能藏进 Yandex adapter：

1. `MarketplaceSpec` 增加声明式 store binding 字段组；Yandex 要求同组
   `business_id + campaign_id` 全部存在，`resolve_publish_store_binding()` 只消费注册表声明；
2. 抽出可同时供 `validate_product_publish()`、人工 payload preview 和
   `request_product_publish()` 使用的共享发布评估函数，确保三条入口使用同一次 payload/digest 算法；
3. 通用 preview response 返回 sanitized payload、summary 和 `validation_digest`；
4. 通用 enqueue request 接受显式确认和 digest，服务端生成 `manual:<uuid>` 与确认时间，随后调用
   `request_product_publish()`；客户端不能提交可信 idempotency key 或伪造 `confirmed_at`；
5. 在平台发布协议中增加类型化 `PublishAdapterError(code, message, retryable, details)`，
   PublishingBus 依据 `retryable` 决定是否重试。未分类异常默认不可重试，避免 4xx 被总线重复发送；
6. pending checkpoint 继续利用现有 `result` 持久化与恢复机制，不增加 Yandex 专用 job 表或回调。

这些修改应同时覆盖现有平台测试，确保共享确认和错误契约仍是唯一入口。

### 5.7 发布结果和远端身份

Yandex 成功结果至少包含：

```json
{
  "ok": true,
  "status": "real_publish_success",
  "offer_id": "ERP-SKU-001",
  "external_id": "ERP-SKU-001",
  "campaign_id": "123456",
  "business_id": "987654",
  "campaign_status": "PUBLISHED",
  "card_status": "HAS_CARD_CAN_UPDATE",
  "checked_at": "..."
}
```

`remote_publish_identity()` 已能提取 `offer_id` 和 `external_id`，现有终态回写可以复用。
SKU 继续由 `product_model/platform_sku.py` 的通用规则生成/保留；服务端预检必须比较当前
`draft.sku` 与历史 `last_publish_task.offer_id`，不一致时阻断。前端锁定输入只提供交互提示，
不能充当身份约束。

## 6. 前端实施设计

### 6.1 授权页

修改 `AuthSettingsPanel.vue`：

- `form` 增加 `yandexCampaignId`；
- `fillFromProps()` 回填 `campaign_id`；
- `storePayload()` 提交 `api_token` 和 `campaign_id`；
- 增加 `testYandexAuth()`，使用 `selectedStorePayload()`；
- 增加 Campaign ID 普通文本输入框；
- Token placeholder 改为 `Yandex API-Key Token`；
- 增加“测试授权”按钮；
- 删除“在线授权校验将在接入对应 API 后启用”；
- 展示 Business ID、店铺名、投放模型、API 可用状态和 scope 检查；
- 保存成功后自动对已保存 Yandex 配置执行一次测试，持久化可信状态。

### 6.2 草稿编辑与发布页

通用页面继续复用，不新增一套 Yandex 专属编辑器。需要补充：

- Yandex 目标下显示 SKU/offerId 的稳定身份提示；
- 已发布草稿锁定 SKU；
- 类目属性面板只消费共享 `CategoryAttributeDefinition` 和规范属性值；如需单位选择，扩展通用控件，
  不判断 `platform === 'yandex'`，也不直接读取 `valueId`；
- 图片面板显示 Yandex URL 投递状态；
- 发布预览按“目录商品 / 上架条件 / 价格 / 库存”分组展示；
- `payloadPreview` 同时保存服务端返回的 summary 和 `validation_digest`；草稿、目标、店铺或 payload
  发生变化时立即清空旧确认；
- “确认并入队”必须回传当前 digest 与显式 confirm，后端重新评估后才创建带
  `approved_publications` 的 job；原来的无 digest 直接 enqueue 不再作为发布入口；
- 发布结果展示 `campaignStatus`、`cardStatus`、警告和字段错误；
- pending 状态持续显示“Yandex 审核中”，不能显示成功绿色状态。

## 7. 错误、重试和安全规则

### 7.1 错误映射

| 情况 | 系统状态 | 用户提示 |
|---|---|---|
| 401 | 权限不足/凭证无效 | 检查 API-Key 是否完整或已撤销 |
| 403 | 权限不足 | 为 token 增加商品管理或价格管理权限 |
| 404 Campaign | 配置错误 | 检查 Campaign ID 是否属于该 API-Key 对应柜台 |
| 420 | 被限流 | 等待后重试，并降低类目/状态轮询频率 |
| 423 | 资源锁定 | 等待 Yandex 完成当前价格或商品处理 |
| 5xx/网络超时 | 可重试失败 | 使用退避重试，不改变 offerId |
| `status: ERROR` | 平台业务失败 | 展示 `errors[].code/message` |
| 单商品 warning | 警告或失败 | 按平台语义映射到具体草稿字段 |

### 7.2 重试

- GET 和只读 POST：指数退避重试；
- `offer-mappings/update`、价格、上架条件和库存：依靠稳定 `offerId` 和 checkpoint 重试；
- Yandex HTTP 边界把错误转换为类型化 `PublishAdapterError`，保留平台 code、脱敏 message、
  `retryable` 和字段错误；
- 401、403、404 及确定性 4xx 业务错误设置 `retryable=False`，PublishingBus 一次失败后终止；
- 420、423、5xx、连接错误和超时设置 `retryable=True`，PublishingBus 才允许退避重试；
- 未分类异常默认 `retryable=False`，不能沿用当前“除确认错误外全部重试”的粗粒度判断；
- 每次请求设置明确 timeout，不能无限阻塞 worker；
- 状态轮询使用 checkpoint 中的 `next_poll_at` 做有界退避并限制最大频率；本期 worker 会持续占用
  直到终态或进程退出，不宣称具备延迟队列调度。

### 7.3 安全

- token 不进入 publish artifact、日志、异常文本、前端响应和测试快照；
- HTTP mock 测试必须检查请求头，但失败输出只能显示脱敏值；
- 前端不能直接写 `auth_status=测试成功`；
- `business_id` 只接受在线校验派生值；
- 发布时重新解析当前已保存凭证和 Campaign ID，不能信任草稿中复制的店铺身份。

## 8. 实施阶段

### 阶段 A：授权闭环

- Yandex HTTP 客户端；
- Campaign ID 输入；
- API-Key、scope、campaign 和 API 可用状态校验；
- 保存后自动持久化在线测试结果；
- `ConfigStore` 动态 auth detail 白名单与静态/秘密/派生数据分流；
- 注册表驱动的凭证存在判断；
- `business_id + campaign_id` 稳定 store binding。

验收：未保存表单可以测试且不落盘；错误 Campaign ID、错误 token、只读 scope 和禁用店铺都不能
通过；保存后状态为“测试成功”；静态 JSON 只含 Campaign ID，token 与在线派生能力只存在 SQLite。

### 阶段 B：实时类目与属性

- 类目树缓存和本地搜索；
- 叶子类目选择；
- 类目属性、枚举、单位和多值转换；
- Yandex CategoryProvider 注册；
- 共享类目 schema/前端生成类型的必要扩展；
- 草稿规范属性 shape 持久化和切换类目清理。

验收：用户可以在现有类目属性页完成一个真实 Yandex 类目的选择和必填属性填写；草稿和通用 UI
不出现 Yandex `parameterId/valueId` 专用字段或平台判断。

### 阶段 C：payload 预览和预检

- 图片公网投递；
- Yandex payload builder；
- 真实类目 schema 校验；
- 稳定 `offerId` 校验；
- 价格、上架条件、库存策略预览；
- 共享发布评估 owner；
- 通用 preview 返回 payload、summary 和 digest；
- 通用人工确认 enqueue 进入 `request_product_publish()` 并保存 `approved_publications`；
- Yandex 获得 `CAP_PREVIEW_PAYLOAD`。

验收：预览结果能解释每个本地字段如何映射到 Yandex 请求且不会执行 Yandex 远端写入；修改草稿、
目标或店铺后旧 digest 失效；未携带有效确认的人工 enqueue 不创建 job。

### 阶段 D：真实创建、编辑与终态确认

- 目录商品 create/update；
- 上架条件；
- Business/Campaign 级价格分流；
- placement/warehouse 级库存分流；
- 单 mutation transition、poller、checkpoint 和恢复执行；
- 通用 `PublishAdapterError.retryable` 契约；
- Yandex 获得 `CAP_PUBLISH`；
- 发布终态、远端身份和日志回写。

验收：同一草稿可以首次创建，再修改标题或描述并更新同一 `offerId`；在任一 mutation 后模拟重启，
恢复时都不重复已完成步骤；确定性 4xx 只尝试一次；只有回读到平台成功状态才显示发布成功。

### 阶段 E：存量商品导入（后续独立能力）

- 分页读取 Business 商品；
- 选择 Campaign 商品状态；
- 映射为 ERP 草稿并建立 `offerId` 绑定；
- 冲突检测和人工确认。

该阶段不阻塞本次 ERP 草稿编辑发布闭环。

## 9. 测试计划

### 9.1 后端单元测试

- Yandex HTTP 请求使用准确主机、方法、路径和 `Api-Key` header；
- 请求不得出现 `Authorization: OAuth` 或 `Bearer`；
- HTTP 200 + `status: ERROR` 正确失败；
- 401、403、404、420、423、5xx 映射正确；
- token 信息解析和 scope 缺失判断；
- campaign 信息解析和 `apiAvailability` 判断；
- Yandex binding 同时绑定 `business_id + campaign_id`，店铺展示名变化不改变 binding；
- Campaign ID 或 Business ID 变化会使旧确认失效；
- 类目树仅返回/选择叶子类目；
- `parameterId/valueId` 到通用属性 schema、字符串 dictionary ID、单位和多值的转换；
- 通用必填属性 owner 对 Yandex schema 生效，发布模块不复制判断；
- payload 字段和图片 URL 校验；
- `draft.sku` 到 `offerId` 映射；
- 已发布草稿修改 SKU 被阻止；
- price `onlyDefaultPrice` 分流；
- FBY 不推送 FBS 库存；
- pending、success、rejected 状态映射；
- 每个远端 mutation 返回后都生成可持久化 checkpoint；
- poller 恢复时不重复已完成子步骤；
- 401/403/404 与确定性业务 4xx 的 `retryable=False`；
- 420/423/5xx/timeout 的 `retryable=True`；
- 未分类异常默认不重试。

### 9.2 后端集成测试

沿用 `tests/test_erp_web_db_integration.py` 的 Ozon 模式，覆盖：

- preview 授权测试不保存未确认凭证；
- 保存后在线测试成功并写入 `business_id` 等派生字段；
- `campaign_id` 写静态配置，token 与动态派生能力只写 `store_auth`，静态 JSON 不出现非空派生字段；
- token 或 Campaign ID 改变时，在自动测试前旧成功态和派生能力已经被原子清除；
- 保存凭证测试失败覆盖陈旧成功态；
- `/api/publish-precheck` 返回 Yandex 结构化缺项；
- `/api/publish-payload-preview` 返回 payload、summary、digest 和 artifact；
- `/api/publish-bus/enqueue` 缺确认、digest 过期或店铺 binding 变化时不创建 job；
- 有效人工确认通过 `request_product_publish()` 创建包含 `approved_publications` 的 Yandex job；
- 在目录、上架条件、价格和库存各阶段保存 pending job 后重启，均从下一 transition 恢复；
- terminal result 写回正确 draft/target，不能串写其他草稿；
- 失败响应保留字段错误但不含 token。

### 9.3 架构测试

更新：

- `tests/architecture/test_platform_contracts.py`；
- `tests/test_ai_context_architecture.py`；
- `tests/test_platform_extensions.py`。

删除/改写“Yandex 不支持发布”的旧断言，改为验证：

- 注册表声明的 tester 可解析；
- CategoryProvider 与能力声明一致；
- PublishingAdapter 与 `CAP_PUBLISH` 一致；
- 无 Yandex 专用绕路 HTTP route；
- route 仍只依赖 facade；
- Yandex HTTP、payload、polling 集中在 focused module；
- Yandex adapter 不导入 `publish_confirmation`、`publish_capabilities`、facade 或 PublishingBus 持久化实现；
- 人工预览、全局任务校验和确认提交共享唯一发布评估/digest owner；
- 通用类目 UI 与共享 schema 不包含 Yandex 专用 `valueId` 分支；
- 动态 Yandex 授权元数据属于 ConfigStore DB-owned auth detail。

### 9.4 前端测试

- Campaign ID 输入和回填；
- 未保存 Token + Campaign ID 的 `testAuth` emit；
- Yandex 授权成功元数据展示；
- 保存后自动测试已保存配置；
- 类目和属性数据规范化，旧数字 dictionary ID 读取后转换为字符串；
- 通用属性面板处理单位/枚举，不出现 Yandex 专用字段判断；
- payload 分组预览、digest 保存与输入变化后失效；
- 未确认不能入队，确认后只提交当前 digest；
- 已发布草稿 SKU 锁定；
- pending、success、failed 发布状态显示。

### 9.5 回归命令

```bash
.venv/bin/python -m pytest tests -q
cd front && pnpm test --run
cd front && pnpm build
```

## 10. 文档与清理

实际代码落地时同步更新 `docs/ai-context-map.md`，记录新增的：

- `marketplaces/yandex_http.py`；
- `runtime_units/yandex_category_api.py`；
- `runtime_units/publish_yandex.py`；
- `schemas/yandex.py`。

同时更新现有公共 owner 的说明：

- `stores/config_store.py`：Yandex 动态 auth detail 持久化字段；
- `marketplace_registry.py` / `runtime_units/publish_confirmation.py`：声明式店铺 binding；
- `schemas/category.py` / `schemas/product.py`：字符串字典 ID 与必要的通用单位 shape；
- `runtime_units/publish_capabilities.py`：人工页面、全局任务和 worker 共享的发布评估/确认入口；
- `runtime_units/publishing_bus_core.py` / `marketplaces/publisher.py`：类型化 retryable 发布错误；
- `runtime_units/publish_workflows.py`：人工 preview 与确认 enqueue 进入共享 Capability。

完成替换后检索并清理：

- “Yandex 在线授权校验将在接入对应 API 后启用”；
- “Yandex发布未接入”；
- `test_auth=""`；
- 对 Yandex `CAP_PUBLISH` 为 false 的旧测试；
- OAuth、Bearer、`api.market.yandex.ru` 等错误或过时写法；
- 只检查 429、不检查 Yandex 420 的限流映射；
- 人工页面无 digest 直接调用 PublishingBus 的旧发布路径；
- `_auth_status_label()` 中与 `MarketplaceSpec.credential_fields` 重复的硬编码凭证判断；
- 通用类目/前端 shape 中的 Yandex `parameterId/valueId` 泄漏；
- Yandex adapter 内的确认 digest、必填属性裁定或 job 数据库写入。

不增加 feature flag、shadow 路径或假成功 fallback。Demo 阶段直接以当前官方 API-Key 方案替换未完成的 Yandex 占位实现，回滚依赖版本控制。

## 11. 完成定义

只有以下条件全部满足，Yandex 才算“已接入编辑发布流程”：

- 用户能输入 API-Key Token 和 Campaign ID，并在保存前测试；
- 保存后能得到可信的测试成功状态、Business ID 和店铺模型；
- Campaign ID、秘密 token 和在线派生店铺能力分别进入正确的静态/SQLite 存储边界；
- token 或 Campaign ID 改变后旧成功态立即失效，不能继承旧店铺能力；
- 发布确认稳定绑定 `business_id + campaign_id`，店铺展示名变化不改变 binding；
- 能读取真实 Yandex 叶子类目和属性；
- Yandex 类目参数转换为共享规范属性 shape，通用 UI 不理解平台 `valueId`；
- 能在现有草稿编辑、图片、核价和预检页面完成所有必填数据；
- 能预览 payload、summary 和 digest，并通过共享 `request_product_publish()` 完成人工确认；
- 未确认、旧 digest、店铺切换或 payload 变化都不能创建发布 job；
- 能首次创建商品；
- 能再次编辑并更新同一 `offerId`；
- 能正确写入上架条件、价格和适用模型的库存；
- 每个远端 mutation 后都持久化 checkpoint，worker 重启后从下一步骤继续；
- 确定性 4xx 不重试，只有类型化 `retryable=True` 的瞬时错误进入退避重试；
- 只有 Yandex 回读证据为成功时才把草稿标成 published；
- 平台错误能定位到字段并给出下一步动作；
- 日志、响应和 artifact 中不泄露 API-Key。
