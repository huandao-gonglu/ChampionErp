# API 接口清单

本文档按 `erp_web/http_route_units/` 的实际路由表整理。每个接口列出用途和顶层参数；对象参数只说明对象用途，不展开对象内部字段。

## GET JSON API


- `GET /api/state`（
  无参数
  ）；读取工作台完整状态，包括当前商品、配置、图片池、商品库、草稿箱和发布日志。

- `GET /api/products-index`（
  无参数
  ）；读取本地商品库索引列表。

- `GET /api/drafts-index`（
  `scope`：草稿范围，可选，默认 `active`
  ）；读取平台草稿箱索引。

- `GET /api/browser-debug/status`（
  `port`：浏览器调试端口，可选
  ）；检查浏览器调试端口连接和当前标签页状态。

- `GET /api/publish-logs`（
  无参数
  ）；读取发布请求、响应和错误日志。

- `GET /api/mercadolibre/published-items`（
  `status`：远程商品状态，可选，默认 `active`
  `page`：页码，可选
  `per_page`：每页数量，可选
  `limit`：每页数量别名，可选
  ）；分页读取 Mercado Libre 远程已发布商品。

- `GET /api/mercadolibre/orders`（
  `limit`：读取数量，可选
  `offset`：分页偏移，可选
  ）；读取 Mercado Libre 最近订单和本地通知缓存。

- `GET /api/ai-config`（
  无参数
  ）；读取脱敏后的 AI 模型、用途绑定和提示词配置。

- `GET /api/publish-bus/status`（
  `job_id`：发布任务 ID，必填
  ）；查询发布队列任务状态。

- `GET /api/category-cache/refresh-status`（
  `job_id`：类目刷新任务 ID，必填
  ）；查询类目缓存刷新任务状态。

- `GET /api/v1/product-research/hot-products/runs`（
  `run_id`：调研运行 ID，可选
  `runId`：调研运行 ID 别名，可选
  ）；读取当前或指定热点商品调研运行结果。

- `GET /api/v1/product-research/source-registry`（
  无参数
  ）；读取选品调研搜索来源注册表和目标市场配置。

## POST JSON API

### 采集与认领


- `POST /api/collect-source`（
  `url`：商品来源链接
  `mode`：采集模式，可选，默认 `browser`
  `cookie`：采集 Cookie，可选
  `platform`：来源平台提示，可选
  `platforms`：认领目标平台列表，可选
  `1688_api`：1688 API 配置对象，可选
  ）；采集单个来源商品并生成或更新本地商品记录。

- `POST /api/collect-batch`（
  `urls`：商品链接列表或多行文本
  `url`：单链接兼容字段，可选
  `mode`：采集模式，可选，默认 `browser`
  `cookie`：采集 Cookie，可选
  `platform`：来源平台提示，可选
  `platforms`：认领目标平台列表，可选
  `1688_api`：1688 API 配置对象，可选
  ）；批量采集多个商品链接并返回逐条结果。

- `POST /api/claim-products`（
  `product_ids`：商品 ID 列表，必填
  `platforms`：目标平台列表，可选，默认 Mercado Libre
  ）；将商品库商品认领到一个或多个平台草稿箱。

- `POST /api/collect-1688`（
  `text`：原始文本，可选
  `html`：原始 HTML，可选
  `source_text`：来源文本别名，可选
  `url`：来源链接，可选
  `source_url`：来源链接别名，可选
  `save`：是否保存为商品，可选
  `product`：商品对象，可选
  `mode`：采集模式，可选
  `cookie`：采集 Cookie，可选
  `platforms`：认领目标平台列表，可选
  `1688_api`：1688 API 配置对象，可选
  ）；处理并保存 1688 文本、HTML 或手动采集 payload。

- `POST /api/collect-1688-clean`（
  `text`：原始文本，可选
  `html`：原始 HTML，可选
  `url`：来源链接，可选
  ）；清洗 1688 原始文本或 HTML，用于表单预填。

- `POST /api/collect-from-browser-tab`（
  `tab_url`：指定浏览器标签 URL，可选
  `platform_hint`：来源平台提示，可选
  `product_url`：商品链接，可选
  `url`：商品链接别名，可选
  `port`：浏览器调试端口，可选
  `platforms`：认领目标平台列表，可选
  `save_only`：是否只保存快照，可选
  `mock_tabs`：测试用标签列表对象，可选
  `mock_snapshot`：测试用快照对象，可选
  ）；从当前调试浏览器标签采集商品或保存页面快照。

- `POST /api/browser-debug/open-profile`（
  无参数
  ）；打开用于采集或登录的浏览器调试 profile。

- `POST /api/open-1688-browser`（
  无参数
  ）；打开带调试端口的 1688 浏览器窗口。

- `POST /api/collect-extension-payload`（
  请求体对象：扩展或手动导入的商品采集 payload
  ）；接收浏览器扩展或手动导入 payload 并生成商品记录。

### 文案与 AI


- `POST /api/generate-copy`（
  `product_id`：商品 ID，必填
  `platform`：目标平台，可选，默认 `mercadolibre`
  `language`：目标语言，可选，默认取草稿语言或平台默认语言
  `mode`：生成模式，可选，默认 `rewrite`
  ）；按商品 ID 和平台生成单个商品文案，只写入对应平台草稿 `platform_drafts`。响应包含 `draft`、`productContext`、`draftsIndex`，并保留兼容字段 `product`。

- `POST /api/generate-copy-batch`（
  `product_ids`：商品 ID 列表，必填
  `platform`：目标平台，可选，默认 `mercadolibre`
  `language`：目标语言，可选
  `mode`：生成模式，可选，默认 `rewrite`
  ）；按商品 ID 列表批量生成指定平台文案，并逐条更新对应平台草稿。

- `POST /api/generate-image-prompts`（
  `product_id`：商品 ID，必填
  `platform`：目标平台，可选
  `selected_image_ids`：选中图片 ID 列表，可选
  `include_bullets`：是否包含卖点，可选
  `include_description`：是否包含描述，可选
  `target_language`：目标语言，可选
  `language`：目标语言别名，可选
  ）；根据商品和选中图片生成 GPT 生图任务包提示词。

- `POST /api/test-ai-model`（
  `model`：AI 模型配置对象，可选
  `config`：AI 模型配置对象别名，可选
  ）；测试 AI 模型配置连接和能力探测。

### 授权与配置


- `POST /api/ai-config/save`（
  `config`：AI 配置对象，可选；不传时使用整个请求体作为配置
  ）；保存 AI 模型、用途绑定和提示词配置。

- `POST /api/mercadolibre/auth-link`（
  `app_id`：Mercado Libre App ID，必填
  `redirect_uri`：OAuth 回调地址，可选
  ）；生成 Mercado Libre OAuth 授权链接。

- `POST /api/mercadolibre/auth-checklist`（
  无参数
  ）；读取 Mercado Libre 授权配置检查清单。

- `POST /api/open-auth-link`（
  `url`：授权链接，必填
  `browser`：浏览器类型，可选，默认系统浏览器
  ）；在本机浏览器打开授权链接。

- `POST /api/mercadolibre/exchange-code`（
  `code_or_url`：授权 code 或完整回调 URL，可选
  `code`：授权 code 别名，可选
  `app_id`：Mercado Libre App ID，可选
  `app_secret`：App Secret，可选
  `client_secret`：Client Secret 别名，可选
  `redirect_uri`：OAuth 回调地址，可选
  `code_verifier`：PKCE 校验码，可选
  `site_id`：站点 ID，可选
  ）；用 Mercado Libre 授权 code 或回调 URL 换取 token。

- `POST /api/mercadolibre/refresh-token`（
  请求体对象：刷新 token 所需授权字段
  ）；刷新 Mercado Libre access token。

- `POST /api/mercadolibre/real-auth-test`（
  `product_id`：商品 ID，必填
  `mode`：测试模式，可选
  `category_id`：类目 ID，可选
  ）；运行 Mercado Libre 真实授权或发布前 API 测试。

- `POST /api/test-store-auth`（
  `platform`：平台标识，必填
  `scope`：测试范围，可选
  ）；测试平台店铺授权配置。

- `POST /api/test-api-config`（
  `kind`：配置类型，必填
  `config`：待测配置对象，必填
  `test_value`：测试值，可选
  ）；测试某类外部 API 配置。

- `POST /api/save-settings`（
  `appConfig`：应用配置对象，可选
  `storeConfig`：平台店铺配置对象，可选
  ）；保存应用配置和平台店铺配置。

### 类目与属性


- `POST /api/category-attrs`（
  `platform`：平台标识，可选，默认 `mercadolibre`
  `category_id`：类目 ID，可选
  ）；读取指定平台类目的必填属性，Mercado Libre 有 token 时可实时拉取。

- `POST /api/category-search`（
  `platform`：平台标识，可选，默认 `mercadolibre`
  `site`：站点或国家，可选
  `country`：站点或国家别名，可选
  `query`：搜索关键词，可选
  `keyword`：搜索关键词别名，可选
  `limit`：结果数量，可选
  ）；从本地类目缓存搜索类目候选。

- `POST /api/category-ai-suggest`（
  `product_id`：商品 ID，必填
  `platform`：平台标识，可选
  `site`：站点或国家，可选
  `country`：站点或国家别名，可选
  `limit`：建议数量，可选
  ）；用 AI 和商品上下文为商品建议类目 ID。

- `POST /api/category-cache/refresh`（
  `platform`：平台标识，可选
  `site`：站点或国家，可选
  `country`：站点或国家别名，可选
  `max_categories`：最大刷新类目数量，可选
  ）；同步刷新平台类目缓存或读取当前本地缓存。

- `POST /api/category-cache/refresh-job`（
  `platform`：平台标识，可选
  `site`：站点或国家，可选
  `country`：站点或国家别名，可选
  `max_categories`：最大刷新类目数量，可选
  ）；启动异步类目缓存刷新任务。

- `POST /api/category-ai-fill`（
  `product_id`：商品 ID，必填
  `platform`：平台标识，可选
  `category_id`：类目 ID，可选
  `category_record`：类目记录对象，可选
  ）；用 AI 为当前商品草稿补齐类目属性。

- `POST /api/category-attribute-translations`（
  `platform`：平台标识，可选
  `category_id`：类目 ID，可选
  `category_path`：类目路径，可选
  `language`：目标语言，可选，默认 `zh-CN`
  `attributes`：属性列表对象，可选
  ）；翻译类目属性名称和值，用于中文辅助显示。

- `POST /api/category-result-translations`（
  `platform`：平台标识，可选
  `language`：目标语言，可选，默认 `zh-CN`
  `categories`：类目结果列表对象，可选
  ）；翻译类目搜索结果，用于中文辅助显示。

- `POST /api/category-precheck`（
  `product_id`：商品 ID，必填
  `platform`：平台标识，可选
  `category_id`：类目 ID，可选
  `category_record`：类目记录对象，可选
  ）；校验商品在指定类目下是否缺少必填属性。

### 商品、草稿与核价


- `POST /api/calculate-price`（
  `platform`：目标平台，可选
  `site`：站点 ID，可选
  `purchase_cost`：采购成本，可选
  `domestic_freight`：国内运费，可选
  `weight_kg`：重量 kg，可选
  `length_cm`：长 cm，可选
  `width_cm`：宽 cm，可选
  `height_cm`：高 cm，可选
  `commission_percent`：佣金比例，可选
  `target_margin_percent`：目标利润率，可选
  `usd_cny_rate`：美元兑人民币汇率，可选
  `mxn_usd_rate`：墨西哥比索兑美元汇率，可选
  `rub_cny_rate`：卢布兑人民币汇率，可选
  `exchange_rate_mode`：汇率模式，可选
  `force_exchange_rate_refresh`：是否强制刷新实时汇率，可选
  `display_currency_mode`：展示币种模式，可选
  ）；根据成本、尺寸、重量、佣金和汇率计算建议售价与利润。

- `POST /api/assign-upc`（
  无参数
  ）；为当前商品分配或写入 UPC。

- `POST /api/save-product`（
  `product`：商品对象，必填
  ）；保存商品资料到本地库。该接口只保存商品来源事实、供应链和内部字段，不保存或覆盖平台草稿 `drafts`。

- `POST /api/load-product`（
  `product_id`：商品 ID，可选
  `product_file_path`：商品文件路径，可选
  ）；按商品 ID 或路径加载商品完整数据。

- `POST /api/load-draft`（
  `draft_id`：草稿 ID，必填
  `draftId`：草稿 ID 别名，可选
  ）；按草稿 ID 加载单条平台草稿，返回 `draft` 和只读 `productContext` 参考信息，不返回整包商品编辑对象。

- `POST /api/save-draft`（
  `draft`：平台草稿对象，必填，需包含 `draft_id`
  ）；按 `draft_id` 保存单条平台草稿，只更新该条 `platform_drafts` 记录，不覆盖同商品下其他平台草稿，也不更新商品公共资料。

- `POST /api/delete-draft`（
  `draft_ids`：草稿 ID 列表，可选
  `draftIds`：草稿 ID 列表别名，可选
  `draft_id`：单个草稿 ID，可选
  `draftId`：单个草稿 ID 别名，可选
  ）；删除一个或多个平台草稿。

- `POST /api/delete-products`（
  `product_ids`：商品 ID 列表，必填
  ）；删除一个或多个商品记录。

### 图片


- `POST /api/image-pool/upload`（
  `product_id`：商品 ID，必填
  `uploads`：上传图片列表对象，必填
  ）；上传参考图或商品图并加入商品图片池。

- `POST /api/image-pool/save`（
  `product_id`：商品 ID，必填
  `image_pool`：图片池列表对象，必填
  ）；保存指定商品的完整图片池列表。

- `POST /api/image-pool/action`（
  `product_id`：商品 ID，必填
  `action`：图片动作，必填
  `uploads`：上传图片列表对象，可选
  `ordered_ids`：排序后的图片 ID 列表，可选
  `image_ids`：图片 ID 列表，可选
  `ids`：图片 ID 列表别名，可选
  `delete_files`：是否删除本地文件，可选
  `image_id`：单张图片 ID，可选
  `replacement`：替换图片对象，可选
  `sku`：SKU，可选
  `sku_id`：SKU 别名，可选
  `platform`：过滤平台，可选
  `selected_only`：是否只保留已选图片，可选
  ）；对图片池执行选择、删除、过滤、设主图等动作。

- `POST /api/image-pool/sync-generated`（
  `product_id`：商品 ID，必填
  ）；把生成图区图片同步合并到当前商品图片池。

- `POST /api/image-translate`（
  `product_id`：商品 ID，必填
  `source_image_ids`：商品图片池资产 ID 列表，可选
  `language`：目标语言，可选
  `target_language`：目标语言别名，可选
  `platform`：目标平台，可选
  `mode`：图片处理模式，可选
  `draft_id`：草稿 ID，可选
  `apply_to_draft`：是否把翻译图写入草稿图片引用，可选
  `draft_image_strategy`：写入策略，可选，支持 `append`、`replace_selected`、`replace_all`
  ）；按目标语言翻译或改写选中商品图片。

- `POST /api/image-edit`（
  `product_id`：商品 ID，必填
  `source_image_ids`：商品图片池资产 ID 列表，必填
  `prompt`：用户本次图生图提示词，必填
  `platform`：目标平台，可选
  `draft_id`：草稿 ID，可选
  `apply_to_draft`：是否把新图追加写入草稿图片引用，可选
  `draft_image_strategy`：写入策略，可选，支持 `append`、`replace_selected`、`replace_all`
  ）；用用户提示词和选中源图调用图片 AI provider 生成新图片，并写入商品图片池。

### 发布


- `POST /api/publish-precheck`（
  `product_id`：商品 ID，必填
  `platforms`：平台列表，可选
  `platform`：单个平台别名，可选
  ）；运行发布前校验并写回平台草稿状态。

- `POST /api/publish-payload-preview`（
  `product_id`：商品 ID，必填
  `platform`：目标平台，可选
  ）；生成指定平台发布 payload 预览。

- `POST /api/publish-product`（
  `product_id`：商品 ID，必填
  `platform`：目标平台，可选
  ）；执行平台发布或直接发布调用。

- `POST /api/mercadolibre/confirm-real-publish`（
  `product_id`：商品 ID，必填
  `confirm_real_publish`：是否确认真实发布，可选
  `confirm`：确认字段别名，可选
  ）；确认 Mercado Libre 真实发布。

- `POST /api/mercadolibre/close-item`（
  `item_id`：远程商品 ID，可选
  `id`：远程商品 ID 别名，可选
  ）；结束或下架 Mercado Libre 远程商品。

- `POST /api/publish-bus/enqueue`（
  `product_id`：商品 ID，必填
  `platforms`：发布平台列表，可选
  ）；把商品发布任务加入本地发布队列。

### 选品调研


- `POST /api/v1/product-research/hot-products/search`（
  `markets`：目标市场请求对象，可选
  `result_options`：结果选项对象，可选
  `target_market_ids`：目标市场 ID 列表，可选
  `targetMarketIds`：目标市场 ID 列表别名，可选
  `market_id`：单个目标市场 ID，可选
  `marketId`：单个目标市场 ID 别名，可选
  ）；创建热点商品调研运行并返回 run 信息。

- `POST /api/v1/product-research/source-registry/save`（
  `config`：选品调研配置对象，可选
  `search_defaults`：搜索默认配置对象，可选
  `provider_runtime`：来源运行时配置对象，可选
  `search_providers`：搜索来源列表对象，可选
  `target_markets`：目标市场列表对象，可选
  `source_registry`：来源注册表对象，可选
  ）；保存选品调研来源注册表、目标市场和搜索方法配置。

- `POST /api/v1/product-research/search-providers/test`（
  `provider`：搜索来源配置对象，必填
  `options`：测试选项对象，可选
  `market`：测试市场，可选
  `language`：测试语言，可选
  `keyword`：测试关键词，可选
  ）；测试单个选品搜索来源或供应商配置。

### 物流


- `POST /api/logistics/yunexpress/preview`（
  `config`：云途配置对象，可选
  `yunexpress`：云途配置对象别名，可选
  `shipment`：发货单对象，可选
  `order`：发货单对象别名，可选
  `payload`：发货单对象别名，可选
  ）；预览云途 YunExpress 发货请求和校验结果。

- `POST /api/logistics/yunexpress/create-shipment`（
  `config`：云途配置对象，可选
  `yunexpress`：云途配置对象别名，可选
  `shipment`：发货单对象，可选
  `order`：发货单对象别名，可选
  `payload`：发货单对象别名，可选
  ）；创建云途 YunExpress 运单。

### Webhook


- `POST /api/mercadolibre/notifications`（
  请求体对象：Mercado Libre webhook 通知对象
  ）；接收 Mercado Libre 订单或资源通知 webhook 并记录。

## 页面与静态入口


- `GET /`（
  无参数
  ）；打开工作台首页。

- `GET /research`（
  无参数
  ）；打开选品调研页面。

- `GET /collect`（
  无参数
  ）；打开采集页面。

- `GET /library`（
  无参数
  ）；打开商品库页面。

- `GET /drafts`（
  无参数
  ）；打开平台草稿箱页面。

- `GET /ml-items`（
  无参数
  ）；打开 Mercado Libre 已发布商品页面。

- `GET /edit`（
  无参数
  ）；打开商品编辑入口页面。

- `GET /media`（
  无参数
  ）；打开媒体或图片编辑入口页面。

- `GET /pricing`（
  无参数
  ）；打开核价页面。

- `GET /publish`（
  无参数
  ）；打开发布预检或发布队列页面。

- `GET /pending`（
  无参数
  ）；打开待处理商品页面。

- `GET /settings`（
  无参数
  ）；打开设置页面。

- `GET /auth`（
  无参数
  ）；打开平台授权页面。

- `GET /logs`（
  无参数
  ）；打开发布日志页面。

- `GET /assets/*`（
  路径通配符：前端资源相对路径，必填
  ）；读取前端构建后的静态资源。

- `GET /file`（
  `path`：本地文件路径，必填
  ）；读取受限目录下的本地图片、缓存、导出或输出文件。

- `GET /auth/mercadolibre`（
  无参数
  ）；展示 Mercado Libre 授权说明页。

- `GET /auth/wildberries`（
  无参数
  ）；展示 Wildberries 授权说明页。

- `GET /auth/ozon`（
  无参数
  ）；展示 Ozon 授权说明页。

- `GET /auth/mercadolibre/callback`（
  `code`：授权 code，可选
  `app_id`：Mercado Libre App ID，可选
  `app_secret`：App Secret，可选
  `client_secret`：Client Secret 别名，可选
  `redirect_uri`：OAuth 回调地址，可选
  `code_verifier`：PKCE 校验码，可选
  `site_id`：站点 ID，可选
  ）；接收 Mercado Libre OAuth 回调并尝试自动换 token。
