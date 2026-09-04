import { apiClient, type AiPresentationTransport } from '@/api/client'
import { withAiForeground } from '@/services/withAiForeground'
import type {
  CategoryAttributeDefinition,
  CategoryAttributeValuesPage,
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
  MercadoLibreUserProduct,
  MercadoLibreUserProductsPage,
  PricingInput,
  PricingDestinationResult,
  PricingResult,
  PricingTargetResult,
  Product,
  ProductIndexItem,
  PublishJob,
  PublishJobListItem,
  PublishJobsPage,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'
import type {
  DraftMutationResponse,
  PayloadPreviewResult,
  ProductOperationResult,
} from './normalizers'
import { normalizeMercadoLibrePublication } from './normalizers/product'
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
  normalizePublishPrecheck,
  normalizeSitesToSell,
  platformList,
  precheckIssues,
  isCategoryDictionaryAttribute,
  normalizeCategoryDictionaryId,
  stringList,
  toBackendSitesToSell,
} from './normalizers'
import { requiredDraftTarget, requiredProductId } from './shared'

export async function fetchPublishLogs(): Promise<PublishLogItem[]> {
  const response = await apiClient.get('/api/publish-logs')
  const data = asRecord(response.data)
  ensureOk(data, '读取发布日志失败')
  return normalizePublishLogs(data.items)
}

function normalizePublishJobListItem(value: unknown): PublishJobListItem {
  const record = asRecord(value)
  const platforms = Array.isArray(record.platforms)
    ? record.platforms.map((value) => {
      const item = asRecord(value)
      return {
        platform: getString(item, ['platform']) as Marketplace,
        draftId: getString(item, ['draft_id', 'draftId']),
        site: getString(item, ['site']),
        sitesToSell: normalizeSitesToSell(item.sites_to_sell),
        marketResults: Array.isArray(item.market_results)
          ? item.market_results.map((value) => {
            const market = asRecord(value)
            return {
              siteId: getString(market, ['site_id', 'siteId']),
              logisticType: getString(market, ['logistic_type', 'logisticType']),
              status: getString(market, ['status']),
              itemId: getString(market, ['item_id', 'itemId']),
              error: getString(market, ['error']),
              errorCode: getString(market, ['error_code', 'errorCode']),
            }
          })
          : [],
        status: getString(item, ['status']),
        stage: getString(item, ['stage']),
        attempts: getNumber(item, ['attempts']),
        error: getString(item, ['error']),
        errorCode: getString(item, ['error_code', 'errorCode']),
        nextAction: getString(item, ['next_action', 'nextAction']),
        updatedAt: getString(item, ['updated_at', 'updatedAt']),
      }
    })
    : []
  return {
    jobId: getString(record, ['job_id', 'jobId']),
    productId: getString(record, ['product_id', 'productId']),
    productName: getString(record, ['product_name', 'productName']),
    draftId: getString(record, ['draft_id', 'draftId']),
    status: getString(record, ['status'], 'queued') as PublishJobListItem['status'],
    rawStatus: getString(record, ['raw_status', 'rawStatus']),
    stage: getString(record, ['stage']),
    attempts: getNumber(record, ['attempts']),
    error: getString(record, ['error']),
    errorCode: getString(record, ['error_code', 'errorCode']),
    nextAction: getString(record, ['next_action', 'nextAction']),
    platforms,
    createdAt: getString(record, ['created_at', 'createdAt']),
    updatedAt: getString(record, ['updated_at', 'updatedAt']),
  }
}

export async function fetchPublishJobs(options: {
  limit?: number
  cursor?: string
  status?: string
  platform?: string
  productId?: string
} = {}): Promise<PublishJobsPage> {
  const params = new URLSearchParams({ limit: String(options.limit || 50) })
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.status) params.set('status', options.status)
  if (options.platform) params.set('platform', options.platform)
  if (options.productId) params.set('product_id', options.productId)
  const response = await apiClient.get(`/api/publish-bus/jobs?${params.toString()}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取发布任务失败')
  return {
    items: Array.isArray(data.items) ? data.items.map(normalizePublishJobListItem) : [],
    nextCursor: getString(data, ['next_cursor', 'nextCursor']),
  }
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

function normalizeMercadoLibreUserProduct(value: unknown): MercadoLibreUserProduct {
  const record = asRecord(value)
  const publication = normalizeMercadoLibrePublication(record) || {
    model: '',
    accountUserId: '',
    sitelessUserProductId: '',
    sitelessFamilyId: '',
    parentItemId: '',
    parentUserProductId: '',
    sellerId: '',
    status: '',
    familyName: '',
    markets: [],
    confirmedPayload: {},
    error: '',
    lastOperation: {},
    updatedAt: '',
  }
  return {
    ...publication,
    productId: getString(record, ['product_id', 'productId']),
    draftId: getString(record, ['draft_id', 'draftId']),
    title: getString(record, ['title'], publication.familyName),
    thumbnail: getString(record, ['thumbnail']),
    updatedAt: getString(record, ['updated_at', 'updatedAt'])
      || publication.markets.map((market) => market.updatedAt).filter(Boolean).sort().at(-1)
      || '',
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

export async function fetchMercadoLibreUserProducts(
  status = 'active',
  page = 1,
  perPage = 50,
  refreshIdentityMapping = false,
): Promise<MercadoLibreUserProductsPage> {
  const params = new URLSearchParams({
    status,
    page: String(page),
    per_page: String(perPage),
    refresh: String(refreshIdentityMapping),
  })
  const response = await apiClient.get(`/api/mercadolibre/user-products?${params.toString()}`)
  const data = asRecord(response.data)
  ensureOk(data, '读取 Mercado Libre User Products 失败')
  return {
    items: Array.isArray(data.items) ? data.items.map(normalizeMercadoLibreUserProduct) : [],
    pagination: normalizeMercadoLibrePagination(data.pagination, page, perPage),
    refreshErrors: Array.isArray(data.refresh_errors) ? data.refresh_errors.map(asRecord) : [],
    refreshScope: getString(data, ['refresh_scope']),
    checkedAt: getString(data, ['checked_at']),
  }
}

export async function pauseMercadoLibreUserProduct(sitelessUserProductId: string): Promise<UnknownRecord> {
  const normalizedId = String(sitelessUserProductId || '').trim()
  if (!normalizedId) throw new Error('暂停 Mercado Libre User Product 需要 Siteless User Product ID。')
  const response = await apiClient.post('/api/mercadolibre/pause-user-product', {
    siteless_user_product_id: normalizedId,
  })
  const data = asRecord(response.data)
  ensureOk(data, '暂停 Mercado Libre User Product 失败')
  return data
}

function normalizeMoney(value: unknown, currency: string) {
  const record = asRecord(value)
  return {
    amount: getString(record, ['amount'], '0'),
    currency: getString(record, ['currency'], currency),
  }
}

function normalizeOptionalMoney(value: unknown, currency: string) {
  return isRecord(value) ? normalizeMoney(value, currency) : null
}

function normalizePricingDestinationResult(
  value: unknown,
  fallbackCurrency: string,
): PricingDestinationResult | null {
  const record = asRecord(value)
  const siteId = getString(record, ['site_id', 'siteId']).toUpperCase()
  const logisticType = getString(record, ['logistic_type', 'logisticType']).toLowerCase()
  const pricingModel = getString(record, ['pricing_model', 'pricingModel']).toLowerCase()
  const price = normalizeOptionalMoney(record.price, fallbackCurrency)
  const netProceeds = normalizeOptionalMoney(record.net_proceeds ?? record.netProceeds, fallbackCurrency)
  if (!siteId || siteId === 'CBT' || !logisticType) return null
  if (pricingModel !== 'price' && pricingModel !== 'net_proceeds') return null
  if ((price === null) === (netProceeds === null)) return null
  if (pricingModel === 'price' ? !price : !netProceeds) return null
  return {
    siteId,
    logisticType,
    pricingModel,
    price,
    netProceeds,
    calculationFingerprint: getString(record, ['calculation_fingerprint', 'calculationFingerprint']),
  }
}

function normalizePricingTargetResult(value: unknown, fallback: Partial<PricingTargetResult> = {}): PricingTargetResult {
  const record = asRecord(value)
  const input = asRecord(record.input)
  const listingCurrency = getString(record, ['listing_currency'], fallback.listingCurrency || '')
  const convertedPrices = asRecord(record.converted_prices)
  const rawDestinationResults = record.destination_results ?? record.destinationResults
  const destinationResults = Array.isArray(rawDestinationResults)
    ? rawDestinationResults
      .map((item) => normalizePricingDestinationResult(item, listingCurrency))
      .filter((item): item is PricingDestinationResult => Boolean(item))
    : []
  const appliedNetProceeds = destinationResults.some((item) => item.pricingModel === 'net_proceeds')
    ? normalizeOptionalMoney(record.applied_net_proceeds ?? record.appliedNetProceeds, listingCurrency)
    : null
  return {
    targetKey: getString(record, ['target_key', 'targetKey'], fallback.targetKey || ''),
    platform: (getString(record, ['platform'], fallback.platform || 'mercadolibre')) as Marketplace,
    site: getString(record, ['site'], fallback.site || ''),
    listingCurrency,
    currencyFingerprint: getString(record, ['currency_fingerprint'], fallback.currencyFingerprint || ''),
    suggestedPrice: normalizeMoney(record.suggested_price, listingCurrency),
    appliedPrice: normalizeMoney(record.applied_price, listingCurrency),
    appliedNetProceeds,
    destinationResults,
    convertedPrices: Object.fromEntries(Object.entries(convertedPrices).map(([key, amount]) => [key, String(amount ?? '0')])),
    calculationBasis: asRecord(record.calculation_basis),
    calculationFingerprint: getString(record, ['calculation_fingerprint']),
    shippingCostUsd: getNumber(record, ['shipping_cost_usd', 'shippingCostUsd'], fallback.shippingCostUsd || 0),
    shippingCostCny: getNumber(record, ['shipping_cost_cny', 'shippingCostCny'], fallback.shippingCostCny || 0),
    totalCostCny: getNumber(record, ['total_cost_cny', 'totalCostCny'], fallback.totalCostCny || 0),
    netRevenueCny: getNumber(record, ['net_revenue_cny', 'netRevenueCny'], fallback.netRevenueCny || 0),
    profitCny: getNumber(record, ['profit_cny', 'profitCny'], fallback.profitCny || 0),
    marginPercent: getNumber(record, ['margin_percent', 'profit_percent', 'marginPercent'], fallback.marginPercent || 0),
    commissionPercent: getNumber(record, ['commission_percent', 'commissionPercent'], fallback.commissionPercent || 0),
    paymentFeePercent: getNumber(record, ['payment_fee_percent', 'paymentFeePercent'], fallback.paymentFeePercent || 0),
    otherFeePercent: getNumber(record, ['other_fee_percent', 'otherFeePercent'], fallback.otherFeePercent || 0),
    pricingMode: getString(record, ['pricing_mode', 'pricingMode'], fallback.pricingMode || 'margin') as PricingTargetResult['pricingMode'],
    targetMarginPercent: getNumber(record, ['target_margin_percent', 'targetMarginPercent'], fallback.targetMarginPercent || 0),
    markupPercent: getNumber(record, ['markup_percent', 'markupPercent'], fallback.markupPercent || 0),
    shippingQuoteMode: getString(record, ['shipping_quote_mode', 'shippingQuoteMode'], fallback.shippingQuoteMode || 'manual') as PricingTargetResult['shippingQuoteMode'],
    shippingCurrency: getString(record, ['shipping_currency', 'shippingCurrency'], fallback.shippingCurrency || 'USD') as PricingTargetResult['shippingCurrency'],
    shippingAmount: getNumber(record, ['shipping_amount', 'shippingAmount'], fallback.shippingAmount || 0),
    shippingSource: getString(record, ['shipping_source', 'shippingSource']),
    commissionCny: getNumber(record, ['commission_cny', 'commissionCny']),
    paymentFeeCny: getNumber(record, ['payment_fee_cny', 'paymentFeeCny']),
    otherFeeCny: getNumber(record, ['other_fee_cny', 'otherFeeCny']),
    minimumPrice: normalizeMoney(record.minimum_price, listingCurrency),
    billableWeightKg: getNumber(record, ['billable_weight_kg', 'billableWeightKg']),
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
    packaging_cost: input.packagingCostCny,
    other_cost: input.otherCostCny,
    weight_kg: input.weightKg,
    length_cm: input.lengthCm,
    width_cm: input.widthCm,
    height_cm: input.heightCm,
    usd_cny_rate: input.exchangeRateMode === 'manual' ? input.usdCnyRate : '',
    mxn_usd_rate: input.exchangeRateMode === 'manual' ? input.mxnUsdRate : '',
    rub_cny_rate: input.exchangeRateMode === 'manual' ? input.rubCnyRate : '',
    exchange_rate_mode: input.exchangeRateMode,
  }
  const targets = input.targets.length
    ? input.targets.map((target) => ({
      target_key: target.targetKey,
      platform: target.platform,
      site: target.site,
      sites_to_sell: toBackendSitesToSell(target.sitesToSell),
      listing_currency: target.listingCurrency,
      commission_percent: target.commissionPercent,
      payment_fee_percent: target.paymentFeePercent,
      other_fee_percent: target.otherFeePercent,
      pricing_mode: target.pricingMode,
      target_margin_percent: target.targetMarginPercent,
      markup_percent: target.markupPercent,
      shipping_quote_mode: target.shippingQuoteMode,
      shipping_currency: target.shippingCurrency,
      shipping_amount: target.shippingAmount,
      manual_price: target.manualPrice,
    }))
    : []
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
  const usdCnyRate = getNumber(commonInput, ['usd_cny_rate'], getNumber(backendInput, ['usd_cny_rate'], getNumber(rates, ['usd_cny_rate'], primary.usdCnyRate)))
  return {
    results,
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
  const mutation = normalizeDraftMutation(data)
  return {
    draft: mutation.draft,
    precheck: normalizePublishPrecheck(result, {
      requireLayeredScopes: platform === 'mercadolibre',
      expectedMarkets: platform === 'mercadolibre' ? target.sitesToSell || [] : undefined,
    }),
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
    validationDigest: getString(data, ['validation_digest', 'validationDigest']),
    summary: asRecord(data.summary),
    warnings: precheckIssues(data.warnings, 'warning'),
    draft: mutation?.draft,
    productContext: mutation?.productContext,
    productsIndex: mutation?.productsIndex,
    draftsIndex: mutation?.draftsIndex,
  }
}

export async function enqueuePublish(draft: DraftDetail, target: MarketplaceTargetSite, validationDigest: string): Promise<PublishJob> {
  const response = await apiClient.post('/api/publish-bus/enqueue', {
    ...requiredDraftTarget(draft, target, '发布入队'),
    confirm: true,
    validation_digest: validationDigest,
  })
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

export async function reconcilePublishJob(jobId: string, platform: Marketplace): Promise<UnknownRecord> {
  const normalizedJobId = String(jobId || '').trim()
  const normalizedPlatform = String(platform || '').trim().toLowerCase()
  if (!normalizedJobId || !normalizedPlatform) throw new Error('发布结果对账需要 Job ID 与平台。')
  const response = await apiClient.post('/api/publish-bus/reconcile', {
    job_id: normalizedJobId,
    platform: normalizedPlatform,
  })
  const data = asRecord(response.data)
  ensureOk(data, '发布结果对账失败')
  return data
}

export async function publishProductDirect(product: Product, platform: Marketplace): Promise<ProductOperationResult> {
  if (String(platform || '').trim().toLowerCase() === 'mercadolibre') {
    throw new Error('Mercado Libre 仅支持通过发布队列提交刊登。')
  }
  const response = await apiClient.post('/api/publish-product', { product_id: requiredProductId(product, '发布商品'), platform }, { validateStatus: () => true })
  return normalizeProductOperation(response.data)
}

export async function fetchCategoryAttrs(platform: Marketplace, categoryId: string, site = ''): Promise<CategorySelection> {
  // 类目属性定义只从实时类目接口瞬时加载（编辑态使用），不再持久化进草稿/商品。
  // 单页 limit=100 请求；若 has_more=true，分页信息保留在 raw 中供诊断。
  const response = await apiClient.post('/api/category-attrs', {
    platform,
    category_id: categoryId,
    site,
    limit: 100,
  })
  const data = asRecord(response.data)
  ensureOk(data, '读取类目属性失败')
  const attributes = (Array.isArray(data.attributes) ? data.attributes : [])
    .map((item): CategoryAttributeDefinition | null => {
      const record = asRecord(item)
      const id = getString(record, ['id', 'attribute_id'])
      if (!id) return null
      const dictionaryId = normalizeCategoryDictionaryId(getString(record, ['dictionary_id']))
      return {
        id,
        name: getString(record, ['name', 'label'], id),
        required: getBoolean(record, ['required']),
        options: Array.isArray(record.options)
          ? record.options
            .map((option) => typeof option === 'string'
              ? option.trim()
              : getString(asRecord(option), ['value', 'name']))
            .filter(Boolean)
          : [],
        valueType: getString(record, ['value_type', 'valueType'], 'string'),
        valueMode: getString(record, ['value_mode', 'valueMode'], 'free_text'),
        allowCustomValues: getBoolean(record, ['allow_custom_values', 'allowCustomValues']),
        hasMoreValues: getBoolean(record, ['has_more_values', 'hasMoreValues']),
        readOnly: getBoolean(record, ['read_only', 'readOnly']),
        unitOptions: Array.isArray(record.unit_options)
          ? record.unit_options.map((option) => getString(asRecord(option), ['name'])).filter(Boolean)
          : stringList(record.unit_options),
        defaultUnit: getString(record, ['default_unit', 'defaultUnit']),
        description: getString(record, ['description', 'help', 'tooltip']),
        dictionaryId,
        isDictionary: isCategoryDictionaryAttribute(dictionaryId, getBoolean(record, ['is_dictionary'])),
        isCollection: getBoolean(record, ['is_collection']),
        maxValueCount: getNumber(record, ['max_value_count']),
        categoryDependent: getBoolean(record, ['category_dependent']),
      }
    })
    .filter((attribute): attribute is CategoryAttributeDefinition => Boolean(attribute))
  return {
    platform,
    categoryId: getString(data, ['category_id'], categoryId),
    categoryPath: getString(data, ['category_path', 'path', 'name']),
    requiredAttributes: attributes.filter((attribute) => attribute.required),
    optionalAttributes: attributes.filter((attribute) => !attribute.required),
    source: getString(data, ['source'], `${platform}_live`),
    fetchedAt: new Date().toISOString(),
    raw: data,
  }
}

export async function fetchCategoryAttributeValues(
  platform: Marketplace,
  categoryId: string,
  attributeId: string,
  site = '',
  query = '',
  limit = 50,
  cursor = '',
): Promise<CategoryAttributeValuesPage> {
  const response = await apiClient.post('/api/category-attribute-values', {
    platform,
    category_id: categoryId,
    attribute_id: attributeId,
    site,
    query,
    limit,
    cursor,
  })
  const data = asRecord(response.data)
  ensureOk(data, '读取平台枚举值失败')
  const values = Array.isArray(data.values)
    ? data.values.flatMap((item) => {
      const record = asRecord(item)
      const id = getString(record, ['id'])
      const value = getString(record, ['value'])
      return id && value
        ? [{
          id,
          value,
          info: getString(record, ['info']),
          picture: getString(record, ['picture']),
        }]
        : []
    })
    : []
  const hasMore = getBoolean(data, ['has_more', 'hasMore'])
  return {
    values,
    nextCursor: getString(data, ['next_cursor', 'nextCursor']),
    hasMore,
    complete: getBoolean(data, ['complete'], !hasMore),
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

export const CATEGORY_MATCH_PATH = '/api/v1/category-match'

/**
 * 类目匹配业务请求 timeout：必须大于后端 Agent deadline（60s）并保留网络余量，
 * 不能沿用低于业务 deadline 的全局默认 timeout。
 */
export const CATEGORY_MATCH_REQUEST_TIMEOUT_MS = 75_000

function normalizeCategoryMatchResult(data: UnknownRecord): CategoryMatchResult {
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
      taskRunId: getString(trace, ['task_run_id']),
    },
  }
}

export async function matchCategory(
  draft: DraftDetail,
  target: MarketplaceTargetSite,
): Promise<CategoryMatchResult> {
  // 同步 focused 业务 response 是唯一结果事实；withAiForeground 只提供前台
  // 实时展示（reserve → observe stream → 业务 header 关联）。业务判断失败
  // （ok=false）仍是合法 200 结果，不抛错，由 caller 读取 failure/status。
  return withAiForeground(
    {
      displayTitle: 'AI 匹配类目',
      initialUserMessage: `为“${draft.title || draft.draftId}”匹配 ${target.platform.toUpperCase()} ${target.site || ''} 类目。`.trim(),
      successNotice: (result) => (
        result.status === 'completed'
          ? '类目匹配完成'
          : '类目匹配结束，仍需人工确认候选'
      ),
    },
    async ({ presentationId }) => {
      const response = await apiClient.post(
        CATEGORY_MATCH_PATH,
        {
          ...requiredDraftTarget(draft, target, '匹配类目'),
          language: target.language,
        },
        {
          aiPresentationId: presentationId,
          timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS,
        },
      )
      return normalizeCategoryMatchResult(asRecord(response.data))
    },
  )
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
        value_type: attr.valueType || '',
        value_mode: attr.valueMode || '',
        allow_custom_values: Boolean(attr.allowCustomValues),
        has_more_values: Boolean(attr.hasMoreValues),
        read_only: Boolean(attr.readOnly),
        unit: attr.unit || '',
        unit_options: attr.unitOptions || [],
        default_unit: attr.defaultUnit || '',
        dictionary_id: normalizeCategoryDictionaryId(attr.dictionaryId),
        is_dictionary: isCategoryDictionaryAttribute(attr.dictionaryId, attr.isDictionary),
        is_collection: Boolean(attr.isCollection),
        max_value_count: attr.maxValueCount || 0,
        category_dependent: Boolean(attr.categoryDependent),
      })),
      optional: category.optionalAttributes.map((attr) => ({
        id: attr.id,
        name: attr.name,
        required: false,
        options: attr.options || [],
        value_type: attr.valueType || '',
        value_mode: attr.valueMode || '',
        allow_custom_values: Boolean(attr.allowCustomValues),
        has_more_values: Boolean(attr.hasMoreValues),
        read_only: Boolean(attr.readOnly),
        unit: attr.unit || '',
        unit_options: attr.unitOptions || [],
        default_unit: attr.defaultUnit || '',
        dictionary_id: normalizeCategoryDictionaryId(attr.dictionaryId),
        is_dictionary: isCategoryDictionaryAttribute(attr.dictionaryId, attr.isDictionary),
        is_collection: Boolean(attr.isCollection),
        max_value_count: attr.maxValueCount || 0,
        category_dependent: Boolean(attr.categoryDependent),
      })),
    },
  }
}

export async function fillCategoryAttributes(
  draft: DraftDetail,
  target: MarketplaceTargetSite,
  categoryId: string,
  category: CategorySelection | null = null,
  presentation: AiPresentationTransport = {},
): Promise<DraftMutationResponse & { needReview: unknown[]; warning?: string }> {
  const response = await apiClient.post('/api/category-ai-fill', {
    ...requiredDraftTarget(draft, target, '填充类目属性'),
    category_id: categoryId,
    category_record: categorySelectionToBackendRecord(category),
  }, {
    aiPresentationId: presentation.presentationId,
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
