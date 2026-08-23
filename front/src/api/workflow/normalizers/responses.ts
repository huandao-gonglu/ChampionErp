import type {
  BrowserDebugStatus,
  DraftIndexItem,
  Marketplace,
  MercadoLibreAuthChecklist,
  MercadoLibreOrderNotification,
  Product,
  ProductIndexItem,
  PublishLogItem,
  UnknownRecord,
} from '@/types/workflow'

import {
  type DeleteProductsResult,
  type DraftMutationResponse,
  type ProductMutationResponse,
  type ProductOperationResult,
  isRecord,
  asRecord,
  getString,
  getNumber,
  getBoolean,
  wireStringList,
  platformList,
  normalizeTargetSites,
  normalizeImageAsset,
} from './core'
import {

  normalizeBackendProduct,
  normalizeDraftDetail,
  normalizeDraftProductContext,
} from './product'

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
  const platform = (getString(record, ['platform']) || 'mercadolibre') as Marketplace
  const platforms = platformList(record.platforms)
  const effectivePlatforms = platforms.length ? platforms : [platform]
  const primaryPlatform = effectivePlatforms.includes(platform) ? platform : effectivePlatforms[0] || platform
  const categoryId = getString(record, ['category_id'])
  const categoryPath = getString(record, ['category_path'])
  return {
    draftId: getString(record, ['draft_id']),
    productId: getString(record, ['product_id']),
    sourceProductId: getString(record, ['source_product_id']),
    platform: primaryPlatform,
    platforms: effectivePlatforms,
    targetSites: normalizeTargetSites(record.target_sites, primaryPlatform, getString(record, ['site']), getString(record, ['language']), {
      categoryId,
      categoryPath,
      publishStatus: getString(record, ['publish_status']),
      status: getString(record, ['status']),
    }),
    site: getString(record, ['site']),
    language: getString(record, ['language']),
    status: getString(record, ['status']) as DraftIndexItem['status'],
    title: getString(record, ['title']),
    productTitle: getString(record, ['product_title']),
    mainImage: getString(record, ['main_image']),
    sourcePlatform: getString(record, ['source_platform']),
    sourceUrl: getString(record, ['source_url']),
    categoryId,
    categoryPath,
    publishStatus: getString(record, ['publish_status']),
    createdAt: getString(record, ['created_at']),
    updatedAt: getString(record, ['updated_at']),
    productFilePath: getString(record, ['product_file_path']),
    raw: record,
  }
}

export function normalizeProductIndexItem(value: unknown): ProductIndexItem {
  const record = asRecord(value)
  const rawDraftStatuses = asRecord(record.draft_statuses)
  const draftStatuses = Object.fromEntries(
    platformList(Object.keys(rawDraftStatuses)).map((platform) => [platform, getString(rawDraftStatuses, [platform])]),
  ) as ProductIndexItem['draftStatuses']
  return {
    productId: getString(record, ['product_id']),
    title: getString(record, ['title']),
    mainImage: getString(record, ['main_image']),
    sourcePlatform: getString(record, ['source_platform']),
    sourceUrl: getString(record, ['source_url']),
    createdAt: getString(record, ['created_at']),
    updatedAt: getString(record, ['updated_at']),
    platforms: platformList(record.platforms),
    draftStatuses,
    productFilePath: getString(record, ['product_file_path']),
    collectStatus: getString(record, ['collect_status']),
    workflowStatus: getString(record, ['workflow_status']),
    aiCopyStatus: getString(record, ['ai_copy_status']),
    imageStatus: getString(record, ['image_status']),
    categoryStatus: getString(record, ['category_status']),
    attributesStatus: getString(record, ['attributes_status']),
    pricingStatus: getString(record, ['pricing_status']),
    precheckStatus: getString(record, ['precheck_status']),
    publishStatus: getString(record, ['publish_status']),
    publishQueueReady: getBoolean(record, ['publish_queue_ready']),
    optimized: getBoolean(record, ['optimized']),
    raw: record,
  }
}

export function normalizePublishLogs(value: unknown): PublishLogItem[] {
  return Array.isArray(value)
    ? value.map((item) => {
      const record = asRecord(item)
      return {
        jobId: getString(record, ['job_id']),
        productId: getString(record, ['product_id']),
        platform: getString(record, ['platform']),
        status: getString(record, ['status']),
        startedAt: getString(record, ['started_at', 'time']),
        finishedAt: getString(record, ['finished_at']),
        errorCode: getString(record, ['error_code']),
        errorMessage: getString(record, ['error_message', 'error']),
        requestPayloadPath: getString(record, ['request_payload_path']),
        responseBodyPath: getString(record, ['response_body_path']),
        raw: record,
      }
    })
    : []
}

export function normalizeProductMutation(data: unknown): ProductMutationResponse {
  const record = asRecord(data)
  ensureOk(record, '请求失败')
  const product = normalizeBackendProduct(record.product, record.imagePool)
  return {
    ok: record.ok !== false,
    product,
    draft: isRecord(record.draft) ? normalizeDraftDetail(record.draft) : undefined,
    productContext: isRecord(record.productContext) ? normalizeDraftProductContext(record.productContext) : undefined,
    imagePool: product.source.imagePool,
    productsIndex: normalizeProductsIndex(record.productsIndex),
    draftsIndex: normalizeDraftsIndex(record.draftsIndex),
    deleted: getNumber(record, ['deleted']),
    deletedDraftId: getString(record, ['deletedDraftId']),
    deletedDraftIds: wireStringList(record.deletedDraftIds),
    deletedIds: wireStringList(record.deletedIds),
    missingIds: wireStringList(record.missingIds),
    affectedProductIds: wireStringList(record.affectedProductIds),
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
    productContext: normalizeDraftProductContext(record.productContext),
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
    readyForAuthLink: getBoolean(record, ['ready_for_auth_link']),
    tokenReady: getBoolean(record, ['token_ready']),
    missingCodes: wireStringList(record.missing_codes),
    fields,
    nextAction: getString(record, ['next_action']),
    copyText: getString(record, ['copy_text']),
    raw: record,
  }
}

export function normalizeMercadoLibreOrderNotification(value: unknown): MercadoLibreOrderNotification {
  const record = asRecord(value)
  return {
    topic: getString(record, ['topic']),
    resource: getString(record, ['resource']),
    userId: getString(record, ['user_id']),
    applicationId: getString(record, ['application_id']),
    attempts: getNumber(record, ['attempts']),
    sent: getString(record, ['sent']),
    receivedAt: getString(record, ['received_at']),
    orderId: getString(record, ['order_id']),
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
    deletedIds: wireStringList(record.deletedIds),
    missingIds: wireStringList(record.missingIds),
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
