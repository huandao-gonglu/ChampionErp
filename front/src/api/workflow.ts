import { API_REQUEST_TIMEOUT_MS, apiClient } from '@/api/client'
import { createEmptyDraft, createEmptyProduct } from '@/constants/initialState'
import type {
  BrowserDebugStatus,
  CategoryPrecheckResult,
  CategorySearchResult,
  CategorySelection,
  CollectBatchRow,
  CollectForm,
  ImageAsset,
  Marketplace,
  MarketplaceDraft,
  MercadoLibreAuthChecklist,
  MercadoLibreTestMode,
  PricingInput,
  PricingResult,
  Product,
  ProductIndexItem,
  PublishJob,
  PublishLogItem,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'

export interface AppStateResponse {
  product: Product
  imagePool: ImageAsset[]
  appConfig: UnknownRecord
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
  mercadolibreAuthChecklist?: MercadoLibreAuthChecklist | null
  outputDir: string
  productsIndex: ProductIndexItem[]
  publishLogs: PublishLogItem[]
}

export interface ProductMutationResponse {
  ok: boolean
  product: Product
  imagePool: ImageAsset[]
  productsIndex: ProductIndexItem[]
  diagnostics?: UnknownRecord
  warning?: string
  message?: string
  raw?: UnknownRecord
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

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function asRecord(value: unknown): UnknownRecord {
  return isRecord(value) ? value : {}
}

function getString(record: UnknownRecord, keys: string[], fallback = ''): string {
  for (const key of keys) {
    const value = record[key]
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value).trim()
    }
  }
  return fallback
}

function getNumber(record: UnknownRecord, keys: string[], fallback = 0): number {
  const text = getString(record, keys)
  const value = Number.parseFloat(text)
  return Number.isFinite(value) ? value : fallback
}

function getBoolean(record: UnknownRecord, keys: string[], fallback = false): boolean {
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

function stringList(value: unknown): string[] {
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

function platformList(value: unknown): Marketplace[] {
  const allowed = new Set<Marketplace>(['mercadolibre', 'wildberries', 'ozon'])
  return stringList(value).filter((item): item is Marketplace => allowed.has(item as Marketplace))
}

function normalizeDimensions(value: unknown) {
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

function normalizeDraft(value: unknown, language: string): MarketplaceDraft {
  const record = asRecord(value)
  const draft = createEmptyDraft(language)
  return {
    ...draft,
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
      mercadolibre: normalizeDraft(drafts.mercadolibre, 'Spanish (Mexico)'),
      wildberries: normalizeDraft(drafts.wildberries, 'Russian'),
      ozon: normalizeDraft(drafts.ozon, 'Russian'),
    },
    raw: record,
  }
}

function toBackendImageAsset(image: ImageAsset): UnknownRecord {
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

function toBackendDraft(draft: MarketplaceDraft): UnknownRecord {
  return {
    enabled: draft.enabled,
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
  }
}

export function toBackendProduct(product: Product): UnknownRecord {
  return {
    ...product.raw,
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
    drafts: {
      mercadolibre: toBackendDraft(product.drafts.mercadolibre),
      wildberries: toBackendDraft(product.drafts.wildberries),
      ozon: toBackendDraft(product.drafts.ozon),
    },
  }
}

function ensureOk(data: UnknownRecord, fallbackMessage: string): void {
  if (data.ok === false) {
    throw new Error(getString(data, ['error', 'message'], fallbackMessage))
  }
}

function normalizeProductsIndex(value: unknown): ProductIndexItem[] {
  return Array.isArray(value) ? value.map(normalizeProductIndexItem) : []
}

function normalizeProductIndexItem(value: unknown): ProductIndexItem {
  const record = asRecord(value)
  return {
    productId: getString(record, ['productId', 'product_id', 'id']),
    title: getString(record, ['title', 'name']),
    mainImage: getString(record, ['mainImage', 'main_image', 'image']),
    sourcePlatform: getString(record, ['sourcePlatform', 'source_platform']),
    sourceUrl: getString(record, ['sourceUrl', 'source_url']),
    createdAt: getString(record, ['createdAt', 'created_at']),
    updatedAt: getString(record, ['updatedAt', 'updated_at']),
    platforms: platformList(record.platforms),
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

function normalizePublishLogs(value: unknown): PublishLogItem[] {
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

function normalizeProductMutation(data: unknown): ProductMutationResponse {
  const record = asRecord(data)
  ensureOk(record, '请求失败')
  const hasProduct = isRecord(record.product)
  const product = normalizeBackendProduct(hasProduct ? record.product : {}, record.imagePool)
  return {
    ok: record.ok !== false,
    product,
    imagePool: product.source.imagePool,
    productsIndex: normalizeProductsIndex(record.productsIndex),
    diagnostics: asRecord(record.diagnostics),
    warning: getString(record, ['warning']) || undefined,
    message: getString(record, ['message']) || undefined,
    raw: record,
  }
}

function normalizeBrowserStatus(value: unknown): BrowserDebugStatus {
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

function normalizeMercadoLibreAuthChecklist(value: unknown): MercadoLibreAuthChecklist {
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

function normalizeProductOperation(data: unknown): ProductOperationResult {
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
    raw: record,
  }
}

function normalizeDeleteProductsResult(data: unknown): DeleteProductsResult {
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
    outputDir: getString(data, ['outputDir']),
    productsIndex: normalizeProductsIndex(data.productsIndex),
    publishLogs: normalizePublishLogs(data.publishLogs),
  }
}

export async function fetchProductsIndex(): Promise<ProductIndexItem[]> {
  const response = await apiClient.get('/api/products-index')
  const data = asRecord(response.data)
  ensureOk(data, '读取商品库失败')
  return normalizeProductsIndex(data.items)
}

export async function fetchPublishLogs(): Promise<PublishLogItem[]> {
  const response = await apiClient.get('/api/publish-logs')
  const data = asRecord(response.data)
  ensureOk(data, '读取发布日志失败')
  return normalizePublishLogs(data.items)
}

export async function saveProduct(product: Product): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/save-product', { product: toBackendProduct(product) })
  return normalizeProductMutation(response.data)
}

export async function loadProduct(productId: string, productFilePath = ''): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/load-product', { product_id: productId, product_file_path: productFilePath })
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
  return data
}

export async function saveCollectSettings(form: CollectForm): Promise<void> {
  const response = await apiClient.post('/api/save-settings', {
    appConfig: {
      alibaba_cookie: form.alibabaCookie,
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
  const response = await apiClient.post('/api/generate-copy', {
    platform,
    target_market: platform,
    language: platform === 'mercadolibre' ? 'Spanish (Mexico)' : 'Russian',
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
  const response = await apiClient.post('/api/generate-image-prompts', {
    product: toBackendProduct(product),
    platform,
    language: targetLanguage,
    target_language: targetLanguage,
    selected_image_ids: product.source.imagePool.filter((image) => image.selected).map((image) => image.id),
    include_bullets: true,
    include_description: true,
  })
  const data = asRecord(response.data)
  ensureOk(data, '生成图片提示词失败')
  return getString(data, ['prompt'])
}

export async function imageTranslate(product: Product, platform: Marketplace, language: string): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-translate', {
    product: toBackendProduct(product),
    platform,
    language,
    image_ids: product.source.imagePool.filter((image) => image.selected).map((image) => image.id),
  }, { timeout: API_REQUEST_TIMEOUT_MS })
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
    usd_cny_rate: input.usdCnyRate,
    mxn_usd_rate: input.mxnUsdRate,
  })
  const data = asRecord(response.data)
  ensureOk(data, '核价失败')
  return {
    suggestedPriceMxn: getNumber(data, ['suggested_price_mxn', 'sale_price_mxn', 'price_mxn']),
    suggestedPriceUsd: getNumber(data, ['suggested_price_usd', 'sale_price_usd', 'price_usd']),
    shippingCostUsd: getNumber(data, ['shipping_cost_usd', 'international_shipping_usd']),
    netRevenueCny: getNumber(data, ['net_revenue_cny']),
    profitCny: getNumber(data, ['profit_cny']),
    marginPercent: getNumber(data, ['margin_percent', 'profit_margin_percent']),
  }
}

export async function publishPrecheck(product: Product, platforms: Marketplace[] = ['mercadolibre']): Promise<{ product: Product; precheck: PublishPrecheck; platformResults: UnknownRecord }> {
  const response = await apiClient.post('/api/publish-precheck', { product: toBackendProduct(product), platforms })
  const data = asRecord(response.data)
  ensureOk(data, '预检失败')
  const firstPlatform = platforms[0] || 'mercadolibre'
  const result = asRecord(asRecord(data.platforms)[firstPlatform])
  return {
    product: normalizeBackendProduct(data.product),
    precheck: {
      ok: result.ok !== false,
      errors: stringList(result.errors),
      warnings: stringList(result.warnings),
      checkedAt: getString(result, ['checked_at'], new Date().toISOString()),
    },
    platformResults: asRecord(data.platforms),
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
  const required = Array.isArray(data.required)
    ? data.required.map((item) => {
      const record = asRecord(item)
      return {
        id: getString(record, ['id', 'attribute_id']),
        name: getString(record, ['name', 'label']),
        required: getBoolean(record, ['required'], true),
      }
    })
    : []
  return {
    platform,
    categoryId,
    categoryPath: getString(data, ['category_path', 'path', 'name']),
    requiredAttributes: required,
  }
}

export async function searchCategories(platform: Marketplace, query: string, site = 'MLM'): Promise<{ results: CategorySearchResult[]; cacheStatus: UnknownRecord }> {
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

export async function fillCategoryAttributes(product: Product, platform: Marketplace, categoryId: string): Promise<ProductMutationResponse & { needReview: unknown[] }> {
  const response = await apiClient.post('/api/category-ai-fill', { product: toBackendProduct(product), platform, category_id: categoryId })
  const data = asRecord(response.data)
  ensureOk(data, 'AI 填充属性失败')
  const normalizedProduct = normalizeBackendProduct(data.product)
  return {
    ok: true,
    product: normalizedProduct,
    imagePool: normalizedProduct.source.imagePool,
    productsIndex: [],
    needReview: Array.isArray(data.need_review) ? data.need_review : [],
  }
}

export async function refreshCategoryCache(platform: Marketplace, site = 'MLM', maxCategories = 500): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/category-cache/refresh', { platform, site, max_categories: maxCategories })
  const data = asRecord(response.data)
  ensureOk(data, '刷新类目缓存失败')
  return data
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

export async function testAiChannel(channel: 'text' | 'image', config: UnknownRecord): Promise<AuthResult> {
  const channelConfig = channel === 'image' && isRecord(config.image_ai)
    ? config.image_ai
    : channel === 'text' && isRecord(config.text_ai)
      ? config.text_ai
      : config
  const response = await apiClient.post('/api/test-ai-channel', { channel, config: channelConfig })
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
  const response = await apiClient.post('/api/mercadolibre/refresh-token', params)
  return normalizeAuthResult(response.data)
}

export async function runMercadoLibreRealAuthTest(product: Product, mode: MercadoLibreTestMode, categoryId = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/real-auth-test', { product: toBackendProduct(product), mode, category_id: categoryId })
  return normalizeAuthResult(response.data)
}

export async function openAuthLink(url: string, browser = 'default'): Promise<AuthResult> {
  const response = await apiClient.post('/api/open-auth-link', { url, browser })
  return normalizeAuthResult(response.data)
}

export async function exchangeMercadoLibreCode(codeOrUrl: string, params: UnknownRecord = {}): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/exchange-code', { code_or_url: codeOrUrl, ...params })
  return normalizeAuthResult(response.data)
}

export async function testStoreAuth(platform: Marketplace, scope = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-store-auth', { platform, scope })
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
  return {
    ok: record.ok !== false,
    message: getString(record, ['message', 'status'], record.ok === false ? '失败' : '成功'),
    error: getString(record, ['error', 'error_message']),
    errorCode: getString(record, ['error_code']),
    nextAction: getString(record, ['next_action']),
    raw: record,
  }
}
