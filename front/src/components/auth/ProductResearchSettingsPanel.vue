<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchProductResearchSettings, saveProductResearchSettings, testProductResearchSearchProvider } from '@/api/workflow'
import type { ProductResearchConfig, ProductResearchMarketSearchMethodBinding, ProductResearchProviderTestResult, ProductResearchSourceRegistryItem, ProductResearchTargetMarket, UnknownRecord } from '@/types/workflow'
import {
  productResearchProviderName,
  productResearchSourceTypeLabel,
  productResearchStrategyLabel,
} from '@/utils/productResearchLabels'

const props = defineProps<{
  aiModels?: UnknownRecord[]
  aiUseCasePrompts?: UnknownRecord
  embedded?: boolean
}>()

const sourceTypeOptions = [
  { value: 'api', label: 'API 搜索' },
  { value: 'ai_search', label: 'AI 搜索' },
  { value: 'crawler', label: '网页采集' },
  { value: 'manual_import', label: '人工导入' },
]

const strategyOptions = [
  { value: 'configured_api', label: '已配置 API' },
  { value: 'ai_web_search', label: 'AI 联网搜索' },
  { value: 'manual_import', label: '人工导入' },
]

const emptySettings = (): ProductResearchConfig => ({
  searchProviders: [],
  targetMarkets: [],
  sourceRegistry: [],
  raw: {},
})

const settings = ref<ProductResearchConfig>(emptySettings())
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const savedMessage = ref('')
const selectedProviderIndex = ref(0)
const selectedMarketIndex = ref(0)
const selectedMarketMethodId = ref('')
const selectedConfigText = ref('{}')
const testingProvider = ref(false)
const providerTestResult = ref<ProductResearchProviderTestResult | null>(null)
const providerTestMarket = ref('amazon-us')
const providerTestKeyword = ref('mahjong gift')

const selectedProvider = computed(() => settings.value.searchProviders[selectedProviderIndex.value] || null)
const selectedMarket = computed(() => settings.value.targetMarkets[selectedMarketIndex.value] || null)
const selectedMarketBinding = computed(() => selectedMarket.value?.searchMethods.find((binding) => binding.methodId === selectedMarketMethodId.value) || null)
const selectedMarketMethod = computed(() => selectedMarketBinding.value ? methodForBinding(selectedMarketBinding.value) : null)
const selectedProviderIsAiSearch = computed(() => selectedProvider.value?.sourceType === 'ai_search' || selectedProvider.value?.providerStrategy === 'ai_web_search')
const selectedProviderIsManualImport = computed(() => selectedProvider.value?.sourceType === 'manual_import' || selectedProvider.value?.providerStrategy === 'manual_import')
const aiModels = computed(() => (props.aiModels || []).map(asRecord).filter((model) => String(model.id || '').trim()))
const webSearchModels = computed(() => aiModels.value.filter((model) => {
  const rawCapabilities = Array.isArray(model.effective_capabilities) && model.effective_capabilities.length ? model.effective_capabilities : model.capabilities
  const capabilities = Array.isArray(rawCapabilities) ? rawCapabilities.map((item) => String(item || '')) : []
  return capabilities.includes('chat') && capabilities.includes('json') && capabilities.includes('web_search') && model.enabled !== false
}))
const strategyOptionsForSelectedProvider = computed(() => {
  if (selectedProviderIsAiSearch.value) return strategyOptions.filter((option) => option.value === 'ai_web_search')
  if (selectedProviderIsManualImport.value) return strategyOptions.filter((option) => option.value === 'manual_import')
  if (selectedProvider.value?.sourceType === 'api') return strategyOptions.filter((option) => option.value === 'configured_api')
  return strategyOptions.filter((option) => option.value !== 'ai_web_search' && option.value !== 'manual_import')
})

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}
}

function splitList(value: string, uppercase = false) {
  return value
    .replaceAll('，', ',')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => uppercase ? item.toUpperCase() : item)
}

function joinList(values: string[]) {
  return values.join(', ')
}

function eventText(event: Event) {
  return (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value
}

function providerLabel(provider: ProductResearchSourceRegistryItem) {
  return productResearchProviderName(provider)
}

function sourceTypeLabel(value: string) {
  return sourceTypeOptions.find((option) => option.value === value)?.label || productResearchSourceTypeLabel(value)
}

function strategyLabel(value: string) {
  return strategyOptions.find((option) => option.value === value)?.label || productResearchStrategyLabel(value)
}

function providerTestMarketDefault(provider: ProductResearchSourceRegistryItem | null) {
  return selectedMarket.value?.id || 'amazon-us'
}

function providerTestLanguageDefault(provider: ProductResearchSourceRegistryItem | null) {
  return provider?.supportedLanguages.find((language) => language && language !== '*') || 'en'
}

function defaultApiConfig(): UnknownRecord {
  return {
    provider_strategy: 'configured_api',
    request: {
      method: 'GET',
      url: '',
      auth_type: 'api_key_header',
      api_key_header: 'x-api-key',
      api_key: '',
      bearer_token: '',
      headers: {},
      query: {},
      body: {},
    },
    response: {
      items_path: 'data.items',
      title_path: 'title',
      keyword_path: 'keyword',
      price_path: 'price.amount',
      url_path: 'url',
      image_path: 'image_url',
    },
  }
}

function defaultAiSearchConfig(): UnknownRecord {
  return {
    provider_strategy: 'ai_web_search',
    ai_model_id: '',
    max_items: 12,
    require_source_url: true,
    require_image_url: true,
    stream: true,
  }
}

function uniqueProviderId(prefix: string) {
  const existing = new Set(settings.value.searchProviders.map((provider) => provider.id))
  let index = settings.value.searchProviders.length + 1
  let candidate = `${prefix}_${index}`
  while (existing.has(candidate)) {
    index += 1
    candidate = `${prefix}_${index}`
  }
  return candidate
}

function uniqueMarketId(base = 'amazon-us') {
  const existing = new Set(settings.value.targetMarkets.map((market) => market.id))
  let candidate = base
  let index = settings.value.targetMarkets.length + 1
  while (existing.has(candidate)) {
    candidate = `${base}-${index}`
    index += 1
  }
  return candidate
}

function defaultManualImportConfig(): UnknownRecord {
  return {
    provider_strategy: 'manual_import',
    items: [],
  }
}

function blankProvider(): ProductResearchSourceRegistryItem {
  const next = settings.value.searchProviders.length + 1
  const id = uniqueProviderId('api_source')
  return {
    id,
    name: `API 搜索手段 ${next}`,
    sourceType: 'api',
    platform: `source_${next}`,
    enabled: true,
    priority: next,
    supportedMarkets: [],
    supportedLanguages: ['en'],
    supportedDataTypes: ['marketplace_products'],
    authRequired: false,
    rateLimitPerMinute: 30,
    complianceNote: '',
    providerStrategy: 'configured_api',
    configJson: defaultApiConfig(),
    raw: {},
  }
}

function blankAiSearchProvider(): ProductResearchSourceRegistryItem {
  const id = uniqueProviderId('ai_search')
  return {
    id,
    name: 'AI 搜索',
    sourceType: 'ai_search',
    platform: 'ai_model',
    enabled: true,
    priority: 1,
    supportedMarkets: [],
    supportedLanguages: [],
    supportedDataTypes: ['ai_web_search'],
    authRequired: false,
    rateLimitPerMinute: 0,
    complianceNote: '',
    providerStrategy: 'ai_web_search',
    configJson: defaultAiSearchConfig(),
    raw: {},
  }
}

function blankMarket(): ProductResearchTargetMarket {
  const id = uniqueMarketId('amazon-us')
  return {
    id,
    platform: 'amazon',
    site: 'amazon.com',
    displayName: id === 'amazon-us' ? 'Amazon US' : `Amazon US ${settings.value.targetMarkets.length + 1}`,
    searchMethods: [],
    raw: {},
  }
}

function searchMethodLabel(provider: ProductResearchSourceRegistryItem | null | undefined) {
  return provider ? providerLabel(provider) : '未知搜索手段'
}

function methodForBinding(binding: ProductResearchMarketSearchMethodBinding) {
  return settings.value.searchProviders.find((provider) => provider.id === binding.methodId) || null
}

function marketBindingFor(providerId: string) {
  return selectedMarket.value?.searchMethods.find((binding) => binding.methodId === providerId) || null
}

function isMethodBound(providerId: string) {
  return Boolean(marketBindingFor(providerId))
}

function syncSelectedMarketMethod() {
  selectedMarketMethodId.value = selectedMarket.value?.searchMethods[0]?.methodId || ''
}

function isAiSearchMethod(provider: ProductResearchSourceRegistryItem | null | undefined) {
  return provider?.sourceType === 'ai_search' || provider?.providerStrategy === 'ai_web_search'
}

function researchWebSearchPromptTemplate(): string {
  return String(asRecord(props.aiUseCasePrompts?.['research.web_search']).user_prompt || '')
}

function marketCurrency(market: ProductResearchTargetMarket) {
  const currencyByMarket: Record<string, string> = {
    'amazon-us': 'USD',
    'amazon-uk': 'GBP',
    'amazon-ca': 'CAD',
    'amazon-au': 'AUD',
  }
  return currencyByMarket[market.id] || 'USD'
}

function renderPromptTemplate(template: string, context: Record<string, string | number>) {
  let rendered = template
  for (const [key, value] of Object.entries(context)) {
    rendered = rendered.replaceAll(`{$${key}}`, String(value))
    rendered = rendered.replaceAll(`{${key}}`, String(value))
  }
  return rendered
}

function defaultAiPrompt(market: ProductResearchTargetMarket) {
  const template = researchWebSearchPromptTemplate()
  if (!template) return ''
  return renderPromptTemplate(template, {
    market: market.id,
    market_id: market.id,
    marketId: market.id,
    display_name: market.displayName || market.id,
    displayName: market.displayName || market.id,
    platform: market.platform,
    site: market.site,
    currency: marketCurrency(market),
    keyword: '',
    keywords: '',
    limit: Number(defaultAiSearchConfig().max_items || 12),
  })
}

function defaultBindingConfig(provider?: ProductResearchSourceRegistryItem | null, market?: ProductResearchTargetMarket | null): UnknownRecord {
  if (isAiSearchMethod(provider) && market) {
    return { prompt: defaultAiPrompt(market) }
  }
  return {}
}

function bindingConfigField(binding: ProductResearchMarketSearchMethodBinding, field: string, fallback = '') {
  return String(binding.configJson[field] ?? fallback)
}

function updateBindingConfigField(binding: ProductResearchMarketSearchMethodBinding, field: string, value: string | number | boolean) {
  binding.configJson = {
    ...binding.configJson,
    [field]: value,
  }
}

function selectedBindingPrompt() {
  if (!selectedMarketBinding.value) return ''
  const savedPrompt = bindingConfigField(selectedMarketBinding.value, 'prompt')
  if (savedPrompt) return savedPrompt
  return selectedMarket.value ? defaultAiPrompt(selectedMarket.value) : ''
}

function resetSelectedBindingPrompt() {
  if (!selectedMarketBinding.value || !selectedMarket.value) return
  updateBindingConfigField(selectedMarketBinding.value, 'prompt', defaultAiPrompt(selectedMarket.value))
}

function ensureAiBindingPrompts() {
  for (const market of settings.value.targetMarkets) {
    for (const binding of market.searchMethods) {
      const provider = methodForBinding(binding)
      if (!isAiSearchMethod(provider)) continue
      if (bindingConfigField(binding, 'prompt')) continue
      binding.configJson = {
        ...binding.configJson,
        prompt: defaultAiPrompt(market),
      }
    }
  }
}

function selectMarketSearchMethod(providerId: string) {
  const market = selectedMarket.value
  selectedMarketMethodId.value = providerId
  if (!market || !providerId) return
  if (market.searchMethods.some((binding) => binding.methodId === providerId)) return
  const provider = settings.value.searchProviders.find((item) => item.id === providerId)
  if (!provider) return
  market.searchMethods.push({
    methodId: provider.id,
    enabled: true,
    configJson: defaultBindingConfig(provider, market),
    raw: {},
  })
}

function removeSelectedMarketSearchMethod() {
  const market = selectedMarket.value
  const methodId = selectedMarketMethodId.value
  if (!market || !methodId) return
  market.searchMethods = market.searchMethods.filter((binding) => binding.methodId !== methodId)
  selectedMarketMethodId.value = market.searchMethods[0]?.methodId || ''
}

function clampSelections() {
  selectedProviderIndex.value = Math.min(Math.max(selectedProviderIndex.value, 0), Math.max(settings.value.searchProviders.length - 1, 0))
  selectedMarketIndex.value = Math.min(Math.max(selectedMarketIndex.value, 0), Math.max(settings.value.targetMarkets.length - 1, 0))
  if (selectedMarket.value && !selectedMarket.value.searchMethods.some((binding) => binding.methodId === selectedMarketMethodId.value)) {
    syncSelectedMarketMethod()
  }
}

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    settings.value = await fetchProductResearchSettings()
    clampSelections()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '读取选品调研配置失败'
  } finally {
    loading.value = false
  }
}

function addProvider(sourceType: 'api' | 'ai_search' = 'api') {
  const provider = sourceType === 'ai_search' ? blankAiSearchProvider() : blankProvider()
  settings.value.searchProviders.push(provider)
  selectedProviderIndex.value = settings.value.searchProviders.length - 1
  if (sourceType === 'ai_search' && selectedMarket.value) providerTestMarket.value = selectedMarket.value.id
}

function removeProvider(index: number) {
  const provider = settings.value.searchProviders[index]
  if (!provider) return
  settings.value.searchProviders.splice(index, 1)
  for (const market of settings.value.targetMarkets) {
    market.searchMethods = market.searchMethods.filter((binding) => binding.methodId !== provider.id)
  }
  clampSelections()
}

function addMarket() {
  settings.value.targetMarkets.push(blankMarket())
  selectedMarketIndex.value = settings.value.targetMarkets.length - 1
  selectedMarketMethodId.value = ''
}

function removeMarket(index: number) {
  settings.value.targetMarkets.splice(index, 1)
  clampSelections()
}

function updateProviderList(field: 'supportedLanguages' | 'supportedDataTypes', value: string) {
  if (!selectedProvider.value) return
  selectedProvider.value[field] = splitList(value)
}

function setProviderSourceType(value: string) {
  if (!selectedProvider.value) return
  selectedProvider.value.sourceType = value
  if (value === 'ai_search') {
    selectedProvider.value.platform = 'ai_model'
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['ai_web_search']
    setSelectedConfigJson({
      ...defaultAiSearchConfig(),
      ...(selectedProvider.value.providerStrategy === 'ai_web_search' ? asRecord(selectedProvider.value.configJson) : {}),
      provider_strategy: 'ai_web_search',
    })
  } else if (value === 'manual_import') {
    selectedProvider.value.platform = selectedProvider.value.platform || 'manual_import'
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['keyword_trend', 'marketplace_products', 'content_trend']
    setSelectedConfigJson({
      ...defaultManualImportConfig(),
      ...(selectedProvider.value.providerStrategy === 'manual_import' ? asRecord(selectedProvider.value.configJson) : {}),
      provider_strategy: 'manual_import',
    })
  } else if (value === 'api') {
    if (!selectedProvider.value.platform || selectedProvider.value.platform === 'ai_model' || selectedProvider.value.platform.startsWith('source_')) {
      selectedProvider.value.platform = selectedProvider.value.id || 'api_source'
    }
    if (selectedProvider.value.providerStrategy !== 'configured_api') {
      setSelectedConfigJson(defaultApiConfig())
    }
  } else if (selectedProvider.value.providerStrategy === 'ai_web_search' || selectedProvider.value.providerStrategy === 'manual_import') {
    setSelectedConfigJson(defaultApiConfig())
  }
}

function setSelectedConfigJson(configJson: UnknownRecord) {
  if (!selectedProvider.value) return
  selectedProvider.value.configJson = configJson
  selectedProvider.value.providerStrategy = String(configJson.provider_strategy || selectedProvider.value.providerStrategy || 'configured_api')
  selectedConfigText.value = JSON.stringify(configJson, null, 2)
}

function setProviderStrategy(value: string) {
  if (!selectedProvider.value) return
  const current = asRecord(selectedProvider.value.configJson)
  const next = {
    ...(value === 'configured_api' ? defaultApiConfig() : {}),
    ...(value === 'ai_web_search' ? defaultAiSearchConfig() : {}),
    ...(value === 'manual_import' ? defaultManualImportConfig() : {}),
    ...current,
    provider_strategy: value,
  }
  if (value === 'ai_web_search') {
    selectedProvider.value.sourceType = 'ai_search'
    selectedProvider.value.platform = 'ai_model'
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['ai_web_search']
  } else if (value === 'manual_import') {
    selectedProvider.value.sourceType = 'manual_import'
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['keyword_trend', 'marketplace_products', 'content_trend']
  } else if (value === 'configured_api' && selectedProvider.value.sourceType === 'ai_search') {
    selectedProvider.value.sourceType = 'api'
  }
  setSelectedConfigJson(next)
}

function apiRequestField(field: string, fallback = '') {
  const request = asRecord(selectedProvider.value?.configJson?.request)
  return String(request[field] ?? fallback)
}

function apiResponseField(field: string, fallback = '') {
  const response = asRecord(selectedProvider.value?.configJson?.response)
  return String(response[field] ?? fallback)
}

function aiSearchConfigField(field: string, fallback = '') {
  const config = asRecord(selectedProvider.value?.configJson)
  return String(config[field] ?? fallback)
}

function updateAiSearchConfigField(field: string, value: string | number | boolean) {
  if (!selectedProvider.value) return
  const configJson = asRecord(selectedProvider.value.configJson)
  setSelectedConfigJson({
    ...defaultAiSearchConfig(),
    ...configJson,
    provider_strategy: 'ai_web_search',
    [field]: value,
  })
}

function apiJsonField(section: 'headers' | 'query' | 'body') {
  const request = asRecord(selectedProvider.value?.configJson?.request)
  const value = request[section]
  if (value && typeof value === 'object') return JSON.stringify(value, null, 2)
  return value ? String(value) : '{}'
}

function updateApiRequestField(field: string, value: string) {
  if (!selectedProvider.value) return
  const configJson = asRecord(selectedProvider.value.configJson)
  const request = {
    ...asRecord(configJson.request),
    [field]: value,
  }
  setSelectedConfigJson({
    ...configJson,
    provider_strategy: 'configured_api',
    request,
  })
}

function updateApiResponseField(field: string, value: string) {
  if (!selectedProvider.value) return
  const configJson = asRecord(selectedProvider.value.configJson)
  const response = {
    ...asRecord(configJson.response),
    [field]: value,
  }
  setSelectedConfigJson({
    ...configJson,
    provider_strategy: 'configured_api',
    response,
  })
}

function apiJsonSectionLabel(section: 'headers' | 'query' | 'body') {
  const labels = {
    headers: '请求头',
    query: '查询参数',
    body: '请求体',
  }
  return labels[section]
}

function updateApiJsonField(section: 'headers' | 'query' | 'body', value: string) {
  if (!selectedProvider.value) return
  try {
    const parsed = JSON.parse(value || '{}')
    if ((section === 'headers' || section === 'query') && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) {
      error.value = `${apiJsonSectionLabel(section)}必须是 JSON 对象`
      return
    }
    const configJson = asRecord(selectedProvider.value.configJson)
    const request = {
      ...asRecord(configJson.request),
      [section]: parsed,
    }
    setSelectedConfigJson({
      ...configJson,
      provider_strategy: 'configured_api',
      request,
    })
    error.value = ''
  } catch {
    error.value = `${apiJsonSectionLabel(section)}不是合法 JSON`
  }
}

function applySelectedConfigJson() {
  if (!selectedProvider.value) return true
  try {
    const parsed = JSON.parse(selectedConfigText.value || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      error.value = '高级配置 (config_json) 必须是 JSON 对象'
      return false
    }
    setSelectedConfigJson(parsed as UnknownRecord)
    return true
  } catch {
    error.value = '高级配置 (config_json) 不是合法 JSON'
    return false
  }
}

async function saveSettings() {
  error.value = ''
  savedMessage.value = ''
  if (!applySelectedConfigJson()) return
  if (!settings.value.searchProviders.every((provider) => provider.id.trim())) {
    error.value = '每个搜索手段都需要 ID'
    return
  }
  if (!settings.value.targetMarkets.every((market) => market.id.trim() && market.platform.trim() && market.site.trim() && market.displayName.trim())) {
    error.value = '每个目标市场都需要 ID、平台、站点和显示名称'
    return
  }
  if (!settings.value.targetMarkets.every((market) => market.searchMethods.every((binding) => binding.methodId.trim()))) {
    error.value = '目标市场关联的搜索手段不能为空'
    return
  }
  ensureAiBindingPrompts()
  saving.value = true
  try {
    settings.value = await saveProductResearchSettings(settings.value)
    savedMessage.value = '已保存选品调研来源配置'
    clampSelections()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '保存选品调研配置失败'
  } finally {
    saving.value = false
  }
}

async function testSelectedProvider() {
  if (!selectedProvider.value) return
  error.value = ''
  savedMessage.value = ''
  providerTestResult.value = null
  if (!applySelectedConfigJson()) return
  testingProvider.value = true
  try {
    providerTestResult.value = await testProductResearchSearchProvider(selectedProvider.value, {
      market: providerTestMarket.value.trim().replace(/^\*$/, '') || providerTestMarketDefault(selectedProvider.value),
      language: providerTestLanguageDefault(selectedProvider.value),
      keyword: providerTestKeyword.value.trim() || 'mahjong gift',
      data_type: selectedProvider.value.supportedDataTypes[0] || 'marketplace_products',
    })
  } catch (exc) {
    providerTestResult.value = {
      ok: false,
      status: 'failed',
      sourceId: selectedProvider.value.id,
      providerStrategy: selectedProvider.value.providerStrategy,
      market: providerTestMarket.value,
      keyword: providerTestKeyword.value,
      itemsFound: 0,
      durationMs: 0,
      error: exc instanceof Error ? exc.message : selectedProvider.value.providerStrategy === 'ai_web_search' ? '测试 AI 搜索失败' : '测试 API 失败',
      sample: {},
      raw: {},
    }
  } finally {
    testingProvider.value = false
  }
}

watch(selectedProvider, (provider) => {
  selectedConfigText.value = JSON.stringify(provider?.configJson || {}, null, 2)
  providerTestMarket.value = providerTestMarketDefault(provider)
  providerTestKeyword.value = 'mahjong gift'
  providerTestResult.value = null
}, { immediate: true })

watch(selectedMarket, () => {
  syncSelectedMarketMethod()
}, { immediate: true })

onMounted(loadSettings)
</script>

<template>
  <section :class="props.embedded ? 'rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70' : 'rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80'">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">选品调研来源</h2>
        <p class="muted mt-1">目标市场管理搜索手段；搜索手段统一返回 HotProductCandidate。</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn-outline" :disabled="loading || saving" @click="loadSettings">刷新</button>
        <button class="btn btn-primary" :disabled="loading || saving" @click="saveSettings">{{ saving ? '保存中' : '保存配置' }}</button>
      </div>
    </div>

    <p v-if="error" class="mt-4 rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-200">{{ error }}</p>
    <p v-if="savedMessage" class="mt-4 rounded-lg border border-success-200 bg-success-50 px-3 py-2 text-sm text-success-700 dark:border-success-500/30 dark:bg-success-500/10 dark:text-success-200">{{ savedMessage }}</p>

    <div class="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
      <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">搜索手段</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ settings.searchProviders.length }} 个来源</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-outline py-1.5 text-sm" @click="addProvider('api')">添加 API</button>
            <button class="btn btn-primary py-1.5 text-sm" @click="addProvider('ai_search')">添加 AI 搜索</button>
          </div>
        </div>
        <p class="mt-2 text-xs text-accent-500 dark:text-accent-400">这里维护可用能力；是否用于某个市场，在右侧目标市场里关联。</p>

        <div class="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div class="space-y-2">
            <button
              v-for="(provider, index) in settings.searchProviders"
              :key="provider.id || index"
              type="button"
              class="w-full rounded-lg border px-3 py-2 text-left text-sm transition"
              :class="selectedProviderIndex === index ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border-accent-200 bg-white text-accent-700 hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:text-accent-200'"
              @click="selectedProviderIndex = index"
            >
              <span class="block truncate font-semibold">{{ providerLabel(provider) }}</span>
              <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ provider.id }} · {{ sourceTypeLabel(provider.sourceType) }}</span>
            </button>
          </div>

          <div v-if="selectedProvider" class="space-y-3">
            <div class="grid gap-3 md:grid-cols-2">
              <label v-if="!selectedProviderIsAiSearch" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">ID</span>
                <input v-model="selectedProvider.id" class="input" placeholder="api_amazon" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">名称</span>
                <input v-model="selectedProvider.name" class="input" :placeholder="selectedProviderIsAiSearch ? 'AI 搜索' : '亚马逊 API 搜索'" />
              </label>
              <label v-if="!selectedProviderIsAiSearch" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">类型</span>
                <select :value="selectedProvider.sourceType" class="input" @change="setProviderSourceType(eventText($event))">
                  <option v-for="option in sourceTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <div v-else class="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-100">
                <span class="block text-xs font-semibold opacity-75">类型</span>
                <span class="mt-1 block font-semibold">AI 搜索</span>
              </div>
              <label v-if="!selectedProviderIsAiSearch" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">平台</span>
                <input v-model="selectedProvider.platform" class="input" placeholder="amazon" />
              </label>
              <div v-else class="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-100">
                <span class="block text-xs font-semibold opacity-75">运行方式</span>
                <span class="mt-1 block font-semibold">AI 联网搜索</span>
              </div>
              <label v-if="!selectedProviderIsAiSearch" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">优先级</span>
                <input v-model.number="selectedProvider.priority" class="input" min="1" type="number" />
              </label>
              <div v-else class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200">
                <span class="block text-xs font-semibold text-accent-500 dark:text-accent-400">基础配置</span>
                <span class="mt-1 block font-semibold">Prompt 由文件维护</span>
              </div>
              <label v-if="strategyOptionsForSelectedProvider.length > 1" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">策略</span>
                <select :value="selectedProvider.providerStrategy" class="input" @change="setProviderStrategy(eventText($event))">
                  <option v-for="option in strategyOptionsForSelectedProvider" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <div v-else-if="!selectedProviderIsAiSearch" class="rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-700 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-200">
                <span class="block text-xs font-semibold text-accent-500 dark:text-accent-400">策略</span>
                <span class="mt-1 block font-semibold">{{ strategyLabel(selectedProvider.providerStrategy) }}</span>
              </div>
            </div>

            <div v-if="!selectedProviderIsAiSearch" class="grid gap-3 md:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">语言</span>
                <input class="input" :value="joinList(selectedProvider.supportedLanguages)" placeholder="en" @input="updateProviderList('supportedLanguages', eventText($event))" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">数据类型</span>
                <input class="input" :value="joinList(selectedProvider.supportedDataTypes)" placeholder="marketplace_products" @input="updateProviderList('supportedDataTypes', eventText($event))" />
              </label>
            </div>

            <div v-if="!selectedProviderIsAiSearch" class="grid gap-3 md:grid-cols-3">
              <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                <input v-model="selectedProvider.enabled" type="checkbox" />
                <span>启用</span>
              </label>
              <label v-if="!selectedProviderIsAiSearch" class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                <input v-model="selectedProvider.authRequired" type="checkbox" />
                <span>需要授权</span>
              </label>
              <label v-if="!selectedProviderIsAiSearch" class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">限速/分钟</span>
                <input v-model.number="selectedProvider.rateLimitPerMinute" class="input" min="0" type="number" />
              </label>
            </div>

            <label v-if="!selectedProviderIsAiSearch" class="block">
              <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">说明</span>
              <input v-model="selectedProvider.complianceNote" class="input" :placeholder="selectedProviderIsAiSearch ? 'AI 联网搜索来源' : '通过 Amazon API 获取热榜'" />
            </label>

            <div v-if="selectedProvider.sourceType === 'ai_search' || selectedProvider.providerStrategy === 'ai_web_search'" class="space-y-3 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="text-sm font-semibold text-accent-950 dark:text-white">AI 搜索配置</h4>
                <div class="flex flex-wrap items-center gap-2">
                  <span :class="webSearchModels.length ? 'badge-success' : 'badge-muted'">AI 模型</span>
                  <span class="badge-info">{{ strategyLabel(selectedProvider.providerStrategy || 'ai_web_search') }}</span>
                </div>
              </div>

              <div class="grid gap-3 md:grid-cols-2">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型覆盖</span>
                  <select class="input" :value="aiSearchConfigField('ai_model_id')" @change="updateAiSearchConfigField('ai_model_id', eventText($event))">
                    <option value="">自动匹配模型</option>
                    <option v-for="model in webSearchModels" :key="String(model.id)" :value="String(model.id)">{{ model.name || model.id }}</option>
                  </select>
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">最大结果</span>
                  <input class="input" min="1" max="50" type="number" :value="Number(aiSearchConfigField('max_items', '12')) || 12" @input="updateAiSearchConfigField('max_items', Number(eventText($event)) || 12)" />
                </label>
              </div>

              <details class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
                <summary class="cursor-pointer text-sm font-semibold text-accent-700 dark:text-accent-200">测试 AI 搜索</summary>
                <div class="mt-3 grid gap-3 md:grid-cols-[120px_minmax(0,1fr)_auto]">
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试市场</span>
                    <input v-model="providerTestMarket" class="input" placeholder="amazon-us / US" />
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试关键词</span>
                    <input v-model="providerTestKeyword" class="input" placeholder="pet storage" />
                  </label>
                  <div class="flex items-end">
                    <button class="btn btn-outline min-h-11 whitespace-nowrap" :disabled="testingProvider" @click="testSelectedProvider">
                      {{ testingProvider ? '测试中' : '测试 AI 搜索' }}
                    </button>
                  </div>
                </div>

                <div
                  v-if="providerTestResult"
                  class="mt-3 rounded-lg border p-3 text-sm"
                  :class="providerTestResult.ok ? 'border-success-200 bg-success-50 text-success-800 dark:border-success-500/30 dark:bg-success-500/10 dark:text-success-100' : 'border-danger-200 bg-danger-50 text-danger-800 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-100'"
                >
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="font-semibold">{{ providerTestResult.ok ? '连通成功' : '连通失败' }} · {{ providerTestResult.status }}</span>
                    <span>{{ providerTestResult.durationMs }} ms · {{ providerTestResult.itemsFound }} 条</span>
                  </div>
                  <div v-if="providerTestResult.error" class="mt-2 break-words">{{ providerTestResult.error }}</div>
                  <pre v-if="Object.keys(providerTestResult.sample).length" class="mt-3 max-h-44 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(providerTestResult.sample, null, 2) }}</pre>
                </div>
              </details>
            </div>

            <div v-if="selectedProvider.sourceType === 'api' || selectedProvider.providerStrategy === 'configured_api'" class="space-y-3 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="text-sm font-semibold text-accent-950 dark:text-white">API 配置</h4>
                <div class="flex flex-wrap items-center gap-2">
                  <span class="badge-info">{{ strategyLabel(selectedProvider.providerStrategy || 'configured_api') }}</span>
                </div>
              </div>

              <div class="grid gap-3 md:grid-cols-[140px_minmax(0,1fr)]">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">方法</span>
                  <select class="input" :value="apiRequestField('method', 'GET')" @change="updateApiRequestField('method', eventText($event))">
                    <option value="GET">GET</option>
                    <option value="POST">POST</option>
                  </select>
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API 地址</span>
                  <input class="input font-mono text-xs" :value="apiRequestField('url')" placeholder="https://api.example.com/hot-list" @input="updateApiRequestField('url', eventText($event))" />
                </label>
              </div>

              <div class="grid gap-3 md:grid-cols-3">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">鉴权</span>
                  <select class="input" :value="apiRequestField('auth_type', 'api_key_header')" @change="updateApiRequestField('auth_type', eventText($event))">
                    <option value="none">无</option>
                    <option value="api_key_header">Header Key</option>
                    <option value="bearer">Bearer Token</option>
                    <option value="custom_headers">自定义 Headers</option>
                  </select>
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Key Header</span>
                  <input class="input" :value="apiRequestField('api_key_header', 'x-api-key')" placeholder="x-api-key" @input="updateApiRequestField('api_key_header', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API Key</span>
                  <input class="input" :value="apiRequestField('api_key')" autocomplete="off" spellcheck="false" placeholder="API Key" @input="updateApiRequestField('api_key', eventText($event))" />
                </label>
                <label class="block md:col-span-3">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Bearer Token</span>
                  <input class="input" :value="apiRequestField('bearer_token')" autocomplete="off" spellcheck="false" placeholder="Bearer Token" @input="updateApiRequestField('bearer_token', eventText($event))" />
                </label>
              </div>

              <div class="grid gap-3 md:grid-cols-3">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Headers JSON</span>
                  <textarea class="input min-h-24 font-mono text-xs" :value="apiJsonField('headers')" spellcheck="false" @change="updateApiJsonField('headers', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Query JSON</span>
                  <textarea class="input min-h-24 font-mono text-xs" :value="apiJsonField('query')" spellcheck="false" @change="updateApiJsonField('query', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Body JSON</span>
                  <textarea class="input min-h-24 font-mono text-xs" :value="apiJsonField('body')" spellcheck="false" @change="updateApiJsonField('body', eventText($event))" />
                </label>
              </div>

              <div class="grid gap-3 md:grid-cols-3">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">结果列表路径</span>
                  <input class="input" :value="apiResponseField('items_path', 'data.items')" placeholder="data.items" @input="updateApiResponseField('items_path', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">标题字段</span>
                  <input class="input" :value="apiResponseField('title_path', 'title')" placeholder="title" @input="updateApiResponseField('title_path', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">关键词字段</span>
                  <input class="input" :value="apiResponseField('keyword_path', 'keyword')" placeholder="keyword" @input="updateApiResponseField('keyword_path', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">价格字段</span>
                  <input class="input" :value="apiResponseField('price_path', 'price.amount')" placeholder="price.amount" @input="updateApiResponseField('price_path', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">链接字段</span>
                  <input class="input" :value="apiResponseField('url_path', 'url')" placeholder="url" @input="updateApiResponseField('url_path', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">图片字段</span>
                  <input class="input" :value="apiResponseField('image_path', 'image_url')" placeholder="image_url" @input="updateApiResponseField('image_path', eventText($event))" />
                </label>
              </div>

              <div class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
                <div class="grid gap-3 md:grid-cols-[120px_minmax(0,1fr)_auto]">
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试市场</span>
                    <input v-model="providerTestMarket" class="input" placeholder="amazon-us / US" />
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试关键词</span>
                    <input v-model="providerTestKeyword" class="input" placeholder="mahjong gift" />
                  </label>
                  <div class="flex items-end">
                    <button class="btn btn-outline min-h-11 whitespace-nowrap" :disabled="testingProvider" @click="testSelectedProvider">
                      {{ testingProvider ? '测试中' : '测试 API' }}
                    </button>
                  </div>
                </div>

                <div
                  v-if="providerTestResult"
                  class="mt-3 rounded-lg border p-3 text-sm"
                  :class="providerTestResult.ok ? 'border-success-200 bg-success-50 text-success-800 dark:border-success-500/30 dark:bg-success-500/10 dark:text-success-100' : 'border-danger-200 bg-danger-50 text-danger-800 dark:border-danger-500/30 dark:bg-danger-500/10 dark:text-danger-100'"
                >
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="font-semibold">{{ providerTestResult.ok ? '连通成功' : '连通失败' }} · {{ providerTestResult.status }}</span>
                    <span>{{ providerTestResult.durationMs }} ms · {{ providerTestResult.itemsFound }} 条</span>
                  </div>
                  <div v-if="providerTestResult.error" class="mt-2 break-words">{{ providerTestResult.error }}</div>
                  <pre v-if="Object.keys(providerTestResult.sample).length" class="mt-3 max-h-44 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(providerTestResult.sample, null, 2) }}</pre>
                </div>
              </div>
            </div>

            <details class="rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
              <summary class="cursor-pointer text-sm font-semibold text-accent-700 dark:text-accent-200">高级配置 (config_json)</summary>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">JSON 内容</span>
                <textarea v-model="selectedConfigText" class="input min-h-32 font-mono text-xs" spellcheck="false" />
              </label>
            </details>

            <button class="btn btn-outline py-1.5 text-sm text-danger-700" @click="removeProvider(selectedProviderIndex)">删除当前搜索手段</button>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">目标市场</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ settings.targetMarkets.length }} 个市场</p>
          </div>
          <button class="btn btn-outline py-1.5 text-sm" @click="addMarket">添加目标市场</button>
        </div>

        <div class="mt-4 grid gap-4 lg:grid-cols-[160px_minmax(0,1fr)]">
          <div class="space-y-2">
            <button
              v-for="(market, index) in settings.targetMarkets"
              :key="market.id || index"
              type="button"
              class="w-full rounded-lg border px-3 py-2 text-left text-sm transition"
              :class="selectedMarketIndex === index ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border-accent-200 bg-white text-accent-700 hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:text-accent-200'"
              @click="selectedMarketIndex = index"
            >
              <span class="block font-semibold">{{ market.displayName || market.id || '未命名' }}</span>
              <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ market.platform || '-' }} · {{ market.site || '-' }}</span>
            </button>
          </div>

          <div v-if="selectedMarket" class="space-y-3">
            <div class="grid gap-3 md:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">目标市场 ID</span>
                <input v-model="selectedMarket.id" class="input" placeholder="amazon-us" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">显示名称</span>
                <input v-model="selectedMarket.displayName" class="input" placeholder="Amazon US" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">平台</span>
                <input v-model="selectedMarket.platform" class="input" placeholder="amazon" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">站点</span>
                <input v-model="selectedMarket.site" class="input" placeholder="amazon.com" />
              </label>
            </div>

            <div class="rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div class="text-xs font-semibold text-accent-600 dark:text-accent-300">搜索手段</div>
                  <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ selectedMarket.searchMethods.length }} 个已关联</p>
                </div>
              </div>

              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">选择搜索手段</span>
                <select class="input" :value="selectedMarketMethodId" @change="selectMarketSearchMethod(eventText($event))">
                  <option value="">请选择搜索手段</option>
                  <option v-for="provider in settings.searchProviders" :key="provider.id" :value="provider.id">
                    {{ providerLabel(provider) }}{{ isMethodBound(provider.id) ? '（已关联）' : '' }}
                  </option>
                </select>
              </label>

              <div v-if="selectedMarketBinding" class="mt-4">
                <div class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
                  <div class="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div class="text-sm font-semibold text-accent-950 dark:text-white">{{ searchMethodLabel(selectedMarketMethod) }}</div>
                      <div class="mt-1 text-xs text-accent-500 dark:text-accent-400">{{ selectedMarketBinding.methodId }}</div>
                    </div>
                    <div class="flex flex-wrap items-center gap-3">
                      <label class="flex items-center gap-2 text-sm text-accent-700 dark:text-accent-200">
                        <input v-model="selectedMarketBinding.enabled" type="checkbox" />
                        <span>启用</span>
                      </label>
                      <button class="btn btn-outline py-1.5 text-xs text-danger-700" type="button" @click="removeSelectedMarketSearchMethod">取消关联</button>
                    </div>
                  </div>

                  <label v-if="isAiSearchMethod(selectedMarketMethod)" class="mt-3 block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">目标市场 Prompt</span>
                    <textarea
                      class="input min-h-36 resize-y font-mono text-xs leading-5"
                      :value="selectedBindingPrompt()"
                      spellcheck="false"
                      @input="updateBindingConfigField(selectedMarketBinding, 'prompt', eventText($event))"
                    />
                  </label>
                  <button
                    v-if="isAiSearchMethod(selectedMarketMethod)"
                    class="btn btn-outline mt-2 py-1.5 text-xs"
                    type="button"
                    @click="resetSelectedBindingPrompt"
                  >
                    用默认模板重生成
                  </button>
                </div>
              </div>
              <div v-else class="mt-3 rounded-lg border border-dashed border-accent-300 p-3 text-xs text-accent-500 dark:border-dark-700 dark:text-accent-300">
                选择一个搜索手段后，本市场的专属配置会出现在这里。
              </div>
            </div>

            <button class="btn btn-outline py-1.5 text-sm text-danger-700" @click="removeMarket(selectedMarketIndex)">删除当前市场</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
