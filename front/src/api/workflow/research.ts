import { apiClient } from '@/api/client'
import type {
  HotProductCandidate,
  ProductResearchConfig,
  ProductResearchMarketSearchMethodBinding,
  ProductResearchProviderTestResult,
  ProductResearchResponse,
  ProductResearchRunSummary,
  ProductResearchSourceRegistryItem,
  ProductResearchSourceStatus,
  ProductResearchTargetMarket,
  UnknownRecord,
} from '@/types/workflow'
import {
  asRecord,
  ensureOk,
  getBoolean,
  getNumber,
  getString,
  wireStringList,
} from './normalizers'

function normalizeHotProductCandidate(value: unknown): HotProductCandidate {
  const record = asRecord(value)
  const priceRecord = asRecord(record.price)
  const amount = getNumber(priceRecord, ['amount'])
  return {
    id: getString(record, ['id']),
    title: getString(record, ['title']),
    imageUrl: getString(record, ['image_url']),
    rank: getNumber(record, ['rank']),
    sourceUrl: getString(record, ['source_url']),
    marketId: getString(record, ['market_id']),
    platform: getString(record, ['platform']),
    site: getString(record, ['site']),
    keyword: getString(record, ['keyword']),
    price: amount
      ? {
          amount,
          currency: getString(priceRecord, ['currency']),
        }
      : undefined,
    rating: getNumber(record, ['rating']),
    reviewCount: getNumber(record, ['review_count']),
    hotScore: getNumber(record, ['hot_score']),
    sourceName: getString(record, ['source_name']),
    collectedAt: getString(record, ['collected_at']),
    raw: record,
  }
}

function normalizeProductResearchSourceStatus(value: unknown): ProductResearchSourceStatus {
  const record = asRecord(value)
  return {
    source: getString(record, ['source']),
    sourceId: getString(record, ['source_id']),
    market: getString(record, ['market']),
    status: getString(record, ['status']),
    itemsFound: getNumber(record, ['items_found']),
    errorMessage: getString(record, ['error_message']),
    providerStrategy: getString(record, ['provider_strategy']),
    rawItemsFound: getNumber(record, ['raw_items_found']),
    itemsFiltered: getNumber(record, ['items_filtered']),
    diagnosticMessage: getString(record, ['diagnostic_message']),
    raw: record,
  }
}

function normalizeProductResearchRun(value: unknown): ProductResearchRunSummary {
  const record = asRecord(value)
  return {
    runId: getString(record, ['run_id']),
    status: getString(record, ['status']),
    searchMode: getString(record, ['search_mode']),
    createdAt: getString(record, ['created_at']),
    completedAt: getString(record, ['completed_at']),
    expiresAt: '',
    description: getString(record, ['description']),
    progressDescription: getString(record, ['progress_description']),
    request: asRecord(record.request),
    raw: record,
  }
}

function normalizeProductResearchResponse(value: unknown): ProductResearchResponse {
  const record = asRecord(value)
  ensureOk(record, '选品搜索失败')
  return {
    run: normalizeProductResearchRun(record.run),
    items: Array.isArray(record.items) ? record.items.map(normalizeHotProductCandidate) : [],
    sourceStatus: Array.isArray(record.source_status)
      ? record.source_status.map(normalizeProductResearchSourceStatus)
      : [],
    description: getString(record, ['description']),
    raw: record,
  }
}

export async function createProductResearchHotProductRun(payload: UnknownRecord): Promise<ProductResearchResponse> {
  const response = await apiClient.post('/api/v1/product-research/hot-products/search', payload)
  return normalizeProductResearchResponse(response.data)
}

export async function fetchProductResearchHotProductRun(runId: string): Promise<ProductResearchResponse> {
  const params = new URLSearchParams({ run_id: runId })
  const response = await apiClient.get(`/api/v1/product-research/hot-products/runs?${params.toString()}`)
  return normalizeProductResearchResponse(response.data)
}

export async function fetchActiveProductResearchHotProductRun(): Promise<ProductResearchResponse | null> {
  const response = await apiClient.get('/api/v1/product-research/hot-products/runs')
  const data = asRecord(response.data)
  ensureOk(data, '读取选品运行状态失败')
  return data.run ? normalizeProductResearchResponse(data) : null
}

function withoutPromptTemplateFields(config: UnknownRecord): UnknownRecord {
  const result = { ...config }
  for (const key of [
    'promptTemplate',
    'prompt_template',
    'promptTemplatePath',
    'prompt_template_path',
    'promptOverride',
    'prompt_override',
    'systemPrompt',
    'system_prompt',
  ]) {
    delete result[key]
  }
  return result
}

function normalizeProductResearchMarketSearchMethodBinding(value: unknown): ProductResearchMarketSearchMethodBinding {
  const record = asRecord(value)
  const configJson = withoutPromptTemplateFields(asRecord(record.config_json))
  const prompt = getString(record, ['prompt'])
  delete configJson.prompt
  return {
    methodId: getString(record, ['method_id']),
    enabled: getBoolean(record, ['enabled'], true),
    prompt,
    configJson,
    raw: record,
  }
}

function normalizeProductResearchProvider(value: unknown): ProductResearchSourceRegistryItem {
  const record = asRecord(value)
  const config = asRecord(record.config_json)
  const sourceType = getString(record, ['source_type'])
  return {
    id: getString(record, ['id']),
    name: getString(record, ['name']),
    sourceType: sourceType || 'api',
    platform: getString(record, ['platform']),
    enabled: getBoolean(record, ['enabled']),
    priority: getNumber(record, ['priority']),
    supportedMarkets: wireStringList(record.supported_markets),
    supportedLanguages: wireStringList(record.supported_languages),
    supportedDataTypes: wireStringList(record.supported_data_types),
    authRequired: getBoolean(record, ['auth_required']),
    rateLimitPerMinute: getNumber(record, ['rate_limit_per_minute']),
    complianceNote: getString(record, ['compliance_note']),
    providerStrategy: getString(config, ['provider_strategy']),
    configJson: config,
    raw: record,
  }
}

function normalizeProductResearchTargetMarket(value: unknown): ProductResearchTargetMarket {
  const record = asRecord(value)
  const rawSearchMethods = Array.isArray(record.search_methods)
    ? record.search_methods
    : []
  return {
    id: getString(record, ['id']),
    platform: getString(record, ['platform']).toLowerCase(),
    site: getString(record, ['site']).toLowerCase(),
    displayName: getString(record, ['display_name']),
    searchMethods: rawSearchMethods.map(normalizeProductResearchMarketSearchMethodBinding),
    raw: record,
  }
}

function normalizeProductResearchConfig(value: unknown): ProductResearchConfig {
  const record = asRecord(value)
  const rawProviders = Array.isArray(record.search_providers)
    ? record.search_providers
    : []
  const rawMarkets = Array.isArray(record.target_markets)
    ? record.target_markets
    : []
  const rawRegistry = Array.isArray(record.source_registry)
    ? record.source_registry
    : []
  return {
    searchProviders: rawProviders.map(normalizeProductResearchProvider),
    targetMarkets: rawMarkets.map(normalizeProductResearchTargetMarket),
    sourceRegistry: rawRegistry.map(normalizeProductResearchProvider),
    raw: record,
  }
}

function withoutProviderPromptConfigFields(config: UnknownRecord): UnknownRecord {
  const result = withoutPromptTemplateFields(config)
  delete result.prompt
  delete result.systemPrompt
  delete result.system_prompt
  delete result.ai_model_id
  delete result.model_id
  return result
}

function withoutBindingPromptConfigFields(config: UnknownRecord): UnknownRecord {
  const result = withoutPromptTemplateFields(config)
  delete result.prompt
  delete result.ai_model_id
  delete result.model_id
  return result
}

function toProductResearchProviderPayload(provider: ProductResearchSourceRegistryItem): UnknownRecord {
  return {
    id: provider.id.trim(),
    name: provider.name.trim() || provider.id.trim(),
    source_type: provider.sourceType || 'api',
    platform: provider.platform.trim().toLowerCase() || provider.id.trim().toLowerCase(),
    enabled: provider.enabled,
    priority: provider.priority || 10,
    supported_markets: provider.supportedMarkets,
    supported_languages: provider.supportedLanguages,
    supported_data_types: provider.supportedDataTypes,
    auth_required: provider.authRequired,
    rate_limit_per_minute: provider.rateLimitPerMinute || null,
    compliance_note: provider.complianceNote,
    config_json: {
      ...withoutProviderPromptConfigFields(provider.configJson),
      provider_strategy: provider.providerStrategy || String(provider.configJson.provider_strategy || ''),
    },
  }
}

function toProductResearchTargetMarketPayload(market: ProductResearchTargetMarket): UnknownRecord {
  return {
    id: market.id.trim(),
    platform: market.platform.trim().toLowerCase(),
    site: market.site.trim().toLowerCase(),
    display_name: market.displayName.trim() || market.id.trim(),
    search_methods: (market.searchMethods || []).map((binding) => ({
      method_id: binding.methodId.trim(),
      enabled: binding.enabled,
      prompt: String(binding.prompt || ''),
      config_json: withoutBindingPromptConfigFields(binding.configJson),
    })),
  }
}

export async function fetchProductResearchSettings(): Promise<ProductResearchConfig> {
  const response = await apiClient.get('/api/v1/product-research/source-registry')
  const data = asRecord(response.data)
  ensureOk(data, '读取选品数据源失败')
  return normalizeProductResearchConfig(data.config)
}

export async function saveProductResearchSettings(config: ProductResearchConfig): Promise<ProductResearchConfig> {
  const response = await apiClient.post('/api/v1/product-research/source-registry/save', {
    config: {
      search_providers: config.searchProviders.map(toProductResearchProviderPayload),
      target_markets: config.targetMarkets.map(toProductResearchTargetMarketPayload),
    },
  })
  const data = asRecord(response.data)
  ensureOk(data, '保存选品数据源失败')
  return normalizeProductResearchConfig(data.config)
}

function normalizeProductResearchProviderTestResult(value: unknown): ProductResearchProviderTestResult {
  const record = asRecord(value)
  return {
    ok: getBoolean(record, ['ok']),
    status: getString(record, ['status']),
    sourceId: getString(record, ['source_id']),
    providerStrategy: getString(record, ['provider_strategy']),
    market: getString(record, ['market']),
    keyword: getString(record, ['keyword']),
    itemsFound: getNumber(record, ['items_found']),
    durationMs: getNumber(record, ['duration_ms']),
    error: getString(record, ['error']),
    sample: asRecord(record.sample),
    raw: record,
  }
}

export async function testProductResearchSearchProvider(
  provider: ProductResearchSourceRegistryItem,
  options: UnknownRecord,
): Promise<ProductResearchProviderTestResult> {
  const response = await apiClient.post('/api/v1/product-research/search-providers/test', {
    provider: toProductResearchProviderPayload(provider),
    options,
  })
  return normalizeProductResearchProviderTestResult(response.data)
}

export async function fetchProductResearchSourceRegistry(): Promise<ProductResearchSourceRegistryItem[]> {
  const config = await fetchProductResearchSettings()
  return config.sourceRegistry
}
