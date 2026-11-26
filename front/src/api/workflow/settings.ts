import { prepareLocalModel } from '@/utils/desktop'
import { refreshOperationAvailability } from '@/api/operationAvailability'
import {  apiClient } from '@/api/client'
import type {
  BackendCanvasAccountStatus,
  BackendCanvasAccountSummary,
  Marketplace,
  MercadoLibreAuthChecklist,
  MercadoLibreTestMode,
  Product,
  UnknownRecord,
} from '@/types/workflow'
import type {
  AiPublicConfig,
  AuthResult,
  ProductMutationResponse,
} from './normalizers'
import {
  asRecord,
  ensureOk,
  getString,
  normalizeMercadoLibreAuthChecklist,
  normalizeProductMutation,
} from './normalizers'


import { normalizeAuthResult, requiredProductId } from './shared'

export interface StoreSettingsResult {
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
}

export async function assignUpc(productId: string): Promise<ProductMutationResponse> {
  const response = await apiClient.post('/api/assign-upc', { product_id: productId })
  return normalizeProductMutation(response.data)
}

export async function fetchAiConfig(): Promise<AiPublicConfig> {
  const response = await apiClient.get('/api/ai-config')
  const data = asRecord(response.data)
  ensureOk(data, '读取 AI 配置失败')
  return { raw: asRecord(data.config) }
}

export async function saveAiConfig(config: UnknownRecord): Promise<AiPublicConfig> {
  const prepared = { ...config }
  if (Array.isArray(config.ai_models)) prepared.ai_models = await Promise.all(config.ai_models.map(model => prepareLocalModel(asRecord(model))))
  const response = await apiClient.post('/api/ai-config/save', { config: prepared })
  const data = asRecord(response.data)
  ensureOk(data, '保存 AI 配置失败')
  await refreshOperationAvailability()
  return { raw: asRecord(data.config) }
}

export async function fetchMercadoLibreAuthChecklist(): Promise<MercadoLibreAuthChecklist> {
  const response = await apiClient.post('/api/mercadolibre/auth-checklist', {})
  const data = asRecord(response.data)
  ensureOk(data, '读取 Mercado Libre 授权清单失败')
  return normalizeMercadoLibreAuthChecklist(data.checklist ?? data)
}

export async function testAiModel(
  model: UnknownRecord,
  conversationId?: string,
): Promise<AuthResult> {
  const response = await apiClient.post(
    '/api/test-ai-model',
    { model: await prepareLocalModel(model) },
    { aiConversationId: conversationId, ...(model.connection_type === 'browser' ? { timeout: 600_000 } : {}) },
  )
  return normalizeAuthResult(response.data)
}

export async function testApiConfig(kind: 'exchange_rate' | '1688', config: UnknownRecord, testValue = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-api-config', { kind, config, test_value: testValue }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function saveStoreSettings(storeConfig: UnknownRecord): Promise<StoreSettingsResult> {
  const response = await apiClient.post('/api/save-settings', { storeConfig })
  const data = asRecord(response.data)
  ensureOk(data, '保存平台授权失败')
  return {
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
  }
}

export async function buildMercadoLibreAuthLink(appId: string, redirectUri: string): Promise<string> {
  const response = await apiClient.post('/api/mercadolibre/auth-link', { app_id: appId, redirect_uri: redirectUri })
  const data = asRecord(response.data)
  ensureOk(data, '生成授权链接失败')
  return getString(data, ['auth_url', 'url', 'link'])
}

export async function refreshMercadoLibreToken(params: UnknownRecord = {}): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/refresh-token', params, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function runMercadoLibreRealAuthTest(product: Product, mode: MercadoLibreTestMode, categoryId = ''): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/real-auth-test', { product_id: requiredProductId(product, '运行真实授权测试'), mode, category_id: categoryId }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function openAuthLink(url: string, browser = 'default'): Promise<AuthResult> {
  const response = await apiClient.post('/api/open-auth-link', { url, browser }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function exchangeMercadoLibreCode(codeOrUrl: string, params: UnknownRecord = {}): Promise<AuthResult> {
  const response = await apiClient.post('/api/mercadolibre/exchange-code', { code_or_url: codeOrUrl, ...params }, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export async function testStoreAuth(platform: Marketplace, scope = '', config: UnknownRecord = {}): Promise<AuthResult> {
  const payload: UnknownRecord = { platform, scope }
  if (Object.keys(config).length) payload.config = config
  const response = await apiClient.post('/api/test-store-auth', payload, { validateStatus: () => true })
  return normalizeAuthResult(response.data)
}

export interface StoreCurrencySelectionResult extends StoreSettingsResult {
  publishReady: boolean
  currencyConfiguration: UnknownRecord
}

export async function saveStoreCurrency(platform: Marketplace, listingCurrency: string): Promise<StoreCurrencySelectionResult> {
  const response = await apiClient.post('/api/store-auth/currency', { platform, listing_currency: listingCurrency }, { validateStatus: () => true })
  const data = asRecord(response.data)
  ensureOk(data, getString(data, ['error'], '保存发布币种失败'))
  return {
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
    publishReady: data.publish_ready === true,
    currencyConfiguration: asRecord(data.currencyConfiguration),
  }
}

export interface CanvasAccountActionResult {
  ok: boolean
  account?: BackendCanvasAccountSummary
  accounts: BackendCanvasAccountSummary[]
  canvasStatus?: BackendCanvasAccountStatus
  loginRequired?: boolean
  gatewayError?: boolean
  error?: string
}

function isPlainRecord(value: unknown): value is UnknownRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function normalizeCanvasStatus(raw: unknown): BackendCanvasAccountStatus {
  const record = asRecord(raw)
  const accounts = Array.isArray(record.accounts) ? record.accounts as BackendCanvasAccountSummary[] : []
  return {
    scope: typeof record.scope === 'string' ? record.scope : 'web_offline',
    desktop_online: record.desktop_online === true,
    accounts,
  }
}

export async function loginCanvasAccount(options: {
  model: UnknownRecord
  profile?: string
  forceLogin?: boolean
  checkOnly?: boolean
}): Promise<CanvasAccountActionResult> {
  const prepared = await prepareLocalModel(options.model)
  const response = await apiClient.post(
    '/api/ai-config/canvas/account-login',
    {
      model: prepared,
      profile: options.profile || '',
      force_login: Boolean(options.forceLogin),
      check_only: Boolean(options.checkOnly),
    },
    { timeout: 600_000, validateStatus: () => true },
  )
  if (!isPlainRecord(response.data)) {
    const status = Number(response.status || 0)
    const error = status === 524
      ? '等待登录响应超时（HTTP 524），若已在弹出窗口完成登录，请稍候或刷新查看账号状态。'
      : `请求异常（HTTP ${status || '未知'}），若已在弹出窗口完成登录，请刷新查看账号状态。`
    return {
      ok: false,
      accounts: [],
      gatewayError: true,
      error,
    }
  }
  const data = response.data
  const hasCanvasStatus = isPlainRecord(data.canvas_status)
  const normalizedStatus = hasCanvasStatus ? normalizeCanvasStatus(data.canvas_status) : undefined
  const accounts = Array.isArray(data.accounts)
    ? data.accounts as BackendCanvasAccountSummary[]
    : (normalizedStatus?.accounts || [])
  const canvasStatus = normalizedStatus ? { ...normalizedStatus, accounts } : undefined
  return {
    ok: data.ok === true,
    account: isPlainRecord(data.account) ? data.account as BackendCanvasAccountSummary : undefined,
    accounts,
    canvasStatus,
    loginRequired: data.login_required === true,
    gatewayError: Number(response.status || 0) >= 500,
    error: getString(data, ['error']),
  }
}

export async function logoutCanvasAccount(profile: string): Promise<CanvasAccountActionResult> {
  const response = await apiClient.post(
    '/api/ai-config/canvas/account-logout',
    { profile },
    { validateStatus: () => true },
  )
  const data = asRecord(response.data)
  ensureOk(data, getString(data, ['error'], '取消 Google 登录失败'))
  const canvasStatus = normalizeCanvasStatus(data.canvas_status)
  const accounts = Array.isArray(data.accounts) ? data.accounts as BackendCanvasAccountSummary[] : canvasStatus.accounts
  return { ok: true, accounts, canvasStatus: { ...canvasStatus, accounts } }
}

export async function toggleCanvasAccount(profile: string, enabled: boolean): Promise<CanvasAccountActionResult> {
  const response = await apiClient.post(
    '/api/ai-config/canvas/account-toggle',
    { profile, enabled },
    { validateStatus: () => true },
  )
  const data = asRecord(response.data)
  ensureOk(data, getString(data, ['error'], '更新 Google 账号启用状态失败'))
  const canvasStatus = normalizeCanvasStatus(data.canvas_status)
  const accounts = Array.isArray(data.accounts) ? data.accounts as BackendCanvasAccountSummary[] : canvasStatus.accounts
  return { ok: true, accounts, canvasStatus: { ...canvasStatus, accounts } }
}

export async function clearStoreAuth(platform: Marketplace): Promise<StoreSettingsResult> {
  const response = await apiClient.post('/api/store-auth/clear', { platform })
  const data = asRecord(response.data)
  ensureOk(data, '清除授权失败')
  return {
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
  }
}
