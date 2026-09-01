import { PRODUCT_SCHEMA_VERSION } from '@/types/workflow.generated'
import type {

  CategoryAttributeValue,
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
  validationDigest: string
  summary: UnknownRecord
  warnings: PrecheckIssue[]
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
    const severity = getString(record, ['severity'], fallbackSeverity)
    const message = getString(
      record,
      ['message'],
      severity === 'warning'
        ? '预检返回了未说明原因的提醒'
        : '预检返回了未说明原因的阻断',
    )
    return {
      code: getString(record, ['code']),
      field: getString(record, ['field']),
      message,
      severity,
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
  const suffix = issue.nextAction ? `（${issue.nextAction}）` : ''
  return `${issue.message}${suffix}`.trim()
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
        }]
      })
      : []
    return [{
      key,
      label: getString(record, ['label'], key),
      titleLimit: getNumber(record, ['title_limit'], 0) || undefined,
      sites,
    }]
  })
}

export function normalizeAttributes(value: unknown): Record<string, CategoryAttributeValue> {
  return Object.fromEntries(Object.entries(asRecord(value)).map(([key, rawValue]) => {
    const record = asRecord(rawValue)
    const rawValues = Array.isArray(record.values) ? record.values : []
    const values = rawValues.flatMap((item) => {
      const option = asRecord(item)
      const rawId = option.dictionary_value_id ?? option.dictionaryValueId
      // 按字符串保留枚举值 ID：Yandex 的大 ID 经过 Number() 会精度丢失。
      const dictionaryValueId = rawId === undefined || rawId === null ? '' : String(rawId).trim()
      const optionValue = getString(option, ['value'])
      if (!optionValue || dictionaryValueId === '0') return []
      return [{
        ...(dictionaryValueId ? { dictionaryValueId } : {}),
        value: optionValue,
      }]
    })
    const isUnitValue = typeof rawValue === 'object'
      && rawValue !== null
      && !values.length
      && String((rawValue as UnknownRecord).unit ?? '').trim() !== ''
    return [
      key,
      values.length
        ? { values }
        : typeof rawValue === 'string' || typeof rawValue === 'number'
          ? String(rawValue)
          : isUnitValue
            ? {
              value: String((rawValue as UnknownRecord).value ?? ''),
              unit: String((rawValue as UnknownRecord).unit ?? ''),
            }
            : '',
    ]
  }))
}

export function toBackendAttributes(value: Record<string, CategoryAttributeValue>): UnknownRecord {
  return Object.fromEntries(Object.entries(value || {}).map(([key, rawValue]) => {
    if (typeof rawValue === 'string') return [key, rawValue]
    if ('values' in rawValue) {
      return [key, {
        values: (rawValue.values || []).map((item) => ({
          ...(String(item.dictionaryValueId ?? '').trim()
            ? { dictionary_value_id: String(item.dictionaryValueId).trim() }
            : {}),
          value: item.value,
        })),
      }]
    }
    return [key, { value: rawValue.value, unit: rawValue.unit }]
  }))
}

export function normalizeValidationErrors(value: unknown): Array<UnknownRecord | string> {
  return Array.isArray(value)
    ? value.map((item) => typeof item === 'string' ? item : asRecord(item))
    : []
}

export function normalizeSitesToSell(value: unknown): NonNullable<MarketplaceTargetSite['sitesToSell']> {
  if (!Array.isArray(value)) return []
  const seenSites = new Set<string>()
  return value.flatMap((item) => {
    const record = asRecord(item)
    const siteId = getString(record, ['site_id']).toUpperCase()
    const logisticType = getString(record, ['logistic_type']).toLowerCase()
    if (!siteId || siteId === 'CBT' || !logisticType) return []
    if (seenSites.has(siteId)) return []
    seenSites.add(siteId)
    const normalized: NonNullable<MarketplaceTargetSite['sitesToSell']>[number] = { siteId, logisticType }
    if (Object.prototype.hasOwnProperty.call(record, 'price')) normalized.price = String(record.price ?? '')
    if (Object.prototype.hasOwnProperty.call(record, 'listing_type_id')) normalized.listingTypeId = String(record.listing_type_id ?? '')
    if (Object.prototype.hasOwnProperty.call(record, 'status')) normalized.status = String(record.status ?? '')
    if (Object.prototype.hasOwnProperty.call(record, 'free_shipping') && typeof record.free_shipping === 'boolean') {
      normalized.freeShipping = record.free_shipping
    }
    if (Object.prototype.hasOwnProperty.call(record, 'sale_terms') && Array.isArray(record.sale_terms)) {
      normalized.saleTerms = record.sale_terms
        .filter((term) => isRecord(term))
        .map((term) => ({ ...term }))
    }
    if (Object.prototype.hasOwnProperty.call(record, 'net_proceeds')) normalized.netProceeds = String(record.net_proceeds ?? '')
    return [normalized]
  })
}

export function toBackendSitesToSell(value: MarketplaceTargetSite['sitesToSell']): UnknownRecord[] {
  const seenSites = new Set<string>()
  return (value || []).flatMap((item) => {
    const siteId = String(item.siteId || '').trim().toUpperCase()
    const logisticType = String(item.logisticType || '').trim().toLowerCase()
    if (!siteId || siteId === 'CBT' || !logisticType || seenSites.has(siteId)) return []
    seenSites.add(siteId)
    return [{
      site_id: siteId,
      logistic_type: logisticType,
      ...(item.price !== undefined ? { price: String(item.price) } : {}),
      ...(item.listingTypeId !== undefined ? { listing_type_id: String(item.listingTypeId) } : {}),
      ...(item.status !== undefined ? { status: String(item.status) } : {}),
      ...(item.freeShipping !== undefined ? { free_shipping: Boolean(item.freeShipping) } : {}),
      ...(item.saleTerms !== undefined ? { sale_terms: item.saleTerms.map((term) => ({ ...term })) } : {}),
      ...(item.netProceeds !== undefined ? { net_proceeds: String(item.netProceeds) } : {}),
    }]
  })
}

export function targetListingFields(record: UnknownRecord, fallback?: Partial<MarketplaceTargetSite>): Partial<MarketplaceTargetSite> {
  const fallbackAttributes = fallback?.attributes || {}
  const fallbackValidationErrors = fallback?.validationErrors || []
  const hasAnyField = (keys: string[]) => keys.some((key) => Object.prototype.hasOwnProperty.call(record, key))
  const hasCategoryId = hasAnyField(['category_id'])
  const hasDescriptionCategoryId = hasAnyField(['description_category_id'])
  const hasCategoryPath = hasAnyField(['category_path'])
  const hasAttributes = hasAnyField(['attributes'])
  const hasValidationErrors = hasAnyField(['validation_errors'])
  const hasCategoryPrecheck = hasAnyField(['category_precheck'])
  const hasPublishStatus = hasAnyField(['publish_status'])
  const hasStatus = hasAnyField(['status'])
  const hasLastPrecheck = hasAnyField(['last_precheck'])
  const hasLastPrecheckTarget = hasAnyField(['last_precheck_target'])
  const hasLastPublishTask = hasAnyField(['last_publish_task'])
  const hasSitesToSell = hasAnyField(['sites_to_sell'])
  return {
    sitesToSell: hasSitesToSell
      ? normalizeSitesToSell(record.sites_to_sell)
      : (fallback?.sitesToSell || []).map((item) => ({ ...item })),
    categoryId: hasCategoryId ? getString(record, ['category_id']) : fallback?.categoryId || '',
    descriptionCategoryId: hasDescriptionCategoryId
      ? getString(record, ['description_category_id'])
      : fallback?.descriptionCategoryId || '',
    categoryPath: hasCategoryPath ? getString(record, ['category_path']) : fallback?.categoryPath || '',
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
    lastPublishTask: hasLastPublishTask
      ? asRecord(record.last_publish_task)
      : fallback?.lastPublishTask || {},
  }
}

export function normalizeTargetSites(value: unknown, platform: Marketplace, site: string, language: string, fallback?: Partial<MarketplaceTargetSite>): MarketplaceTargetSite[] {
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
      // 发布币种是核价时写入的店铺配置快照，不再从站点 option 回填。
      listingCurrency: getString(record, ['listing_currency']),
      currencyFingerprint: getString(record, ['currency_fingerprint']),
      ...targetListingFields(record, index === 0 ? fallback : undefined),
    }]
  })
  return targets.length ? targets : [{ platform, site, language, listingCurrency: '', ...targetListingFields({}, fallback) }]
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

export function toBackendTargetSite(target: MarketplaceTargetSite): UnknownRecord {
  return {
    platform: target.platform,
    site: target.site,
    language: target.language,
    listing_currency: target.listingCurrency,
    currency_fingerprint: target.currencyFingerprint || '',
    sites_to_sell: toBackendSitesToSell(target.sitesToSell),
    category_id: target.categoryId || '',
    description_category_id: target.descriptionCategoryId || '',
    category_path: target.categoryPath || '',
    attributes: toBackendAttributes(target.attributes || {}),
    validation_errors: target.validationErrors || [],
    category_precheck: target.categoryPrecheck || {},
    publish_status: target.publishStatus || '',
    status: target.status || '',
    last_precheck: target.lastPrecheck || {},
    last_precheck_target: target.lastPrecheckTarget || {},
    last_publish_task: target.lastPublishTask || {},
  }
}
