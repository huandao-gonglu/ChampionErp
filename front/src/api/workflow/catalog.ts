import {  apiClient } from '@/api/client'
import { listingLanguageLabel } from '@/constants/locales'
import type {
  BrowserDebugStatus,
  CollectBatchRow,
  DraftIndexItem,
  DraftDetail,
  CollectForm,
  ImageAsset,
  Marketplace,
  Product,
  ProductIndexItem,
  TransientCollectCredentials,
  UnknownRecord,
} from '@/types/workflow'
import type {

  AuthResult,
  DeleteProductsResult,
  DraftMutationResponse,
  ProductMutationResponse,
} from './normalizers'
import {
  asRecord,
  ensureOk,
  getBoolean,
  getNumber,
  getString,
  normalizeBackendProduct,
  normalizeBrowserStatus,
  normalizeDeleteProductsResult,
  normalizeDraftMutation,
  normalizeDraftsIndex,
  normalizeProductMutation,
  normalizeProductsIndex,
  stringList,
  toBackendImageAsset,
  toBackendDraftDetail,
  toBackendProduct,
} from './normalizers'


import { imageTranslateTimeoutMs, normalizeAuthResult, requiredProductId } from './shared'

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

export async function saveProduct(product: Product): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/save-product', { product: toBackendProduct(product) })
  return normalizeProductMutation(response.data)
}

export async function loadProduct(productId: string, productFilePath = ''): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/load-product', { product_id: productId, product_file_path: productFilePath })
  return normalizeProductMutation(response.data)
}

export async function loadDraft(draftId: string): Promise<DraftMutationResponse> {
  const response = await apiClient.post('/api/load-draft', { draft_id: draftId })
  return normalizeDraftMutation(response.data)
}

export async function saveDraft(draft: DraftDetail): Promise<DraftMutationResponse> {
  const response = await apiClient.post('/api/save-draft', { draft: toBackendDraftDetail(draft) })
  return normalizeDraftMutation(response.data)
}

export async function deleteDraft(draftIds: string | string[]): Promise<ProductMutationResponse> {
  const payload = Array.isArray(draftIds) ? { draft_ids: draftIds } : { draft_id: draftIds }
  const response = await apiClient.post('/api/delete-draft', payload)
  return normalizeProductMutation(response.data)
}

export async function deleteProducts(productIds: string[]): Promise<DeleteProductsResult> {
  const response = await apiClient.post('/api/delete-products', { product_ids: productIds })
  return normalizeDeleteProductsResult(response.data)
}

function collectCredentialsPayload(credentials: TransientCollectCredentials): UnknownRecord {
  const cookie = String(credentials.alibabaCookie || '').trim()
  return cookie ? { cookie } : {}
}

function collectApiOptions(form: CollectForm): UnknownRecord {
  return {
    base_url: form.alibabaApiBaseUrl,
    method: form.alibabaApiMethod,
    api_version: form.alibabaApiVersion,
    timeout_seconds: form.alibabaApiTimeoutSeconds,
  }
}

export async function collectProduct(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<ProductMutationResponse> {
  const url = form.productUrl.trim()
  if (!url || form.mode === 'extension' || form.mode === 'manual') {
    return importManualProduct(form)
  }
  const path = form.platform.toLowerCase() === '1688' ? '/api/collect-1688' : '/api/collect-source'
  const response = await apiClient.post(path, {
    url,
    mode: form.mode,
    ...collectCredentialsPayload(credentials),
    '1688_api': collectApiOptions(form),
    platform: form.platform,
  })
  return normalizeProductMutation(response.data)
}

export async function clean1688Text(text: string, url = ''): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/collect-1688-clean', { text, html: text, url })
  const data = asRecord(response.data)
  ensureOk(data, '清洗 1688 文本失败')
  return data
}

export async function collectBatch(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<{ rows: CollectBatchRow[]; productsIndex: ProductIndexItem[] }> {
  const response = await apiClient.post('/api/collect-batch', {
    urls: form.productUrls,
    mode: form.mode === 'extension' ? 'manual' : form.mode,
    ...collectCredentialsPayload(credentials),
    '1688_api': collectApiOptions(form),
    platform: form.platform === 'manual' ? '' : form.platform,
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
  })
  return normalizeProductMutation(response.data)
}

export async function claimProducts(productIds: string[], platform?: Marketplace): Promise<UnknownRecord> {
  const response = await apiClient.post('/api/claim-products', { product_ids: productIds, platform })
  const data = asRecord(response.data)
  ensureOk(data, '推到草稿箱失败')
  if (productIds.length && getNumber(data, ['claimed_count']) <= 0) {
    const firstItem = asRecord(Array.isArray(data.items) ? data.items[0] : {})
    throw new Error(getString(firstItem, ['error'], '没有商品被推到草稿箱'))
  }
  return data
}

export async function saveCollectSettings(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<void> {
  const alibabaCookie = String(credentials.alibabaCookie || '').trim()
  const response = await apiClient.post('/api/save-settings', {
    appConfig: {
      ...(alibabaCookie ? { alibaba_cookie: alibabaCookie } : {}),
      '1688_api': {
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
    product_id: requiredProductId(product, '上传图片'),
    uploads: uploads.map((upload, index) => ({
      ...upload,
      platforms: [],
      selected: true,
      is_main: index === 0 && product.source.imagePool.length === 0,
    })),
  })
  return normalizeProductMutation(response.data)
}

export async function saveImagePool(product: Product, imagePool: ImageAsset[]): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/save', {
    product_id: requiredProductId(product, '保存图片池'),
    image_pool: imagePool.map(toBackendImageAsset),
  })
  return normalizeProductMutation(response.data)
}

export async function imagePoolAction(product: Product, action: string, payload: UnknownRecord = {}): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/action', { action, ...payload, product_id: requiredProductId(product, '更新图片池') })
  return normalizeProductMutation(response.data)
}

export async function generateCopy(
  product: Product,
  platform: Marketplace,
  options: { draftId?: string; language?: string; mode?: 'rewrite' | 'generate' } = {},
): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/generate-copy', {
    product_id: requiredProductId(product, '生成文案'),
    platform,
    ...(options.draftId ? { draft_id: options.draftId } : {}),
    ...(options.language ? { language: options.language } : {}),
    ...(options.mode ? { mode: options.mode } : {}),
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
    product_id: requiredProductId(product, '生成图片提示词'),
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

export interface ImageTranslateOptions {
  draftId?: string
  applyToDraft?: boolean
  draftImageStrategy?: 'pool_only' | 'append' | 'replace_selected' | 'replace_all'
  sourceImageIds?: string[]
}

export type ImageEditOptions = ImageTranslateOptions

export async function imageTranslate(product: Product, platform: Marketplace, language: string, options: ImageTranslateOptions = {}): Promise<ProductMutationResponse> {
  const listingLanguage = language || product.drafts[platform]?.language || listingLanguageLabel(platform)
  const selectedImageIds = options.sourceImageIds ?? product.source.imagePool.filter((image) => image.selected).map((image) => image.id)
  if (!selectedImageIds.length) throw new Error('请先勾选要翻译/重绘的图片')
  const response = await apiClient.post('/api/image-translate', {
    product_id: requiredProductId(product, '翻译图片'),
    platform,
    language: listingLanguage,
    target_language: listingLanguage,
    draft_id: options.draftId,
    apply_to_draft: options.applyToDraft,
    draft_image_strategy: options.draftImageStrategy,
    source_image_ids: selectedImageIds,
  }, { timeout: imageTranslateTimeoutMs(selectedImageIds) })
  return normalizeProductMutation(response.data)
}

export async function imageEdit(product: Product, platform: Marketplace, prompt: string, options: ImageEditOptions = {}): Promise<ProductMutationResponse> {
  const userPrompt = String(prompt || '').trim()
  if (!userPrompt) throw new Error('请输入图生图提示词')
  const selectedImageIds = options.sourceImageIds ?? product.source.imagePool.filter((image) => image.selected).map((image) => image.id)
  if (!selectedImageIds.length) throw new Error('请先勾选要用于图生图的图片')
  const response = await apiClient.post('/api/image-edit', {
    product_id: requiredProductId(product, '图生图'),
    platform,
    prompt: userPrompt,
    draft_id: options.draftId,
    apply_to_draft: options.applyToDraft,
    draft_image_strategy: options.draftImageStrategy,
    source_image_ids: selectedImageIds,
  }, { timeout: imageTranslateTimeoutMs(selectedImageIds) })
  return normalizeProductMutation(response.data)
}
