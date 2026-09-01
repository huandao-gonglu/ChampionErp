import type {
  BrowserDebugStatus,
  DraftIndexItem,
  Marketplace,
  MercadoLibreAuthChecklist,
  MercadoLibreOrderNotification,
  Product,
  ProductIndexItem,
  PrecheckIssue,
  PublishLogItem,
  PublishPrecheck,
  PublishPrecheckMarketCheck,
  PublishPrecheckScope,
  PublishPrecheckScopeStatus,
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
  precheckIssues,
  precheckIssueSummary,
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

function normalizePublishPrecheckScope(value: unknown): PublishPrecheckScope | null {
  const scope = asRecord(value)
  if (!Object.keys(scope).length) return null

  const errors = precheckIssues(scope.errors, 'error')
  const warnings = precheckIssues(scope.warnings, 'warning')
  const rawStatus = getString(scope, ['status']).trim().toLowerCase()
  const passed = rawStatus === 'passed' && scope.ok === true && errors.length === 0
  const status: PublishPrecheckScopeStatus = passed ? 'passed' : 'blocked'

  return {
    ok: passed,
    status,
    errors,
    warnings,
  }
}

type ExpectedPublishPrecheckMarket = {
  siteId: string
  logisticType: string
}

type NormalizePublishPrecheckOptions = {
  requireLayeredScopes?: boolean
  expectedMarkets?: readonly ExpectedPublishPrecheckMarket[]
}

function precheckMarketIdentity(siteId: unknown, logisticType: unknown): string {
  return `${String(siteId || '').trim().toUpperCase()}\u0000${String(logisticType || '').trim().toLowerCase()}`
}

function precheckMarketIdentityIsValid(identity: string): boolean {
  const [siteId, logisticType] = identity.split('\u0000')
  return Boolean(siteId && logisticType)
}

function hasDuplicateIdentity(identities: readonly string[]): boolean {
  return new Set(identities).size !== identities.length
}

function sameIdentitySet(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false
  const rightSet = new Set(right)
  return left.every((identity) => rightSet.has(identity))
}

function contractIssue(
  code: string,
  field: string,
  message: string,
  nextAction: string,
): PrecheckIssue {
  return {
    code,
    field,
    message,
    severity: 'error',
    nextAction,
  }
}

/**
 * 统一归一化 API 返回与商品 publish_preview 中持久化的发布预检。
 * 任一层级明确阻断、返回错误或显式 ok=false 时，整体结果一律失败关闭。
 */
export function normalizePublishPrecheck(
  value: unknown,
  options: NormalizePublishPrecheckOptions = {},
): PublishPrecheck {
  const result = asRecord(value)
  const rawErrorItems = precheckIssues(result.errors, 'error')
  const warningItems = precheckIssues(result.warnings, 'warning')
  const parent = normalizePublishPrecheckScope(result.parent)
  const marketChecks = Array.isArray(result.markets)
    ? result.markets.flatMap((value) => {
      const scope = normalizePublishPrecheckScope(value)
      if (!scope) return []
      const market = asRecord(value)
      const check: PublishPrecheckMarketCheck = {
        ...scope,
        siteId: getString(market, ['site_id']).toUpperCase(),
        logisticType: getString(market, ['logistic_type']).toLowerCase(),
      }
      return [check]
    })
    : []
  const scopes = [...(parent ? [parent] : []), ...marketChecks]
  const hasBlockedScope = scopes.some((scope) => (
    scope.status === 'blocked' || scope.errors.length > 0 || !scope.ok
  ))
  const hasScopeErrorDetails = scopes.some((scope) => scope.errors.length > 0)
  const missingRequiredScopes = Boolean(
    options.requireLayeredScopes && (!parent || marketChecks.length === 0),
  )
  const expectedMarketIdentities = (options.expectedMarkets || []).map((market) => (
    precheckMarketIdentity(market.siteId, market.logisticType)
  ))
  const actualMarketIdentities = marketChecks.map((market) => (
    precheckMarketIdentity(market.siteId, market.logisticType)
  ))
  const marketScopesMismatch = Boolean(
    options.requireLayeredScopes
    && !missingRequiredScopes
    && (
      options.expectedMarkets === undefined
      || expectedMarketIdentities.length === 0
      || expectedMarketIdentities.some((identity) => !precheckMarketIdentityIsValid(identity))
      || actualMarketIdentities.some((identity) => !precheckMarketIdentityIsValid(identity))
      || hasDuplicateIdentity(expectedMarketIdentities)
      || hasDuplicateIdentity(actualMarketIdentities)
      || !sameIdentitySet(expectedMarketIdentities, actualMarketIdentities)
    )
  )
  const contractErrors: PrecheckIssue[] = []
  if (missingRequiredScopes) {
    contractErrors.push(contractIssue(
      'LAYERED_PRECHECK_REQUIRED',
      'sites_to_sell',
      '缺少当前 Mercado 父级与销售市场分层预检结果',
      '重新执行上架预检',
    ))
  }
  if (marketScopesMismatch) {
    contractErrors.push(contractIssue(
      'LAYERED_PRECHECK_MARKETS_MISMATCH',
      'sites_to_sell',
      '销售市场预检结果与当前选择不一致',
      '重新执行上架预检',
    ))
  }

  const rawTopStatus = getString(result, ['status']).trim().toLowerCase()
  const topStatusInvalid = Boolean(
    rawTopStatus && rawTopStatus !== 'passed' && rawTopStatus !== 'blocked'
  )
  const topStateContradiction = Boolean(
    (result.ok !== true && result.ok !== false)
    || topStatusInvalid
    || (rawTopStatus === 'passed' && result.ok === false)
    || (rawTopStatus === 'blocked' && result.ok === true)
    || ((result.ok === true || rawTopStatus === 'passed') && (rawErrorItems.length > 0 || hasBlockedScope))
  )
  const hasActionableFailureDetails = Boolean(
    rawErrorItems.length || hasScopeErrorDetails || contractErrors.length
  )
  const resultWouldFail = Boolean(
    result.ok !== true || rawErrorItems.length || hasBlockedScope || contractErrors.length
  )
  const needsInconsistencyIssue = Boolean(
    topStateContradiction || (resultWouldFail && !hasActionableFailureDetails)
  )
  const errorItems = [
    ...rawErrorItems,
    ...contractErrors,
    ...(needsInconsistencyIssue && !rawErrorItems.some((item) => item.code === 'PRECHECK_RESULT_INCONSISTENT')
      ? [contractIssue(
          'PRECHECK_RESULT_INCONSISTENT',
          'precheck',
          '预检状态与检查明细不一致',
          '重新执行上架预检',
        )]
      : []),
  ]

  return {
    ok: result.ok === true && errorItems.length === 0 && !hasBlockedScope,
    errors: errorItems.map(precheckIssueSummary),
    warnings: warningItems.map(precheckIssueSummary),
    errorItems,
    warningItems,
    checkedAt: getString(result, ['checked_at', 'checkedAt']),
    parent,
    marketChecks,
  }
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
