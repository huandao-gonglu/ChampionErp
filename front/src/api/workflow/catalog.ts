import { apiClient, type AiPresentationTransport } from '@/api/client'
import { listingLanguageValue } from '@/constants/locales'
import type {
  CollectBatchResultRow,
  CollectionVerification,
  DraftIndexItem,
  DraftDetail,
  DraftProductContext,
  CollectForm,
  ImageAsset,
  Marketplace,
  MarketplaceTargetSite,
  Product,
  ProductIndexItem,
  TransientCollectCredentials,
  UnknownRecord,
} from '@/types/workflow'
import type {

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
  normalizeDeleteProductsResult,
  normalizeDraftMutation,
  normalizeDraftsIndex,
  normalizeProductMutation,
  normalizeProductsIndex,
  toBackendImageAsset,
  toBackendDraftDetail,
  toBackendProduct,
} from './normalizers'


import { imageTranslateTimeoutMs, requiredProductId } from './shared'

export async function fetchProductsIndex(): Promise<ProductIndexItem[]> {
  const response = await apiClient.get('/api/products-index')
  const data = asRecord(response.data)
  ensureOk(data, '读取采集箱失败')
  return normalizeProductsIndex(data.items)
}

export async function fetchDraftsIndex(scope = 'all'): Promise<DraftIndexItem[]> {
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

export async function duplicateDraft(draftId: string): Promise<DraftMutationResponse> {
  const response = await apiClient.post('/api/duplicate-draft', { draft_id: draftId })
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

export type CollectionAttempt =
  | (ProductMutationResponse & { verification?: undefined })
  | { verification: CollectionVerification; diagnostics: UnknownRecord }

function normalizeVerification(value: unknown): CollectionVerification | undefined {
  const record = asRecord(value)
  const browserTabId = getString(record, ['browser_tab_id'])
  const sourceUrl = getString(record, ['source_url'])
  return browserTabId && sourceUrl ? { browserTabId, sourceUrl, platform: getString(record, ['platform']) } : undefined
}

function normalizeCollectionAttempt(value: unknown): CollectionAttempt {
  const data = asRecord(value)
  const verification = normalizeVerification(data.verification)
  if (data.status === 'waiting_verification' && verification) {
    return { verification, diagnostics: asRecord(data.diagnostics) }
  }
  return normalizeProductMutation(data)
}

export async function inspectCollectionVerification(verification: CollectionVerification, signal: AbortSignal): Promise<{
  status: 'waiting_verification' | 'loading' | 'ready' | 'unavailable'; message: string
}> {
  const response = await apiClient.post('/api/collect-verification', {
    browser_tab_id: verification.browserTabId, source_url: verification.sourceUrl,
  }, { signal })
  const data = asRecord(response.data)
  ensureOk(data, '检查验证状态失败')
  const status = getString(data, ['status'])
  if (!['waiting_verification', 'loading', 'ready', 'unavailable'].includes(status)) throw new Error('验证状态无效，请重新采集。')
  return { status: status as 'waiting_verification' | 'loading' | 'ready' | 'unavailable', message: getString(data, ['message']) }
}

export async function collectProduct(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<CollectionAttempt> {
  const url = form.productUrl.trim()
  if (!url) throw new Error('请先输入商品链接。')
  const path = form.platform.toLowerCase() === '1688' ? '/api/collect-1688' : '/api/collect-source'
  const response = await apiClient.post(path, {
    url,
    mode: form.mode,
    ...collectCredentialsPayload(credentials),
    platform: form.platform === 'unknown' ? '' : form.platform,
  })
  return normalizeCollectionAttempt(response.data)
}

export async function collectBatch(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<{ rows: CollectBatchResultRow[]; productsIndex: ProductIndexItem[] }> {
  const response = await apiClient.post('/api/collect-batch', {
    urls: form.productUrls,
    mode: form.mode,
    ...collectCredentialsPayload(credentials),
    platform: ['manual', 'unknown'].includes(form.platform) ? '' : form.platform,
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
        verification: normalizeVerification(record.verification),
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

/** 验证完成后只读取原商品页，沿用原采集连接。 */
export async function resumeCollection(verification: CollectionVerification): Promise<CollectionAttempt> {
  const response = await apiClient.post('/api/collect-resume', {
    browser_tab_id: verification.browserTabId,
    source_url: verification.sourceUrl,
    platform: verification.platform,
  })
  return normalizeCollectionAttempt(response.data)
}

export interface ClaimProductsResult {
  claimedCount: number
  draftCount: number
  failures: { productId: string; error: string }[]
  productsIndex: ProductIndexItem[]
  draftsIndex: DraftIndexItem[]
}

export async function claimProducts(productIds: string[], targets: MarketplaceTargetSite[]): Promise<ClaimProductsResult> {
  const response = await apiClient.post('/api/claim-products', {
    product_ids: productIds,
    targets: targets.map(({ platform, site, language }) => ({ platform, site, language })),
  })
  const data = asRecord(response.data)
  ensureOk(data, '推到草稿箱失败')
  if (productIds.length && getNumber(data, ['claimed_count']) <= 0) {
    const firstItem = asRecord(Array.isArray(data.items) ? data.items[0] : {})
    throw new Error(getString(firstItem, ['error'], '没有商品被推到草稿箱'))
  }
  return {
    claimedCount: getNumber(data, ['claimed_count']),
    draftCount: getNumber(data, ['draft_count']),
    failures: (Array.isArray(data.items) ? data.items : []).map(asRecord)
      .filter((item) => !item.ok)
      .map((item) => ({ productId: getString(item, ['product_id']), error: getString(item, ['error'], '推到草稿箱失败') })),
    productsIndex: normalizeProductsIndex(data.productsIndex),
    draftsIndex: normalizeDraftsIndex(data.draftsIndex),
  }
}

export async function saveCollectSettings(
  form: CollectForm,
  credentials: TransientCollectCredentials = {},
): Promise<void> {
  const alibabaCookie = String(credentials.alibabaCookie || '').trim()
  const response = await apiClient.post('/api/save-settings', {
    appConfig: {
      ...(alibabaCookie ? { alibaba_cookie: alibabaCookie } : {}),
      collect_output_dir: form.outputDir,
    },
  })
  ensureOk(asRecord(response.data), '保存设置失败')
}

export async function uploadImages(product: Pick<DraftProductContext, 'productId' | 'imagePool'>, uploads: Array<{ filename: string; data_url: string }>): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/image-pool/upload', {
    product_id: requiredProductId(product, '上传图片'),
    uploads: uploads.map((upload, index) => ({
      ...upload,
      platforms: [],
      selected: true,
      is_main: index === 0 && product.imagePool.length === 0,
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
  presentation: AiPresentationTransport = {},
): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/generate-copy', {
    product_id: requiredProductId(product, '生成文案'),
    platform,
    ...(options.draftId ? { draft_id: options.draftId } : {}),
    ...(options.language ? { language: options.language } : {}),
    ...(options.mode ? { mode: options.mode } : {}),
  }, { aiPresentationId: presentation.presentationId })
  return normalizeProductMutation(response.data)
}

export async function generateCopyBatch(
  productIds: string[],
  platform: Marketplace,
  presentation: AiPresentationTransport = {},
): Promise<UnknownRecord> {
  const response = await apiClient.post(
    '/api/generate-copy-batch',
    { product_ids: productIds, platform },
    { aiPresentationId: presentation.presentationId },
  )
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
  const listingLanguage = targetLanguage || product.drafts[platform]?.language || listingLanguageValue(platform)
  const response = await apiClient.post('/api/generate-image-prompts', {
    product_id: requiredProductId(product, '生成图片提示词'),
    platform,
    language: listingLanguage,
    target_language: listingLanguage,
    selected_image_ids: product.source.imagePool.filter((image) => image.selected).map((image) => image.id),
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

export async function imageTranslate(
  product: Product,
  platform: Marketplace,
  language: string,
  options: ImageTranslateOptions = {},
  presentation: AiPresentationTransport = {},
): Promise<ProductMutationResponse> {
  const listingLanguage = language || product.drafts[platform]?.language || listingLanguageValue(platform)
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
  }, {
    timeout: imageTranslateTimeoutMs(selectedImageIds),
    aiPresentationId: presentation.presentationId,
  })
  return normalizeProductMutation(response.data)
}

export async function imageEdit(
  product: Product,
  platform: Marketplace,
  prompt: string,
  options: ImageEditOptions = {},
  presentation: AiPresentationTransport = {},
): Promise<ProductMutationResponse> {
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
  }, {
    timeout: imageTranslateTimeoutMs(selectedImageIds),
    aiPresentationId: presentation.presentationId,
  })
  return normalizeProductMutation(response.data)
}
