import { createEmptyDraft, createEmptyProduct } from '@/constants/initialState'
import { listingLanguageLabel } from '@/constants/locales'
import type {
  BrowserDebugStatus,
  DraftDetail,
  DraftIndexItem,
  DraftProductContext,
  ImageAsset,
  Marketplace,
  MarketplaceDraft,
  MercadoLibreAuthChecklist,
  MercadoLibreOrderNotification,
  Product,
  ProductIndexItem,
  PublishLogItem,
  PrecheckIssue,
  UnknownRecord,
} from '@/types/workflow'

export interface AppStateResponse {
  product: Product
  imagePool: ImageAsset[]
  appConfig: UnknownRecord
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
  mercadolibreAuthChecklist?: MercadoLibreAuthChecklist | null
  mercadolibreOrderNotifications?: MercadoLibreOrderNotification[]
  outputDir: string
  productsIndex: ProductIndexItem[]
  draftsIndex?: DraftIndexItem[]
  publishLogs: PublishLogItem[]
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

export function precheckIssueFromUnknown(value: unknown, fallbackSeverity: 'error' | 'warning'): PrecheckIssue {
  const record = asRecord(value)
  if (Object.keys(record).length) {
    const message = getString(record, ['message', 'error', 'code'], fallbackSeverity === 'error' ? '预检错误' : '预检提醒')
    return {
      code: getString(record, ['code']),
      field: getString(record, ['field']),
      message,
      severity: getString(record, ['severity'], fallbackSeverity),
      nextAction: getString(record, ['next_action', 'nextAction']),
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
  const allowed = new Set<Marketplace>(['mercadolibre', 'wildberries', 'ozon'])
  return stringList(value).filter((item): item is Marketplace => allowed.has(item as Marketplace))
}

export function normalizeDimensions(value: unknown) {
  const record = asRecord(value)
  if (Object.keys(record).length) {
    return {
      lengthCm: getString(record, ['lengthCm', 'length_cm', 'length', 'package_length']),
      widthCm: getString(record, ['widthCm', 'width_cm', 'width', 'package_width']),
      heightCm: getString(record, ['heightCm', 'height_cm', 'height', 'package_height']),
    }
  }
  const text = String(value || '')
  const match = text.match(/(\d+(?:\.\d+)?)\D+(\d+(?:\.\d+)?)\D+(\d+(?:\.\d+)?)/)
  return {
    lengthCm: match?.[1] || '',
    widthCm: match?.[2] || '',
    heightCm: match?.[3] || '',
  }
}

export function normalizeImageAsset(value: unknown): ImageAsset {
  const record = asRecord(value)
  const platforms = platformList(record.platforms ?? record.platforms_json)
  const width = getNumber(record, ['width', 'width_px'])
  const height = getNumber(record, ['height', 'height_px'])
  const id = getString(record, ['id', 'asset_id'], `image_${Math.random().toString(36).slice(2, 8)}`)
  const path = getString(record, ['path', 'local_path'])
  const url = getString(record, ['url'])
  const previewUrl = getString(record, ['previewUrl', 'preview_url'], url || path)
  return {
    id,
    url,
    path,
    previewUrl,
    origin: getString(record, ['origin', 'source_kind'], 'source'),
    usage: getString(record, ['usage', 'asset_type'], 'detail'),
    platforms: platforms.length ? platforms : ['mercadolibre', 'wildberries', 'ozon'],
    isMain: getBoolean(record, ['isMain', 'is_main', 'is_primary']),
    selected: getBoolean(record, ['selected'], true),
    status: getString(record, ['status'], previewUrl ? 'ready' : 'empty'),
    width,
    height,
    targetLanguage: getString(record, ['targetLanguage', 'target_language']) || undefined,
    translatedFromId: getString(record, ['translatedFromId', 'translated_from_id']) || undefined,
  }
}

export function normalizeDraft(value: unknown, language: string): MarketplaceDraft {
  const record = asRecord(value)
  const draft = createEmptyDraft(language)
  const packageDimensions = asRecord(record.package_dimensions ?? record.packageDimensions)
  const saleTerms = Array.isArray(record.sale_terms) ? record.sale_terms.map((item) => asRecord(item)) : Array.isArray(record.saleTerms) ? record.saleTerms.map((item) => asRecord(item)) : []
  return {
    ...draft,
    draftId: getString(record, ['draftId', 'draft_id']),
    enabled: getBoolean(record, ['enabled'], draft.enabled),
    title: getString(record, ['title']),
    description: getString(record, ['description']),
    bullets: stringList(record.bullets),
    categoryId: getString(record, ['categoryId', 'category_id', 'subject_id']),
    categoryPath: getString(record, ['categoryPath', 'category_path']),
    attributes: Object.fromEntries(Object.entries(asRecord(record.attributes)).map(([key, value]) => [key, String(value ?? '')])),
    price: getString(record, ['price', 'sale_price']),
    images: stringList(record.images),
    status: getString(record, ['status'], draft.status) as MarketplaceDraft['status'],
    language: getString(record, ['language'], language),
    stock: getString(record, ['stock']),
    sku: getString(record, ['sku']),
    upc: getString(record, ['upc', 'gtin', 'barcode']),
    packageDimensions: {
      lengthCm: getString(packageDimensions, ['length_cm', 'lengthCm']),
      widthCm: getString(packageDimensions, ['width_cm', 'widthCm']),
      heightCm: getString(packageDimensions, ['height_cm', 'heightCm']),
      weightKg: getString(packageDimensions, ['weight_kg', 'weightKg']),
    },
    saleTerms,
    allowGtinExemption: getBoolean(record, ['allow_gtin_exemption', 'allowGtinExemption', 'gtin_exempt']),
    validationErrors: Array.isArray(record.validation_errors)
      ? record.validation_errors.map((item) => typeof item === 'string' ? item : asRecord(item))
      : Array.isArray(record.validationErrors)
        ? record.validationErrors.map((item) => typeof item === 'string' ? item : asRecord(item))
        : [],
  }
}

export function normalizeBackendProduct(value: unknown, imagePoolOverride?: unknown): Product {
  const record = asRecord(value)
  const source = asRecord(record.source)
  const drafts = asRecord(record.drafts)
  const imagePoolRaw = Array.isArray(imagePoolOverride)
    ? imagePoolOverride
    : Array.isArray(source.image_pool)
      ? source.image_pool
      : Array.isArray(source.imagePool)
        ? source.imagePool
        : []
  const dimensions = normalizeDimensions(source.dimensions ?? record.dimensions)
  const product = createEmptyProduct()
  return {
    ...product,
    productId: getString(record, ['productId', 'product_id', 'id']),
    name: getString(record, ['name', 'title'], getString(source, ['title'])),
    brand: getString(record, ['brand'], getString(source, ['brand'])),
    model: getString(record, ['model'], getString(source, ['model'])),
    category: getString(record, ['category', 'category_name']),
    sku: getString(record, ['sku']),
    stock: getString(record, ['stock']),
    upc: getString(record, ['upc']),
    cost: getString(record, ['cost', 'source_price_cny_for_cost'], getString(source, ['price'])),
    materials: stringList(record.materials ?? record.source_material ?? source.materials ?? source.material),
    sellingPoints: stringList(record.sellingPoints ?? record.selling_points ?? source.bullets),
    packageIncludes: stringList(record.packageIncludes ?? record.package_includes ?? source.package_contents),
    source: {
      sourceUrl: getString(source, ['sourceUrl', 'source_url'], getString(record, ['source_url'])),
      sourcePlatform: getString(source, ['sourcePlatform', 'source_platform'], getString(record, ['source_platform'])),
      title: getString(source, ['title'], getString(record, ['name', 'title'])),
      price: getString(source, ['price'], getString(record, ['detected_price', 'source_price_cny'])),
      currency: getString(source, ['currency'], getString(record, ['detected_currency'], '')),
      description: getString(source, ['description'], getString(record, ['description', 'source_text'])),
      dimensions,
      weightKg: getString(source, ['weightKg', 'weight_kg'], getString(record, ['weight_kg', 'source_weight_kg'])),
      imagePool: imagePoolRaw.map(normalizeImageAsset),
      attributes: Object.fromEntries(Object.entries(asRecord(source.attributes ?? record.attributes)).map(([key, value]) => [key, String(value ?? '')])),
      collectStatus: getString(source, ['collect_status'], getString(record, ['collect_status'])),
      collectDiagnostics: asRecord(source.collect_diagnostics),
    },
    drafts: {
      mercadolibre: normalizeDraft(drafts.mercadolibre, listingLanguageLabel('mercadolibre')),
      wildberries: normalizeDraft(drafts.wildberries, listingLanguageLabel('wildberries')),
      ozon: normalizeDraft(drafts.ozon, listingLanguageLabel('ozon')),
    },
    raw: record,
  }
}

export function toBackendImageAsset(image: ImageAsset): UnknownRecord {
  return {
    id: image.id,
    asset_id: image.id,
    url: image.url,
    path: image.path,
    local_path: image.path,
    preview_url: image.previewUrl,
    origin: image.origin,
    usage: image.usage,
    platforms: image.platforms,
    is_main: image.isMain,
    selected: image.selected,
    status: image.status,
    width: image.width,
    height: image.height,
    target_language: image.targetLanguage,
    translated_from_id: image.translatedFromId,
  }
}

export function toBackendDraft(draft: MarketplaceDraft): UnknownRecord {
  return {
    enabled: draft.enabled,
    draft_id: draft.draftId,
    title: draft.title,
    description: draft.description,
    bullets: draft.bullets,
    category_id: draft.categoryId,
    category_path: draft.categoryPath,
    attributes: draft.attributes,
    price: draft.price,
    sale_price: draft.price,
    images: draft.images,
    status: draft.status,
    language: draft.language,
    stock: draft.stock,
    sku: draft.sku,
    upc: draft.upc,
    package_dimensions: {
      length_cm: draft.packageDimensions.lengthCm,
      width_cm: draft.packageDimensions.widthCm,
      height_cm: draft.packageDimensions.heightCm,
      weight_kg: draft.packageDimensions.weightKg,
    },
    sale_terms: draft.saleTerms,
    allow_gtin_exemption: draft.allowGtinExemption,
  }
}

export function normalizeDraftDetail(value: unknown): DraftDetail {
  const record = asRecord(value)
  const platform = (getString(record, ['platform']) || 'mercadolibre') as Marketplace
  const draft = normalizeDraft(record, listingLanguageLabel(platform))
  return {
    ...draft,
    productId: getString(record, ['productId', 'product_id']),
    platform,
    site: getString(record, ['site', 'site_id']),
    createdAt: getString(record, ['createdAt', 'created_at']),
    updatedAt: getString(record, ['updatedAt', 'updated_at']),
    raw: record,
  }
}

export function normalizeDraftProductContext(value: unknown): DraftProductContext {
  const record = asRecord(value)
  const dimensions = normalizeDimensions(record.dimensions)
  const imagePoolRaw = Array.isArray(record.image_pool)
    ? record.image_pool
    : Array.isArray(record.imagePool)
      ? record.imagePool
      : []
  return {
    productId: getString(record, ['productId', 'product_id']),
    title: getString(record, ['title']),
    sourceTitle: getString(record, ['sourceTitle', 'source_title']),
    sourcePlatform: getString(record, ['sourcePlatform', 'source_platform']),
    sourceUrl: getString(record, ['sourceUrl', 'source_url']),
    brand: getString(record, ['brand']),
    model: getString(record, ['model']),
    sku: getString(record, ['sku']),
    stock: getString(record, ['stock']),
    cost: getString(record, ['cost']),
    sourcePrice: getString(record, ['sourcePrice', 'source_price']),
    currency: getString(record, ['currency']),
    weightKg: getString(record, ['weightKg', 'weight_kg']),
    dimensions,
    imagePool: imagePoolRaw.map(normalizeImageAsset),
    raw: record,
  }
}

export function toBackendDraftDetail(draft: DraftDetail): UnknownRecord {
  return {
    ...asRecord(draft.raw),
    ...toBackendDraft(draft),
    draft_id: draft.draftId,
    product_id: draft.productId,
    platform: draft.platform,
    site: draft.site,
  }
}

export function toBackendProduct(product: Product): UnknownRecord {
  const rawProduct = { ...asRecord(product.raw) }
  delete rawProduct.drafts
  return {
    ...rawProduct,
    product_id: product.productId,
    id: product.productId,
    name: product.name,
    brand: product.brand,
    model: product.model,
    category: product.category,
    sku: product.sku,
    stock: product.stock,
    upc: product.upc,
    cost: product.cost,
    materials: product.materials,
    selling_points: product.sellingPoints,
    package_includes: product.packageIncludes,
    source: {
      ...asRecord(product.raw.source),
      source_url: product.source.sourceUrl,
      source_platform: product.source.sourcePlatform,
      title: product.source.title,
      price: product.source.price,
      currency: product.source.currency,
      description: product.source.description,
      dimensions: {
        length_cm: product.source.dimensions.lengthCm,
        width_cm: product.source.dimensions.widthCm,
        height_cm: product.source.dimensions.heightCm,
      },
      weight_kg: product.source.weightKg,
      image_pool: product.source.imagePool.map(toBackendImageAsset),
      attributes: product.source.attributes,
      collect_status: product.source.collectStatus,
      collect_diagnostics: product.source.collectDiagnostics,
    },
    source_url: product.source.sourceUrl,
    source_platform: product.source.sourcePlatform,
    dimensions: `${product.source.dimensions.lengthCm} x ${product.source.dimensions.widthCm} x ${product.source.dimensions.heightCm} cm`,
    weight_kg: product.source.weightKg,
  }
}

export function ensureOk(data: UnknownRecord, fallbackMessage: string): void {
  if (data.ok === false) {
    throw new Error(getString(data, ['error', 'message'], fallbackMessage))
  }
}

export function normalizeProductsIndex(value: unknown): ProductIndexItem[] {
  return Array.isArray(value) ? value.map(normalizeProductIndexItem) : []
}

export function normalizeDraftsIndex(value: unknown): DraftIndexItem[] {
  return Array.isArray(value) ? value.map(normalizeDraftIndexItem) : []
}

export function normalizeDraftIndexItem(value: unknown): DraftIndexItem {
  const record = asRecord(value)
  const platform = getString(record, ['platform']) as Marketplace
  return {
    draftId: getString(record, ['draftId', 'draft_id']),
    productId: getString(record, ['productId', 'product_id']),
    platform,
    site: getString(record, ['site']),
    status: getString(record, ['status']) as DraftIndexItem['status'],
    title: getString(record, ['title']),
    productTitle: getString(record, ['productTitle', 'product_title']),
    mainImage: getString(record, ['mainImage', 'main_image', 'image']),
    sourcePlatform: getString(record, ['sourcePlatform', 'source_platform']),
    sourceUrl: getString(record, ['sourceUrl', 'source_url']),
    categoryId: getString(record, ['categoryId', 'category_id']),
    categoryPath: getString(record, ['categoryPath', 'category_path']),
    price: getString(record, ['price']),
    publishStatus: getString(record, ['publishStatus', 'publish_status']),
    createdAt: getString(record, ['createdAt', 'created_at']),
    updatedAt: getString(record, ['updatedAt', 'updated_at']),
    productFilePath: getString(record, ['productFilePath', 'product_file_path']),
    raw: record,
  }
}

export function normalizeProductIndexItem(value: unknown): ProductIndexItem {
  const record = asRecord(value)
  const rawDraftStatuses = asRecord(record.draftStatuses ?? record.draft_statuses)
  const draftStatuses = Object.fromEntries(
    platformList(Object.keys(rawDraftStatuses)).map((platform) => [platform, getString(rawDraftStatuses, [platform])]),
  ) as ProductIndexItem['draftStatuses']
  return {
    productId: getString(record, ['productId', 'product_id', 'id']),
    title: getString(record, ['title', 'name']),
    mainImage: getString(record, ['mainImage', 'main_image', 'image']),
    sourcePlatform: getString(record, ['sourcePlatform', 'source_platform']),
    sourceUrl: getString(record, ['sourceUrl', 'source_url']),
    createdAt: getString(record, ['createdAt', 'created_at']),
    updatedAt: getString(record, ['updatedAt', 'updated_at']),
    platforms: platformList(record.platforms),
    draftStatuses,
    productFilePath: getString(record, ['productFilePath', 'product_file_path']),
    collectStatus: getString(record, ['collectStatus', 'collect_status']),
    workflowStatus: getString(record, ['workflowStatus', 'workflow_status']),
    aiCopyStatus: getString(record, ['aiCopyStatus', 'ai_copy_status']),
    imageStatus: getString(record, ['imageStatus', 'image_status']),
    categoryStatus: getString(record, ['categoryStatus', 'category_status']),
    attributesStatus: getString(record, ['attributesStatus', 'attributes_status']),
    pricingStatus: getString(record, ['pricingStatus', 'pricing_status']),
    precheckStatus: getString(record, ['precheckStatus', 'precheck_status']),
    publishStatus: getString(record, ['publishStatus', 'publish_status']),
    publishQueueReady: getBoolean(record, ['publishQueueReady', 'publish_queue_ready']),
    optimized: getBoolean(record, ['optimized']),
    raw: record,
  }
}

export function normalizePublishLogs(value: unknown): PublishLogItem[] {
  return Array.isArray(value)
    ? value.map((item) => {
      const record = asRecord(item)
      return {
        jobId: getString(record, ['jobId', 'job_id']),
        productId: getString(record, ['productId', 'product_id']),
        platform: getString(record, ['platform', 'shop']),
        status: getString(record, ['status']),
        startedAt: getString(record, ['startedAt', 'started_at', 'time']),
        finishedAt: getString(record, ['finishedAt', 'finished_at']),
        errorCode: getString(record, ['errorCode', 'error_code']),
        errorMessage: getString(record, ['errorMessage', 'error_message', 'error']),
        requestPayloadPath: getString(record, ['requestPayloadPath', 'request_payload_path']),
        responseBodyPath: getString(record, ['responseBodyPath', 'response_body_path']),
        raw: record,
      }
    })
    : []
}

export function normalizeProductMutation(data: unknown): ProductMutationResponse {
  const record = asRecord(data)
  ensureOk(record, '请求失败')
  const hasProduct = isRecord(record.product)
  const product = normalizeBackendProduct(hasProduct ? record.product : {}, record.imagePool)
  return {
    ok: record.ok !== false,
    product,
    draft: isRecord(record.draft) ? normalizeDraftDetail(record.draft) : undefined,
    productContext: isRecord(record.productContext ?? record.product_context) ? normalizeDraftProductContext(record.productContext ?? record.product_context) : undefined,
    imagePool: product.source.imagePool,
    productsIndex: normalizeProductsIndex(record.productsIndex),
    draftsIndex: normalizeDraftsIndex(record.draftsIndex),
    deleted: getNumber(record, ['deleted']),
    deletedDraftId: getString(record, ['deletedDraftId', 'deleted_draft_id']),
    deletedDraftIds: stringList(record.deletedDraftIds || record.deleted_draft_ids || record.deletedIds || record.deleted_ids),
    deletedIds: stringList(record.deletedIds || record.deleted_ids || record.deletedDraftIds || record.deleted_draft_ids),
    missingIds: stringList(record.missingIds || record.missing_ids),
    affectedProductIds: stringList(record.affectedProductIds || record.affected_product_ids),
    diagnostics: asRecord(record.diagnostics),
    warning: getString(record, ['warning']) || undefined,
    message: getString(record, ['message']) || undefined,
    raw: record,
  }
}

export function normalizeDraftMutation(data: unknown): DraftMutationResponse {
  const record = asRecord(data)
  ensureOk(record, '草稿请求失败')
  return {
    ok: record.ok !== false,
    draft: normalizeDraftDetail(record.draft),
    productContext: normalizeDraftProductContext(record.productContext ?? record.product_context),
    productsIndex: normalizeProductsIndex(record.productsIndex),
    draftsIndex: normalizeDraftsIndex(record.draftsIndex),
    message: getString(record, ['message']) || undefined,
    raw: record,
  }
}

export function normalizeBrowserStatus(value: unknown): BrowserDebugStatus {
  const record = asRecord(value)
  const tabs = Array.isArray(record.current_tabs)
    ? record.current_tabs.map((item) => {
      const tab = asRecord(item)
      return {
        platformDetected: getString(tab, ['platform_detected']),
        title: getString(tab, ['title']),
        url: getString(tab, ['url']),
      }
    })
    : []
  return {
    connected: getBoolean(record, ['connected']),
    port: getNumber(record, ['port'], 9222),
    tabsCount: getNumber(record, ['tabs_count'], tabs.length),
    tabs,
    errorCode: getString(record, ['error_code']),
    errorMessage: getString(record, ['error_message']),
    nextAction: getString(record, ['next_action']),
    powershellCommand: getString(record, ['powershell_command']),
    cmdCommand: getString(record, ['cmd_command']),
    profileDir: getString(record, ['profile_dir']),
  }
}

export function normalizeMercadoLibreAuthChecklist(value: unknown): MercadoLibreAuthChecklist {
  const record = asRecord(value)
  const fields = Array.isArray(record.fields)
    ? record.fields.map((item) => {
      const field = asRecord(item)
      return {
        key: getString(field, ['key']),
        label: getString(field, ['label']),
        ok: getBoolean(field, ['ok']),
        value: getString(field, ['value']),
      }
    })
    : []
  return {
    platform: 'mercadolibre',
    readyForAuthLink: getBoolean(record, ['ready_for_auth_link', 'readyForAuthLink']),
    tokenReady: getBoolean(record, ['token_ready', 'tokenReady']),
    missingCodes: stringList(record.missing_codes ?? record.missingCodes),
    fields,
    nextAction: getString(record, ['next_action', 'nextAction']),
    copyText: getString(record, ['copy_text', 'copyText']),
    raw: record,
  }
}

export function normalizeMercadoLibreOrderNotification(value: unknown): MercadoLibreOrderNotification {
  const record = asRecord(value)
  return {
    topic: getString(record, ['topic']),
    resource: getString(record, ['resource']),
    userId: getString(record, ['user_id', 'userId']),
    applicationId: getString(record, ['application_id', 'applicationId']),
    attempts: getNumber(record, ['attempts']),
    sent: getString(record, ['sent']),
    receivedAt: getString(record, ['received_at', 'receivedAt']),
    orderId: getString(record, ['order_id', 'orderId']),
    error: getString(record, ['error']),
    raw: record,
  }
}

export function normalizeProductOperation(data: unknown): ProductOperationResult {
  const record = asRecord(data)
  const normalizedProduct = record.product ? normalizeBackendProduct(record.product, record.imagePool) : undefined
  return {
    ok: record.ok !== false,
    status: getString(record, ['status']),
    message: getString(record, ['message']),
    error: getString(record, ['error', 'error_message']),
    product: normalizedProduct,
    imagePool: normalizedProduct?.source.imagePool || (Array.isArray(record.imagePool) ? record.imagePool.map(normalizeImageAsset) : []),
    productsIndex: normalizeProductsIndex(record.productsIndex),
    draftsIndex: normalizeDraftsIndex(record.draftsIndex),
    raw: record,
  }
}

export function normalizeDeleteProductsResult(data: unknown): DeleteProductsResult {
  const record = asRecord(data)
  ensureOk(record, '删除商品失败')
  const normalizedProduct = record.product ? normalizeBackendProduct(record.product, record.imagePool) : undefined
  return {
    ok: record.ok !== false,
    deleted: getNumber(record, ['deleted']),
    deletedIds: stringList(record.deletedIds || record.deleted_ids),
    missingIds: stringList(record.missingIds || record.missing_ids),
    productsIndex: normalizeProductsIndex(record.productsIndex),
    product: normalizedProduct,
    imagePool: normalizedProduct?.source.imagePool || (Array.isArray(record.imagePool) ? record.imagePool.map(normalizeImageAsset) : []),
    message: getString(record, ['message']),
    error: getString(record, ['error', 'error_message']),
    raw: record,
  }
}

export function diagnosticsToCollectDiagnostics(raw: unknown, product: Product, fallbackMessage = '采集完成。') {
  const record = asRecord(raw)
  const errorCode = getString(record, ['error_code'])
  const success = getBoolean(record, ['success'], Boolean(product.source.title || product.name))
  return {
    status: success ? 'success' as const : errorCode ? 'failed' as const : 'idle' as const,
    progress: success ? 100 : errorCode ? 0 : 0,
    message: getString(record, ['error_message'], fallbackMessage),
    downloadedImages: getNumber(record, ['images_found_count'], product.source.imagePool.length),
    extractedBullets: getNumber(record, ['bullets_found_count'], product.sellingPoints.length),
    antiBotWarning: ['LOGIN', 'CAPTCHA', 'SECURITY', 'VERIFY', 'ROBOT'].some((token) => `${errorCode} ${getString(record, ['error_message'])}`.toUpperCase().includes(token)),
    lastSourceUrl: getString(record, ['source_url', 'final_url'], product.source.sourceUrl),
    errorCode,
    nextAction: getString(record, ['next_action']),
    htmlSnapshotPath: getString(record, ['html_snapshot_path']),
    screenshotPath: getString(record, ['screenshot_path']),
    raw: record,
  }
}
