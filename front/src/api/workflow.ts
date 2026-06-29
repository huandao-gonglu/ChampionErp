import { API_REQUEST_TIMEOUT_MS, apiClient } from '@/api/client'
import { listingLanguageLabel } from '@/constants/locales'
import type {
  BrowserDebugStatus,
  CategoryAttributeTranslations,
  CategoryPrecheckResult,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  CollectBatchRow,
  DraftIndexItem,
  CollectForm,
  ImageAsset,
  Marketplace,
  MercadoLibreOrderItem,
  MercadoLibreOrderLine,
  MercadoLibreOrdersPage,
  MercadoLibrePublishedPage,
  MercadoLibreRemoteItem,
  MercadoLibreAuthChecklist,
  MercadoLibreTestMode,
  HotProductCandidate,
  PricingInput,
  PricingResult,
  Product,
  ProductIndexItem,
  ProductResearchConfig,
  ProductResearchMarketSearchMethodBinding,
  ProductResearchProviderTestResult,
  ProductResearchResponse,
  ProductResearchSourceRegistryItem,
  ProductResearchSourceStatus,
  ProductResearchTargetMarket,
  ProductResearchRunSummary,
  PublishJob,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'
import type {
  AiPublicConfig,
  AppStateResponse,
  AuthResult,
  DeleteProductsResult,
  PayloadPreviewResult,
  ProductMutationResponse,
  ProductOperationResult,
} from './workflow/normalizers'
import {
  asRecord,
  ensureOk,
  getBoolean,
  getNumber,
  getString,
  isRecord,
  normalizeBackendProduct,
  normalizeBrowserStatus,
  normalizeDeleteProductsResult,
  normalizeDraftsIndex,
  normalizeMercadoLibreAuthChecklist,
  normalizeMercadoLibreOrderNotification,
  normalizeProductMutation,
  normalizeProductOperation,
  normalizeProductsIndex,
  normalizePublishLogs,
  platformList,
  precheckIssueSummary,
  precheckIssues,
  stringList,
  toBackendImageAsset,
  toBackendProduct,
} from './workflow/normalizers'

const IMAGE_TRANSLATE_TIMEOUT_PER_IMAGE_MS = API_REQUEST_TIMEOUT_MS

function imageTranslateTimeoutMs(product: Product): number {
  const selectedCount = product.source.imagePool.filter((image) => image.selected).length
  const imageCount = selectedCount || product.source.imagePool.length || 1
  return IMAGE_TRANSLATE_TIMEOUT_PER_IMAGE_MS * imageCount
}

export type {
  AiPublicConfig,
  AppStateResponse,
  AuthResult,
  DeleteProductsResult,
  PayloadPreviewResult,
  ProductMutationResponse,
  ProductOperationResult,
} from './workflow/normalizers'
export {
  diagnosticsToCollectDiagnostics,
  normalizeBackendProduct,
  normalizeImageAsset,
  toBackendProduct,
} from './workflow/normalizers'

export async function fetchState(): Promise<AppStateResponse> {
  const response = await apiClient.get('/api/state')
  const data = asRecord(response.data)
  ensureOk(data, '读取状态失败')
  const product = normalizeBackendProduct(data.product, data.imagePool)
  return {
    product,
    imagePool: product.source.imagePool,
    appConfig: asRecord(data.appConfig),
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
    mercadolibreAuthChecklist: data.mercadolibreAuthChecklist ? normalizeMercadoLibreAuthChecklist(data.mercadolibreAuthChecklist) : null,
    mercadolibreOrderNotifications: Array.isArray(data.mercadolibreOrderNotifications)
      ? data.mercadolibreOrderNotifications.map(normalizeMercadoLibreOrderNotification)
      : [],
    outputDir: getString(data, ['outputDir']),
    productsIndex: normalizeProductsIndex(data.productsIndex),
    draftsIndex: normalizeDraftsIndex(data.draftsIndex),
    publishLogs: normalizePublishLogs(data.publishLogs),
  }
}

export async function fetchProductsIndex(): Promise<ProductIndexItem[]> {
  const response = await apiClient.get('/api/products-index')
  const data = asRecord(response.data)
  ensureOk(data, '读取商品库失败')
  return normalizeProductsIndex(data.items)
}

export async function fetchDraftsIndex(scope = 'active'): Promise<DraftIndexItem[]> {
  const params = new URLSearchParams({ scope })
  const response = await apiClient.get(`/api/drafts-index?${params.toString()}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取草稿箱失败')
  return normalizeDraftsIndex(data.items)
}

export async function fetchPublishLogs(): Promise<PublishLogItem[]> {
  const response = await apiClient.get('/api/publish-logs')
  const data = asRecord(response.data)
  ensureOk(data, '读取发布日志失败')
  return normalizePublishLogs(data.items)
}

function normalizeMercadoLibreOrderLine(value: unknown): MercadoLibreOrderLine {
  const record = asRecord(value)
  return {
    itemId: getString(record, ['item_id', 'itemId']),
    title: getString(record, ['title']),
    sellerSku: getString(record, ['seller_sku', 'sellerSku']),
    quantity: getString(record, ['quantity']),
  }
}

function normalizeMercadoLibreOrderItem(value: unknown): MercadoLibreOrderItem {
  const record = asRecord(value)
  return {
    id: getString(record, ['id']),
    status: getString(record, ['status']),
    statusDetail: getString(record, ['status_detail', 'statusDetail']),
    dateCreated: getString(record, ['date_created', 'dateCreated']),
    dateClosed: getString(record, ['date_closed', 'dateClosed']),
    lastUpdated: getString(record, ['last_updated', 'lastUpdated']),
    totalAmount: getNumber(record, ['total_amount', 'totalAmount']),
    paidAmount: getNumber(record, ['paid_amount', 'paidAmount']),
    currencyId: getString(record, ['currency_id', 'currencyId']),
    buyerId: getString(record, ['buyer_id', 'buyerId']),
    buyerNickname: getString(record, ['buyer_nickname', 'buyerNickname']),
    shippingId: getString(record, ['shipping_id', 'shippingId']),
    shippingStatus: getString(record, ['shipping_status', 'shippingStatus']),
    paymentStatuses: stringList(record.payment_statuses ?? record.paymentStatuses),
    items: Array.isArray(record.items) ? record.items.map(normalizeMercadoLibreOrderLine) : [],
    itemTitles: stringList(record.item_titles ?? record.itemTitles),
    itemIds: stringList(record.item_ids ?? record.itemIds),
    raw: record,
  }
}

export async function fetchMercadoLibreOrders(limit = 10, offset = 0): Promise<MercadoLibreOrdersPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  const response = await apiClient.get(`/api/mercadolibre/orders?${params.toString()}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取 Mercado Libre 订单失败')
  const pagination = asRecord(data.pagination)
  return {
    items: Array.isArray(data.items) ? data.items.map(normalizeMercadoLibreOrderItem) : [],
    notifications: Array.isArray(data.notifications) ? data.notifications.map(normalizeMercadoLibreOrderNotification) : [],
    total: getNumber(pagination, ['total']),
    checkedAt: getString(data, ['checked_at', 'checkedAt']),
  }
}

function normalizeMercadoLibreRemoteItem(value: unknown): MercadoLibreRemoteItem {
  const record = asRecord(value)
  return {
    id: getString(record, ['id']),
    title: getString(record, ['title']),
    status: getString(record, ['status']),
    subStatus: stringList(record.sub_status ?? record.subStatus),
    permalink: getString(record, ['permalink']),
    thumbnail: getString(record, ['thumbnail']),
    price: getNumber(record, ['price']),
    currencyId: getString(record, ['currency_id', 'currencyId']),
    availableQuantity: getNumber(record, ['available_quantity', 'availableQuantity']),
    soldQuantity: getNumber(record, ['sold_quantity', 'soldQuantity']),
    categoryId: getString(record, ['category_id', 'categoryId']),
    listingTypeId: getString(record, ['listing_type_id', 'listingTypeId']),
    sellerSku: getString(record, ['seller_sku', 'sellerSku']),
    dateCreated: getString(record, ['date_created', 'dateCreated']),
    lastUpdated: getString(record, ['last_updated', 'lastUpdated']),
    raw: record,
  }
}

function normalizeMercadoLibrePagination(value: unknown, fallbackPage: number, fallbackPerPage: number) {
  const record = asRecord(value)
  const total = getNumber(record, ['total'])
  const perPage = getNumber(record, ['per_page', 'perPage']) || fallbackPerPage
  const page = getNumber(record, ['page']) || fallbackPage
  const totalPages = getNumber(record, ['total_pages', 'totalPages']) || Math.max(1, Math.ceil(total / Math.max(1, perPage)))
  return {
    page,
    perPage,
    offset: getNumber(record, ['offset']),
    total,
    totalPages,
    hasPrev: getBoolean(record, ['has_prev', 'hasPrev']) || page > 1,
    hasNext: getBoolean(record, ['has_next', 'hasNext']) || (total > 0 && page < totalPages),
  }
}

export async function fetchMercadoLibrePublishedItems(status = 'active', page = 1, perPage = 50): Promise<MercadoLibrePublishedPage> {
  const params = new URLSearchParams({
    status,
    page: String(page),
    per_page: String(perPage),
  })
  const response = await apiClient.get(`/api/mercadolibre/published-items?${params.toString()}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取 Mercado Libre 已发布商品失败')
  return {
    items: Array.isArray(data.items) ? data.items.map(normalizeMercadoLibreRemoteItem) : [],
    pagination: normalizeMercadoLibrePagination(data.pagination, page, perPage),
  }
}

function normalizeHotProductCandidate(value: unknown): HotProductCandidate {
  const record = asRecord(value)
  const priceRecord = asRecord(record.price)
  const amount = getNumber(priceRecord, ['amount'])
  return {
    id: getString(record, ['id']),
    title: getString(record, ['title']),
    imageUrl: getString(record, ['image_url', 'imageUrl']),
    rank: getNumber(record, ['rank']),
    sourceUrl: getString(record, ['source_url', 'sourceUrl']),
    marketId: getString(record, ['market_id', 'marketId', 'market']),
    platform: getString(record, ['platform']),
    site: getString(record, ['site']),
    keyword: getString(record, ['keyword']),
    price: amount
      ? {
          amount,
          currency: getString(priceRecord, ['currency']),
        }
      : undefined,
    rating: getNumber(record, ['rating']),
    reviewCount: getNumber(record, ['review_count', 'reviewCount']),
    hotScore: getNumber(record, ['hot_score', 'hotScore']),
    sourceName: getString(record, ['source_name', 'sourceName']),
    collectedAt: getString(record, ['collected_at', 'collectedAt']),
    raw: record,
  }
}

function normalizeProductResearchSourceStatus(value: unknown): ProductResearchSourceStatus {
  const record = asRecord(value)
  return {
    source: getString(record, ['source']),
    sourceId: getString(record, ['source_id', 'sourceId']),
    market: getString(record, ['market']),
    status: getString(record, ['status']),
    itemsFound: getNumber(record, ['items_found', 'itemsFound']),
    errorMessage: getString(record, ['error_message', 'errorMessage']),
    providerStrategy: getString(record, ['provider_strategy', 'providerStrategy']),
    rawItemsFound: getNumber(record, ['raw_items_found', 'rawItemsFound']),
    itemsFiltered: getNumber(record, ['items_filtered', 'itemsFiltered']),
    diagnosticMessage: getString(record, ['diagnostic_message', 'diagnosticMessage']),
    raw: record,
  }
}

function normalizeProductResearchRun(value: unknown): ProductResearchRunSummary {
  const record = asRecord(value)
  return {
    runId: getString(record, ['run_id', 'runId']),
    status: getString(record, ['status']),
    searchMode: getString(record, ['search_mode', 'searchMode']),
    createdAt: getString(record, ['created_at', 'createdAt']),
    completedAt: getString(record, ['completed_at', 'completedAt']),
    description: getString(record, ['description']),
    progressDescription: getString(record, ['progress_description', 'progressDescription']),
    request: asRecord(record.request),
    raw: record,
  }
}

function normalizeProductResearchResponse(value: unknown): ProductResearchResponse {
  const record = asRecord(value)
  ensureOk(record, '选品搜索失败')
  return {
    run: normalizeProductResearchRun(record.run),
    items: Array.isArray(record.items) ? record.items.map(normalizeHotProductCandidate) : [],
    sourceStatus: Array.isArray(record.source_status)
      ? record.source_status.map(normalizeProductResearchSourceStatus)
      : Array.isArray(record.sourceStatus)
        ? record.sourceStatus.map(normalizeProductResearchSourceStatus)
        : [],
    description: getString(record, ['description']) || getString(asRecord(record.run), ['description']),
    raw: record,
  }
}

export async function createProductResearchHotProductRun(payload: UnknownRecord): Promise<ProductResearchResponse> {
  const response = await apiClient.post('/api/v1/product-research/hot-products/search', payload)
  return normalizeProductResearchResponse(response.data)
}

export async function fetchProductResearchHotProductRun(runId: string): Promise<ProductResearchResponse> {
  const params = new URLSearchParams({ run_id: runId })
  const response = await apiClient.get(`/api/v1/product-research/hot-products/runs?${params.toString()}`)
  return normalizeProductResearchResponse(response.data)
}

export async function fetchActiveProductResearchHotProductRun(): Promise<ProductResearchResponse | null> {
  const response = await apiClient.get('/api/v1/product-research/hot-products/runs')
  const data = asRecord(response.data)
  ensureOk(data, '读取选品运行状态失败')
  return data.run ? normalizeProductResearchResponse(data) : null
}

function normalizeProductResearchProvider(value: unknown): ProductResearchSourceRegistryItem {
  const record = asRecord(value)
  const config = asRecord(record.config_json ?? record.configJson)
  const sourceType = getString(record, ['source_type', 'sourceType'])
  return {
    id: getString(record, ['id', 'source_id', 'sourceId']),
    name: getString(record, ['name']),
    sourceType: sourceType === 'third_party_api' ? 'api' : sourceType || 'api',
    platform: getString(record, ['platform']),
    enabled: getBoolean(record, ['enabled']),
    priority: getNumber(record, ['priority']),
    supportedMarkets: stringList(record.supported_markets ?? record.supportedMarkets),
    supportedLanguages: stringList(record.supported_languages ?? record.supportedLanguages),
    supportedDataTypes: stringList(record.supported_data_types ?? record.supportedDataTypes),
    authRequired: getBoolean(record, ['auth_required', 'authRequired']),
    rateLimitPerMinute: getNumber(record, ['rate_limit_per_minute', 'rateLimitPerMinute']),
    complianceNote: getString(record, ['compliance_note', 'complianceNote']),
    providerStrategy: getString(config, ['provider_strategy', 'providerStrategy']),
    configJson: config,
    raw: record,
  }
}

function normalizeProductResearchTargetMarket(value: unknown): ProductResearchTargetMarket {
  const record = asRecord(value)
  const rawSearchMethods = Array.isArray(record.search_methods)
    ? record.search_methods
    : Array.isArray(record.searchMethods)
      ? record.searchMethods
      : []
  return {
    id: getString(record, ['id', 'market_id', 'marketId', 'market', 'code']),
    platform: getString(record, ['platform']).toLowerCase(),
    site: getString(record, ['site']).toLowerCase(),
    displayName: getString(record, ['display_name', 'displayName', 'name']),
    searchMethods: rawSearchMethods.map(normalizeProductResearchMarketSearchMethodBinding),
    raw: record,
  }
}

function normalizeProductResearchMarketSearchMethodBinding(value: unknown): ProductResearchMarketSearchMethodBinding {
  const record = asRecord(value)
  return {
    methodId: getString(record, ['method_id', 'methodId', 'provider_id', 'providerId', 'id']),
    enabled: getBoolean(record, ['enabled'], true),
    configJson: asRecord(record.config_json ?? record.configJson),
    raw: record,
  }
}

function normalizeProductResearchConfig(value: unknown): ProductResearchConfig {
  const record = asRecord(value)
  const rawProviders = Array.isArray(record.search_providers)
    ? record.search_providers
    : Array.isArray(record.searchProviders)
      ? record.searchProviders
      : Array.isArray(record.source_registry)
        ? record.source_registry
        : []
  const rawMarkets = Array.isArray(record.target_markets)
    ? record.target_markets
    : Array.isArray(record.targetMarkets)
      ? record.targetMarkets
      : []
  const rawRegistry = Array.isArray(record.source_registry)
    ? record.source_registry
    : Array.isArray(record.sourceRegistry)
      ? record.sourceRegistry
      : rawProviders
  return {
    searchProviders: rawProviders.map(normalizeProductResearchProvider),
    targetMarkets: rawMarkets.map(normalizeProductResearchTargetMarket),
    sourceRegistry: rawRegistry.map(normalizeProductResearchProvider),
    raw: record,
  }
}

function toProductResearchProviderPayload(provider: ProductResearchSourceRegistryItem): UnknownRecord {
  return {
    id: provider.id.trim(),
    name: provider.name.trim() || provider.id.trim(),
    source_type: provider.sourceType || 'api',
    platform: provider.platform.trim().toLowerCase() || provider.id.trim().toLowerCase(),
    enabled: provider.enabled,
    priority: provider.priority || 10,
    supported_markets: provider.supportedMarkets,
    supported_languages: provider.supportedLanguages,
    supported_data_types: provider.supportedDataTypes,
    auth_required: provider.authRequired,
    rate_limit_per_minute: provider.rateLimitPerMinute || null,
    compliance_note: provider.complianceNote,
    config_json: {
      ...provider.configJson,
      provider_strategy: provider.providerStrategy || String(provider.configJson.provider_strategy || ''),
    },
  }
}

function toProductResearchTargetMarketPayload(market: ProductResearchTargetMarket): UnknownRecord {
  return {
    id: market.id.trim(),
    platform: market.platform.trim().toLowerCase(),
    site: market.site.trim().toLowerCase(),
    display_name: market.displayName.trim() || market.id.trim(),
    search_methods: (market.searchMethods || []).map((binding) => ({
      method_id: binding.methodId.trim(),
      enabled: binding.enabled,
      config_json: binding.configJson,
    })),
  }
}

export async function fetchProductResearchSettings(): Promise<ProductResearchConfig> {
  const response = await apiClient.get('/api/v1/product-research/source-registry')
  const data = asRecord(response.data)
  ensureOk(data, '读取选品数据源失败')
  return normalizeProductResearchConfig(data.config)
}

export async function saveProductResearchSettings(config: ProductResearchConfig): Promise<ProductResearchConfig> {
  const response = await apiClient.post('/api/v1/product-research/source-registry/save', {
    config: {
      search_providers: config.searchProviders.map(toProductResearchProviderPayload),
      target_markets: config.targetMarkets.map(toProductResearchTargetMarketPayload),
    },
  })
  const data = asRecord(response.data)
  ensureOk(data, '保存选品数据源失败')
  return normalizeProductResearchConfig(data.config)
}

function normalizeProductResearchProviderTestResult(value: unknown): ProductResearchProviderTestResult {
  const record = asRecord(value)
  return {
    ok: getBoolean(record, ['ok']),
    status: getString(record, ['status']),
    sourceId: getString(record, ['source_id', 'sourceId']),
    providerStrategy: getString(record, ['provider_strategy', 'providerStrategy']),
    market: getString(record, ['market']),
    keyword: getString(record, ['keyword']),
    itemsFound: getNumber(record, ['items_found', 'itemsFound']),
    durationMs: getNumber(record, ['duration_ms', 'durationMs']),
    error: getString(record, ['error', 'error_message', 'errorMessage']),
    sample: asRecord(record.sample),
    raw: record,
  }
}

export async function testProductResearchSearchProvider(provider: ProductResearchSourceRegistryItem, options: UnknownRecord): Promise<ProductResearchProviderTestResult> {
  const response = await apiClient.post('/api/v1/product-research/search-providers/test', {
    provider: toProductResearchProviderPayload(provider),
    options,
  })
  return normalizeProductResearchProviderTestResult(response.data)
}

export async function completeProductResearchProviderByAi(provider: ProductResearchSourceRegistryItem, modelId = ''): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/v1/product-research/search-providers/ai-complete', {
    provider: toProductResearchProviderPayload(provider),
    model_id: modelId,
  })
  const data = asRecord(response.data)
  ensureOk(data, 'AI 补全搜索手段失败')
  return asRecord(data.suggestion)
}

export async function fetchProductResearchSourceRegistry(): Promise<ProductResearchSourceRegistryItem[]> {
  const config = await fetchProductResearchSettings()
  return config.sourceRegistry
}

export async function closeMercadoLibrePublishedItem(itemId: string): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/mercadolibre/close-item', { item_id: itemId })
  const data = asRecord(response.data)
  ensureOk(data, '删除 Mercado Libre 商品失败')
  return data
}

export async function saveProduct(product: Product): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/save-product', { product: toBackendProduct(product) })
  return normalizeProductMutation(response.data)
}

export async function loadProduct(productId: string, productFilePath = ''): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/load-product', { product_id: productId, product_file_path: productFilePath })
  return normalizeProductMutation(response.data)
}

export async function loadDraft(draftId: string): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/load-draft', { draft_id: draftId })
  return normalizeProductMutation(response.data)
}

export async function deleteDraft(draftId: string): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/delete-draft', { draft_id: draftId })
  return normalizeProductMutation(response.data)
}

export async function deleteProducts(productIds: string[]): Promise<DeleteProductsResult> {
  const response = await apiClient.post('/api/delete-products', { product_ids: productIds })
  return normalizeDeleteProductsResult(response.data)
}

export async function collectProduct(form: CollectForm): Promise<ProductMutationResponse> {
  const url = form.productUrl.trim()
  if (!url || form.mode === 'extension' || form.mode === 'manual') {
    return importManualProduct(form)
  }
  const path = form.platform.toLowerCase() === '1688' ? '/api/collect-1688' : '/api/collect-source'
  const response = await apiClient.post(path, {
    url,
    mode: form.mode,
    cookie: form.alibabaCookie,
    '1688_api': {
      app_key: form.alibabaAppKey,
      app_secret: form.alibabaAppSecret,
      access_token: form.alibabaAccessToken,
      base_url: form.alibabaApiBaseUrl,
      method: form.alibabaApiMethod,
      api_version: form.alibabaApiVersion,
      timeout_seconds: form.alibabaApiTimeoutSeconds,
    },
    platform: form.platform,
    platforms: form.selectedClaimPlatforms,
  })
  return normalizeProductMutation(response.data)
}

export async function clean1688Text(text: string, url = ''): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/collect-1688-clean', { text, html: text, url })
  const data = asRecord(response.data)
  ensureOk(data, '清洗 1688 文本失败')
  return data
}

export async function collectBatch(form: CollectForm): Promise<{ rows: CollectBatchRow[]; productsIndex: ProductIndexItem[] }> {
  const response = await apiClient.post('/api/collect-batch', {
    urls: form.productUrls,
    mode: form.mode === 'extension' ? 'manual' : form.mode,
    cookie: form.alibabaCookie,
    '1688_api': {
      app_key: form.alibabaAppKey,
      app_secret: form.alibabaAppSecret,
      access_token: form.alibabaAccessToken,
      base_url: form.alibabaApiBaseUrl,
      method: form.alibabaApiMethod,
      api_version: form.alibabaApiVersion,
      timeout_seconds: form.alibabaApiTimeoutSeconds,
    },
    platform: form.platform === 'manual' ? '' : form.platform,
    platforms: form.selectedClaimPlatforms,
  })
  const data = asRecord(response.data)
  ensureOk(data, '批量采集失败')
  const rows = Array.isArray(data.items)
    ? data.items.map((item) => {
      const record = asRecord(item)
      return {
        url: getString(record, ['url']),
        platform: getString(record, ['platform']),
        status: getString(record, ['status']),
        ok: getBoolean(record, ['ok']),
        title: getString(record, ['title']),
        image: getString(record, ['image']),
        error: getString(record, ['error']),
        errorCode: getString(record, ['error_code']),
        nextAction: getString(record, ['next_action']),
        productId: getString(record, ['product_id']),
        product: record.product ? normalizeBackendProduct(record.product) : undefined,
      }
    })
    : []
  return { rows, productsIndex: normalizeProductsIndex(data.productsIndex) }
}

export async function collectFromBrowserTab(form: CollectForm, saveOnly = false): Promise<ProductMutationResponse & { browserStatus?: BrowserDebugStatus }> {
  const response = await apiClient.post('/api/collect-from-browser-tab', {
    product_url: form.productUrl,
    platform_hint: form.platform === 'manual' ? '' : form.platform,
    platforms: form.selectedClaimPlatforms,
    save_only: saveOnly,
  })
  const data = asRecord(response.data)
  const result = normalizeProductMutation(data)
  return { ...result, browserStatus: data.browserStatus ? normalizeBrowserStatus(data.browserStatus) : undefined }
}

export async function open1688Browser(): Promise<string> {
  const response = await apiClient.post('/api/open-1688-browser', {})
  const data = asRecord(response.data)
  ensureOk(data, '打开 1688 浏览器失败')
  return getString(data, ['message'], '已打开浏览器会话')
}

export async function fetchBrowserDebugStatus(): Promise<BrowserDebugStatus> {
  const response = await apiClient.get('/api/browser-debug/status')
  return normalizeBrowserStatus(response.data)
}

export async function openBrowserProfile(): Promise<AuthResult> {
  const response = await apiClient.post('/api/browser-debug/open-profile', {})
  const data = asRecord(response.data)
  return normalizeAuthResult(data)
}

export async function importManualProduct(form: CollectForm): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/collect-extension-payload', {
    source_url: form.productUrl,
    platform: form.platform,
    title: form.manualTitle,
    price: form.manualPrice,
    bullets: form.manualBullets,
    description: form.manualDescription,
    dimensions: form.manualDimensions,
    weight: form.manualWeight,
    images: stringList(form.manualImages),
    raw_html_optional: form.rawText,
    platforms: form.selectedClaimPlatforms,
  })
  return normalizeProductMutation(response.data)
}

export async function claimProducts(productIds: string[], platforms: Marketplace[]): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/claim-products', { product_ids: productIds, platforms })
  const data = asRecord(response.data)
  ensureOk(data, '认领失败')
  if (productIds.length && getNumber(data, ['claimed_count']) <= 0) {
    const firstItem = asRecord(Array.isArray(data.items) ? data.items[0] : {})
    throw new Error(getString(firstItem, ['error'], '没有商品被推到平台草稿箱'))
  }
  return data
}

export async function saveCollectSettings(form: CollectForm): Promise<void> {
  const response = await apiClient.post('/api/save-settings', {
    appConfig: {
      alibaba_cookie: form.alibabaCookie,
      '1688_api': {
        app_key: form.alibabaAppKey,
        app_secret: form.alibabaAppSecret,
        access_token: form.alibabaAccessToken,
        base_url: form.alibabaApiBaseUrl,
        method: form.alibabaApiMethod,
        api_version: form.alibabaApiVersion,
        timeout_seconds: form.alibabaApiTimeoutSeconds,
      },
      collect_output_dir: form.outputDir,
      auto_ai_recognition: form.autoAiRecognition ? '1' : '0',
    },
  })
  ensureOk(asRecord(response.data), '保存设置失败')
}

export async function uploadImages(product: Product, uploads: Array<{ filename: string; data_url: string }>): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/upload', {
    product: toBackendProduct(product),
    uploads: uploads.map((upload, index) => ({
      ...upload,
      platforms: ['mercadolibre'],
      selected: true,
      is_main: index === 0 && product.source.imagePool.length === 0,
    })),
  })
  return normalizeProductMutation(response.data)
}

export async function saveImagePool(product: Product, imagePool: ImageAsset[]): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/save', {
    product_id: product.productId,
    product: toBackendProduct(product),
    image_pool: imagePool.map(toBackendImageAsset),
  })
  return normalizeProductMutation(response.data)
}

export async function imagePoolAction(product: Product, action: string, payload: UnknownRecord = {}): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/action', { product: toBackendProduct(product), action, ...payload })
  return normalizeProductMutation(response.data)
}

export async function syncGeneratedImages(product: Product): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/sync-generated', { product: toBackendProduct(product) })
  return normalizeProductMutation(response.data)
}

export async function generateCopy(product: Product, platform: Marketplace): Promise<ProductMutationResponse> {
  const draftLanguage = product.drafts[platform]?.language || listingLanguageLabel(platform)
  const response = await apiClient.post('/api/generate-copy', {
    platform,
    target_market: platform,
    language: draftLanguage,
    listing_language: draftLanguage,
    product: toBackendProduct(product),
  })
  return normalizeProductMutation(response.data)
}

export async function generateCopyBatch(productIds: string[], platform: Marketplace): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/generate-copy-batch', { product_ids: productIds, platform })
  const data = asRecord(response.data)
  ensureOk(data, '批量文案失败')
  const failedCount = getNumber(data, ['failed_count', 'failedCount'])
  if (failedCount > 0) {
    const failures = Array.isArray(data.items)
      ? data.items
        .map((item) => {
          const record = asRecord(item)
          if (record.ok === true) return ''
          const productId = getString(record, ['product_id', 'productId'])
          const error = getString(record, ['error', 'warning', 'message'], '生成失败')
          return [productId, error].filter(Boolean).join('：')
        })
        .filter(Boolean)
      : []
    throw new Error(failures.length ? failures.join('；') : `批量文案失败 ${failedCount} 个商品`)
  }
  return data
}

export async function generateImagePrompts(product: Product, platform: Marketplace, targetLanguage = ''): Promise<string> {
  const listingLanguage = targetLanguage || product.drafts[platform]?.language || listingLanguageLabel(platform)
  const response = await apiClient.post('/api/generate-image-prompts', {
    product: toBackendProduct(product),
    platform,
    language: listingLanguage,
    target_language: listingLanguage,
    selected_image_ids: product.source.imagePool.filter((image) => image.selected).map((image) => image.id),
    include_bullets: true,
    include_description: true,
  })
  const data = asRecord(response.data)
  ensureOk(data, '生成图片提示词失败')
  return getString(data, ['prompt'])
}

export async function imageTranslate(product: Product, platform: Marketplace, language: string): Promise<ProductMutationResponse> {
  const listingLanguage = language || product.drafts[platform]?.language || listingLanguageLabel(platform)
  const selectedImageIds = product.source.imagePool.filter((image) => image.selected).map((image) => image.id)
  const response = await apiClient.post('/api/image-translate', {
    product: toBackendProduct(product),
    platform,
    language: listingLanguage,
    target_language: listingLanguage,
    image_ids: selectedImageIds,
  }, { timeout: imageTranslateTimeoutMs(product) })
  return normalizeProductMutation(response.data)
}

export async function calculatePrice(input: PricingInput): Promise<PricingResult> {
  const response = await apiClient.post('/api/calculate-price', {
    platform: input.platform,
    site: input.site,
    purchase_cost: input.purchaseCostCny,
    domestic_freight: input.domesticFreightCny,
    weight_kg: input.weightKg,
    length_cm: input.lengthCm,
    width_cm: input.widthCm,
    height_cm: input.heightCm,
    commission_percent: input.commissionPercent,
    target_margin_percent: input.targetMarginPercent,
    usd_cny_rate: input.exchangeRateMode === 'manual' ? input.usdCnyRate : '',
    mxn_usd_rate: input.exchangeRateMode === 'manual' ? input.mxnUsdRate : '',
    exchange_rate_mode: input.exchangeRateMode,
    display_currency_mode: input.displayCurrencyMode,
  })
  const data = asRecord(response.data)
  ensureOk(data, '核价失败')
  const backendInput = asRecord(data.input)
  const exchangeRates = asRecord(data.exchange_rates)
  const rates = asRecord(exchangeRates.rates)
  const suggestedPriceUsd = getNumber(data, ['suggested_price_usd', 'sale_price_usd', 'price_usd'])
  const usdCnyRate = getNumber(backendInput, ['usd_cny_rate'], getNumber(rates, ['usd_cny_rate']))
  return {
    suggestedPriceMxn: getNumber(data, ['suggested_price_mxn', 'sale_price_mxn', 'price_mxn']),
    suggestedPriceUsd,
    suggestedPriceCny: Math.round(suggestedPriceUsd * usdCnyRate * 100) / 100,
    wbPriceRub: getNumber(data, ['wb_price_rub']),
    shippingCostUsd: getNumber(data, ['shipping_cost_usd', 'international_shipping_usd']),
    shippingCostCny: getNumber(data, ['shipping_cost_cny']),
    totalCostCny: getNumber(data, ['total_cost_cny']),
    netRevenueCny: getNumber(data, ['net_revenue_cny']),
    profitCny: getNumber(data, ['profit_cny']),
    marginPercent: getNumber(data, ['profit_percent', 'margin_percent', 'profit_margin_percent']),
    usdCnyRate,
    mxnUsdRate: getNumber(backendInput, ['mxn_usd_rate'], getNumber(rates, ['mxn_usd_rate'])),
    rubUsdRate: getNumber(backendInput, ['rub_usd_rate'], getNumber(rates, ['rub_usd_rate'])),
    rubCnyRate: getNumber(rates, ['rub_cny_rate'], getNumber(backendInput, ['rub_cny_rate'])),
    exchangeRateMode: getString(data, ['exchange_rate_mode'], input.exchangeRateMode),
    exchangeRateSource: getString(exchangeRates, ['source']),
    exchangeRateFetchedAt: getString(exchangeRates, ['fetched_at']),
    exchangeRateCached: getBoolean(exchangeRates, ['cached']),
  }
}

export async function publishPrecheck(product: Product, platforms: Marketplace[] = ['mercadolibre']): Promise<{ product: Product; precheck: PublishPrecheck; platformResults: UnknownRecord; productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[] }> {
  const response = await apiClient.post('/api/publish-precheck', { product: toBackendProduct(product), platforms })
  const data = asRecord(response.data)
  ensureOk(data, '预检失败')
  const firstPlatform = platforms[0] || 'mercadolibre'
  const result = asRecord(asRecord(data.platforms)[firstPlatform])
  const errorItems = precheckIssues(result.errors, 'error')
  const warningItems = precheckIssues(result.warnings, 'warning')
  return {
    product: normalizeBackendProduct(data.product),
    precheck: {
      ok: result.ok !== false,
      errors: errorItems.map(precheckIssueSummary),
      warnings: warningItems.map(precheckIssueSummary),
      errorItems,
      warningItems,
      checkedAt: getString(result, ['checked_at'], new Date().toISOString()),
    },
    platformResults: asRecord(data.platforms),
    productsIndex: normalizeProductsIndex(data.productsIndex),
    draftsIndex: normalizeDraftsIndex(data.draftsIndex),
  }
}

export async function runCategoryPrecheck(product: Product, platform: Marketplace, categoryId: string): Promise<CategoryPrecheckResult> {
  const response = await apiClient.post('/api/category-precheck', { product: toBackendProduct(product), platform, category_id: categoryId })
  const data = asRecord(response.data)
  ensureOk(data, '类目预检失败')
  return {
    ok: stringList(data.errors).length === 0 && stringList(data.missing_fields).length === 0,
    errors: stringList(data.errors),
    missingFields: stringList(data.missing_fields),
    checkedAt: new Date().toISOString(),
    raw: data,
  }
}

export async function previewPublishPayload(product: Product, platform: Marketplace): Promise<PayloadPreviewResult> {
  const response = await apiClient.post('/api/publish-payload-preview', { product: toBackendProduct(product), platform })
  const data = asRecord(response.data)
  ensureOk(data, '生成 payload 失败')
  return {
    platform,
    status: getString(data, ['status']),
    path: getString(data, ['path']),
    payload: asRecord(data.payload),
    warning: getString(data, ['warning']),
  }
}

export async function enqueuePublish(product: Product, platforms: Marketplace[] = ['mercadolibre']): Promise<PublishJob> {
  const response = await apiClient.post('/api/publish-bus/enqueue', { product: toBackendProduct(product), platforms })
  const data = asRecord(response.data)
  ensureOk(data, '发布入队失败')
  return {
    jobId: getString(data, ['job_id']),
    status: getString(data, ['status'], 'queued') as PublishJob['status'],
    platforms: platformList(data.platforms ?? data.eligible_platforms),
    createdAt: new Date().toISOString(),
  }
}

export async function fetchPublishJob(jobId: string): Promise<UnknownRecord> {
  const response = await apiClient.get(`/api/publish-bus/status?job_id=${encodeURIComponent(jobId)}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取任务状态失败')
  return asRecord(data.job)
}

export async function publishProductDirect(product: Product, platform: Marketplace): Promise<ProductOperationResult> {
  const response = await apiClient.post('/api/publish-product', { product: toBackendProduct(product), platform }, { validateStatus: () => true })
  return normalizeProductOperation(response.data)
}

export async function confirmMercadoLibreRealPublish(product: Product, confirm = false): Promise<ProductOperationResult> {
  const response = await apiClient.post('/api/mercadolibre/confirm-real-publish', { product: toBackendProduct(product), confirm_real_publish: confirm, confirm }, { validateStatus: () => true })
  return normalizeProductOperation(response.data)
}

export async function fetchCategoryAttrs(platform: Marketplace, categoryId: string): Promise<CategorySelection> {
  const response = await apiClient.post('/api/category-attrs', {
    platform,
    category_id: categoryId,
  })
  const data = asRecord(response.data)
  ensureOk(data, '读取类目属性失败')
  const attributeOptions = (record: UnknownRecord) => {
    if (Array.isArray(record.options)) return record.options.map(String).filter(Boolean)
    if (Array.isArray(record.values)) {
      return record.values
        .map((item) => {
          const option = asRecord(item)
          return getString(option, ['name', 'value_name', 'id'])
        })
        .filter(Boolean)
    }
    return stringList(record.options)
  }
  const required = Array.isArray(data.required)
    ? data.required.map((item) => {
      const record = asRecord(item)
      return {
        id: getString(record, ['id', 'attribute_id']),
        name: getString(record, ['name', 'label']),
        required: getBoolean(record, ['required'], false),
        options: attributeOptions(record),
      }
    })
    : []
  const optionalFromRequired = required.filter((item) => !item.required)
  const requiredOnly = required.filter((item) => item.required)
  const optional = Array.isArray(data.optional)
    ? data.optional.map((item) => {
      const record = asRecord(item)
      return {
        id: getString(record, ['id', 'attribute_id']),
        name: getString(record, ['name', 'label']),
        required: false,
        options: attributeOptions(record),
      }
    })
    : []
  return {
    platform,
    categoryId,
    categoryPath: getString(data, ['category_path', 'path', 'name']),
    requiredAttributes: requiredOnly,
    optionalAttributes: [...optionalFromRequired, ...optional].filter((item, index, items) => item.id && items.findIndex((candidate) => candidate.id === item.id) === index),
  }
}

export async function searchCategories(platform: Marketplace, query: string, site = ''): Promise<{ results: CategorySearchResult[]; cacheStatus: UnknownRecord }> {
  const response = await apiClient.post('/api/category-search', { platform, query, site, limit: 20 })
  const data = asRecord(response.data)
  ensureOk(data, '搜索类目失败')
  const results = Array.isArray(data.results)
    ? data.results.map((item) => {
      const record = asRecord(item)
      return {
        id: getString(record, ['id', 'category_id']),
        name: getString(record, ['name', 'title']),
        path: getString(record, ['path', 'category_path'], getString(record, ['name', 'title'])),
        raw: record,
      }
    })
    : []
  return { results, cacheStatus: asRecord(data.cache_status) }
}

export async function suggestCategories(product: Product, platform: Marketplace, site = ''): Promise<{ results: CategorySearchResult[]; cacheStatus: UnknownRecord; terms: string[] }> {
  const response = await apiClient.post('/api/category-ai-suggest', { product: toBackendProduct(product), platform, site, limit: 5 })
  const data = asRecord(response.data)
  ensureOk(data, '匹配类目失败')
  const results = Array.isArray(data.suggestions)
    ? data.suggestions.map((item) => {
      const record = asRecord(item)
      return {
        id: getString(record, ['id', 'category_id']),
        name: getString(record, ['name', 'title']),
        path: getString(record, ['path', 'category_path'], getString(record, ['name', 'title'])),
        raw: record,
      }
    })
    : []
  return { results, cacheStatus: asRecord(data.cache_status), terms: Array.isArray(data.terms) ? data.terms.map(String) : [] }
}

function categorySelectionToBackendRecord(category: CategorySelection | null): UnknownRecord | null {
  if (!category) return null
  return {
    category_id: category.categoryId,
    category_path: category.categoryPath,
    path_original: category.categoryPath ? [category.categoryPath] : [],
    attributes_cache: {
      required: category.requiredAttributes.map((attr) => ({
        id: attr.id,
        name: attr.name,
        required: attr.required,
        options: attr.options || [],
      })),
      optional: category.optionalAttributes.map((attr) => ({
        id: attr.id,
        name: attr.name,
        required: false,
        options: attr.options || [],
      })),
    },
  }
}

export async function fillCategoryAttributes(product: Product, platform: Marketplace, categoryId: string, category: CategorySelection | null = null): Promise<ProductMutationResponse & { needReview: unknown[] }> {
  const response = await apiClient.post('/api/category-ai-fill', {
    product: toBackendProduct(product),
    platform,
    category_id: categoryId,
    category_record: categorySelectionToBackendRecord(category),
  })
  const data = asRecord(response.data)
  ensureOk(data, 'AI 填充属性失败')
  const normalizedProduct = normalizeBackendProduct(data.product)
  return {
    ok: true,
    product: normalizedProduct,
    imagePool: normalizedProduct.source.imagePool,
    productsIndex: [],
    draftsIndex: [],
    needReview: Array.isArray(data.need_review) ? data.need_review : [],
    warning: getString(data, ['warning']),
    raw: data,
  }
}

export async function fetchCategoryAttributeTranslations(category: CategorySelection, language = 'zh-CN'): Promise<{ translations: CategoryAttributeTranslations; source: string }> {
  const response = await apiClient.post('/api/category-attribute-translations', {
    platform: category.platform,
    category_id: category.categoryId,
    category_path: category.categoryPath,
    language,
    attributes: [...category.requiredAttributes, ...category.optionalAttributes],
  })
  const data = asRecord(response.data)
  ensureOk(data, '翻译类目属性失败')
  const rawTranslations = asRecord(data.translations)
  const translations: CategoryAttributeTranslations = {}
  for (const [attrId, value] of Object.entries(rawTranslations)) {
    const record = asRecord(value)
    translations[attrId] = {
      label: getString(record, ['label', 'zh_label']),
      help: getString(record, ['help', 'zh_help']),
      values: Object.fromEntries(Object.entries(asRecord(record.values)).map(([key, text]) => [key, String(text || '')]).filter(([, text]) => text.trim())),
    }
  }
  return { translations, source: getString(data, ['source']) }
}

export async function fetchCategoryResultTranslations(platform: Marketplace, categories: CategorySearchResult[], language = 'zh-CN'): Promise<{ translations: CategoryResultTranslations; source: string }> {
  const response = await apiClient.post('/api/category-result-translations', {
    platform,
    language,
    categories: categories.map((item) => ({
      id: item.id,
      name: item.name,
      path: item.path,
      raw: item.raw,
    })),
  })
  const data = asRecord(response.data)
  ensureOk(data, '翻译候选类目失败')
  const translations = Object.fromEntries(
    Object.entries(asRecord(data.translations))
      .map(([key, value]) => [key, String(value || '').trim()])
      .filter(([, value]) => value),
  )
  return { translations, source: getString(data, ['source']) }
}

export async function refreshCategoryCache(platform: Marketplace, site = '', maxCategories = 500): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/category-cache/refresh', { platform, site, max_categories: maxCategories })
  const data = asRecord(response.data)
  ensureOk(data, '刷新类目缓存失败')
  return data
}

export async function startCategoryCacheRefresh(platform: Marketplace, site = '', maxCategories = 500): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/category-cache/refresh-job', { platform, site, max_categories: maxCategories })
  const data = asRecord(response.data)
  ensureOk(data, '启动类目缓存刷新失败')
  return asRecord(data.job)
}

export async function fetchCategoryCacheRefreshJob(jobId: string): Promise<UnknownRecord> {
  const response = await apiClient.get('/api/category-cache/refresh-status', { params: { job_id: jobId } })
  const data = asRecord(response.data)
  ensureOk(data, '读取类目刷新进度失败')
  return asRecord(data.job)
}

export async function assignUpc(): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/assign-upc', {})
  return normalizeProductMutation(response.data)
}

export async function fetchAiConfig(): Promise<AiPublicConfig> {
  const response = await apiClient.get('/api/ai-config')
  const data = asRecord(response.data)
  ensureOk(data, '读取 AI 配置失败')
  return { raw: asRecord(data.config) }
}

export async function saveAiConfig(config: UnknownRecord): Promise<AiPublicConfig> {
  const response = await apiClient.post('/api/ai-config/save', { config })
  const data = asRecord(response.data)
  ensureOk(data, '保存 AI 配置失败')
  return { raw: asRecord(data.config) }
}

export async function fetchMercadoLibreAuthChecklist(): Promise<MercadoLibreAuthChecklist> {
  const response = await apiClient.post('/api/mercadolibre/auth-checklist', {})
  const data = asRecord(response.data)
  ensureOk(data, '读取 Mercado Libre 授权清单失败')
  return normalizeMercadoLibreAuthChecklist(data.checklist ?? data)
}

export async function testAiModel(model: UnknownRecord): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-ai-model', { model })
  return normalizeAuthResult(response.data)
}

export async function testApiConfig(kind: 'exchange_rate' | '1688', config: UnknownRecord, testValue = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-api-config', { kind, config, test_value: testValue }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function saveStoreSettings(storeConfig: UnknownRecord): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/save-settings', { storeConfig })
  const data = asRecord(response.data)
  ensureOk(data, '保存平台授权失败')
  return asRecord(data.storeAuthSummary)
}

export async function buildMercadoLibreAuthLink(appId: string, redirectUri: string): Promise<string> {
  const response = await apiClient.post('/api/mercadolibre/auth-link', { app_id: appId, redirect_uri: redirectUri })
  const data = asRecord(response.data)
  ensureOk(data, '生成授权链接失败')
  return getString(data, ['auth_url', 'url', 'link'])
}

export async function refreshMercadoLibreToken(params: UnknownRecord = {}): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/refresh-token', params, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function runMercadoLibreRealAuthTest(product: Product, mode: MercadoLibreTestMode, categoryId = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/real-auth-test', { product: toBackendProduct(product), mode, category_id: categoryId }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function openAuthLink(url: string, browser = 'default'): Promise<AuthResult> {
  const response = await apiClient.post('/api/open-auth-link', { url, browser }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function exchangeMercadoLibreCode(codeOrUrl: string, params: UnknownRecord = {}): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/exchange-code', { code_or_url: codeOrUrl, ...params }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function testStoreAuth(platform: Marketplace, scope = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-store-auth', { platform, scope }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function clearStoreAuth(platform: Marketplace): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/save-settings', { storeConfig: { [platform]: {} } })
  const data = asRecord(response.data)
  ensureOk(data, '清除授权失败')
  return asRecord(data.storeAuthSummary)
}

function normalizeAuthResult(value: unknown): AuthResult {
  const record = asRecord(value)
  const explanation = asRecord(record.auth_explanation)
  const explanationTitle = getString(explanation, ['title'])
  const explanationMessage = getString(explanation, ['plain_message'])
  return {
    ok: record.ok !== false,
    message: getString(record, ['message', 'status'], record.ok === false ? '失败' : '成功'),
    error: explanationTitle || explanationMessage || getString(record, ['error', 'error_message']),
    errorCode: getString(record, ['error_code'], getString(explanation, ['code'])),
    nextAction: getString(record, ['next_action'], getString(explanation, ['next_action'])),
    raw: record,
  }
}
