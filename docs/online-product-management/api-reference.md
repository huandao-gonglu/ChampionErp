# Ozon、Yandex Market、Mercado Libre 在线商品管理 API 对接参考

> 整理日期：2026-09-19  
> 范围：同步在线商品、调价、改库存、编辑商品内容、停售与恢复销售。  
> 业务口径：Ozon Seller API；Yandex Market Partner API；Mercado Libre Global Selling / CBT 跨境自发货（Remote）。  
> 不包含：新建商品发布流程、订单、物流核价、促销管理、平台仓补货、永久删除商品。

**导航：** [核验范围](#verification) · [Ozon](#ozon) · [Yandex](#yandex) · [Mercado](#mercado) · [联调检查](#validation) · [官方来源](#sources) · [待补核验](#pending)

<a id="verification"></a>

## 0. 阅读说明与核验范围

这是一份面向开发的接口参考，不是三家平台的完整 OpenAPI，也不是已经通过真实店铺测试的 SDK。

| 标记 | 含义 | 本文适用范围 |
| --- | --- | --- |
| **A：官方正文已核对** | 本次读取到官方接口文档，核对了方法、路径与所列核心参数；仍需店铺联调 | Yandex 主体接口 |
| **B：版本已核对，参数需复核** | 官方公告可以确认相关接口版本或变更；完整 Schema 页面访问受限，以下请求为对接草案 | Ozon 主体接口 |
| **C：待官方正文复核** | 官方文档入口可以定位，但本次未读到完整正文，不能认定参数和适用条件已完成当前版本核验 | Mercado Global Selling 主体接口 |

**重要限制：Ozon 官方 Schema 页面本次出现重定向错误；Mercado 部分官方页面返回 403 或不可访问。因此，B/C 节中的参数、条件必填项和示例不得直接作为“已验证的生产契约”。尤其 Mercado，应先在已授权账号下核对商品模型，再启用写操作。**

示例中的店铺 ID、商品 ID、类目 ID、属性 ID、仓库 ID 和图片地址都是示意值，不属于你的真实账号。`example.com` 图片地址必须替换成平台能访问的真实 HTTPS 图片。

表格中“必填”描述本文请求的使用方式；对于 B/C 级内容，也属于待复核项。没有列出的可选促销、税务、合规参数不代表平台不支持，只是不属于本次范围。

### 0.1 五类能力总览

| 操作 | Ozon（B） | Yandex（A） | Mercado Global Selling（C） |
| --- | --- | --- | --- |
| 同步在线商品 | 商品列表 → 详情 → 价格/库存/属性 | 账号商品目录 + 店铺商品状态 + 内容/库存详情 | Global 用户商品搜索 → CBT 商品详情 → 站点刊登映射 |
| 调价 | `/v1/product/import/prices` | 账号基础价或店铺独立价接口 | `/global/items/{item_id}`；必须区分 `price` 与 `net_proceeds` 的适用场景 |
| 改库存 | `/v2/products/stocks`，指定卖家仓库 | 按是否存在仓库组选择 v2 / v3 接口 | `/global/items/{item_id}`；Remote 商品库存，变体需单独识别 |
| 编辑内容 | 属性、图片专用接口；名称等使用商品导入更新接口 | `/v2/businesses/{businessId}/offer-mappings/update` | `/global/items/{item_id}`；描述资源和新商品模型需单独核对 |
| 停售/恢复 | 库存归零/恢复；需要归档时调用 archive / unarchive | hidden-offers / hidden-offers/delete | `status=paused` / `status=active`，区分全局与单站点 |

各平台官方入口与已读取资料见文末“来源与复核入口”。

### 0.2 本地保存的共同标识

以下是**实现建议，不是平台请求参数**：

| 字段 | 用途 |
| --- | --- |
| `platform`、`account_id` | 区分平台和授权账号 |
| `seller_sku` | 卖家 SKU，按字符串保存，保留前导零 |
| `platform_product_id` | 平台内部商品 ID；不能与 SKU 混用 |
| `shop_id` / `site_id` | 店铺或站点 |
| `warehouse_id` | 库存归属仓库；平台不提供时不要凭空生成 |
| `variation_id` | Mercado 等平台的变体标识 |
| `raw_status`、`raw_sub_status` | 保留平台原始状态，避免统一状态丢失原因 |
| `currency`、`price_type` | 区分币种，以及售价/基础价/净收入等价格含义 |
| `remote_snapshot`、`last_synced_at` | 最近一次平台原始快照和同步时间 |

**“同步在线商品”应当同步已发布商品的当前记录，包括停售、缺货、审核中等状态，而不只是买家端当前在售的记录。** 暂停后不能删除本地关联，否则无法可靠恢复。归档记录应单独补拉并合并。

---

<a id="ozon"></a>

## 1. Ozon Seller API

**核验等级：B。** 接口版本及部分废弃字段依据官方公告核对；请求 Schema 需在正式接入前按 [O1] 复核。[O2][O3][O4][O5]

### 1.1 基础地址、鉴权与标识

```text
Base URL: https://api-seller.ozon.ru
```

```http
Client-Id: YOUR_CLIENT_ID
Api-Key: YOUR_API_KEY
Content-Type: application/json
```

| 标识 | 含义 | 使用原则 |
| --- | --- | --- |
| `offer_id` | 卖家自定义商品编码 | 示例优先使用此字段，必须与已发布商品一致 |
| `product_id` | Ozon 内部商品 ID | 归档等操作使用；不是展示 SKU |
| `sku` | Ozon SKU | 仅用于明确接受 `sku` 的接口，不能替代 `product_id` |
| `warehouse_id` | 卖家仓库 ID | 写库存时使用，不能填写承运商 ID 或揽收点 ID |
| `task_id` | 商品导入/更新任务 ID | 异步结果查询使用 |

请求中不同接口对 ID 的 JSON 类型可能不同。应用内部可用字符串保存大整数标识，但序列化时要遵守各接口 Schema；不要为了统一模型把所有平台 ID 都直接发送为字符串或数字。

**密钥管理提醒：** 官方公告指出，自 2026-09-03 起新生成的 Seller API Key 有效期为 3 个月。不要把 Key 当作永久有效配置；需要保存到期信息并支持轮换。[O2]

### 1.2 同步在线商品

#### 1.2.1 获取商品列表

```http
POST /v3/product/list
```

```json
{
  "filter": {
    "visibility": "ALL"
  },
  "last_id": "",
  "limit": 100
}
```

| 参数 | 位置 | 类型 | 必填/使用方式 | 说明 |
| --- | --- | --- | --- | --- |
| `filter` | Body | object | 按示例传入 | 查询条件 |
| `filter.visibility` | Body | string | 建议显式传入 | 常用 `ALL`；归档列表单独使用 `ARCHIVED`，完整枚举需复核 |
| `filter.offer_id` | Body | string[] | 否 | 定向查询卖家 SKU；不筛选时省略 |
| `filter.product_id` | Body | ID[] | 否 | 定向查询平台商品；元素类型以当前 Schema 为准 |
| `last_id` | Body | string | 分页时需要 | 首次空字符串，后续使用上次返回的游标 |
| `limit` | Body | integer | 是 | 示例每批 100，不把此值当作官方最大值 |

主要读取 `result.items[]` 中的 `offer_id`、`product_id`，以及返回的 `result.last_id`、`result.total` 等分页信息。具体响应层级在首次联调时断言校验。

分页策略：保存当前页 → 使用返回游标请求下一页 → 无下一页或空列表时停止。设置“游标不得重复”的保护，防止死循环。归档商品另外请求并按商品标识合并；不要假定 `ALL` 一定包含所有特殊状态。

#### 1.2.2 批量获取详情

```http
POST /v3/product/info/list
```

```json
{
  "offer_id": [
    "SKU-001",
    "SKU-002"
  ]
}
```

| 参数 | 类型 | 必填/使用方式 | 说明 |
| --- | --- | --- | --- |
| `offer_id` | string[] | 三组选一 | 按卖家 SKU 获取详情 |
| `product_id` | ID[] | 三组选一 | 按 Ozon 商品 ID 获取详情 |
| `sku` | ID[] | 三组选一 | 按 Ozon SKU 获取详情 |

建议每次只使用一种标识集合，避免混用。主要读取 `items[]` 中的商品名称、图片、分类信息、归档信息、审核/销售状态、价格字段和错误信息；不要只凭一个布尔字段判断是否能购买。

旧版 `/v2/product/list`、`/v2/product/info`、`/v2/product/info/list` 不作为新代码入口，官方已发布替代版本与停用公告。[O3]

#### 1.2.3 获取完整属性快照

```http
POST /v4/product/info/attributes
```

```json
{
  "filter": {
    "offer_id": [
      "SKU-001"
    ],
    "visibility": "ALL"
  },
  "last_id": "",
  "limit": 100,
  "sort_dir": "ASC"
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `filter.offer_id` / `filter.product_id` | ID 数组 | 选择待编辑商品 |
| `filter.visibility` | string | 查询范围 |
| `last_id` | string | 翻页游标 |
| `limit` | integer | 页大小，示例 100 |
| `sort_dir` | string | 排序方向，示例 `ASC` |

保存属性 ID、词典值 ID、复杂属性、尺寸重量、类目和类型等信息，作为编辑时的原始快照。**不能把完整 GET 响应原样提交给更新接口**：响应中的只读字段必须剔除。

#### 1.2.4 获取价格快照

```http
POST /v5/product/info/prices
```

```json
{
  "filter": {
    "offer_id": [
      "SKU-001"
    ],
    "visibility": "ALL"
  },
  "cursor": "",
  "limit": 100
}
```

参数为 `filter`、`cursor`、`limit`。注意这里使用 `cursor`，不要套用列表接口的 `last_id`。读取 `items[]` 内的价格对象及返回游标；记录当前售价、划线价、最低价、币种等需要保留的字段。

#### 1.2.5 获取库存快照

```http
POST /v4/product/info/stocks
```

```json
{
  "filter": {
    "offer_id": [
      "SKU-001"
    ],
    "visibility": "ALL"
  },
  "cursor": "",
  "limit": 100
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `filter.offer_id` / `filter.product_id` | ID 数组 | 查询范围 |
| `filter.visibility` | string | 可见性筛选 |
| `cursor` | string | 分页游标 |
| `limit` | integer | 页大小 |

关注库存类型、实物数量、预留数量及返回状态。**不要直接把所有库存类型相加后作为某个卖家仓的可售库存写回。** 官方曾调整该响应中的仓库字段；不能把聚合库存查询当成可靠的“每仓库存写回模板”。[O4]

本节价格/库存查询用于在线状态同步。需要严格的逐仓账实核对时，应另行核对当前账号适用的逐仓查询接口；本文不把尚未核对的逐仓查询结构当作已确定契约。

### 1.3 调价

```http
POST /v1/product/import/prices
```

```json
{
  "prices": [
    {
      "offer_id": "SKU-001",
      "price": "310.10",
      "currency_code": "CNY"
    }
  ]
}
```

**本例仅适用于该商品/账号以 CNY 接收价格的情况。** 必须使用真实账号接受的币种，不能因为买家端显示卢布就把人民币金额原样按 RUB 提交。

| 参数 | 类型 | 必填/使用方式 | 说明 |
| --- | --- | --- | --- |
| `prices` | object[] | 是 | 本批价格修改 |
| `prices[].offer_id` | string | 与 `product_id` 选一 | 原卖家 SKU |
| `prices[].product_id` | integer | 与 `offer_id` 选一 | Ozon 商品 ID |
| `prices[].price` | string | 是 | 新价格，十进制金额字符串 |
| `prices[].currency_code` | string | 按账号/接口要求 | 如 `CNY`、`RUB`；必须与商品定价币种匹配 |
| `prices[].old_price` | string | 否 | 划线价；仅在明确要设置/清除时传入 |
| `prices[].min_price` | string | 否 | 平台相关最低价格，不是本地成本 |
| `prices[].vat` | string | 否 | 税率字段；按账号实际配置，不复制示例税率 |

处理每条商品的更新结果和错误；HTTP 200 不代表批次中每个商品都已成功。提交后通过价格查询接口回读。

**更新边界：** 修改售价不需要调用商品导入接口，也不应顺带覆盖图片、属性、库存。对于划线价、最低价等字段，先读取旧值，再按当前接口“省略/清空”的语义处理，不要统一补 `0`。

**当前废弃字段：** 2026-09-11 官方已把 `prices.auto_action_enabled` 和 `prices.manage_elastic_boosting_through_price` 标为过时，本参考不在新请求中使用它们。[O2]

### 1.4 改库存

#### 1.4.1 获取卖家仓库 ID

辅助接口：

```http
POST /v1/warehouse/list
```

```json
{
  "limit": 100,
  "offset": 0
}
```

`limit`、`offset` 为分页参数；遍历返回结果，保存仓库标识、名称及状态。官方公告曾增加该接口的分页参数，旧代码中固定发送 `{}` 的写法需要复核。[O4]

不要使用 FBO 仓、承运商、揽收点等不同资源的 ID 替代卖家仓 ID。仓库是否可以接收库存，还受账号履约方式和仓库状态影响。

#### 1.4.2 设置卖家仓可售数量

```http
POST /v2/products/stocks
```

```json
{
  "stocks": [
    {
      "offer_id": "SKU-001",
      "warehouse_id": 123456789012345,
      "stock": 100
    }
  ]
}
```

| 参数 | 类型 | 必填/使用方式 | 说明 |
| --- | --- | --- | --- |
| `stocks` | object[] | 是 | 库存更新列表 |
| `stocks[].offer_id` | string | 与 `product_id` 选一 | 卖家 SKU |
| `stocks[].product_id` | integer | 与 `offer_id` 选一 | Ozon 商品 ID |
| `stocks[].warehouse_id` | integer | 是 | 要更新的卖家仓库 |
| `stocks[].stock` | integer | 是 | 目标库存数量，非负整数；不是增减量 |

库存是“设置为 100”，不是“增加 100”。同一商品多仓要按不同仓库分别更新。

查看逐条 `updated` / `errors` 等结果，不要只检查外层响应。库存有频率限制，官方明确存在 `Stock is updated too frequently`、`TOO_MANY_REQUESTS` 等错误；只推送有变化的库存并合并短时间内连续修改。[O5]

此接口不是平台仓实物库存的任意改数入口。平台仓库存由入库、出库、预留等业务决定。

### 1.5 编辑商品内容

按修改内容选择接口，避免所有修改都重新导入整个商品。

| 修改内容 | 首选接口 | 说明 |
| --- | --- | --- |
| 类目属性、适用的描述属性 | `/v1/product/attributes/update` | 先取得该类目的属性定义及原有值 |
| 图片 | `/v1/product/pictures/import` | 以期望最终图片列表组织请求 |
| 商品名称、尺寸重量等基础信息 | `/v3/product/import` | 使用原 `offer_id`，合并必要旧数据；不是通用 PATCH |
| 异步更新结果 | `/v1/product/import/info` | 对返回了 `task_id` 的更新任务轮询 |

#### 1.5.1 修改属性

```http
POST /v1/product/attributes/update
```

```json
{
  "items": [
    {
      "offer_id": "SKU-001",
      "attributes": [
        {
          "id": 999999,
          "complex_id": 0,
          "values": [
            {
              "value": "更新后的属性值"
            }
          ]
        }
      ]
    }
  ]
}
```

`999999` 是占位属性 ID，不是通用“描述”字段。商品描述在 Ozon 中通常需要按类目支持的属性组织；不要擅自发送一个根级 `description` 字段。

| 参数 | 类型 | 使用方式 |
| --- | --- | --- |
| `items` | object[] | 本批商品 |
| `items[].offer_id` | string | 原卖家 SKU |
| `items[].attributes` | object[] | 本次要提交的属性 |
| `attributes[].id` | integer | Ozon 属性 ID，不是 `attribute_id` 字段名 |
| `attributes[].complex_id` | integer | 普通属性常用 0；复杂属性按对应规则 |
| `attributes[].values` | object[] | 该属性的值集合 |
| `values[].value` | string | 文本值或相应值的表示 |
| `values[].dictionary_value_id` | integer | 词典型属性使用官方返回的值 ID，不可自造 |

**集合语义待联调：** 多值属性应提交该属性期望保留的完整值集合；不要默认“只发一个值”就是追加。未修改属性是否全部保留，以当前官方方法说明为准。

属性定义与词典查询辅助接口：

```http
POST /v1/description-category/attribute
POST /v1/description-category/attribute/values
```

| 接口 | 主要参数 |
| --- | --- |
| `/attribute` | `description_category_id`、`type_id`；可选 `language` |
| `/attribute/values` | `description_category_id`、`type_id`、`attribute_id`、`last_value_id`、`limit`；可选 `language` |

从商品详情取得原类目和类型，按对应定义判断是否必填、是否词典型、是否支持多值。这里的 `attribute_id` 是词典查询参数，与更新请求中的 `attributes[].id` 不同。

#### 1.5.2 更新图片

```http
POST /v1/product/pictures/import
```

```json
{
  "product_id": 123456789,
  "images": [
    "https://example.com/products/sku-001/main.jpg",
    "https://example.com/products/sku-001/detail.jpg"
  ]
}
```

| 参数 | 类型 | 必填/使用方式 | 说明 |
| --- | --- | --- | --- |
| `product_id` | integer | 是 | Ozon 商品 ID |
| `images` | string[] | 本例必填 | 按期望顺序提交图片 URL 列表 |
| `color_image` | string | 否 | 需要时设置颜色图片，适用性按类目/当前 Schema |

不要把接口当作“向原图集追加一张图片”。需要保留原图时先读取原列表，合并后再提交。图片下载和审核可能异步完成，提交成功后仍需回读状态。

旧示例中的 `images360` 不纳入本参考；官方更新记录中已出现该字段移除说明。使用前以当前图片接口 Schema 为准。[O6]

#### 1.5.3 更新名称、尺寸重量等基础内容

```http
POST /v3/product/import
```

以下是**“读取旧快照后合并修改”的结构模板，不是任意类目通用的最小有效请求**：

```json
{
  "items": [
    {
      "offer_id": "SKU-001",
      "name": "更新后的商品名称",
      "description_category_id": 100001,
      "type_id": 100002,
      "price": "310.10",
      "currency_code": "CNY",
      "vat": "0",
      "depth": 100,
      "width": 100,
      "height": 50,
      "dimension_unit": "mm",
      "weight": 300,
      "weight_unit": "g",
      "images": [
        "https://example.com/products/sku-001/main.jpg"
      ],
      "attributes": [
        {
          "id": 999999,
          "complex_id": 0,
          "values": [
            {
              "value": "从原商品保留或按类目填写的属性值"
            }
          ]
        }
      ]
    }
  ]
}
```

| 参数 | 类型 | 处理原则 |
| --- | --- | --- |
| `items[].offer_id` | string | 必须使用既有商品编码，不能生成新编码 |
| `name` | string | 新名称；受平台名称规则影响 |
| `description_category_id`、`type_id` | integer | 本次不改类目时使用原值 |
| `attributes`、`complex_attributes` | array | 保留必要旧属性并满足当前类目必填规则 |
| `images`、`color_image` | array/string | 不改图时保留符合当前接口要求的原数据 |
| `depth`、`width`、`height`、`dimension_unit` | number/string | 尺寸及单位，使用包装后的正确值 |
| `weight`、`weight_unit` | number/string | 重量及单位 |
| `price`、`currency_code`、`vat` | string | 仅因导入契约需要而携带时，保留现有有效值；不在内容编辑中暗改价格 |
| `barcode` | string | 类目或商品需要时，保留有效原值 |

示例中的类目、类型、属性、价格、税率全部需要替换。不要直接复制 `vat="0"` 到所有商品。

该接口具有创建/更新语义：如果原 `offer_id` 不存在，可能进入新建流程。实现“编辑已有商品”时应先确认平台商品存在，再调用；这份文档不将它作为发布新品入口。[O7]

#### 1.5.4 查询异步结果

```http
POST /v1/product/import/info
```

```json
{
  "task_id": 123456789
}
```

`task_id` 使用上一步实际返回值。检查商品级 `status`、`errors` 和平台商品 ID。不要把任务已创建、`skipped`、已导入和最终可售混为一谈；任务结束后再回读详情和状态。

### 1.6 停售与恢复销售

**实现建议：把“暂停售卖”与“商品归档”分成两个内部动作，不把归档当作任何履约模式下都立即生效的停售开关。**

#### 1.6.1 卖家仓商品临时停售

使用库存接口，将计划停售范围内的卖家仓库存设置为 0：

```http
POST /v2/products/stocks
```

```json
{
  "stocks": [
    {
      "offer_id": "SKU-001",
      "warehouse_id": 123456789012345,
      "stock": 0
    }
  ]
}
```

如果该商品在多个卖家仓销售，需要处理所有目标仓。仅把一个仓归零，不代表其他仓也停售；FBO 等其他库存来源也不能通过该请求直接归零。

恢复销售时，提交当前真实可售数量，而不是无条件恢复停售前的旧数值。已预留订单、仓库关闭、商品审核问题等需要分别处理。

#### 1.6.2 归档商品

```http
POST /v1/product/archive
```

```json
{
  "product_id": [
    123456789
  ]
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `product_id` | integer[] | 需要归档的 Ozon 商品 ID 列表；字段名为单数，但值为数组 |

归档是否允许、已有库存/订单如何处理，受实际状态与履约方式限制。检查返回结果并回读归档状态，不能仅凭发送成功宣称商品已全面停售。

#### 1.6.3 从归档恢复

```http
POST /v1/product/unarchive
```

```json
{
  "product_id": [
    123456789
  ]
}
```

参数与归档相同。恢复后重新检查库存、售价、审核状态和仓库状态。官方存在恢复数量/总量限制相关错误，不能无限循环重试。[O5]

---
<a id="yandex"></a>

## 2. Yandex Market

**核验等级：A。下述核心写接口及参数已对照官方正文；仓库列表、新版库存读取的完整 Schema 单独注明。**

### 2.1 鉴权、账号和标识

```text
Base URL: https://api.partner.market.yandex.ru
```

```http
Api-Key: YOUR_YANDEX_API_KEY
Content-Type: application/json
```

新接入使用 `Api-Key`。令牌需要商品/卡片管理权限 `offers-and-cards-management`；调价还需要 `pricing`。只读同步可选相应只读权限。[Y1]

| 标识 | 类型 | 含义 |
| --- | --- | --- |
| `businessId` | integer | 卖家账号/商品目录所在的业务账户 |
| `campaignId` | integer | 具体店铺的 API 技术 ID，不是广告活动 ID，也不应直接抄后台另一个“店铺 ID” |
| `offerId` / `sku` | string | 卖家 SKU；不同接口的参数名不同，不能任意互换 |
| `marketSku` | integer | Market 商品卡片标识，不等于卖家 SKU |
| `partnerWarehouseId` | integer | 新仓库模型中的卖家仓库 ID |

账号及店铺发现入口：

```http
GET /v2/campaigns
```

保存返回的店铺与 business 关联，再进入下面的业务接口。SKU 按字符串保存，保留前导零。[Y2]

### 2.2 同步在线商品

#### 2.2.1 拉取账号商品目录

```http
POST /v2/businesses/{businessId}/offer-mappings?limit=100
```

首次请求体：

```json
{}
```

下一页：将响应 `result.paging.nextPageToken` 放进查询参数 `pageToken`，请求体保持筛选条件不变。[Y3]

| 位置 | 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| Path | `businessId` | integer | 是 | 账号 ID |
| Query | `limit` | integer | 否 | 1–100，默认 50 |
| Query | `pageToken` | string | 否 | 下一页令牌；第一页不传 |
| Query | `language` | string | 否 | 默认 `RU`；实际可用语言以账号与接口枚举为准 |
| Body | `archived` | boolean | 否 | 默认 false；同步归档商品时另做一轮 true |
| Body | `offerIds` | string[] | 否 | 精确查询，1–100 个 |
| Body | `cardStatuses` | string[] | 否 | 按卡片状态筛选 |
| Body | `categoryIds` | integer[] | 否 | 按类目筛选 |
| Body | `vendorNames` / `tags` | string[] | 否 | 按品牌/标签筛选 |

**互斥规则：指定 `offerIds` 时，不再传 `pageToken`、`limit`、`cardStatuses`、`categoryIds`、`vendorNames`、`tags`、`archived`。**

重点保存 `result.offerMappings[]` 中的 `offer.offerId`、名称、图片、描述、类目、品牌、基础价、归档标志，以及 `mapping.marketSku`。这是账号目录，不应单独用它判断每个店铺是否已可购买。[Y3]

#### 2.2.2 拉取具体店铺的销售状态及店铺价格

```http
POST /v2/campaigns/{campaignId}/offers?limit=200
```

```json
{}
```

| 位置 | 参数 | 类型 | 说明 |
| --- | --- | --- | --- |
| Path | `campaignId` | integer | 店铺 ID |
| Query | `limit` | integer | 默认 100，最大 200 |
| Query | `pageToken` | string | 响应中的下一页令牌 |
| Body | `offerIds` | string[] | 精确查询，1–200 个；不与其他筛选或分页参数同时使用 |
| Body | `statuses` | string[] | 可选状态过滤；全量同步建议不只筛选在售 |
| Body | `categoryIds` / `vendorNames` / `tags` | array | 可选筛选 |

回读 `result.offers[]` 的 `offerId`、`basicPrice`、`campaignPrice`、`status`、`errors`、`warnings`。分页仍使用 `result.paging.nextPageToken`。[Y4]

常用状态包括 `PUBLISHED`、`CHECKING`、`DISABLED_BY_PARTNER`、`REJECTED_BY_MARKET`、`DISABLED_AUTOMATICALLY`、`NO_STOCKS`、`ARCHIVED`。请保存原始值，而不是只保存“上架/下架”一个布尔值。

**旧 `available` 字段已标记废弃，计划于 2026-10-19 停止支持。新代码采用第 2.6 节的隐藏/恢复接口。**[Y4]

#### 2.2.3 拉取属性、卡片处理结果

```http
POST /v2/businesses/{businessId}/offer-cards
```

```json
{
  "offerIds": ["SKU-001"],
  "withRecommendations": false
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `businessId` | integer，Path | 账号 ID |
| `offerIds` | string[]，Body | 按 SKU 查询；最多 200 个 |
| `withRecommendations` | boolean，Body | 是否返回内容改进建议，默认 false |
| `limit` / `pageToken` | integer / string，Query | 全量分页时使用；limit 默认 100、最大 200 |

按 SKU 查询与全量分页分开调用。读取 `result.offerCards[]` 的 `parameterValues`、`cardStatus`、`errors`、`warnings`、`mapping` 等字段，用于属性编辑回填和修改后的审核检查。[Y5]

`HAS_CARD_CAN_NOT_UPDATE` 与 `HAS_CARD_CAN_UPDATE` 等状态需要区别处理；平台卡片并非任何卖家都能任意修改。

#### 2.2.4 库存读取：必须对应仓库模式

| 适用模式 | 读取接口 |
| --- | --- |
| 有仓库组的 FBS/DBS/Express；以及官方指定的 FBY/LaaS 场景 | `POST /v2/campaigns/{campaignId}/offers/stocks` |
| 没有仓库组的 FBS/DBS/Express | `POST /v3/businesses/{businessId}/offers/stocks` |

旧模式精确读取示例：

```http
POST /v2/campaigns/{campaignId}/offers/stocks
```

```json
{
  "offerIds": ["SKU-001"]
}
```

旧模式的 `offerIds` 最多 500 个；精确查询不加分页。全量查询使用 `limit`（默认 50、最大 100）、`pageToken`，可用 `archived` 区分归档。回读 `result.warehouses[].offers[].stocks[]` 的 `type` 与 `count`，不要把 `FIT`、`FREEZE` 和 `AVAILABLE` 混为一个可售数。[Y13]

**新版读取路径已由官方旧接口迁移说明确认；本次未取得新版读取的完整参数正文。其分页、筛选体与响应结构须从 [Y14] 复核，不直接照搬旧版。新版写库存参数已在第 2.4.2 节核实。**

### 2.3 调价

#### 2.3.1 修改账号级基础价

```http
POST /v2/businesses/{businessId}/offer-prices/updates
```

```json
{
  "offers": [
    {
      "offerId": "SKU-001",
      "price": {
        "value": 1990,
        "currencyId": "RUR"
      }
    }
  ]
}
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `businessId` | integer，Path | 是 | 账号 ID |
| `offers` | object[] | 是 | 1–500 条，`offerId` 不重复 |
| `offers[].offerId` | string | 是 | 卖家 SKU |
| `offers[].price` | object | 是 | 价格对象 |
| `price.value` | number | 是 | 大于 0 的基础价 |
| `price.currencyId` | string | 是 | 该账号支持的币种；俄区示例为 `RUR`，不是 Ozon 的币种写法 |
| `price.discountBase` | integer | 否 | 划线参考价；需满足平台折扣范围 |
| `price.minimumForBestseller` | number | 否 | 相关活动最低价；本项目不新增活动功能，但更新时要处理原有值 |

若要保留划线价，价格更新中需要再次传入 `discountBase`。`minimumForBestseller` 省略时会删除原值；实现时明确决定“保留”还是“清除”，不要假定所有缺省字段都不变。[Y6]

基础价作用于账号层面。店铺存在独立价格时，不应仅改基础价就认定该店铺最终展示价已经改变。

#### 2.3.2 修改指定店铺价格

```http
POST /v2/campaigns/{campaignId}/offer-prices/updates
```

```json
{
  "offers": [
    {
      "offerId": "SKU-001",
      "price": {
        "value": 1890,
        "currencyId": "RUR"
      }
    }
  ]
}
```

核心参数与基础价类似，但目标是 `campaignId`；`offers` 为 1–2000 条。可选 `price.vat` 是平台税率代码，不要把税率百分比直接填进去。[Y7]

**前置条件：通过 `POST /v2/businesses/{businessId}/settings` 查询设置，`onlyDefaultPrice=false` 时才可使用独立店铺价格。** 该设置查询的完整 Schema 不在本范围展开。若只允许基础价，应使用上一接口，而不是重复请求店铺价接口。[Y7]

**回读**：查询该店铺 `offers`，检查 `basicPrice` 和 `campaignPrice`，并在处理延迟后判断是否生效。

### 2.4 改库存

以下只针对卖家可管理的库存，不把平台 FBY 仓库存改写成自有仓库存。

#### 2.4.1 有仓库组的账号

```http
PUT /v2/campaigns/{campaignId}/offers/stocks
```

```json
{
  "skus": [
    {
      "sku": "SKU-001",
      "items": [
        {
          "count": 100,
          "updatedAt": "2026-09-19T00:00:00Z"
        }
      ]
    }
  ]
}
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `campaignId` | integer，Path | 是 | 对应仓库范围的店铺 ID |
| `skus` | object[] | 是 | 1–2000 条，SKU 唯一 |
| `skus[].sku` | string | 是 | 此处叫 `sku`，不是 `offerId` |
| `skus[].items` | object[] | 是 | 每个 SKU 恰好一个元素 |
| `items[].count` | integer | 是 | 当前库存绝对值；0–2,000,000,000 |
| `items[].updatedAt` | string，ISO 8601 | 否 | 库存采集时刻，带时区；省略使用当前时刻 |

这里只传 `campaignId`，不要额外塞一个假定的 `warehouseId`。同一仓库组按官方规则只需向组内一个仓库对应的店铺提交，组内同步，避免多次冲突写入。[Y8]

示例时间仅为格式演示，实际请求必须用真实采集时间，不能长期写死。

#### 2.4.2 没有仓库组的账号：新版接口

```http
POST /v3/businesses/{businessId}/offers/stocks/update
```

```json
{
  "skuItems": [
    {
      "sku": "SKU-001",
      "partnerWarehouseId": 12345,
      "count": 100,
      "updatedAt": "2026-09-19T00:00:00Z"
    }
  ]
}
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `businessId` | integer，Path | 是 | 账号 ID |
| `skuItems` | object[] | 是 | 1–2000 条；`sku + partnerWarehouseId` 组合唯一 |
| `skuItems[].sku` | string | 是 | 1–255 字符的卖家 SKU |
| `skuItems[].partnerWarehouseId` | integer | 是 | 大于 0 的卖家仓库 ID |
| `skuItems[].count` | integer | 是 | 库存绝对值；0–2,000,000,000 |
| `skuItems[].updatedAt` | string，ISO 8601 | 否 | 实际采集时间，带时区；省略使用当前时刻 |

`partnerWarehouseId` 的官方获取入口为：

```http
POST /v3/businesses/{businessId}/warehouses
```

仓库发现接口的完整筛选和分页参数需在接入时另查 Schema；不要把 `campaignId` 当作 `partnerWarehouseId`。[Y9]

**不要以“哪个接口返回 200 就用哪个”自动猜仓库模式。** 建议把账号的库存模式存成明确配置，并使用对应的读取接口验证。[Y8][Y9][Y13]

### 2.5 编辑商品内容

#### 2.5.1 更新已有商品

```http
POST /v2/businesses/{businessId}/offer-mappings/update
```

```json
{
  "offerMappings": [
    {
      "offer": {
        "offerId": "SKU-001",
        "name": "Защитная накладка для домкрата",
        "description": "Резиновая защитная накладка. Совместимость и размеры указаны в характеристиках."
      }
    }
  ]
}
```

**更新已有商品时，传原 `offerId` 与需要改动的字段，未变化字段可省略。** 这里不要生成新 SKU，否则可能变成新增目录项。[Y10]

| 参数 | 类型 | 必填/条件 | 说明 |
| --- | --- | --- | --- |
| `businessId` | integer，Path | 是 | 账号 ID |
| `offerMappings` | object[] | 是 | 文档提示最多传 100 项；即使 Schema 表中出现更大上限，也按 100 分批 |
| `offerMappings[].offer` | object | 是 | 待更新商品 |
| `offer.offerId` | string | 是 | 原卖家 SKU，1–255 字符 |
| `offer.name` | string | 修改标题时 | 最长 256 字符 |
| `offer.description` | string | 修改描述时 | 最长 6000 字符；HTML 仅限官方允许范围 |
| `offer.pictures` | string[] | 修改图片时 | 图片 URL 数组，第一张为主图；按目标完整列表处理 |
| `offer.vendor` | string | 修改品牌时 | 品牌名称，不虚构品牌 |
| `offer.vendorCode` | string | 修改货号时 | 厂商货号 |
| `offer.barcodes` | string[] | 修改条码时 | 真实条码列表 |
| `offer.manufacturerCountries` | string[] | 修改产地时 | 按接口要求的国家名称，不擅自改为 ISO 代码 |
| `offer.marketCategoryId` | integer | 提交类目属性时一并传 | 平台类目 ID |
| `offer.parameterValues` | object[] | 修改属性时 | 新属性结构，见下一节 |
| `offer.weightDimensions` | object | 修改包装尺寸/重量时 | `length`、`width`、`height` 为厘米；`weight` 为千克 |
| `offer.deleteParameters` | string[] | 显式清除字段时 | 如 `DESCRIPTION`、`PICTURES`、`PARAMETERS`；按官方枚举使用 |
| `onlyPartnerMediaContent` | boolean，根级 | 否 | 默认 false；true 会移除平台补充的相关内容，不作为默认更新参数 |

普通编辑无需传 `mapping.marketSku`，避免无意改变商品卡片关联。[Y10]

**迁移提醒：旧 `params`、`category` 等字段已标记废弃，计划于 2026-10-12 停止支持。新实现使用 `parameterValues` 和 `marketCategoryId`。**[Y10]

#### 2.5.2 属性 ID、枚举值和单位从哪里来

```http
POST /v2/category/{categoryId}/parameters
```

注意路径是单数 `category`。

| 位置 | 参数 | 类型 | 说明 |
| --- | --- | --- | --- |
| Path | `categoryId` | integer | 类目 ID，正整数 |
| Query | `businessId` | integer | 可选；需要账号相关规格属性信息时传 |

此处不臆造请求体。读取 `result.parameters[]` 的 `id`、`type`、`required`、`multivalue`、`allowCustomValues`、`values`、单位与约束，然后生成编辑控件和更新请求。[Y11]

`parameterValues` 元素：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `parameterId` | integer | 属性 ID |
| `valueId` | integer | 枚举型属性使用对应枚举值 ID |
| `value` | string | 文本/数值等按属性约定转为字符串；不凭中文标签猜值 |
| `unitId` | integer | 需要指定单位时传对应单位 ID |

以下数字仅示意结构，**必须替换成实际类目 Schema 中的值**：

```json
{
  "offerMappings": [
    {
      "offer": {
        "offerId": "SKU-001",
        "marketCategoryId": 99999999,
        "parameterValues": [
          {
            "parameterId": 99999998,
            "value": "Резина"
          }
        ]
      }
    }
  ]
}
```

更新后同时检查逐项错误/警告，并回读 `offer-mappings` 与 `offer-cards`，不要仅凭 HTTP 200 认为标题或属性已通过审核。[Y5][Y10]

### 2.6 停售、恢复销售

#### 2.6.1 在指定店铺隐藏商品

```http
POST /v2/campaigns/{campaignId}/hidden-offers
```

```json
{
  "hiddenOffers": [
    {
      "offerId": "SKU-001"
    }
  ]
}
```

#### 2.6.2 恢复指定店铺展示

```http
POST /v2/campaigns/{campaignId}/hidden-offers/delete
```

```json
{
  "hiddenOffers": [
    {
      "offerId": "SKU-001"
    }
  ]
}
```

两个接口的参数相同：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `campaignId` | integer，Path | 是 | 停售/恢复的目标店铺 |
| `hiddenOffers` | object[] | 是 | 1–500 项，SKU 不重复 |
| `hiddenOffers[].offerId` | string | 是 | 卖家 SKU |

**恢复接口的请求体仍叫 `hiddenOffers`，不是 `offerIds`；HTTP 方法仍是 `POST`，不是 `DELETE`。** 隐藏与恢复可能延迟几分钟，需回读店铺商品状态。恢复展示不会自动解决库存为零、审核失败或店铺关闭等问题。[Y12][Y15]

---
<a id="mercado"></a>

## 3. Mercado Libre Global Selling / CBT

**核验等级：C。本节是待复核的接入草案，不是已经验证的当前接口契约。** 官方 Global Selling 的商品创建/更新、同步修改、描述、变体和定价文档入口可以定位，但本次正文访问受限。下列候选路由和参数必须结合该账号当前商品模型核验后才可启用。[M1][M2]

本节按跨境 Remote 商品管理组织，不将本地卖家示例、Full 仓库或 Fully Managed 模式直接套入你的店铺。

### 3.1 鉴权与商品层级

```text
Base URL: https://api.mercadolibre.com
```

```http
Authorization: Bearer YOUR_ACCESS_TOKEN
Content-Type: application/json
```

OAuth 授权、令牌刷新与应用权限的官方入口见 [M3]。本文不写死令牌有效期，也不在示例 URL 中明文携带令牌。

建议先保存以下映射，字段名是**本地模型建议**，不是一个可以直接提交给平台的请求体：

```text
Global Selling 账号
  └─ CBT 全局商品 global_item_id
       ├─ 站点刊登 local_item_id + site_id + logistic_type + seller_id
       └─ 其他站点刊登 local_item_id + site_id + logistic_type + seller_id
```

| 标识 | 示例形态 | 用途及风险 |
| --- | --- | --- |
| 全局商品 ID | `CBT123456789` | 跨站点关联主键；不等于墨西哥等站点的刊登 ID |
| 站点刊登 ID | `MLM1234567890` | 指定站点商品记录 |
| 站点 ID | `MLM`、`MCO` 等 | 本身不足以定位商品；还要有刊登及授权用户关系 |
| 物流模式 | `remote` | 不与 Full 或其他模式混用 |
| 全局/站点用户 ID | integer | 搜索范围和权限校验；不要只存一个不明含义的 `user_id` |
| 变体 ID | integer | 更新已存在的尺码、颜色等具体变体 |
| User Product ID | 返回值原样保存 | 若账号返回新版产品模型，先识别模型，不把它当成 item ID |

**本地保护规则建议：** 每次写请求都显式选择 `GLOBAL` 或 `SITE` 作用范围。无法确认全局与站点对应关系时，不允许“批量调价/批量停售”猜 ID 执行。

### 3.2 同步在线商品

#### 3.2.1 商品列表搜索：候选契约

```http
GET /marketplace/users/{user_id}/items/search
```

| 位置 | 参数 | 类型 | 用法；均需按当前官方契约复核 |
| --- | --- | --- | --- |
| Path | `user_id` | integer | 明确是全局用户还是对应站点用户；两者的结果范围不能混为一谈 |
| Query | `status` | string | 按需查 `active`、`paused` 等；不能只拉 active |
| Query | `seller_sku` | string | 精确按卖家 SKU 查询时使用，注意 URL 编码 |
| Query | `limit` | integer | 单页数量；具体默认值与最大值待复核 |
| Query | `offset` | integer | 普通分页模式的偏移；不与扫描模式随意混用 |
| Query | `search_type` | string | 大目录扫描候选值 `scan`，需核对该 Global Selling 路由是否支持 |
| Query | `scroll_id` | string | 扫描模式后续令牌；仅在响应实际提供且官方允许时使用 |

只读联调示例，示例的分页大小不代表已验证上限：

```http
GET /marketplace/users/{user_id}/items/search?status=active&limit=50&offset=0
```

另拉 paused 等状态，按平台商品 ID 去重。普通分页是否存在窗口上限、扫描是否支持当前账号，都要先做只读验证，不套用本地卖家接口的旧限制。[M1]

#### 3.2.2 全局商品及站点映射：候选契约

```http
GET /marketplace/items/{item_id}
```

| 参数 | 位置 | 类型 | 说明 |
| --- | --- | --- | --- |
| `item_id` | Path | string | 首先使用搜索得到的 CBT 商品 ID；当地刊登 ID 能否用在同一路由需单独确认 |

回读时重点核验下列字段是否存在，以及其实际层级：`item_id`、`seller_id`、`site_id`、`site_items`、`title`、`status`、`sub_status`、`price`、`currency_id`、`available_quantity`、`variations`、`attributes`、`pictures`。**这里是字段核验清单，不承诺它们都在同一个响应中返回。**

若返回 `site_items`，保存每项的站点刊登 ID、站点、卖家 ID、物流模式；再按官方当前的当地刊登读取接口补齐价格/状态。不能用 CBT 父记录的单个状态代替所有站点状态。[M1][M2]

**不默认使用 `GET /users/{id}/items/search` 替代上述 Global Selling 路由，也不把本地 `GET /items/{id}` 的所有能力直接认定为跨境账号均可调用。**

### 3.3 调价

#### 3.3.1 更新候选入口

```http
PUT /global/items/{item_id}
```

| 参数 | 位置 | 类型 | 说明 |
| --- | --- | --- | --- |
| `item_id` | Path | string | 目标 CBT 商品或站点刊登 ID；哪个层级允许何种定价字段，必须先核验 |
| `price` | Body | number | 候选价格字段；不能未经确认当作所有站点买家零售价 |
| `net_proceeds` | Body | number | 候选净收入定价字段；不是本地商品零售价的同义词 |

以下是两种**互斥使用的候选请求**，不是建议同时发送两个价格字段。

全局商品价格候选示例：

```http
PUT /global/items/CBT123456789
```

```json
{
  "price": 39.9
}
```

站点刊登净收入候选示例：

```http
PUT /global/items/MLM1234567890
```

```json
{
  "net_proceeds": 35.5
}
```

**生产启用前必须确认：** 当前 Remote 模式是否允许该字段；币种和金额口径；父商品更新的传播范围；当地最终售价如何回读；自动定价是否会覆盖手动更新。未确认前禁止把统一界面的“售价”直接映射成 `net_proceeds`。[M1][M2][M7]

不要为了让接口接受请求而自动在 `price` 与 `net_proceeds` 之间切换重试，这可能产生业务含义完全不同的修改。

### 3.4 改库存

#### 3.4.1 无变体商品：候选契约

```http
PUT /global/items/{global_item_id}
```

```json
{
  "available_quantity": 100
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `global_item_id` | string，Path | 已识别的 CBT 商品 ID |
| `available_quantity` | integer，Body | 待设置库存绝对值；需核对账号模式允许更新的库存层级 |

本地实现按“设置为 100”处理，不按“增加 100”处理。库存到底由父商品共享还是允许站点独立写入，必须用当前账号返回数据和官方契约确认，不能对同一批实物库存给每个站点各补一次。[M1][M2]

#### 3.4.2 已有变体商品：候选契约

```http
PUT /global/items/{global_item_id}
```

```json
{
  "variations": [
    {
      "id": 12345678901,
      "available_quantity": 40
    },
    {
      "id": 12345678902,
      "available_quantity": 60
    }
  ]
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `variations` | object[] | 当前商品的既有变体列表；数组是合并还是替换必须复核 |
| `variations[].id` | integer | 平台返回的变体 ID，不是本地颜色/尺码 ID |
| `variations[].available_quantity` | integer | 对应变体的库存绝对值 |

在确认数组更新语义前，不发送只含一个变体的片段去赌“其他变体不会被删”。不要同时传一个不一致的父级数量和变体数量。新版 User Products 或其他库存模型的专用接口不由本文旧 item 结构推断生成。[M4]

库存写入后的状态变化也应回读确认；不能假定补库存一定自动恢复销售，更不能让库存同步覆盖用户的主动停售意图。

### 3.5 编辑商品内容

#### 3.5.1 标题：候选契约

```http
PUT /global/items/{global_item_id}
```

```json
{
  "title": "Rubber Jack Pad Protective Adapter"
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `global_item_id` | string，Path | 明确更新全局商品内容 |
| `title` | string，Body | 目标标题；可修改条件、长度、语言与向当地刊登传播规则需复核 |

有成交、目录关联或商品模型限制时，不能承诺标题一定可改。遇到限制不得通过“重新创建商品”绕过，因为这超出当前在线商品修改范围。[M1][M2][M8]

#### 3.5.2 属性：候选契约

```http
PUT /global/items/{global_item_id}
```

```json
{
  "attributes": [
    {
      "id": "MATERIAL",
      "value_name": "Rubber"
    }
  ]
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `attributes` | object[] | 待提交属性集合；需确认是局部合并还是全量替换 |
| `attributes[].id` | string | 当前类目真实属性 ID；示例 `MATERIAL` 仅表示形态，不保证每个类目都存在 |
| `attributes[].value_id` | string | 有固定枚举且要求枚举 ID 时使用 |
| `attributes[].value_name` | string | 允许自定义值时使用；不能用它规避固定枚举约束 |

属性定义、必填性、可编辑性需要从当前商品类目对应的官方定义获得。品牌、GTIN、型号等身份字段不当作自由文本任意更换。[M5][M8]

#### 3.5.3 图片：候选契约

```http
PUT /global/items/{global_item_id}
```

```json
{
  "pictures": [
    {
      "source": "https://example.com/product-main.jpg"
    },
    {
      "source": "https://example.com/product-side.jpg"
    }
  ]
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `pictures` | object[] | 目标图片列表；按可能覆盖整组的高风险字段处理 |
| `pictures[].source` | string | 新图片的可访问 URL，需核验当前 Global Selling 更新接口是否接受 |
| `pictures[].id` | string | 保留平台已有图片时的候选标识；与 `source` 的用法需按图片文档复核 |

只改主图时，先读出原完整图片列表，再按已确认的更新语义生成目标集合，防止附图丢失。示例地址不能实际上传。[M6]

#### 3.5.4 描述：明确未完成当前写路径核验

描述的纯文本字段可在本地先保存为 `plain_text: string`，但**本次没有核实当前 CBT 商品描述究竟通过哪个独立写资源更新，以及该资源接受父商品还是当地刊登 ID**。[M9]

因此本文不虚构 `/global/items/{id}/description` 或 `/marketplace/items/{id}/description` 中的某一个为确定可用路径，也不把创建商品时的 `description` 请求体直接当作更新契约。

实现状态建议：标题/属性/图片各自经复核后逐项开放；“描述发布更新”先标记 `requires_contract_verification`。本地编辑草稿可以保留，但界面不能提示“已同步平台”。

### 3.6 停售、恢复销售

#### 3.6.1 全局停售/恢复：候选契约

```http
PUT /global/items/{global_item_id}
```

停售：

```json
{
  "status": "paused"
}
```

恢复：

```json
{
  "status": "active"
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `global_item_id` | string，Path | CBT 全局商品 ID |
| `status` | string，Body | 候选目标值 `paused` 或 `active` |

操作前须确认父级修改是否会影响所有站点；操作后逐个回读关联刊登。恢复请求不能被理解为可以强制绕过审核、账号限制或缺货原因。[M1][M2]

#### 3.6.2 指定站点停售/恢复：两种可能定位方式，不自动互相回退

候选方式 A：使用已确认可写的站点刊登 ID。

```http
PUT /global/items/{local_item_id}
```

请求体仍是上面的 `status` 对象。

候选方式 B：在 CBT 父商品上指定站点与物流模式。

```http
PUT /global/items/{global_item_id}
```

```json
{
  "site_id": "MLM",
  "logistic_type": "remote",
  "status": "paused"
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `site_id` | string | 目标站点 |
| `logistic_type` | string | 本文目标模式为 `remote` |
| `status` | string | `paused`；恢复时改为 `active` |

**A/B 是待官方和账号联调确认的候选路径，不是已证实对所有账号都成立的两种等价调用。** 一个报错时不得去掉站点条件改成全局写入，以免把“单站点停售”升级为“全部站点停售”。[M1][M2]

本范围不使用 `closed`、删除商品或重建商品来实现可恢复停售。

### 3.7 Mercado 写操作启用清单

以下为开发验收建议，不是平台新增参数：

| 能力 | 启用前必须确认 |
| --- | --- |
| 同步 | 全局用户与站点用户关系、分页规则、父子商品映射、站点详情读取路径 |
| 调价 | 目标 ID 类型、`price` / `net_proceeds` 口径与币种、自动定价关系、回读字段 |
| 库存 | Remote 库存归属、变体数组语义、是否共享库存、正库存与主动停售的交互 |
| 内容 | 标题/属性/图片可修改条件、数组覆盖语义；描述独立更新契约 |
| 停售/恢复 | 父级与站点级作用范围、允许的状态转换、不能恢复的业务原因 |

---

<a id="validation"></a>

## 4. 最小实现与联调检查

以下内容是**实现建议**，不代表三家平台存在这些同名 API 或字段。

### 4.1 五类操作分开，不用“完整商品保存”包办所有更新

```text
syncProducts(account, scope)
updatePrice(product, priceType, amount, currency, scope)
setStock(product, warehouseOrStockScope, quantity)
updateContent(product, changedFields)
setSaleState(product, desiredState, scope)
```

商品内容、价格、库存和销售状态分开提交，减少为了改库存而误覆盖标题、图片或税务字段的风险。图片/属性/变体数组的局部合并行为必须逐接口确认。

### 4.2 写入成功的判断

```text
保存平台原始快照
  → 校验账号、商品、店铺/站点、仓库与字段权限
  → 提交修改
  → 解析逐商品错误及任务号
  → 等待平台处理后回读目标字段
  → 标记生效，或保留失败原因
```

建议本地状态为 `pending`、`submitted`、`confirmed`、`failed`。这些是本地状态，不要当作平台参数发送。

HTTP 成功不等于每个 SKU 成功，也不等于买家端已更新。保留 HTTP 状态、平台业务错误码、请求时间、作用范围与回读结果。鉴权信息和访问令牌应脱敏。

### 4.3 重试和覆盖保护

临时网络错误或限流可以做有上限的退避重试；字段错误、类目不允许修改、权限缺失、错误商品模型等应停止并暴露原因，不应反复尝试其他语义不同的接口。

库存重试前确认快照没有过期；批量接口只重试失败项。长期保留“用户主动停售”的本地意图，库存同步不能擅自恢复销售。一次全量同步失败或只拉到部分页时，不把未出现商品批量标记为已删除。

### 4.4 最小验收用例

| 场景 | 验收结果 |
| --- | --- |
| 同步在售、停售、缺货商品 | 都保留 ID 与平台原始状态，不因停售丢失关联 |
| 修改价格 | 只改变目标层级/站点的正确价格口径，回读币种与金额一致 |
| 修改库存 | 只改变目标仓库/库存范围，不重复分配跨站点共享库存 |
| 修改标题、图片、一个属性 | 未选择修改的其他字段和变体不丢失；审核结果可见 |
| 指定范围停售并恢复 | 不误伤其他站点/店铺；恢复失败时保留具体业务原因 |
| 部分失败、超时、重复提交 | 不把整批误报成功；不覆盖更新后的库存和内容 |

---

<a id="sources"></a>

## 5. 来源与复核入口

访问/整理日期均为 **2026-09-19**。链接可能受地区、登录或防护策略影响。下列“公告”只能证明对应变更，不能替代完整请求 Schema；标记“待正文复核”的页面不表示本次已经读到正文。

### 5.1 Ozon

| 编号 | 官方资料 | 本次用途 |
| --- | --- | --- |
| O1 | [Seller API 文档][O1] | 完整 Schema 复核入口；本次重定向受限 |
| O2 | [Ozon Seller API 官方公告][O2] | 新密钥有效期、2026-09-11 调价字段弃用等近期变更 |
| O3 | [官方历史版本公告][O3]、[价格接口版本公告][O8] | 商品列表/详情/库存/价格版本迁移 |
| O4 | [官方仓库与库存变更公告][O4] | 仓库分页及库存响应字段变更 |
| O5 | [官方库存更新与恢复归档公告][O5] | 库存重复更新、恢复归档限制相关变更 |
| O6 | [官方图片接口变更公告检索入口][O6] | 图片旧字段复核；不作为完整 Schema |
| O7 | [自动导入与更新商品][O7] | 导入与更新业务入口；请求 Schema 仍以 O1 为准 |

### 5.2 Yandex Market

| 编号 | 官方资料 | 本次用途 |
| --- | --- | --- |
| Y1 | [鉴权][Y1] | Api-Key 方式 |
| Y2 | [API 总览][Y2] | business / campaign 标识与接口组织 |
| Y3 | [getOfferMappings][Y3] | 账号商品目录、分页与过滤 |
| Y4 | [getCampaignOffers][Y4] | 店铺商品状态、价格、旧 available 字段迁移 |
| Y5 | [getOfferCardsContentStatus][Y5] | 属性、卡片状态、错误回读 |
| Y6 | [updateBusinessPrices][Y6] | 账号基础价 |
| Y7 | [updatePrices][Y7] | 店铺价格与 onlyDefaultPrice 前提 |
| Y8 | [updateStocks][Y8] | 有仓库组账号的库存写入 |
| Y9 | [updateStocksOnPartnerWarehouses][Y9] | 无仓库组账号的新版库存写入 |
| Y10 | [updateOfferMappings][Y10] | 商品内容更新、字段结构及废弃日期 |
| Y11 | [getCategoryContentParameters][Y11] | 类目属性、枚举值及单位 |
| Y12 | [addHiddenOffers][Y12] | 隐藏商品 |
| Y13 | [getStocks][Y13] | 库存读取及新旧接口适用范围 |
| Y14 | [getStocksOnPartnerWarehouses][Y14] | 新版库存读取完整 Schema 复核入口；本次正文未取得 |
| Y15 | [deleteHiddenOffers][Y15] | 恢复展示 |

### 5.3 Mercado Global Selling

**以下为官方复核入口。本次无法取得完整正文；不以链接存在为理由认定第 3 节所有参数已获官方验证。**

| 编号 | 官方资料 | 需要复核的内容 |
| --- | --- | --- |
| M1 | [Sync and modify listings / Update items][M1] | 搜索、同步、库存、状态更新的当前契约 |
| M2 | [Global Selling item create/update][M2] | `/global/items` 更新路由、商品模型与作用范围 |
| M3 | [Authentication and authorization][M3] | 授权、刷新令牌与权限 |
| M4 | [Variations][M4] | 变体 ID、库存与数组更新语义 |
| M5 | [Attributes][M5] | 类目属性定义及可修改条件 |
| M6 | [Pictures][M6] | 图片 source/id 和更新规则 |
| M7 | [Pricing reference][M7] | price / net_proceeds 的适用模式与币种 |
| M8 | [Validations][M8] | 更新校验及业务限制 |
| M9 | [Item description][M9] | 描述的独立读写路由、方法与 plain_text 参数 |

---

<a id="pending"></a>

## 6. 本版仍需补齐的核验项

| 平台 | 未完成项 | 对开发的具体影响 |
| --- | --- | --- |
| Ozon | 完整 Schema 的参数类型、条件必填、批量上限与响应结构逐项核验 | 本文 B 级示例可用于梳理适配器，不能跳过官方 Schema 校验直接上线 |
| Yandex | 新版库存读取、仓库发现和设置查询的完整 Schema；真实账号仓库模式 | 核心写入已列出，仍需补齐账号发现及回读闭环 |
| Mercado | 当前 Global Selling 全部候选契约的正文核验；描述具体写路径 | C 级请求默认不启用生产写入，逐能力核验后开放 |
| 三个平台 | 真实店铺授权、实际商品字段权限、一次小范围读写与回读测试 | 本文未执行真实店铺 API，也未修改你的任何商品 |

[O1]: https://docs.ozon.ru/api/seller/
[O2]: https://t.me/s/OzonSellerAPI
[O3]: https://t.me/s/OzonEnSellerAPI?before=168
[O4]: https://t.me/s/OzonSellerAPI?before=592
[O5]: https://t.me/s/OzonEnSellerAPI?after=207
[O6]: https://t.me/s/OzonSellerAPI?q=images360
[O7]: https://dev.ozon.ru/start/294-Avtomaticheskii-import-i-obnovlenie-tovarov-v-Seller-API/
[O8]: https://t.me/s/OzonSellerAPI?before=468
[Y1]: https://yandex.ru/dev/market/partner-api/doc/ru/concepts/authorization
[Y2]: https://yandex.ru/dev/market/partner-api/doc/en/overview/
[Y3]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/getOfferMappings
[Y4]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/offers/getCampaignOffers
[Y5]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/content/getOfferCardsContentStatus
[Y6]: https://yandex.ru/dev/market/partner-api/doc/en/reference/prices/updateBusinessPrices
[Y7]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/prices/updatePrices
[Y8]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/updateStocks
[Y9]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/updateStocksOnPartnerWarehouses
[Y10]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/business-offer-mappings/updateOfferMappings
[Y11]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/content/getCategoryContentParameters
[Y12]: https://yandex.ru/dev/market/partner-api/doc/en/reference/hidden-offers/addHiddenOffers
[Y13]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/getStocks
[Y14]: https://yandex.ru/dev/market/partner-api/doc/ru/reference/stocks/getStocksOnPartnerWarehouses
[Y15]: https://yandex.ru/dev/market/partner-api/doc/en/reference/hidden-offers/deleteHiddenOffers
[M1]: https://global-selling.mercadolibre.com/devsite/sync-and-modify-listings-gs
[M2]: https://global-selling.mercadolibre.com/devsite/en_us/receive-notifications/global-selling-item-create-update-global-items
[M3]: https://global-selling.mercadolibre.com/devsite/authentication-and-authorization-global-selling
[M4]: https://global-selling.mercadolibre.com/devsite/variations-global-selling
[M5]: https://global-selling.mercadolibre.com/devsite/atributtes-global-selling
[M6]: https://global-selling.mercadolibre.com/devsite/pictures
[M7]: https://global-selling.mercadolibre.com/devsite/pricing-reference
[M8]: https://global-selling.mercadolibre.com/devsite/validations-cbt
[M9]: https://global-selling.mercadolibre.com/devsite/item-description
