import { preparePublishWithProgress } from './publishPreparationStream'
import type { BackendPublishPreparationProgress } from '@/types/workflow'
import { apiClient } from '@/api/client'
import { selectedPricingTargets } from '@/types/pricing'
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
  PricingInput,
  PricingDestinationResult,
  PricingResult,
  PricingTargetResult,
  ProductIndexItem,
  PublishJob,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'
import type {
  DraftMutationResponse,
  PayloadPreviewResult,
} from './normalizers'
import {
  asRecord,
  ensureOk,
  getBoolean,
  getNumber,
  getString,
  isRecord,
  normalizeDraftMutation,
  normalizeDraftDetail,
  normalizeDraftsIndex,
  normalizeProductsIndex,
  normalizePublishPrecheck,
  platformList,
  precheckIssues,
  isCategoryDictionaryAttribute,
  normalizeCategoryDictionaryId,
  stringList,
  toBackendSitesToSell,
} from './normalizers'
import { requiredDraftTarget } from './shared'

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
    ...(record.shipping_currency ? {
      shippingAmount: getNumber(record, ['shipping_amount']),
      shippingCurrency: getString(record, ['shipping_currency']),
    } : {}),
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
    commissionPercent: getNumber(record, ['commission_percent', 'commissionPercent']),
    paymentFeePercent: getNumber(record, ['payment_fee_percent', 'paymentFeePercent'], fallback.paymentFeePercent || 0),
    otherFeePercent: getNumber(record, ['other_fee_percent', 'otherFeePercent'], fallback.otherFeePercent || 0),
    pricingMode: getString(record, ['pricing_mode', 'pricingMode'], fallback.pricingMode || 'margin') as PricingTargetResult['pricingMode'],
    targetMarginPercent: getNumber(record, ['target_margin_percent', 'targetMarginPercent'], fallback.targetMarginPercent || 0),
    markupPercent: getNumber(record, ['markup_percent', 'markupPercent'], fallback.markupPercent || 0),
    shippingQuoteMode: getString(record, ['shipping_quote_mode', 'shippingQuoteMode'], fallback.shippingQuoteMode || 'manual') as PricingTargetResult['shippingQuoteMode'],
    shippingCurrency: getString(record, ['shipping_currency', 'shippingCurrency'], fallback.shippingCurrency || 'USD') as PricingTargetResult['shippingCurrency'],
    shippingAmount: getNumber(record, ['shipping_amount', 'shippingAmount'], fallback.shippingAmount || 0),
    shippingSource: getString(record, ['shipping_source', 'shippingSource']),
    shippingCandidates: Array.isArray(record.shipping_candidates) ? record.shipping_candidates.map(asRecord) : [],
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

function draftPricingParameters(input: PricingInput, targets: PricingInput['targets']) {
  return {
    template_source: input.templateSource || null,
    common: {
      domestic_freight_cny: input.domesticFreightCny,
      packaging_cost_cny: input.packagingCostCny, other_cost_cny: input.otherCostCny,
      exchange_rate_mode: input.exchangeRateMode,
      exchange_rate_overrides: input.exchangeRateOverrides || {},
      ...(input.exchangeRateMode === 'manual' ? {
        usd_cny_rate: input.usdCnyRate, mxn_usd_rate: input.mxnUsdRate, rub_cny_rate: input.rubCnyRate,
      } : {}),
    },
    targets: Object.fromEntries(targets.map(target => [target.targetKey.toLowerCase(), {
      return_rate_percent: target.returnRatePercent === '' ? 0 : target.returnRatePercent ?? 0,
      return_disposition: target.returnDisposition || 'discard',
      return_handling_cost_cny: target.returnHandlingCostCny === '' ? 0 : target.returnHandlingCostCny ?? 0,
      payment_fee_percent: target.paymentFeePercent,
      other_fee_percent: target.otherFeePercent, pricing_mode: target.pricingMode,
      target_margin_percent: target.targetMarginPercent, markup_percent: target.markupPercent,
      shipping_quote_mode: target.shippingQuoteMode, shipping_currency: target.shippingCurrency,
      shipping_amount: target.shippingAmount, manual_price: target.manualPrice,
      shipping_selection: target.shippingSelection || null,
    }])),
  }
}

/** 只传页面当前的费用和覆盖配置，SKU 采购成本与包装事实由后端装配。 */
export function draftPricingPayload(draft: DraftDetail, input: PricingInput): UnknownRecord {
  const targets = selectedPricingTargets(input)
  return {
    draft_id: draft.draftId,
    expected_updated_at: draft.updatedAt,
    target_keys: targets.map(target => target.targetKey.toLowerCase()),
    ...draftPricingParameters(input, targets),
    target_selections: Object.fromEntries(draft.targetSites.filter(target =>
      target.platform === 'mercadolibre' && target.site.toLowerCase() === 'cbt'
      && targets.some(item => item.targetKey.toLowerCase() === `${target.platform}:${target.site}`.toLowerCase()),
    ).map(target => [`${target.platform}:${target.site}`.toLowerCase(), toBackendSitesToSell(target.sitesToSell)])),
    sku_updates: draft.skuItems.map(row => ({
      sku_id: row.sku_id, selected: row.selected,
      pricing_overrides: row.pricing_overrides || {},
    })),
  }
}

/** 保存草稿时记录完整参数与模板来源；预览结果仍须通过应用售价确认。 */
export function draftWithPricingInput(draft: DraftDetail, input: PricingInput, savedInput: PricingInput): DraftDetail {
  const parameters = draftPricingParameters(input, input.targets)
  // 仅编辑其他草稿资料时保留原始核价状态，首次核价仍可套用默认模板。
  if (JSON.stringify(parameters) === JSON.stringify(draftPricingParameters(savedInput, savedInput.targets))) return draft
  const storedTargets = asRecord(draft.pricing.targets)
  return {
    ...draft,
    pricing: {
      ...draft.pricing,
      template_source: parameters.template_source,
      common: { ...asRecord(draft.pricing.common), ...asRecord(parameters.common) },
      targets: {
        ...storedTargets,
        ...Object.fromEntries(Object.entries(asRecord(parameters.targets)).map(([key, target]) => [
          key, { ...asRecord(storedTargets[key]), ...asRecord(target) },
        ])),
      },
    },
  }
}

function normalizePricingResult(data: UnknownRecord, input: PricingInput): PricingResult {
  if (data.ok === false && (!Array.isArray(data.results) || !data.results.length)) ensureOk(data, '核价失败')
  const backendInput = asRecord(data.input)
  const commonInput = asRecord(backendInput.common)
  const exchangeRates = asRecord(data.exchange_rates)
  const rates = asRecord(exchangeRates.rates)
  const rawResults = Array.isArray(data.results) ? data.results : [data]
  const fallbackTargets = selectedPricingTargets(input)
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

export interface DraftPricingBatch {
  previewId?: string
  items: { skuId: string; result: PricingResult }[]
  pricingBySku: Record<string, UnknownRecord>
  applied: boolean
  errors: UnknownRecord[]
  draft?: DraftDetail
  productsIndex?: ProductIndexItem[]
  draftsIndex?: DraftIndexItem[]
  metrics: { batchId: string; durationMs: number; ozonDiscoveryMs: number }
}

export async function priceDraft(draft: DraftDetail, input: PricingInput, apply = false, previewId = ''): Promise<DraftPricingBatch> {
  const response = await apiClient.post(`/api/draft-pricing/${apply ? 'apply' : 'preview'}`,
    { ...draftPricingPayload(draft, input), ...(apply ? { preview_id: previewId } : {}) }, { timeout: 0 })
  const data = asRecord(response.data)
  ensureOk(data, '草稿核价失败')
  return normalizeDraftPricingBatch(data, draft, input)
}

/** 只恢复服务端仍有效的预览，不重新查询费用或计算售价。 */
export async function restoreDraftPricing(draft: DraftDetail, input: PricingInput): Promise<DraftPricingBatch | null> {
  const response = await apiClient.post('/api/draft-pricing/restore', draftPricingPayload(draft, input))
  const data = asRecord(response.data)
  ensureOk(data, '恢复核价预览失败')
  return data.preview_id ? normalizeDraftPricingBatch(data, draft, input) : null
}

function normalizeDraftPricingBatch(data: UnknownRecord, draft: DraftDetail, input: PricingInput): DraftPricingBatch {
  const items = Array.isArray(data.items) ? data.items.map(asRecord) : []
  const expected = new Set(draft.skuItems.filter(row => row.selected).map(row => row.sku_id))
  const ids = new Set(items.map(item => getString(item, ['sku_id'])))
  if (items.length !== expected.size || ids.size !== expected.size || [...expected].some(id => !ids.has(id))) {
    throw new Error('核价返回的 SKU 与本次选择不一致，请重新核价。')
  }
  const metrics = asRecord(data.metrics)
  return {
    previewId: getString(data, ['preview_id']),
    items: items.filter(item => Array.isArray(asRecord(item.result).results)).map(item => ({
      skuId: getString(item, ['sku_id']), result: {
        ...normalizePricingResult(asRecord(item.result), input), previewId: getString(data, ['preview_id']),
      },
    })),
    pricingBySku: asRecord(data.sku_pricing) as Record<string, UnknownRecord>,
    applied: data.applied === true,
    errors: Array.isArray(data.errors) ? data.errors.map(asRecord) : [],
    draft: data.draft ? normalizeDraftDetail(data.draft) : undefined,
    productsIndex: data.productsIndex ? normalizeProductsIndex(data.productsIndex) : undefined,
    draftsIndex: data.draftsIndex ? normalizeDraftsIndex(data.draftsIndex) : undefined,
    metrics: { batchId: getString(metrics, ['batch_id']), durationMs: getNumber(metrics, ['duration_ms']),
      ozonDiscoveryMs: getNumber(metrics, ['ozon_discovery_ms']) },
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

export async function runCategoryPrecheck(draft: DraftDetail, target: MarketplaceTargetSite, categoryId: string): Promise<CategoryPrecheckResult & DraftMutationResponse> {
  const response = await apiClient.post('/api/category-precheck', { ...requiredDraftTarget(draft, target, '类目预检'), category_id: categoryId })
  const data = asRecord(response.data)
  ensureOk(data, '类目预检失败')
  return {
    ...normalizeDraftMutation(data),
    ok: stringList(data.errors).length === 0 && stringList(data.missing_fields).length === 0,
    errors: stringList(data.errors),
    missingFields: stringList(data.missing_fields),
    checkedAt: new Date().toISOString(),
    // 预检记录只保存检查结论，不能把返回的整份草稿再嵌入草稿自身。
    raw: {
      ok: stringList(data.errors).length === 0 && stringList(data.missing_fields).length === 0,
      platform: data.platform, site: data.site, category_id: data.category_id,
      category_path: data.category_path, missing_fields: stringList(data.missing_fields),
      errors: stringList(data.errors),
    },
  }
}

export async function previewPublishPayload(draft: DraftDetail, target: MarketplaceTargetSite, onProgress?: (progress: BackendPublishPreparationProgress) => void): Promise<PayloadPreviewResult & { draft?: DraftDetail; productContext?: DraftMutationResponse['productContext']; productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[] }> {
  // 素材数量决定总耗时；传输逐项超时由后端控制，页面等待实际准备结果。
  const body = requiredDraftTarget(draft, target, '预览发布 payload')
  const data = onProgress
    ? await preparePublishWithProgress(body, onProgress)
    : asRecord((await apiClient.post('/api/publish-payload-preview', body, { timeout: 0 })).data)
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

export async function fetchCategoryAttrs(
  platform: Marketplace,
  categoryId: string,
  site = '',
  options: { forceRefresh?: boolean } = {},
): Promise<CategorySelection> {
  // 完整取得属性定义后再更新编辑态；任何分页失败都不返回看似完整的半份规则。
  const rows: unknown[] = []
  const cursors = new Set<string>()
  const ids = new Set<string>()
  let cursor = ''
  let resolvedSite: string | undefined
  let data: Record<string, unknown> = {}
  let isFirstPage = true
  do {
    const response = await apiClient.post('/api/category-attrs', {
      platform,
      category_id: categoryId,
      site,
      limit: 100,
      ...(cursor ? { cursor } : {}),
      ...(isFirstPage && options.forceRefresh ? { force_refresh: true } : {}),
    })
    isFirstPage = false
    data = asRecord(response.data)
    ensureOk(data, '读取类目属性失败')
    if (getString(data, ['category_id'], categoryId) !== categoryId
      || getString(data, ['platform'], platform) !== platform
      || (resolvedSite !== undefined && getString(data, ['site'], resolvedSite) !== resolvedSite)) {
      throw new Error('类目属性身份已变化，请重新读取')
    }
    resolvedSite = getString(data, ['site'], site)
    if (!Array.isArray(data.attributes)) throw new Error('类目属性响应不完整，请重新读取')
    for (const item of data.attributes) {
      const id = getString(asRecord(item), ['id', 'attribute_id'])
      if (!id || ids.has(id)) throw new Error('属性分页存在缺失或重复，请重新读取')
      ids.add(id)
      rows.push(item)
    }
    if (!data.has_more) break
    cursor = getString(data, ['next_cursor'])
    if (!cursor || cursors.has(cursor)) throw new Error('属性分页不完整，请重新读取')
    cursors.add(cursor)
  } while (cursor)
  const attributes = rows
    .map((item): CategoryAttributeDefinition | null => {
      const record = asRecord(item)
      const id = getString(record, ['id', 'attribute_id'])
      if (!id) return null
      const dictionaryId = normalizeCategoryDictionaryId(getString(record, ['dictionary_id']))
      const optionsList: string[] = []
      const optionLabelsZh: Record<string, string> = {}
      if (Array.isArray(record.options)) {
        for (const option of record.options) {
          if (typeof option === 'string') {
            const trimmed = option.trim()
            if (trimmed) optionsList.push(trimmed)
          } else {
            const optRecord = asRecord(option)
            const val = getString(optRecord, ['value', 'name'])
            if (!val) continue
            optionsList.push(val)
            const labelZh = getString(optRecord, ['label_zh', 'labelZh'])
            if (labelZh) optionLabelsZh[val] = labelZh
          }
        }
      }
      return {
        id,
        name: getString(record, ['name', 'label'], id),
        nameZh: getString(record, ['name_zh', 'nameZh']) || undefined,
        required: getBoolean(record, ['required']),
        options: optionsList,
        ...(Object.keys(optionLabelsZh).length ? { optionLabelsZh } : {}),
        valueType: getString(record, ['value_type', 'valueType'], 'string'),
        variationRole: getString(record, ['variation_role']),
        managedBy: getString(record, ['managed_by']),
        valueMode: getString(record, ['value_mode', 'valueMode'], 'free_text'),
        allowCustomValues: getBoolean(record, ['allow_custom_values', 'allowCustomValues']),
        hasMoreValues: getBoolean(record, ['has_more_values', 'hasMoreValues']),
        readOnly: getBoolean(record, ['read_only', 'readOnly']),
        textFormat: getString(record, ['text_format']) === 'name_value_lines' ? 'name_value_lines' : '',
        formatHint: getString(record, ['format_hint']),
        maxLength: record.max_length == null ? undefined : getNumber(record, ['max_length']),
        minValue: record.min_value == null ? undefined : String(record.min_value),
        maxValue: record.max_value == null ? undefined : String(record.max_value),
        unitOptions: Array.isArray(record.unit_options)
          ? record.unit_options.map((option) => getString(asRecord(option), ['name'])).filter(Boolean)
          : stringList(record.unit_options),
        defaultUnit: getString(record, ['default_unit', 'defaultUnit']),
        description: getString(record, ['description', 'help', 'tooltip']),
        descriptionZh: getString(record, ['description_zh', 'descriptionZh']) || undefined,
        dictionaryId,
        isDictionary: isCategoryDictionaryAttribute(dictionaryId, getBoolean(record, ['is_dictionary'])),
        isCollection: getBoolean(record, ['is_collection']),
        maxValueCount: getNumber(record, ['max_value_count']),
        categoryDependent: getBoolean(record, ['category_dependent']),
        skipValueLocalization: getBoolean(record, ['skip_value_localization', 'skipValueLocalization']),
      }
    })
    .filter((attribute): attribute is CategoryAttributeDefinition => Boolean(attribute))
  return {
    platform,
    categoryId: getString(data, ['category_id'], categoryId),
    descriptionCategoryId: getString(data, ['description_category_id', 'descriptionCategoryId']) || undefined,
    categoryPath: getString(data, ['category_path', 'path', 'name']),
    categoryPathZh: getString(data, ['category_path_zh', 'categoryPathZh']) || undefined,
    cacheSource: getString(data, ['cache_source', 'cacheSource']) || undefined,
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
      const labelZh = getString(record, ['label_zh', 'labelZh'])
      return id && value
        ? [{
          id,
          value,
          ...(labelZh ? { labelZh } : {}),
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

export async function searchCategories(
  platform: Marketplace,
  query: string,
  site = '',
  limit = 20,
): Promise<{ results: CategorySearchResult[]; translatedQuery?: string; searchLanguage?: string }> {
  const response = await apiClient.post('/api/category-search', { platform, query, site, limit })
  const data = asRecord(response.data)
  ensureOk(data, '搜索类目失败')
  const results = Array.isArray(data.results)
    ? data.results.map((item) => {
      const record = asRecord(item)
      const nameZh = getString(record, ['name_zh', 'nameZh'])
      const pathZh = getString(record, ['path_zh', 'pathZh'])
      return {
        id: getString(record, ['id', 'category_id']),
        name: getString(record, ['name', 'title']),
        ...(nameZh ? { nameZh } : {}),
        path: getString(record, ['path', 'category_path'], getString(record, ['name', 'title'])),
        ...(pathZh ? { pathZh } : {}),
        forbidden: getBoolean(record, ['forbidden']),
        forbiddenReason: getString(record, ['forbidden_reason', 'forbiddenReason']),
        raw: record,
      }
    })
    : []
  const translatedQuery = getString(data, ['translated_query', 'translatedQuery'])
  const searchLanguage = getString(data, ['search_language', 'searchLanguage'])
  return {
    results,
    ...(translatedQuery ? { translatedQuery } : {}),
    ...(searchLanguage ? { searchLanguage } : {}),
  }
}

export const CATEGORY_MATCH_PATH = '/api/v1/category-match'

/**
 * 类目匹配业务请求 timeout：必须大于后端总 deadline（150s）并保留网络余量，
 * 不能沿用低于业务 deadline 的全局默认 timeout。
 */
export const CATEGORY_MATCH_REQUEST_TIMEOUT_MS = 180_000

function normalizeCategoryMatchResult(data: UnknownRecord): CategoryMatchResult {
  const selectedCategoryId = getString(data, ['selected_category_id', 'selectedCategoryId'])
  const candidates = Array.isArray(data.candidates)
    ? data.candidates.map((item) => {
      const record = asRecord(item)
      const pathSegments = stringList(record.path_segments ?? record.pathSegments)
      const nameZh = getString(record, ['name_zh', 'nameZh'])
      const pathZh = getString(record, ['path_zh', 'pathZh'])
      return {
        id: getString(record, ['category_id']),
        name: getString(record, ['name']),
        ...(nameZh ? { nameZh } : {}),
        path: pathSegments.join(' / ') || getString(record, ['name']),
        ...(pathZh ? { pathZh } : {}),
        forbidden: getBoolean(record, ['forbidden']),
        forbiddenReason: getString(record, ['forbidden_reason', 'forbiddenReason']),
        raw: { ...record, aiRecommended: getString(record, ['category_id']) === selectedCategoryId && data.status === 'completed',
          matchEvidence: getString(record, ['category_id']) === selectedCategoryId ? stringList(asRecord(data.decision).evidence) : [] },
      }
    }).filter((item) => item.id)
    : []
  candidates.sort((left, right) => {
    if (left.id === selectedCategoryId) return -1
    if (right.id === selectedCategoryId) return 1
    if (Boolean(left.forbidden) !== Boolean(right.forbidden)) return left.forbidden ? 1 : -1
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

export async function matchCategories(
  draft: DraftDetail,
  targets: MarketplaceTargetSite[],
  conversationId: string,
): Promise<CategoryMatchResult[]> {
  const requests = targets.map(target => ({
    ...requiredDraftTarget(draft, target, '匹配类目'),
    language: target.language,
  }))
  if (requests.length === 1) {
    const response = await apiClient.post(CATEGORY_MATCH_PATH, requests[0], {
      aiConversationId: conversationId,
      timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS,
    })
    return [normalizeCategoryMatchResult(asRecord(response.data))]
  }
  const response = await apiClient.post(`${CATEGORY_MATCH_PATH}/batch`, {
    draft_id: draft.draftId, targets: requests,
  }, {
    aiConversationId: conversationId,
    timeout: CATEGORY_MATCH_REQUEST_TIMEOUT_MS * requests.length,
  })
  const items = asRecord(response.data).items
  if (!Array.isArray(items) || items.length !== requests.length) throw new Error('类目匹配结果与目标数量不一致')
  return items.map(item => normalizeCategoryMatchResult(asRecord(item)))
}
