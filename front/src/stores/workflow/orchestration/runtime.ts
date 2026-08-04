import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { saveDraft as saveDraftApi } from '@/api/workflow/catalog'
import { diagnosticsToCollectDiagnostics } from '@/api/workflow/normalizers'
import { createDefaultCollectDiagnostics } from '@/constants/initialState'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'
import { useWorkflowSettingsStore } from '@/stores/workflow/settings'
import type {
  CategoryAttributeSchema,
  CategoryPrecheckResult,
  CategorySearchResult,
  CategorySelection,
  DraftDetail,
  DraftIndexItem,
  Marketplace,
  MarketplaceDraft,
  MarketplaceTargetSite,
  PrecheckIssue,
  PricingTargetInput,
  Product,
  ProductIndexItem,
  PublishJob,
  UnknownRecord,
  WorkflowStep,
} from '@/types/workflow'

export function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function collectStats(product: Product) {
  return {
    downloadedImages: product.source.imagePool.length,
    extractedBullets: product.sellingPoints.length,
  }
}

export function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error(`读取文件失败：${file.name}`))
    reader.readAsDataURL(file)
  })
}

export function parseNumber(value: string | number): number {
  const parsed = typeof value === 'number' ? value : Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function normalizeProgressKey(value: unknown): string {
  return String(value || '').trim().toLowerCase()
}

export function workflowProgressDraft(
  product: Product,
  currentDraft: DraftDetail,
  marketplace: Marketplace,
): MarketplaceDraft | DraftDetail | undefined {
  const normalizedMarketplace = normalizeProgressKey(marketplace)
  const currentPlatforms = currentDraft.platforms.map(normalizeProgressKey)
  const currentDraftMatches = Boolean(
    currentDraft.draftId
    && (
      normalizeProgressKey(currentDraft.platform) === normalizedMarketplace
      || currentPlatforms.includes(normalizedMarketplace)
    ),
  )
  return currentDraftMatches ? currentDraft : product.drafts[marketplace]
}

export function publishJobMatchesProgressContext(
  job: PublishJob | null,
  marketplace: Marketplace,
  draft: MarketplaceDraft | DraftDetail | undefined,
  activeTargetKey = '',
): boolean {
  if (!job) return false
  const normalizedMarketplace = normalizeProgressKey(marketplace)
  if (!job.platforms.some((platform) => normalizeProgressKey(platform) === normalizedMarketplace)) return false

  const draftId = String(draft?.draftId || '').trim()
  if (job.draftId && String(job.draftId).trim() !== draftId) return false

  if (job.targetKey) {
    const selectedTargetKey = normalizeProgressKey(activeTargetKey)
    const contextTargetKey = selectedTargetKey.startsWith(`${normalizedMarketplace}:`)
      ? selectedTargetKey
      : `${normalizedMarketplace}:${normalizeProgressKey(draft?.site)}`
    if (normalizeProgressKey(job.targetKey) !== contextTargetKey) return false
  }
  return true
}

export function precheckIssueFromRaw(value: unknown, fallbackSeverity: 'error' | 'warning'): PrecheckIssue {
  const record = isRecord(value) ? value : {}
  return {
    code: String(record.code || ''),
    field: String(record.field || ''),
    message: String(record.message || value || ''),
    severity: String(record.severity || fallbackSeverity) as PrecheckIssue['severity'],
    nextAction: String(record.next_action || record.nextAction || ''),
  }
}

export function precheckMessages(items: unknown): string[] {
  if (!Array.isArray(items)) return []
  return items.map((item) => isRecord(item) ? String(item.message || item.code || '') : String(item || '')).filter(Boolean)
}

export function categorySelectionFromProduct(product: Product, platform: Marketplace): CategorySelection | null {
  const categories = isRecord(product.raw.local_platform_categories) ? product.raw.local_platform_categories : {}
  const record = isRecord(categories[platform]) ? categories[platform] as UnknownRecord : null
  if (!record) return null
  const attrs = isRecord(record.attributes) ? record.attributes : {}
  const normalizeAttr = (item: unknown, requiredFallback: boolean) => {
    const attr = isRecord(item) ? item : {}
    return {
      id: String(attr.id || attr.attribute_id || ''),
      name: String(attr.name || attr.label || attr.id || attr.attribute_id || ''),
      required: typeof attr.required === 'boolean' ? attr.required : requiredFallback,
      options: Array.isArray(attr.options) ? attr.options.map(String) : [],
    }
  }
  const requiredAttributes = Array.isArray(attrs.required)
    ? attrs.required.map((item) => normalizeAttr(item, true)).filter((item) => item.id && item.required)
    : []
  const optionalAttributes = Array.isArray(attrs.optional)
    ? attrs.optional.map((item) => normalizeAttr(item, false)).filter((item) => item.id)
    : []
  const categoryId = String(record.category_id || record.subject_id || record.type_id || product.drafts[platform].categoryId || '')
  if (!categoryId && !requiredAttributes.length && !optionalAttributes.length) return null
  return {
    platform,
    categoryId,
    categoryPath: String(record.category_path || record.path || record.name_original || product.drafts[platform].categoryPath || ''),
    requiredAttributes,
    optionalAttributes,
    raw: record,
  }
}

export function categoryAttributeSchemaFromSelection(selection: CategorySelection, target: MarketplaceTargetSite): CategoryAttributeSchema {
  const normalizeAttributes = (items: CategorySelection['requiredAttributes']) => items.map((item) => ({
    id: item.id,
    name: item.name || item.id,
    required: Boolean(item.required),
    options: [...(item.options || [])],
    valueType: item.valueType || 'string',
    unit: item.unit || '',
    description: item.description || '',
  }))
  return {
    version: 1,
    platform: target.platform,
    site: target.site,
    categoryId: selection.categoryId,
    categoryPath: selection.categoryPath || String(target.categoryPath || ''),
    source: selection.source || `${target.platform}_live`,
    fetchedAt: selection.fetchedAt || new Date().toISOString(),
    required: normalizeAttributes(selection.requiredAttributes),
    optional: normalizeAttributes(selection.optionalAttributes).map((item) => ({ ...item, required: false })),
  }
}

export function categorySelectionFromAttributeSchema(schema: CategoryAttributeSchema | null | undefined, target: MarketplaceTargetSite): CategorySelection | null {
  if (!schema || !schema.categoryId) return null
  if (schema.platform && schema.platform !== target.platform) return null
  if (schema.site && schema.site !== target.site) return null
  if (target.categoryId && schema.categoryId !== target.categoryId) return null
  return {
    platform: target.platform,
    categoryId: schema.categoryId,
    categoryPath: schema.categoryPath || String(target.categoryPath || ''),
    requiredAttributes: schema.required.map((item) => ({ ...item, options: [...(item.options || [])] })),
    optionalAttributes: schema.optional.map((item) => ({ ...item, required: false, options: [...(item.options || [])] })),
    source: schema.source,
    fetchedAt: schema.fetchedAt,
    raw: {
      platform: target.platform,
      site: target.site,
      category_id: schema.categoryId,
      category_path: schema.categoryPath,
      attributes: {
        required: schema.required,
        optional: schema.optional,
      },
    },
  }
}


export function createWorkflowRuntime() {
  const catalogStore = useWorkflowCatalogStore()
  const collectionStore = useWorkflowCollectionStore()
  const publishingStore = useWorkflowPublishingStore()
  const settingsStore = useWorkflowSettingsStore()
  const activityStore = useWorkflowActivityStore()
  const {
    product,
    productsIndex,
    draftsIndex,
    selectedProductIds,
    currentDraft,
    currentDraftProductContext,
    imagePrompt,
  } = storeToRefs(catalogStore)
  const {
    collectForm,
    collectDiagnostics,
    collectBatchRows,
    browserDebugStatus,
  } = storeToRefs(collectionStore)
  const { fillFormFromState } = collectionStore
  const {
    pricingInput,
    pricingResult,
    category,
    categoryQuery,
    categoryResults,
    categoryRecommendations,
    categoryAutoMatching,
    categoryAutoMatchMessage,
    categoryAutoMatchCurrent,
    categoryAutoMatchTotal,
    categoryAutoMatchProductName,
    categoryAttributeTranslations,
    categoryAttributeTranslationsSource,
    categoryAttributeTranslating,
    categoryAttributeLoading,
    categoryAttributeError,
    categoryResultTranslations,
    categoryResultTranslationsSource,
    categoryResultTranslating,
    categoryPrecheck,
    precheck,
    precheckResults,
    payloadPreview,
    copyGenerating,
    publishJob,
    publishJobStatus,
    publishLogs,
    mercadoLibreOrders,
    mercadoLibreOrderNotifications,
    mercadoLibreOrdersTotal,
    mercadoLibreOrdersCheckedAt,
    mercadoLibreRemoteItems,
    mercadoLibreRemoteStatus,
    mercadoLibreRemotePage,
    mercadoLibreRemotePerPage,
    mercadoLibreRemoteTotal,
    mercadoLibreRemoteTotalPages,
    activeMarketplace,
    platformOptions,
    publishResult,
    activePublishTargetKey,
  } = storeToRefs(publishingStore)
  const {
    refreshPublishJob,
    refreshPublishLogs,
    refreshMercadoLibreRemoteItems,
    refreshMercadoLibreOrders,
    closeMercadoLibreRemoteItem,
  } = publishingStore
  const {
    appConfig,
    aiConfig,
    storeConfig,
    storeAuthSummary,
    mercadolibreAuthChecklist,
    lastAuthResult,
    authLink,
  } = storeToRefs(settingsStore)
  const {
    loadAiConfig,
    saveAiSettings,
    testAiSettings,
    testPlatformApiConfig,
    saveStoreConfig,
    testAuth,
    loadMercadoLibreChecklist,
    generateMercadoLibreAuthLink,
    openMercadoLibreAuth,
    refreshMercadoLibreAuthToken,
    runMercadoLibreAuthTest,
    exchangeMlCode,
    clearPlatformAuth,
  } = settingsStore
  const { logs, loading, error } = storeToRefs(activityStore)
  const { addLog, setError } = activityStore
  const requestSequence = {
    categoryAttributeTranslation: 0,
    categoryResultTranslation: 0,
    categoryAttributeLoad: 0,
  }
  const currentStage = ref(0)

  const draft = computed(() => product.value.drafts[activeMarketplace.value])

  function activeMarketplaceSite(): string {
    const draftSite = String(product.value.drafts[activeMarketplace.value]?.site || '').trim()
    if (draftSite) return draftSite
    return platformOptions.value.find((option) => option.key === activeMarketplace.value)?.sites[0]?.code || ''
  }

  function targetKey(platform: Marketplace, site: string) {
    return `${String(platform || '').trim().toLowerCase()}:${String(site || '').trim().toLowerCase()}`
  }

  function targetSiteKey(target: MarketplaceTargetSite) {
    return targetKey(target.platform, target.site)
  }

  function cloneAttributes(value: unknown): Record<string, string> {
    const record = isRecord(value) ? value : {}
    return Object.fromEntries(Object.entries(record).map(([key, rawValue]) => [key, String(rawValue ?? '')]))
  }

  function cloneValidationErrors(value: unknown): Array<UnknownRecord | string> {
    return Array.isArray(value)
      ? value.map((item) => isRecord(item) ? { ...item } : String(item || ''))
      : []
  }

  function categoryRecommendationForTarget(target: MarketplaceTargetSite) {
    return categoryRecommendations.value[targetSiteKey(target)]
  }

  function applyCategoryRecommendationForTarget(target: MarketplaceTargetSite) {
    const recommendation = categoryRecommendationForTarget(target)
    categoryQuery.value = recommendation?.query || ''
    categoryResults.value = recommendation?.results || []
  }

  function setCategoryRecommendation(target: MarketplaceTargetSite, query: string, results: CategorySearchResult[], error = '') {
    categoryRecommendations.value = {
      ...categoryRecommendations.value,
      [targetSiteKey(target)]: {
        query,
        results,
        error,
      },
    }
    if (targetSiteKey(target) === targetSiteKey(selectedPublishTarget.value)) {
      applyCategoryRecommendationForTarget(target)
    }
  }

  function categoryPrecheckFromTarget(value: unknown): CategoryPrecheckResult | null {
    if (!isRecord(value) || !Object.keys(value).length) return null
    return {
      ok: value.ok !== false,
      errors: Array.isArray(value.errors) ? value.errors.map(String) : [],
      missingFields: Array.isArray(value.missingFields)
        ? value.missingFields.map(String)
        : Array.isArray(value.missing_fields)
          ? value.missing_fields.map(String)
          : [],
      checkedAt: String(value.checkedAt || value.checked_at || ''),
      raw: value,
    }
  }

  function normalizeDraftTarget(target: MarketplaceTargetSite, draftDetail: DraftDetail, useRootFallback = false): MarketplaceTargetSite {
    const site = platformSite(target.platform, target.site)
    return {
      ...target,
      platform: target.platform,
      site: target.site || site?.code || '',
      language: target.language || site?.language || draftDetail.language || '',
      currency: target.currency || site?.currency || draftDetail.currency || '',
      categoryId: String(target.categoryId || (useRootFallback ? draftDetail.categoryId : '') || ''),
      categoryPath: String(target.categoryPath || (useRootFallback ? draftDetail.categoryPath : '') || ''),
      categoryAttributeSchema: target.categoryAttributeSchema || null,
      attributes: Object.keys(target.attributes || {}).length ? cloneAttributes(target.attributes) : useRootFallback ? cloneAttributes(draftDetail.attributes) : {},
      validationErrors: (target.validationErrors || []).length ? cloneValidationErrors(target.validationErrors) : useRootFallback ? cloneValidationErrors(draftDetail.validationErrors) : [],
      categoryPrecheck: target.categoryPrecheck || {},
      publishStatus: String(target.publishStatus || (useRootFallback ? draftDetail.publishStatus : '') || ''),
      status: String(target.status || (useRootFallback ? draftDetail.status : '') || ''),
      lastPrecheck: target.lastPrecheck || (useRootFallback ? draftDetail.lastPrecheck : {}) || {},
      lastPrecheckTarget: target.lastPrecheckTarget || (useRootFallback ? draftDetail.lastPrecheckTarget : {}) || {},
      publishLogs: Array.isArray(target.publishLogs) ? target.publishLogs : [],
    }
  }

  function mergeTargetDetails(targets: MarketplaceTargetSite[], previousTargets: MarketplaceTargetSite[], draftDetail: DraftDetail): MarketplaceTargetSite[] {
    const previousByKey = new Map(previousTargets.map((target) => [targetSiteKey(target), target]))
    return targets.map((target, index) => {
      const previous = previousByKey.get(targetSiteKey(target))
      return normalizeDraftTarget({ ...(previous || {}), ...target }, draftDetail, !previous && index === 0)
    })
  }

  function persistActiveTargetListingFields(extra: Partial<MarketplaceTargetSite> = {}) {
    if (!currentDraft.value.draftId) return
    if (!currentDraft.value.targetSites.length && currentPublishTargets.value.length) {
      currentDraft.value.targetSites = currentPublishTargets.value.map((target, index) => normalizeDraftTarget(target, currentDraft.value, index === 0))
    }
    const key = activePublishTargetKey.value || targetSiteKey(selectedPublishTarget.value)
    const index = currentDraft.value.targetSites.findIndex((target) => targetSiteKey(target) === key)
    if (index < 0) return
    const existing = currentDraft.value.targetSites[index]
    const activeSchema = category.value
      && category.value.categoryId === currentDraft.value.categoryId
      && category.value.platform === existing.platform
      ? categoryAttributeSchemaFromSelection(category.value, existing)
      : existing.categoryAttributeSchema || null
    currentDraft.value.targetSites.splice(index, 1, {
      ...existing,
      categoryId: currentDraft.value.categoryId,
      categoryPath: currentDraft.value.categoryPath,
      categoryAttributeSchema: activeSchema,
      attributes: cloneAttributes(currentDraft.value.attributes),
      validationErrors: cloneValidationErrors(currentDraft.value.validationErrors),
      publishStatus: currentDraft.value.publishStatus,
      status: currentDraft.value.status,
      lastPrecheck: currentDraft.value.lastPrecheck,
      lastPrecheckTarget: currentDraft.value.lastPrecheckTarget,
      ...extra,
    })
  }

  function invalidateCategoryAttributeLoad() {
    requestSequence.categoryAttributeLoad += 1
    categoryAttributeLoading.value = false
    categoryAttributeError.value = ''
  }

  function applyTargetListingToDraft(target: MarketplaceTargetSite) {
    currentDraft.value.categoryId = String(target.categoryId || '')
    currentDraft.value.categoryPath = String(target.categoryPath || '')
    currentDraft.value.attributes = cloneAttributes(target.attributes)
    currentDraft.value.validationErrors = cloneValidationErrors(target.validationErrors)
    currentDraft.value.publishStatus = String(target.publishStatus || '')
    currentDraft.value.lastPrecheck = isRecord(target.lastPrecheck) ? target.lastPrecheck : {}
    currentDraft.value.lastPrecheckTarget = isRecord(target.lastPrecheckTarget) ? target.lastPrecheckTarget : {}
    categoryPrecheck.value = categoryPrecheckFromTarget(target.categoryPrecheck)
    category.value = categorySelectionFromAttributeSchema(target.categoryAttributeSchema, target)
    applyCategoryRecommendationForTarget(target)
    requestSequence.categoryAttributeTranslation += 1
    requestSequence.categoryResultTranslation += 1
    categoryAttributeTranslations.value = {}
    categoryAttributeTranslationsSource.value = ''
    categoryResultTranslations.value = {}
    categoryResultTranslationsSource.value = ''
  }

  function configuredTargetsForLanguage(language: string): MarketplaceTargetSite[] {
    const selectedLanguage = String(language || '').trim().toLowerCase()
    if (!selectedLanguage) return []
    return platformOptions.value.flatMap((platform) => platform.sites
      .filter((site) => String(site.language || '').trim().toLowerCase() === selectedLanguage)
      .map((site) => ({ platform: platform.key, site: site.code, language: site.language, currency: site.currency })))
  }

  function configuredSelectedTargets(language: string, targets: MarketplaceTargetSite[]): MarketplaceTargetSite[] {
    const configuredTargets = configuredTargetsForLanguage(language)
    const selectedKeys = new Set(targets.map((target) => targetKey(target.platform, target.site)).filter(Boolean))
    return configuredTargets.filter((target) => selectedKeys.has(targetKey(target.platform, target.site)))
  }

  function targetPlatforms(targets: MarketplaceTargetSite[]): Marketplace[] {
    return Array.from(new Set(targets.map((target) => target.platform).filter(Boolean)))
  }

  function pricingTargetKey(platform: Marketplace, site: string) {
    return targetKey(platform, site)
  }

  function platformSite(platform: Marketplace, site: string) {
    const option = platformOptions.value.find((item) => item.key === platform)
    return option?.sites.find((item) => item.code.toLowerCase() === String(site || '').toLowerCase()) || option?.sites[0]
  }

  function pricingTargetDefaults(platform: Marketplace) {
    return platform === 'yandex' || platform === 'ozon'
      ? { commissionPercent: 20, paymentFeePercent: 0, targetMarginPercent: 30 }
      : { commissionPercent: 16, paymentFeePercent: 0, targetMarginPercent: 30 }
  }

  function pricingTargetsFromDraft(draftDetail: DraftDetail): MarketplaceTargetSite[] {
    const selected = (draftDetail.targetSites || []).filter((target) => target.platform && target.site)
    if (selected.length) return selected
    const site = platformSite(draftDetail.platform, draftDetail.site)
    return [{
      platform: draftDetail.platform,
      site: draftDetail.site || site?.code || '',
      language: draftDetail.language || site?.language || '',
      currency: draftDetail.currency || site?.currency || '',
    }].filter((target) => target.platform && target.site)
  }

  function normalizedDraftTargets(draftDetail: DraftDetail): MarketplaceTargetSite[] {
    const selected = (draftDetail.targetSites || []).filter((target) => target.platform && target.site)
    if (selected.length) {
      return selected.map((target, index) => normalizeDraftTarget(target, draftDetail, index === 0)).filter((target) => target.platform && target.site)
    }
    const site = platformSite(draftDetail.platform, draftDetail.site)
    return [{
      platform: draftDetail.platform,
      site: draftDetail.site || site?.code || '',
      language: draftDetail.language || site?.language || '',
      currency: draftDetail.currency || site?.currency || '',
    }].map((target) => normalizeDraftTarget(target, draftDetail, true)).filter((target) => target.platform && target.site)
  }

  const currentPublishTargets = computed(() => currentDraft.value.draftId ? normalizedDraftTargets(currentDraft.value) : [])

  const selectedPublishTarget = computed<MarketplaceTargetSite>(() => {
    const targets = currentPublishTargets.value
    if (!targets.length) return { platform: '', site: '', language: '', currency: '' }
    const selected = targets.find((target) => pricingTargetKey(target.platform, target.site) === activePublishTargetKey.value)
    return selected || targets[0]
  })
  const categoryAutoMatchTargetError = computed(() => categoryRecommendationForTarget(selectedPublishTarget.value)?.error || '')

  function syncActivePublishTarget(preferred?: MarketplaceTargetSite, invalidateCategoryLoad = false) {
    const targets = currentPublishTargets.value
    if (!targets.length) {
      activePublishTargetKey.value = ''
      return
    }
    const preferredKey = preferred ? pricingTargetKey(preferred.platform, preferred.site) : ''
    const existing = targets.find((target) => pricingTargetKey(target.platform, target.site) === (preferredKey || activePublishTargetKey.value))
    const selected = existing || targets[0]
    if (invalidateCategoryLoad) invalidateCategoryAttributeLoad()
    activePublishTargetKey.value = pricingTargetKey(selected.platform, selected.site)
    activeMarketplace.value = selected.platform
    applyTargetListingToDraft(selected)
  }

  function pricingTargetRecord(pricing: UnknownRecord, key: string): UnknownRecord {
    const targets = isRecord(pricing.targets) ? pricing.targets as UnknownRecord : {}
    const direct = targets[key]
    if (isRecord(direct)) return direct as UnknownRecord
    const upperKey = key.toUpperCase()
    const matched = Object.entries(targets).find(([candidate]) => candidate.toLowerCase() === key || candidate.toUpperCase() === upperKey)
    return matched && isRecord(matched[1]) ? matched[1] as UnknownRecord : {}
  }

  function recordNumber(record: UnknownRecord, keys: string[], fallback = 0): number {
    for (const key of keys) {
      const value = record[key]
      const parsed = parseNumber(typeof value === 'number' ? value : String(value ?? ''))
      if (parsed > 0 || value === 0 || value === '0') return parsed
    }
    return fallback
  }

  function pricingTargetInput(target: MarketplaceTargetSite, pricing: UnknownRecord): PricingTargetInput {
    const site = platformSite(target.platform, target.site)
    const key = pricingTargetKey(target.platform, target.site)
    const saved = pricingTargetRecord(pricing, key)
    const defaults = pricingTargetDefaults(target.platform)
    return {
      targetKey: key,
      platform: target.platform,
      site: target.site || site?.code || '',
      currency: target.currency || site?.currency || 'USD',
      commissionPercent: recordNumber(saved, ['commissionPercent', 'commission_percent'], defaults.commissionPercent),
      paymentFeePercent: recordNumber(saved, ['paymentFeePercent', 'payment_fee_percent'], defaults.paymentFeePercent),
      targetMarginPercent: recordNumber(saved, ['targetMarginPercent', 'target_margin_percent'], pricingInput.value.targetMarginPercent || defaults.targetMarginPercent),
      shippingCostUsd: recordNumber(saved, ['shippingCostUsd', 'shipping_cost_usd'], 0),
      shippingCostCny: recordNumber(saved, ['shippingCostCny', 'shipping_cost_cny'], 0),
      russiaFreightRate: recordNumber(saved, ['russiaFreightRate', 'russia_freight_rate'], 0),
      appliedPrice: recordNumber(saved, ['appliedPrice', 'applied_price'], 0),
    }
  }

  const imagePool = computed(() => product.value.source.imagePool)
  const selectedImages = computed(() => imagePool.value.filter((image) => image.selected))
  const selectedProducts = computed(() => productsIndex.value.filter((item) => selectedProductIds.value.includes(item.productId)))

  function draftDetailFromProduct(platform: Marketplace, sourceProduct: Product = product.value): DraftDetail {
    const sourceDraft = sourceProduct.drafts[platform]
    const rawDrafts = isRecord(sourceProduct.raw.drafts) ? sourceProduct.raw.drafts : {}
    const rawDraft = isRecord(rawDrafts[platform]) ? rawDrafts[platform] as UnknownRecord : {}
    return {
      ...sourceDraft,
      productId: sourceProduct.productId,
      sourceProductId: sourceProduct.productId,
      platform,
      platforms: sourceDraft.platforms.length ? sourceDraft.platforms : [platform],
      site: String(rawDraft.site || rawDraft.site_id || ''),
      createdAt: String(rawDraft.created_at || ''),
      updatedAt: String(rawDraft.updated_at || ''),
      raw: rawDraft,
    }
  }

  function applyMutationIndexes(result: { productsIndex?: ProductIndexItem[]; draftsIndex?: DraftIndexItem[] }) {
    if (result.productsIndex?.length) productsIndex.value = result.productsIndex
    if (result.draftsIndex) draftsIndex.value = result.draftsIndex
  }

  const workflowSteps = computed<WorkflowStep[]>(() => {
    const hasCollected = Boolean(product.value.source.title || product.value.name)
    const hasLibrary = productsIndex.value.length > 0
    const hasEdit = Boolean(product.value.productId && (product.value.sku || product.value.stock || product.value.upc || product.value.brand || product.value.model))
    const progressDraft = workflowProgressDraft(product.value, currentDraft.value, activeMarketplace.value)
    const hasCopy = Boolean(
      progressDraft
      && (
        ['copy_ready', 'images_ready', 'ready_to_publish', 'published'].includes(progressDraft.status)
        || (progressDraft.title && progressDraft.description)
      ),
    )
    const hasImages = Boolean(
      progressDraft
      && (
        progressDraft.images.length > 0
        || ['images_ready', 'ready_to_publish', 'published'].includes(progressDraft.status)
      ),
    )
    const hasPrice = Boolean(progressDraft?.price || pricingResult.value?.results.length)
    const hasCategory = Boolean(progressDraft?.categoryId || category.value)
    const hasPrecheck = Boolean(precheck.value?.ok || ['ready_to_publish', 'published'].includes(progressDraft?.status || ''))
    const publishJobMatches = publishJobMatchesProgressContext(
      publishJob.value,
      activeMarketplace.value,
      progressDraft,
      activePublishTargetKey.value,
    )
    const hasPublished = (publishJobMatches && publishJob.value?.status === 'completed') || progressDraft?.status === 'published'
    const flags = [hasCollected, hasLibrary, hasCopy, hasImages, hasEdit, hasPrice, hasCategory, hasPrecheck, hasPublished]
    return [
      ['collect', '采集商品', '链接、Cookie、浏览器标签、手动导入'],
      ['library', '商品库', 'SQLite 本地商品库和草稿复制'],
      ['copy', 'AI 文案', '生成目标平台标题、描述、卖点'],
      ['images', '图片处理', '上传、图片池、图片翻译'],
      ['edit', '商品编辑', '基础信息、SKU、UPC、库存'],
      ['pricing', '核价', '成本、运费、汇率、佣金'],
      ['category', '类目属性', '搜索类目并补齐必填属性'],
      ['precheck', '发布预检', '生成 payload 并检查缺项'],
      ['publish', '发布队列', '入队、状态、发布日志'],
    ].map(([key, title, description], index) => ({
      key,
      title,
      description,
      status: flags[index] ? 'done' : index === currentStage.value ? 'active' : index < currentStage.value ? 'blocked' : 'pending',
    } satisfies WorkflowStep))
  })

  const progressPercent = computed(() => {
    const done = workflowSteps.value.filter((step) => step.status === 'done').length
    return Math.round((done / workflowSteps.value.length) * 100)
  })

  function restorePrecheckFromProduct() {
    const previews = isRecord(product.value.raw.publish_preview) ? product.value.raw.publish_preview : {}
    const raw = isRecord(previews[activeMarketplace.value]) ? previews[activeMarketplace.value] as UnknownRecord : null
    if (!raw) {
      precheck.value = null
      return
    }
    const errorItems = Array.isArray(raw.errors) ? raw.errors.map((item) => precheckIssueFromRaw(item, 'error')) : []
    const warningItems = Array.isArray(raw.warnings) ? raw.warnings.map((item) => precheckIssueFromRaw(item, 'warning')) : []
    precheck.value = {
      ok: raw.ok === true,
      errors: precheckMessages(raw.errors),
      warnings: precheckMessages(raw.warnings),
      errorItems,
      warningItems,
      checkedAt: String(raw.checked_at || raw.checkedAt || ''),
    }
  }

  function restoreCategoryFromProduct() {
    category.value = categorySelectionFromProduct(product.value, activeMarketplace.value)
  }

  function syncCollectDiagnosticsFromProduct(message = '已读取后端商品状态。', raw?: UnknownRecord) {
    if (raw && Object.keys(raw).length) {
      collectDiagnostics.value = diagnosticsToCollectDiagnostics(raw, product.value, message)
      return
    }
    const stats = collectStats(product.value)
    collectDiagnostics.value = {
      ...createDefaultCollectDiagnostics(),
      status: product.value.source.title || product.value.name ? 'success' : 'idle',
      progress: product.value.source.title || product.value.name ? 100 : 0,
      message,
      downloadedImages: stats.downloadedImages,
      extractedBullets: stats.extractedBullets,
      antiBotWarning: false,
      lastSourceUrl: product.value.source.sourceUrl,
      raw: product.value.source.collectDiagnostics,
      errorCode: String(product.value.source.collectDiagnostics.error_code || ''),
      nextAction: String(product.value.source.collectDiagnostics.next_action || ''),
      htmlSnapshotPath: String(product.value.source.collectDiagnostics.html_snapshot_path || ''),
      screenshotPath: String(product.value.source.collectDiagnostics.screenshot_path || ''),
    }
  }

  function syncPricingInputFromProduct() {
    const draftDetail = currentDraft.value
    const hasDraft = Boolean(draftDetail.draftId)
    const pricing = hasDraft && isRecord(draftDetail.pricing) ? draftDetail.pricing : {}
    const common = isRecord(pricing.common) ? pricing.common as UnknownRecord : {}
    const context = currentDraftProductContext.value
    const pkg = draftDetail.packageDimensions
    pricingInput.value.purchaseCostCny = recordNumber(common, ['purchaseCostCny', 'purchase_cost_cny', 'purchase_cost'], parseNumber(context.cost || context.sourcePrice || product.value.cost || product.value.source.price || pricingInput.value.purchaseCostCny))
    pricingInput.value.domesticFreightCny = recordNumber(common, ['domesticFreightCny', 'domestic_freight_cny', 'domestic_freight'], pricingInput.value.domesticFreightCny)
    pricingInput.value.weightKg = recordNumber(common, ['weightKg', 'weight_kg'], parseNumber(pkg.weightKg || context.weightKg || product.value.source.weightKg || pricingInput.value.weightKg))
    pricingInput.value.lengthCm = recordNumber(common, ['lengthCm', 'length_cm'], parseNumber(pkg.lengthCm || context.dimensions.lengthCm || product.value.source.dimensions.lengthCm || pricingInput.value.lengthCm))
    pricingInput.value.widthCm = recordNumber(common, ['widthCm', 'width_cm'], parseNumber(pkg.widthCm || context.dimensions.widthCm || product.value.source.dimensions.widthCm || pricingInput.value.widthCm))
    pricingInput.value.heightCm = recordNumber(common, ['heightCm', 'height_cm'], parseNumber(pkg.heightCm || context.dimensions.heightCm || product.value.source.dimensions.heightCm || pricingInput.value.heightCm))
    pricingInput.value.commissionPercent = recordNumber(common, ['commissionPercent', 'commission_percent'], pricingInput.value.commissionPercent)
    pricingInput.value.targetMarginPercent = recordNumber(common, ['targetMarginPercent', 'target_margin_percent'], pricingInput.value.targetMarginPercent)
    pricingInput.value.usdCnyRate = recordNumber(common, ['usdCnyRate', 'usd_cny_rate'], pricingInput.value.usdCnyRate)
    pricingInput.value.mxnUsdRate = recordNumber(common, ['mxnUsdRate', 'mxn_usd_rate'], pricingInput.value.mxnUsdRate)
    pricingInput.value.rubCnyRate = recordNumber(common, ['rubCnyRate', 'rub_cny_rate'], pricingInput.value.rubCnyRate)
    pricingInput.value.platform = hasDraft ? draftDetail.platform : activeMarketplace.value
    pricingInput.value.site = hasDraft ? draftDetail.site : activeMarketplaceSite()
    pricingInput.value.targets = hasDraft ? pricingTargetsFromDraft(draftDetail).map((target) => pricingTargetInput(target, pricing)) : []
  }

  function syncDraftPackageDimensionsFromPricingInput() {
    const packageDimensions = {
      lengthCm: String(pricingInput.value.lengthCm || ''),
      widthCm: String(pricingInput.value.widthCm || ''),
      heightCm: String(pricingInput.value.heightCm || ''),
      weightKg: String(pricingInput.value.weightKg || ''),
    }
    const current = currentDraft.value.packageDimensions
    const changed = Object.entries(packageDimensions).some(([key, value]) => (
      current[key as keyof typeof packageDimensions] !== value
    ))
    currentDraft.value.packageDimensions = packageDimensions
    if (changed) {
      precheck.value = null
      precheckResults.value = {}
      payloadPreview.value = null
      currentDraft.value.lastPrecheck = {}
      currentDraft.value.lastPrecheckTarget = {}
      persistActiveTargetListingFields({ lastPrecheck: {}, lastPrecheckTarget: {} })
    }
    return packageDimensions
  }

  async function persistCurrentDraftForPublish() {
    if (!currentDraft.value.draftId) {
      throw new Error('请先从草稿箱选择一个草稿再进行发布预检。')
    }
    if (!currentPublishTargets.value.length) {
      throw new Error('当前草稿没有目标站点，请先在草稿箱选择目标市场。')
    }
    syncDraftPackageDimensionsFromPricingInput()
    persistActiveTargetListingFields(categoryPrecheck.value ? { categoryPrecheck: categoryPrecheck.value.raw || categoryPrecheck.value } : {})
    syncActivePublishTarget()
    const target = selectedPublishTarget.value
    const result = await saveDraftApi(currentDraft.value)
    currentDraft.value = result.draft
    currentDraftProductContext.value = result.productContext
    syncActivePublishTarget(target)
    applyMutationIndexes(result)
    return result.draft
  }


  return {
    product, productsIndex, draftsIndex, selectedProductIds, currentDraft, currentDraftProductContext,
    imagePrompt, collectForm, collectDiagnostics, collectBatchRows, browserDebugStatus, fillFormFromState,
    pricingInput, pricingResult, category, categoryQuery, categoryResults, categoryRecommendations,
    categoryAutoMatching, categoryAutoMatchMessage, categoryAutoMatchCurrent, categoryAutoMatchTotal, categoryAutoMatchProductName,
    categoryAttributeTranslations, categoryAttributeTranslationsSource, categoryAttributeTranslating, categoryAttributeLoading, categoryAttributeError, categoryResultTranslations,
    categoryResultTranslationsSource, categoryResultTranslating, categoryPrecheck, precheck, precheckResults, payloadPreview,
    copyGenerating, publishJob, publishJobStatus, publishLogs, mercadoLibreOrders, mercadoLibreOrderNotifications,
    mercadoLibreOrdersTotal, mercadoLibreOrdersCheckedAt, mercadoLibreRemoteItems, mercadoLibreRemoteStatus, mercadoLibreRemotePage, mercadoLibreRemotePerPage,
    mercadoLibreRemoteTotal, mercadoLibreRemoteTotalPages, activeMarketplace, platformOptions, publishResult, activePublishTargetKey,
    refreshPublishJob, refreshPublishLogs, refreshMercadoLibreRemoteItems, refreshMercadoLibreOrders, closeMercadoLibreRemoteItem, appConfig,
    aiConfig, storeConfig, storeAuthSummary, mercadolibreAuthChecklist, lastAuthResult, authLink,
    loadAiConfig, saveAiSettings, testAiSettings, testPlatformApiConfig, saveStoreConfig, testAuth,
    loadMercadoLibreChecklist, generateMercadoLibreAuthLink, openMercadoLibreAuth, refreshMercadoLibreAuthToken, runMercadoLibreAuthTest, exchangeMlCode,
    clearPlatformAuth, logs, loading, error, addLog, setError,
    requestSequence, currentStage, draft, currentPublishTargets, selectedPublishTarget, categoryAutoMatchTargetError,
    imagePool, selectedImages, selectedProducts, workflowSteps, progressPercent, activeMarketplaceSite,
    targetKey, targetSiteKey, cloneAttributes, cloneValidationErrors, categoryRecommendationForTarget, applyCategoryRecommendationForTarget,
    setCategoryRecommendation, categoryPrecheckFromTarget, normalizeDraftTarget, mergeTargetDetails, persistActiveTargetListingFields, invalidateCategoryAttributeLoad,
    applyTargetListingToDraft, configuredTargetsForLanguage, configuredSelectedTargets, targetPlatforms, pricingTargetKey, platformSite,
    pricingTargetDefaults, pricingTargetsFromDraft, normalizedDraftTargets, syncActivePublishTarget, pricingTargetRecord, recordNumber,
    pricingTargetInput, draftDetailFromProduct, applyMutationIndexes, restorePrecheckFromProduct, restoreCategoryFromProduct, syncCollectDiagnosticsFromProduct,
    syncPricingInputFromProduct, syncDraftPackageDimensionsFromPricingInput, persistCurrentDraftForPublish,
  }
}

export type WorkflowRuntime = ReturnType<typeof createWorkflowRuntime>
