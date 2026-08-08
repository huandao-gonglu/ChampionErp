import { PRODUCT_SCHEMA_VERSION } from '@/types/workflow.generated'
import type {

  CategoryAttributeDefinition,
  CategoryAttributeSchema,
  CategoryAttributeValue,
  CurrencyResolution,
  DraftDetail,
  DraftImageRef,
  DraftIndexItem,
  DraftImageRole,
  DraftProductContext,
  ImageAsset,
  Marketplace,
  MarketplaceOption,
  MarketplaceSiteOption,
  MarketplaceTargetSite,
  MercadoLibreAuthChecklist,
  Product,
  ProductIndexItem,
  PrecheckIssue,
  UnknownRecord,
} from '@/types/workflow'


export interface AppStateResponse {
  schemaVersion: number
  product: Product
  imagePool: ImageAsset[]
  appConfig: UnknownRecord
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
  mercadolibreAuthChecklist?: MercadoLibreAuthChecklist | null
  outputDir: string
  platformOptions: MarketplaceOption[]
}

export { PRODUCT_SCHEMA_VERSION }

export function normalizeCategoryDictionaryId(value: unknown): string {
  const text = String(value ?? '').trim()
  return text === '0' ? '' : text
}

export function isCategoryDictionaryAttribute(dictionaryId: unknown, explicit = false): boolean {
  const rawId = String(dictionaryId ?? '').trim()
  if (rawId === '0') return false
  return Boolean(rawId) || explicit
}

export interface ProductMutationResponse {
  ok: boolean
  product: Product
  draft?: DraftDetail
  productContext?: DraftProductContext
  imagePool: ImageAsset[]
  productsIndex: ProductIndexItem[]
  draftsIndex?: DraftIndexItem[]
  deleted?: number
  deletedDraftId?: string
  deletedDraftIds?: string[]
  deletedIds?: string[]
  missingIds?: string[]
  affectedProductIds?: string[]
  diagnostics?: UnknownRecord
  warning?: string
  message?: string
  raw?: UnknownRecord
}

export interface DraftMutationResponse {
  ok: boolean
  draft: DraftDetail
  productContext: DraftProductContext
  productsIndex: ProductIndexItem[]
  draftsIndex: DraftIndexItem[]
  message?: string
  raw: UnknownRecord
}

export interface AiPublicConfig {
  raw: UnknownRecord
}

export interface AuthResult {
  ok: boolean
  message: string
  error: string
  errorCode: string
  nextAction: string
  raw: UnknownRecord
}

export interface PayloadPreviewResult {
  platform: Marketplace
  site: string
  target: UnknownRecord | MarketplaceTargetSite
  status: string
  path: string
  payload: UnknownRecord
  warning: string
}

export interface ProductOperationResult {
  ok: boolean
  status: string
  message: string
  error: string
  product?: Product
  imagePool: ImageAsset[]
  productsIndex: ProductIndexItem[]
  draftsIndex?: DraftIndexItem[]
  raw: UnknownRecord
}

export interface DeleteProductsResult {
  ok: boolean
  deleted: number
  deletedIds: string[]
  missingIds: string[]
  productsIndex: ProductIndexItem[]
  product?: Product
  imagePool: ImageAsset[]
  message: string
  error: string
  raw: UnknownRecord
}

export function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function asRecord(value: unknown): UnknownRecord {
  return isRecord(value) ? value : {}
}

export function getString(record: UnknownRecord, keys: string[], fallback = ''): string {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value).trim()
    }
  }
  return fallback
}

export function getNumber(record: UnknownRecord, keys: string[], fallback = 0): number {
  const text = getString(record, keys)
  const value = Number.parseFloat(text)
  return Number.isFinite(value) ? value : fallback
}

export function getBoolean(record: UnknownRecord, keys: string[], fallback = false): boolean {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'boolean') return value
    if (typeof value === 'number') return value !== 0
    if (typeof value === 'string' && value.trim()) {
      return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase())
    }
  }
  return fallback
}

export function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean)
  if (typeof value === 'string') {
    return value
      .replaceAll('；', '\n')
      .replaceAll(';', '\n')
      .split(/\n|,/)
      .map((item) => item.trim())
      .filter(Boolean)
  }
  return []
}

export function wireStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item ?? '').trim()).filter(Boolean)
    : []
}

export function precheckIssueFromUnknown(value: unknown, fallbackSeverity: 'error' | 'warning'): PrecheckIssue {
  const record = asRecord(value)
  if (Object.keys(record).length) {
    const message = getString(record, ['message', 'error', 'code'], fallbackSeverity === 'error' ? '预检错误' : '预检提醒')
    return {
      code: getString(record, ['code']),
      field: getString(record, ['field']),
      message,
      severity: getString(record, ['severity'], fallbackSeverity),
      nextAction: getString(record, ['next_action']),
    }
  }
  return {
    code: '',
    field: '',
    message: String(value || '').trim(),
    severity: fallbackSeverity,
    nextAction: '',
  }
}

export function precheckIssues(value: unknown, fallbackSeverity: 'error' | 'warning'): PrecheckIssue[] {
  const rawItems = Array.isArray(value) ? value : stringList(value)
  return rawItems
    .map((item) => precheckIssueFromUnknown(item, fallbackSeverity))
    .filter((item) => item.message || item.field || item.code)
}

export function precheckIssueSummary(issue: PrecheckIssue): string {
  const prefix = issue.field ? `${issue.field}：` : ''
  const suffix = issue.nextAction ? `（${issue.nextAction}）` : ''
  return `${prefix}${issue.message}${suffix}`.trim()
}

export function platformList(value: unknown): Marketplace[] {
  const selected = new Set<Marketplace>()
  wireStringList(value).forEach((item) => {
    const platform = item.trim().toLowerCase() as Marketplace
    if (platform) selected.add(platform)
  })
  return Array.from(selected)
}

export function normalizeMarketplaceOptions(value: unknown): MarketplaceOption[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<Marketplace>()
  return value.flatMap((item) => {
    const record = asRecord(item)
    const key = getString(record, ['key']).toLowerCase() as Marketplace
    if (!key || seen.has(key)) return []
    seen.add(key)
    const siteSeen = new Set<string>()
    const sites = Array.isArray(record.sites)
      ? record.sites.flatMap((site): MarketplaceSiteOption[] => {
        const siteRecord = asRecord(site)
        const code = getString(siteRecord, ['code']).trim()
        const siteKey = getString(siteRecord, ['key'], code).trim()
        if (!siteKey || !code || siteSeen.has(siteKey.toLowerCase())) return []
        siteSeen.add(siteKey.toLowerCase())
        return [{
          key: siteKey,
          code,
          label: getString(siteRecord, ['label'], code),
          language: getString(siteRecord, ['language'], ''),
          marketCurrency: getString(siteRecord, ['market_currency'], ''),
          listingCurrency: getString(siteRecord, ['listing_currency'], ''),
        }]
      })
      : []
    return [{ key, label: getString(record, ['label'], key), sites }]
  })
}

export function normalizeAttributes(value: unknown): Record<string, CategoryAttributeValue> {
  return Object.fromEntries(Object.entries(asRecord(value)).map(([key, rawValue]) => {
    const record = asRecord(rawValue)
    const rawValues = Array.isArray(record.values) ? record.values : []
    const values = rawValues.flatMap((item) => {
      const option = asRecord(item)
      const dictionaryValueId = getNumber(option, ['dictionary_value_id', 'dictionaryValueId'])
      const optionValue = getString(option, ['value'])
      return dictionaryValueId > 0 && optionValue
        ? [{ dictionaryValueId, value: optionValue }]
        : []
    })
    return [
      key,
      values.length
        ? { values }
        : typeof rawValue === 'string' || typeof rawValue === 'number'
          ? String(rawValue)
          : '',
    ]
  }))
}

export function toBackendAttributes(value: Record<string, CategoryAttributeValue>): UnknownRecord {
  return Object.fromEntries(Object.entries(value || {}).map(([key, rawValue]) => {
    if (typeof rawValue === 'string') return [key, rawValue]
    return [key, {
      values: (rawValue.values || []).map((item) => ({
        dictionary_value_id: item.dictionaryValueId,
        value: item.value,
      })),
    }]
  }))
}

export function normalizeValidationErrors(value: unknown): Array<UnknownRecord | string> {
  return Array.isArray(value)
    ? value.map((item) => typeof item === 'string' ? item : asRecord(item))
    : []
}

export function normalizeCategoryAttributeDefinition(value: unknown, requiredFallback: boolean): CategoryAttributeDefinition | null {
  const record = asRecord(value)
  const raw = asRecord(record.raw)
  const id = getString(record, ['id'])
  if (!id) return null
  const options = (Array.isArray(record.options) ? record.options : [])
    .map((item) => String(item ?? '').trim())
    .filter(Boolean)
  const rawDictionaryId = getString(record, ['dictionary_id'], getString(raw, ['dictionary_id']))
  return {
    id,
    name: getString(record, ['name'], id),
    required: getBoolean(record, ['required'], requiredFallback),
    options,
    valueType: getString(record, ['value_type'], 'string'),
    unit: getString(record, ['unit']),
    description: getString(record, ['description']),
    dictionaryId: normalizeCategoryDictionaryId(rawDictionaryId),
    isDictionary: isCategoryDictionaryAttribute(rawDictionaryId, getBoolean(record, ['is_dictionary'])),
    isCollection: getBoolean(record, ['is_collection'], getBoolean(raw, ['is_collection'])),
    maxValueCount: getNumber(record, ['max_value_count'], getNumber(raw, ['max_value_count'])),
    categoryDependent: getBoolean(record, ['category_dependent'], getBoolean(raw, ['category_dependent'])),
  }
}

export function normalizeCategoryAttributeSchema(value: unknown): CategoryAttributeSchema | null {
  const record = asRecord(value)
  const categoryId = getString(record, ['category_id'])
  if (!categoryId) return null
  const normalizeList = (items: unknown, requiredFallback: boolean) => Array.isArray(items)
    ? items
      .map((item) => normalizeCategoryAttributeDefinition(item, requiredFallback))
      .filter((item): item is CategoryAttributeDefinition => Boolean(item))
    : []
  return {
    version: Math.max(1, getNumber(record, ['version'], 1)),
    platform: getString(record, ['platform']).toLowerCase(),
    site: getString(record, ['site']),
    categoryId,
    categoryPath: getString(record, ['category_path']),
    source: getString(record, ['source']),
    fetchedAt: getString(record, ['fetched_at']),
    required: normalizeList(record.required, true),
    optional: normalizeList(record.optional, false),
  }
}

export function targetListingFields(record: UnknownRecord, fallback?: Partial<MarketplaceTargetSite>): Partial<MarketplaceTargetSite> {
  const fallbackAttributes = fallback?.attributes || {}
  const fallbackValidationErrors = fallback?.validationErrors || []
  const hasAnyField = (keys: string[]) => keys.some((key) => Object.prototype.hasOwnProperty.call(record, key))
  const hasCategoryId = hasAnyField(['category_id'])
  const hasDescriptionCategoryId = hasAnyField(['description_category_id'])
  const hasCategoryPath = hasAnyField(['category_path'])
  const hasCategoryAttributeSchema = hasAnyField(['category_attribute_schema'])
  const hasAttributes = hasAnyField(['attributes'])
  const hasValidationErrors = hasAnyField(['validation_errors'])
  const hasCategoryPrecheck = hasAnyField(['category_precheck'])
  const hasPublishStatus = hasAnyField(['publish_status'])
  const hasStatus = hasAnyField(['status'])
  const hasLastPrecheck = hasAnyField(['last_precheck'])
  const hasLastPrecheckTarget = hasAnyField(['last_precheck_target'])
  return {
    categoryId: hasCategoryId ? getString(record, ['category_id']) : fallback?.categoryId || '',
    descriptionCategoryId: hasDescriptionCategoryId
      ? getString(record, ['description_category_id'])
      : fallback?.descriptionCategoryId || '',
    categoryPath: hasCategoryPath ? getString(record, ['category_path']) : fallback?.categoryPath || '',
    categoryAttributeSchema: hasCategoryAttributeSchema
      ? normalizeCategoryAttributeSchema(record.category_attribute_schema)
      : fallback?.categoryAttributeSchema || null,
    attributes: hasAttributes ? normalizeAttributes(record.attributes) : { ...fallbackAttributes },
    validationErrors: hasValidationErrors
      ? normalizeValidationErrors(record.validation_errors)
      : [...fallbackValidationErrors],
    categoryPrecheck: hasCategoryPrecheck
      ? asRecord(record.category_precheck)
      : fallback?.categoryPrecheck || {},
    publishStatus: hasPublishStatus ? getString(record, ['publish_status']) : fallback?.publishStatus || '',
    status: hasStatus ? getString(record, ['status']) : fallback?.status ? String(fallback.status) : '',
    lastPrecheck: hasLastPrecheck
      ? asRecord(record.last_precheck)
      : fallback?.lastPrecheck || {},
    lastPrecheckTarget: hasLastPrecheckTarget
      ? asRecord(record.last_precheck_target)
      : fallback?.lastPrecheckTarget || {},
  }
}

export function normalizeTargetSites(value: unknown, platform: Marketplace, site: string, language: string, listingCurrency: string, fallback?: Partial<MarketplaceTargetSite>): MarketplaceTargetSite[] {
  const rawItems = Array.isArray(value) ? value : []
  const targets = rawItems.flatMap((value, index): MarketplaceTargetSite[] => {
    const record = asRecord(value)
    const targetPlatform = getString(record, ['platform']).toLowerCase() as Marketplace
    const targetSite = getString(record, ['site'])
    if (!targetPlatform || !targetSite) return []
    return [{
      platform: targetPlatform,
      site: targetSite,
      language: getString(record, ['language'], language),
      marketCurrency: getString(record, ['market_currency'], fallback?.marketCurrency || ''),
      listingCurrency: getString(record, ['listing_currency'], listingCurrency),
      currencyResolution: (() => {
        const resolution = asRecord(record.currency_resolution)
        return {
          mode: getString(resolution, ['mode'], 'unresolved') as CurrencyResolution['mode'],
          listingCurrency: getString(resolution, ['listing_currency']),
          allowedCurrencies: wireStringList(resolution.allowed_currencies),
          source: getString(resolution, ['source']),
          verifiedAt: getString(resolution, ['verified_at']),
        }
      })(),
      ...targetListingFields(record, index === 0 ? fallback : undefined),
    }]
  })
  return targets.length ? targets : [{ platform, site, language, marketCurrency: fallback?.marketCurrency || '', listingCurrency, ...targetListingFields({}, fallback) }]
}

export function normalizeDimensions(value: unknown) {
  const record = asRecord(value)
  return {
    lengthCm: getString(record, ['length_cm']),
    widthCm: getString(record, ['width_cm']),
    heightCm: getString(record, ['height_cm']),
  }
}

export function normalizeImageAsset(value: unknown): ImageAsset {
  const record = asRecord(value)
  const platforms = platformList(record.platforms)
  const width = getNumber(record, ['width'])
  const height = getNumber(record, ['height'])
  const id = getString(record, ['id'], `image_${Math.random().toString(36).slice(2, 8)}`)
  const path = getString(record, ['path'])
  const url = getString(record, ['url'])
  const previewUrl = getString(record, ['preview_url'], url || path)
  return {
    id,
    url,
    path,
    previewUrl,
    origin: getString(record, ['origin'], 'source'),
    usage: getString(record, ['usage'], 'detail'),
    platforms,
    isMain: getBoolean(record, ['is_main']),
    selected: getBoolean(record, ['selected'], true),
    status: getString(record, ['status'], previewUrl ? 'ready' : 'empty'),
    width,
    height,
    targetLanguage: getString(record, ['target_language']) || undefined,
    derivedFromId: getString(record, ['derived_from_id']) || undefined,
    provider: getString(record, ['provider']) || undefined,
    storageKey: getString(record, ['storage_key']) || undefined,
    contentSha256: getString(record, ['content_sha256']) || undefined,
    deliveryProvider: getString(record, ['delivery_provider']) || undefined,
    deliveryError: getString(record, ['delivery_error']) || undefined,
  }
}

const draftImageRoles: DraftImageRole[] = ['main', 'detail', 'size', 'scene', 'package', 'selling_point', 'material', 'other']

export function normalizeDraftImageRole(value: unknown, order: number): DraftImageRole {
  const role = String(value || '').trim().toLowerCase() as DraftImageRole
  if (draftImageRoles.includes(role)) return role
  return order === 0 ? 'main' : 'detail'
}

export function normalizeDraftImageRef(value: unknown, order = 0): DraftImageRef | null {
  const record = asRecord(value)
  const assetId = getString(record, ['asset_id'])
  if (!assetId) return null
  return {
    assetId,
    role: normalizeDraftImageRole(record.role, order),
    order: getNumber(record, ['order'], order),
    label: getString(record, ['label']) || undefined,
    note: getString(record, ['note']) || undefined,
    altText: getString(record, ['alt_text']) || undefined,
    sourceAssetId: getString(record, ['source_asset_id']) || undefined,
  }
}

export function normalizeDraftImageRefs(value: unknown): DraftImageRef[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  const refs = value.flatMap((item, index) => {
    const ref = normalizeDraftImageRef(item, index)
    if (!ref || seen.has(ref.assetId)) return []
    seen.add(ref.assetId)
    return [ref]
  }).sort((left, right) => left.order - right.order)
  refs.forEach((ref, index) => { ref.order = index })
  let mainSeen = false
  refs.forEach((ref) => {
    if (ref.role !== 'main') return
    if (mainSeen) {
      ref.role = 'detail'
    } else {
      mainSeen = true
    }
  })
  if (refs.length && !mainSeen) refs[0].role = 'main'
  return refs
}

export function toBackendDraftImageRef(ref: DraftImageRef): UnknownRecord {
  return {
    asset_id: ref.assetId,
    role: ref.role,
    order: ref.order,
    label: ref.label,
    note: ref.note,
    alt_text: ref.altText,
    source_asset_id: ref.sourceAssetId,
  }
}

export function toBackendCategoryAttributeDefinition(attribute: CategoryAttributeDefinition): UnknownRecord {
  const dictionaryId = normalizeCategoryDictionaryId(attribute.dictionaryId)
  return {
    id: attribute.id,
    name: attribute.name,
    required: attribute.required,
    options: attribute.options || [],
    value_type: attribute.valueType || 'string',
    unit: attribute.unit || '',
    description: attribute.description || '',
    dictionary_id: dictionaryId,
    is_dictionary: isCategoryDictionaryAttribute(attribute.dictionaryId, attribute.isDictionary),
    is_collection: Boolean(attribute.isCollection),
    max_value_count: attribute.maxValueCount || 0,
    category_dependent: Boolean(attribute.categoryDependent),
  }
}

export function toBackendCategoryAttributeSchema(schema: CategoryAttributeSchema | null | undefined): UnknownRecord {
  if (!schema) return {}
  return {
    version: schema.version,
    platform: schema.platform,
    site: schema.site,
    category_id: schema.categoryId,
    category_path: schema.categoryPath,
    source: schema.source,
    fetched_at: schema.fetchedAt,
    required: schema.required.map(toBackendCategoryAttributeDefinition),
    optional: schema.optional.map(toBackendCategoryAttributeDefinition),
  }
}

export function toBackendTargetSite(target: MarketplaceTargetSite): UnknownRecord {
  return {
    platform: target.platform,
    site: target.site,
    language: target.language,
    market_currency: target.marketCurrency,
    listing_currency: target.listingCurrency,
    currency_resolution: target.currencyResolution ? {
      mode: target.currencyResolution.mode,
      listing_currency: target.currencyResolution.listingCurrency,
      allowed_currencies: target.currencyResolution.allowedCurrencies,
      source: target.currencyResolution.source,
      verified_at: target.currencyResolution.verifiedAt,
    } : {},
    category_id: target.categoryId || '',
    description_category_id: target.descriptionCategoryId || '',
    category_path: target.categoryPath || '',
    category_attribute_schema: toBackendCategoryAttributeSchema(target.categoryAttributeSchema),
    attributes: toBackendAttributes(target.attributes || {}),
    validation_errors: target.validationErrors || [],
    category_precheck: target.categoryPrecheck || {},
    publish_status: target.publishStatus || '',
    status: target.status || '',
    last_precheck: target.lastPrecheck || {},
    last_precheck_target: target.lastPrecheckTarget || {},
  }
}
