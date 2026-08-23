import {  apiClient } from '@/api/client'
import type {

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

export async function testAiModel(model: UnknownRecord): Promise<AuthResult> {
  const response = await apiClient.post('/api/test-ai-model', { model })
  return normalizeAuthResult(response.data)
}

export async function testApiConfig(kind: 'exchange_rate' | '1688' | 'yunexpress', config: UnknownRecord, testValue = ''): Promise<AuthResult> {
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

export async function clearStoreAuth(platform: Marketplace): Promise<StoreSettingsResult> {
  const response = await apiClient.post('/api/store-auth/clear', { platform })
  const data = asRecord(response.data)
  ensureOk(data, '清除授权失败')
  return {
    storeConfig: asRecord(data.storeConfig),
    storeAuthSummary: asRecord(data.storeAuthSummary),
  }
}
