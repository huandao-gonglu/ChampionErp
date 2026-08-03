import { apiClient } from '@/api/client'
import type {
  CategoryMatchResult,
  CategoryPrecheckResult,
  CategorySearchResult,
  CategorySelection,
  DraftIndexItem,
  DraftDetail,
  Marketplace,
  MarketplaceTargetSite,
  MercadoLibreOrderItem,
  MercadoLibreOrderLine,
  MercadoLibreOrdersPage,
  MercadoLibrePublishedPage,
  MercadoLibreRemoteItem,
  PricingInput,
  PricingResult,
  PricingTargetResult,
  Product,
  ProductIndexItem,
  PublishJob,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'
import type {
  DraftMutationResponse,
  PayloadPreviewResult,
  ProductOperationResult,
} from './normalizers'
import {
  asRecord,
  ensureOk,
  getBoolean,
  getNumber,
  getString,
  isRecord,
  normalizeDraftMutation,
  normalizeDraftsIndex,
  normalizeMercadoLibreOrderNotification,
  normalizeProductOperation,
  normalizeProductsIndex,
  normalizePublishLogs,
  platformList,
  precheckIssueSummary,
  precheckIssues,
  stringList,
} from './normalizers'
import { requiredDraftTarget, requiredProductId } from './shared'

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

export async function closeMercadoLibrePublishedItem(itemId: string): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/mercadolibre/close-item', { item_id: itemId })
  const data = asRecord(response.data)
  ensureOk(data, '删除 Mercado Libre 商品失败')
  return data
}

function normalizePricingTargetResult(value: unknown, fallback: Partial<PricingTargetResult> = {}): PricingTargetResult {
  const record = asRecord(value)
  const input = asRecord(record.input)
  const currency = getString(record, ['currency'], fallback.currency || 'USD')
  const currencySuggested = currency === 'MXN'
    ? getNumber(record, ['suggested_price_mxn', 'sale_price_mxn', 'price_mxn'])
    : currency === 'RUB'
      ? getNumber(record, ['wb_price_rub', 'suggested_price_rub', 'price_rub'])
      : getNumber(record, ['suggested_price_usd', 'sale_price_usd', 'price_usd'])
  return {
    targetKey: getString(record, ['target_key', 'targetKey'], fallback.targetKey || ''),
    platform: (getString(record, ['platform'], fallback.platform || 'mercadolibre')) as Marketplace,
    site: getString(record, ['site'], fallback.site || ''),
    currency,
    suggestedPrice: getNumber(record, ['suggested_price', 'suggestedPrice'], fallback.suggestedPrice || currencySuggested),
    suggestedPriceUsd: getNumber(record, ['suggested_price_usd', 'suggestedPriceUsd'], fallback.suggestedPriceUsd || 0),
    suggestedPriceCny: getNumber(record, ['suggested_price_cny', 'suggestedPriceCny'], fallback.suggestedPriceCny || 0),
    appliedPrice: getNumber(record, ['applied_price', 'appliedPrice'], fallback.appliedPrice || currencySuggested),
    shippingCostUsd: getNumber(record, ['shipping_cost_usd', 'shippingCostUsd'], fallback.shippingCostUsd || 0),
    shippingCostCny: getNumber(record, ['shipping_cost_cny', 'shippingCostCny'], fallback.shippingCostCny || 0),
    totalCostCny: getNumber(record, ['total_cost_cny', 'totalCostCny'], fallback.totalCostCny || 0),
    netRevenueCny: getNumber(record, ['net_revenue_cny', 'netRevenueCny'], fallback.netRevenueCny || 0),
    profitCny: getNumber(record, ['profit_cny', 'profitCny'], fallback.profitCny || 0),
    marginPercent: getNumber(record, ['margin_percent', 'profit_percent', 'marginPercent'], fallback.marginPercent || 0),
    commissionPercent: getNumber(record, ['commission_percent', 'commissionPercent'], fallback.commissionPercent || 0),
    paymentFeePercent: getNumber(record, ['payment_fee_percent', 'paymentFeePercent'], fallback.paymentFeePercent || 0),
    targetMarginPercent: getNumber(record, ['target_margin_percent', 'targetMarginPercent'], fallback.targetMarginPercent || 0),
    usdCnyRate: getNumber(record, ['usd_cny_rate', 'usdCnyRate'], getNumber(input, ['usd_cny_rate', 'usdCnyRate'], fallback.usdCnyRate || 0)),
    mxnUsdRate: getNumber(record, ['mxn_usd_rate', 'mxnUsdRate'], getNumber(input, ['mxn_usd_rate', 'mxnUsdRate'], fallback.mxnUsdRate || 0)),
    rubCnyRate: getNumber(record, ['rub_cny_rate', 'rubCnyRate'], getNumber(input, ['rub_cny_rate', 'rubCnyRate'], fallback.rubCnyRate || 0)),
    isLoss: getBoolean(record, ['is_loss', 'isLoss'], fallback.isLoss || false),
    errors: Array.isArray(record.errors) ? record.errors.map((item) => isRecord(item) ? item : String(item || '')).filter(Boolean) : [],
    raw: record,
  }
}

export async function calculatePrice(input: PricingInput): Promise<PricingResult> {
  const common = {
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
    rub_cny_rate: input.exchangeRateMode === 'manual' ? input.rubCnyRate : '',
    exchange_rate_mode: input.exchangeRateMode,
    display_currency_mode: input.displayCurrencyMode,
  }
  const targets = input.targets.length
    ? input.targets.map((target) => ({
      target_key: target.targetKey,
      platform: target.platform,
      site: target.site,
      currency: target.currency,
      commission_percent: target.commissionPercent,
      payment_fee_percent: target.paymentFeePercent,
      target_margin_percent: target.targetMarginPercent,
      shipping_cost_usd: target.shippingCostUsd,
      shipping_cost_cny: target.shippingCostCny,
      russia_freight_rate: target.russiaFreightRate,
      applied_price: target.appliedPrice,
    }))
    : [{ platform: input.platform, site: input.site, commission_percent: input.commissionPercent, target_margin_percent: input.targetMarginPercent }]
  const response = await apiClient.post('/api/calculate-price', {
    ...common,
    platform: input.platform,
    site: input.site,
    common,
    targets,
  })
  const data = asRecord(response.data)
  if (data.ok === false && !Array.isArray(data.results)) ensureOk(data, '核价失败')
  const backendInput = asRecord(data.input)
  const commonInput = asRecord(backendInput.common)
  const exchangeRates = asRecord(data.exchange_rates)
  const rates = asRecord(exchangeRates.rates)
  const rawResults = Array.isArray(data.results) ? data.results : [data]
  const fallbackTargets = input.targets.length ? input.targets : []
  const results = rawResults.map((item, index) => normalizePricingTargetResult(item, fallbackTargets[index]))
  const primary = results[0] || normalizePricingTargetResult(data, {
    targetKey: `${input.platform}:${input.site}`,
    platform: input.platform,
    site: input.site,
  })
  const suggestedPriceUsd = getNumber(data, ['suggested_price_usd', 'sale_price_usd', 'price_usd'], primary.suggestedPriceUsd)
  const usdCnyRate = getNumber(commonInput, ['usd_cny_rate'], getNumber(backendInput, ['usd_cny_rate'], getNumber(rates, ['usd_cny_rate'], primary.usdCnyRate)))
  return {
    results,
    suggestedPriceMxn: getNumber(data, ['suggested_price_mxn', 'sale_price_mxn', 'price_mxn'], primary.currency === 'MXN' ? primary.suggestedPrice : 0),
    suggestedPriceUsd,
    suggestedPriceCny: getNumber(data, ['suggested_price_cny'], primary.suggestedPriceCny || Math.round(suggestedPriceUsd * usdCnyRate * 100) / 100),
    wbPriceRub: getNumber(data, ['wb_price_rub']),
    shippingCostUsd: getNumber(data, ['shipping_cost_usd', 'international_shipping_usd'], primary.shippingCostUsd),
    shippingCostCny: getNumber(data, ['shipping_cost_cny'], primary.shippingCostCny),
    totalCostCny: getNumber(data, ['total_cost_cny'], primary.totalCostCny),
    netRevenueCny: getNumber(data, ['net_revenue_cny'], primary.netRevenueCny),
    profitCny: getNumber(data, ['profit_cny'], primary.profitCny),
    marginPercent: getNumber(data, ['profit_percent', 'margin_percent', 'profit_margin_percent'], primary.marginPercent),
    usdCnyRate,
    mxnUsdRate: getNumber(commonInput, ['mxn_usd_rate'], getNumber(backendInput, ['mxn_usd_rate'], getNumber(rates, ['mxn_usd_rate'], primary.mxnUsdRate))),
    rubUsdRate: getNumber(commonInput, ['rub_usd_rate'], getNumber(backendInput, ['rub_usd_rate'], getNumber(rates, ['rub_usd_rate']))),
    rubCnyRate: getNumber(rates, ['rub_cny_rate'], getNumber(commonInput, ['rub_cny_rate'], getNumber(backendInput, ['rub_cny_rate'], primary.rubCnyRate))),
    exchangeRateMode: getString(data, ['exchange_rate_mode'], input.exchangeRateMode),
    exchangeRateSource: getString(exchangeRates, ['source']),
    exchangeRateFetchedAt: getString(exchangeRates, ['fetched_at']),
    exchangeRateCached: getBoolean(exchangeRates, ['cached']),
  }
}

export async function publishPrecheck(draft: DraftDetail, target: MarketplaceTargetSite): Promise<{ draft: DraftDetail; precheck: PublishPrecheck; platformResults: UnknownRecord; productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[]; productContext?: DraftMutationResponse['productContext'] }> {
  const response = await apiClient.post('/api/publish-precheck', requiredDraftTarget(draft, target, '发布预检'))
  const data = asRecord(response.data)
  ensureOk(data, '预检失败')
  const platform = getString(data, ['platform'], target.platform)
  const result = asRecord(asRecord(data.platforms)[platform])
  const errorItems = precheckIssues(result.errors, 'error')
  const warningItems = precheckIssues(result.warnings, 'warning')
  const mutation = normalizeDraftMutation(data)
  return {
    draft: mutation.draft,
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
    productContext: mutation.productContext,
  }
}

export async function runCategoryPrecheck(draft: DraftDetail, target: MarketplaceTargetSite, categoryId: string): Promise<CategoryPrecheckResult> {
  const response = await apiClient.post('/api/category-precheck', { ...requiredDraftTarget(draft, target, '类目预检'), category_id: categoryId })
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

export async function previewPublishPayload(draft: DraftDetail, target: MarketplaceTargetSite): Promise<PayloadPreviewResult & { draft?: DraftDetail; productContext?: DraftMutationResponse['productContext']; productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[] }> {
  const response = await apiClient.post('/api/publish-payload-preview', requiredDraftTarget(draft, target, '预览发布 payload'))
  const data = asRecord(response.data)
  ensureOk(data, '生成 payload 失败')
  const mutation = isRecord(data.draft) ? normalizeDraftMutation(data) : null
  return {
    platform: getString(data, ['platform'], target.platform),
    site: getString(data, ['site'], target.site),
    target: asRecord(data.target),
    status: getString(data, ['status']),
    path: getString(data, ['path']),
    payload: asRecord(data.payload),
    warning: getString(data, ['warning']),
    draft: mutation?.draft,
    productContext: mutation?.productContext,
    productsIndex: mutation?.productsIndex,
    draftsIndex: mutation?.draftsIndex,
  }
}

export async function enqueuePublish(draft: DraftDetail, target: MarketplaceTargetSite): Promise<PublishJob> {
  const response = await apiClient.post('/api/publish-bus/enqueue', requiredDraftTarget(draft, target, '发布入队'))
  const data = asRecord(response.data)
  ensureOk(data, '发布入队失败')
  const responseTarget = asRecord(data.target)
  const targetPlatform = getString(responseTarget, ['platform'], target.platform)
  const targetSite = getString(responseTarget, ['site'], target.site)
  return {
    jobId: getString(data, ['job_id']),
    status: getString(data, ['status'], 'queued') as PublishJob['status'],
    platforms: platformList(data.platforms ?? data.eligible_platforms),
    createdAt: new Date().toISOString(),
    draftId: getString(data, ['draft_id'], draft.draftId),
    targetKey: `${targetPlatform}:${targetSite}`.toLowerCase(),
  }
}

export async function fetchPublishJob(jobId: string): Promise<UnknownRecord> {
  const response = await apiClient.get(`/api/publish-bus/status?job_id=${encodeURIComponent(jobId)}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取任务状态失败')
  return asRecord(data.job)
}

export async function publishProductDirect(product: Product, platform: Marketplace): Promise<ProductOperationResult> {
  const response = await apiClient.post('/api/publish-product', { product_id: requiredProductId(product, '发布商品'), platform }, { validateStatus: () => true })
  return normalizeProductOperation(response.data)
}

export async function confirmMercadoLibreRealPublish(product: Product, confirm = false): Promise<ProductOperationResult> {
  const response = await apiClient.post('/api/mercadolibre/confirm-real-publish', { product_id: requiredProductId(product, '确认真实发布'), confirm_real_publish: confirm, confirm }, { validateStatus: () => true })
  return normalizeProductOperation(response.data)
}

export async function fetchCategoryAttrs(platform: Marketplace, categoryId: string, site = '', categoryRecord?: UnknownRecord): Promise<CategorySelection> {
  const response = await apiClient.post('/api/category-attrs', {
    platform,
    category_id: categoryId,
    site,
    category_record: categoryRecord,
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
        valueType: getString(record, ['value_type', 'valueType'], 'string'),
        unit: getString(record, ['unit']),
        description: getString(record, ['description', 'help', 'tooltip']),
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
        valueType: getString(record, ['value_type', 'valueType'], 'string'),
        unit: getString(record, ['unit']),
        description: getString(record, ['description', 'help', 'tooltip']),
      }
    })
    : []
  return {
    platform,
    categoryId,
    categoryPath: getString(data, ['category_path', 'path', 'name']),
    requiredAttributes: requiredOnly,
    optionalAttributes: [...optionalFromRequired, ...optional].filter((item, index, items) => item.id && items.findIndex((candidate) => candidate.id === item.id) === index),
    source: getString(data, ['source'], `${platform}_live`),
    fetchedAt: new Date().toISOString(),
    raw: asRecord(data.category),
  }
}

export async function searchCategories(platform: Marketplace, query: string, site = '', limit = 20): Promise<{ results: CategorySearchResult[] }> {
  const response = await apiClient.post('/api/category-search', { platform, query, site, limit })
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
  return { results }
}

export async function matchCategory(
  draft: DraftDetail,
  target: MarketplaceTargetSite,
): Promise<CategoryMatchResult> {
  const response = await apiClient.post('/api/category-match', {
    ...requiredDraftTarget(draft, target, '匹配类目'),
    language: target.language,
  })
  const data = asRecord(response.data)
  ensureOk(data, '匹配类目失败')
  const selectedCategoryId = getString(data, ['selected_category_id', 'selectedCategoryId'])
  const candidates = Array.isArray(data.candidates)
    ? data.candidates.map((item) => {
      const record = asRecord(item)
      const pathSegments = stringList(record.path_segments ?? record.pathSegments)
      return {
        id: getString(record, ['category_id']),
        name: getString(record, ['name']),
        path: pathSegments.join(' / ') || getString(record, ['name']),
        raw: record,
      }
    }).filter((item) => item.id)
    : []
  candidates.sort((left, right) => {
    if (left.id === selectedCategoryId) return -1
    if (right.id === selectedCategoryId) return 1
    return 0
  })
  const decision = asRecord(data.decision)
  const failureRecord = asRecord(data.failure)
  const trace = asRecord(data.trace)
  return {
    ok: getBoolean(data, ['ok']),
    status: getString(data, ['status'], 'failed') as CategoryMatchResult['status'],
    selectedCategoryId,
    candidates,
    query: getString(data, ['query']),
    decision: {
      confidenceBand: getString(decision, ['confidence_band'], 'low') as CategoryMatchResult['decision']['confidenceBand'],
      modelConfidence: getNumber(decision, ['model_confidence']),
      decisionScore: getNumber(decision, ['decision_score']),
      abstained: getBoolean(decision, ['abstained']),
      evidence: stringList(decision.evidence),
      searchCount: getNumber(decision, ['search_count']),
    },
    failure: Object.keys(failureRecord).length
      ? {
        code: getString(failureRecord, ['code']),
        message: getString(failureRecord, ['message']),
        stage: getString(failureRecord, ['stage']),
        retryable: getBoolean(failureRecord, ['retryable']),
      }
      : null,
    trace: {
      conversationId: getString(trace, ['conversation_id']),
      taskRunId: getString(trace, ['task_run_id']),
    },
  }
}

function categorySelectionToBackendRecord(category: CategorySelection | null): UnknownRecord | null {
  if (!category) return null
  return {
    ...asRecord(category.raw),
    platform: category.platform,
    category_id: category.categoryId,
    category_path: category.categoryPath,
    path_original: category.categoryPath ? [category.categoryPath] : [],
    attributes: {
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

export async function fillCategoryAttributes(draft: DraftDetail, target: MarketplaceTargetSite, categoryId: string, category: CategorySelection | null = null): Promise<DraftMutationResponse & { needReview: unknown[]; warning?: string }> {
  const response = await apiClient.post('/api/category-ai-fill', {
    ...requiredDraftTarget(draft, target, '填充类目属性'),
    category_id: categoryId,
    category_record: categorySelectionToBackendRecord(category),
  })
  const data = asRecord(response.data)
  ensureOk(data, 'AI 填充属性失败')
  const result = normalizeDraftMutation(data)
  return {
    ...result,
    needReview: Array.isArray(data.need_review) ? data.need_review : [],
    warning: getString(data, ['warning']),
  }
}
