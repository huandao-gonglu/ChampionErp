<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchProductResearchSettings, saveProductResearchSettings, testProductResearchSearchProvider } from '@/api/workflow'
import type { ProductResearchConfig, ProductResearchProviderTestResult, ProductResearchSourceRegistryItem, ProductResearchTargetMarket, UnknownRecord } from '@/types/workflow'

const props = defineProps<{
  textAiConfig?: UnknownRecord
}>()

const sourceTypeOptions = [
  { value: 'api', label: 'API 搜索' },
  { value: 'ai_search', label: 'AI 搜索' },
  { value: 'crawler', label: '网页采集' },
  { value: 'manual_import', label: '人工导入' },
]

const strategyOptions = [
  { value: 'seeded_mock', label: '本地种子数据' },
  { value: 'configured_api', label: '已配置 API' },
  { value: 'text_ai_web_search', label: 'AI 联网搜索' },
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
const selectedConfigText = ref('{}')
const testingProvider = ref(false)
const providerTestResult = ref<ProductResearchProviderTestResult | null>(null)
const providerTestMarket = ref('US')
const providerTestKeyword = ref('mahjong gift')
const aiCompletingProvider = ref(false)
const aiCompletionMessage = ref('')

const selectedProvider = computed(() => settings.value.searchProviders[selectedProviderIndex.value] || null)
const selectedMarket = computed(() => settings.value.targetMarkets[selectedMarketIndex.value] || null)
const textAiConfig = computed(() => asRecord(props.textAiConfig))
const textAiReady = computed(() => Boolean(
  String(textAiConfig.value.base_url || '').trim()
  && String(textAiConfig.value.api_key || '').trim()
  && String(textAiConfig.value.model || '').trim(),
))

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

function eventChecked(event: Event) {
  return (event.target as HTMLInputElement).checked
}

function providerLabel(provider: ProductResearchSourceRegistryItem) {
  return provider.name || provider.id || provider.platform || '未命名搜索手段'
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
    provider_strategy: 'text_ai_web_search',
    ai_config_ref: 'text_ai',
    max_items: 12,
    require_source_url: true,
  }
}

function blankProvider(): ProductResearchSourceRegistryItem {
  const next = settings.value.searchProviders.length + 1
  return {
    id: `api_source_${next}`,
    name: `api_搜索手段_${next}`,
    sourceType: 'api',
    platform: `source_${next}`,
    enabled: true,
    priority: next,
    supportedMarkets: ['US', 'GB'],
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

function blankMarket(): ProductResearchTargetMarket {
  return {
    market: 'US',
    name: 'United States',
    enabled: true,
    language: 'en',
    currency: 'USD',
    referenceMarkets: ['GB', 'CA', 'AU'],
    providerIds: settings.value.searchProviders[0]?.id ? [settings.value.searchProviders[0].id] : [],
    raw: {},
  }
}

function clampSelections() {
  selectedProviderIndex.value = Math.min(Math.max(selectedProviderIndex.value, 0), Math.max(settings.value.searchProviders.length - 1, 0))
  selectedMarketIndex.value = Math.min(Math.max(selectedMarketIndex.value, 0), Math.max(settings.value.targetMarkets.length - 1, 0))
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

function addProvider() {
  settings.value.searchProviders.push(blankProvider())
  selectedProviderIndex.value = settings.value.searchProviders.length - 1
}

function removeProvider(index: number) {
  const provider = settings.value.searchProviders[index]
  if (!provider) return
  settings.value.searchProviders.splice(index, 1)
  for (const market of settings.value.targetMarkets) {
    market.providerIds = market.providerIds.filter((id) => id !== provider.id)
  }
  clampSelections()
}

function addMarket() {
  settings.value.targetMarkets.push(blankMarket())
  selectedMarketIndex.value = settings.value.targetMarkets.length - 1
}

function removeMarket(index: number) {
  settings.value.targetMarkets.splice(index, 1)
  clampSelections()
}

function updateProviderList(field: 'supportedMarkets' | 'supportedLanguages' | 'supportedDataTypes', value: string) {
  if (!selectedProvider.value) return
  selectedProvider.value[field] = splitList(value, field === 'supportedMarkets')
}

function updateMarketReferences(value: string) {
  if (!selectedMarket.value) return
  selectedMarket.value.referenceMarkets = splitList(value, true)
}

function setProviderSourceType(value: string) {
  if (!selectedProvider.value) return
  selectedProvider.value.sourceType = value
  if (value === 'ai_search') {
    if (!selectedProvider.value.platform || selectedProvider.value.platform.startsWith('source_')) {
      selectedProvider.value.platform = 'text_ai'
    }
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['ai_web_search']
    setSelectedConfigJson({
      ...defaultAiSearchConfig(),
      ...asRecord(selectedProvider.value.configJson),
      provider_strategy: 'text_ai_web_search',
      ai_config_ref: 'text_ai',
    })
  } else if (value === 'api' && !selectedProvider.value.providerStrategy) {
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
    ...(value === 'text_ai_web_search' ? defaultAiSearchConfig() : {}),
    ...current,
    provider_strategy: value,
  }
  if (value === 'text_ai_web_search') {
    selectedProvider.value.sourceType = 'ai_search'
    if (!selectedProvider.value.platform || selectedProvider.value.platform.startsWith('source_')) {
      selectedProvider.value.platform = 'text_ai'
    }
    selectedProvider.value.authRequired = false
    selectedProvider.value.supportedDataTypes = ['ai_web_search']
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
    provider_strategy: 'text_ai_web_search',
    ai_config_ref: 'text_ai',
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

function updateApiJsonField(section: 'headers' | 'query' | 'body', value: string) {
  if (!selectedProvider.value) return
  try {
    const parsed = JSON.parse(value || '{}')
    if ((section === 'headers' || section === 'query') && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) {
      error.value = `${section} 必须是 JSON 对象`
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
    error.value = `${section} 不是合法 JSON`
  }
}

function stripJsonFence(value: string) {
  return value
    .trim()
    .replace(/^```(?:json)?/i, '')
    .replace(/```$/i, '')
    .trim()
}

function mergeStringList(current: string[], suggested: unknown, uppercase = false) {
  const next = splitList(Array.isArray(suggested) ? suggested.join(',') : String(suggested || ''), uppercase)
  return next.length ? next : current
}

function openAiChatUrl(baseUrl: string) {
  const trimmed = baseUrl.trim().replace(/\/+$/, '')
  return trimmed.endsWith('/chat/completions') ? trimmed : `${trimmed}/chat/completions`
}

function aiAutocompletePrompt(provider: ProductResearchSourceRegistryItem) {
  return [
    '你是 ERP 选品调研 API 配置助手。只返回 JSON，不要解释。',
    '目标：根据用户已填写的搜索手段信息，补全不易理解的技术字段。',
    '不要编造 API Key、Token、真实私密参数。不要覆盖用户已填写的 URL、Key、Token。',
    'JSON schema:',
    '{',
    '  "supported_markets": ["US"],',
    '  "supported_languages": ["en"],',
    '  "supported_data_types": ["marketplace_products"],',
    '  "request": {',
    '    "method": "GET",',
    '    "auth_type": "api_key_header",',
    '    "api_key_header": "x-api-key",',
    '    "headers": {},',
    '    "query": {"market": "{market}", "q": "{keyword}"},',
    '    "body": {}',
    '  },',
    '  "response": {',
    '    "items_path": "data.items",',
    '    "title_path": "title",',
    '    "keyword_path": "keyword",',
    '    "price_path": "price.amount",',
    '    "currency_path": "price.currency",',
    '    "url_path": "url",',
    '    "image_path": "image_url"',
    '  },',
    '  "test": {"market": "US", "keyword": "mahjong gift"}',
    '}',
    '',
    `用户已填信息：${JSON.stringify({
      id: provider.id,
      name: provider.name,
      source_type: provider.sourceType,
      platform: provider.platform,
      supported_markets: provider.supportedMarkets,
      supported_languages: provider.supportedLanguages,
      supported_data_types: provider.supportedDataTypes,
      config_json: provider.configJson,
      note: provider.complianceNote,
    }, null, 2)}`,
  ].join('\n')
}

async function completeSelectedProviderByAi() {
  if (!selectedProvider.value) return
  error.value = ''
  aiCompletionMessage.value = ''
  if (!textAiReady.value) {
    error.value = '请先在上方文本 AI 填写 Base URL、模型和 API Key。'
    return
  }
  if (!applySelectedConfigJson()) return
  aiCompletingProvider.value = true
  try {
    const response = await fetch(openAiChatUrl(String(textAiConfig.value.base_url || '')), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${String(textAiConfig.value.api_key || '').trim()}`,
      },
      body: JSON.stringify({
        model: String(textAiConfig.value.model || '').trim(),
        messages: [
          { role: 'system', content: 'You return strict JSON only.' },
          { role: 'user', content: aiAutocompletePrompt(selectedProvider.value) },
        ],
        temperature: 0.1,
      }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(String(asRecord(payload).error || response.statusText || 'AI 补全失败'))
    }
    const rawChoices = asRecord(payload).choices
    const choices = Array.isArray(rawChoices) ? rawChoices : []
    const firstChoice = asRecord(choices[0])
    const message = asRecord(firstChoice.message)
    const content = String(message.content || '').trim()
    const suggestion = asRecord(JSON.parse(stripJsonFence(content)))
    const currentConfig = asRecord(selectedProvider.value.configJson)
    const currentRequest = asRecord(currentConfig.request)
    const suggestedRequest = asRecord(suggestion.request)
    const currentResponse = asRecord(currentConfig.response)
    const suggestedResponse = asRecord(suggestion.response)
    selectedProvider.value.supportedMarkets = mergeStringList(selectedProvider.value.supportedMarkets, suggestion.supported_markets, true)
    selectedProvider.value.supportedLanguages = mergeStringList(selectedProvider.value.supportedLanguages, suggestion.supported_languages)
    selectedProvider.value.supportedDataTypes = mergeStringList(selectedProvider.value.supportedDataTypes, suggestion.supported_data_types)
    setSelectedConfigJson({
      ...currentConfig,
      provider_strategy: 'configured_api',
      request: {
        ...currentRequest,
        method: String(suggestedRequest.method || currentRequest.method || 'GET'),
        auth_type: String(suggestedRequest.auth_type || currentRequest.auth_type || 'api_key_header'),
        api_key_header: String(suggestedRequest.api_key_header || currentRequest.api_key_header || 'x-api-key'),
        headers: Object.keys(asRecord(suggestedRequest.headers)).length ? asRecord(suggestedRequest.headers) : asRecord(currentRequest.headers),
        query: Object.keys(asRecord(suggestedRequest.query)).length ? asRecord(suggestedRequest.query) : asRecord(currentRequest.query),
        body: Object.keys(asRecord(suggestedRequest.body)).length ? asRecord(suggestedRequest.body) : asRecord(currentRequest.body),
      },
      response: {
        ...currentResponse,
        ...suggestedResponse,
      },
    })
    const test = asRecord(suggestion.test)
    providerTestMarket.value = String(test.market || providerTestMarket.value || selectedProvider.value.supportedMarkets[0] || 'US').toUpperCase()
    providerTestKeyword.value = String(test.keyword || providerTestKeyword.value || 'mahjong gift')
    aiCompletionMessage.value = 'AI 已补全技术字段，请检查后保存或测试 API。'
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : 'AI 补全失败'
  } finally {
    aiCompletingProvider.value = false
  }
}

function applySelectedConfigJson() {
  if (!selectedProvider.value) return true
  try {
    const parsed = JSON.parse(selectedConfigText.value || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      error.value = 'config_json 必须是 JSON 对象'
      return false
    }
    setSelectedConfigJson(parsed as UnknownRecord)
    return true
  } catch {
    error.value = 'config_json 不是合法 JSON'
    return false
  }
}

function toggleMarketProvider(providerId: string) {
  if (!selectedMarket.value) return
  const ids = new Set(selectedMarket.value.providerIds)
  if (ids.has(providerId)) ids.delete(providerId)
  else ids.add(providerId)
  selectedMarket.value.providerIds = Array.from(ids)
}

async function saveSettings() {
  error.value = ''
  savedMessage.value = ''
  if (!applySelectedConfigJson()) return
  if (!settings.value.searchProviders.every((provider) => provider.id.trim())) {
    error.value = '每个搜索手段都需要 ID'
    return
  }
  if (!settings.value.targetMarkets.every((market) => market.market.trim())) {
    error.value = '每个目标市场都需要市场代码'
    return
  }
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
      market: providerTestMarket.value.trim().toUpperCase() || selectedProvider.value.supportedMarkets[0] || 'US',
      language: selectedProvider.value.supportedLanguages[0] || 'en',
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
      error: exc instanceof Error ? exc.message : selectedProvider.value.providerStrategy === 'text_ai_web_search' ? '测试 AI 搜索失败' : '测试 API 失败',
      sample: {},
      raw: {},
    }
  } finally {
    testingProvider.value = false
  }
}

watch(selectedProvider, (provider) => {
  selectedConfigText.value = JSON.stringify(provider?.configJson || {}, null, 2)
  providerTestMarket.value = provider?.supportedMarkets[0] || 'US'
  providerTestKeyword.value = 'mahjong gift'
  providerTestResult.value = null
}, { immediate: true })

onMounted(loadSettings)
</script>

<template>
  <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="card-title">选品调研来源</h2>
        <p class="muted mt-1">先维护搜索手段，再把它绑定到目标市场。</p>
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
          <button class="btn btn-outline py-1.5 text-sm" @click="addProvider">添加搜索手段</button>
        </div>

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
              <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ provider.id }} · {{ provider.sourceType }}</span>
            </button>
          </div>

          <div v-if="selectedProvider" class="space-y-3">
            <div class="grid gap-3 md:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">ID</span>
                <input v-model="selectedProvider.id" class="input" placeholder="api_amazon" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">名称</span>
                <input v-model="selectedProvider.name" class="input" placeholder="api_亚马逊" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">类型</span>
                <select :value="selectedProvider.sourceType" class="input" @change="setProviderSourceType(eventText($event))">
                  <option v-for="option in sourceTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">平台</span>
                <input v-model="selectedProvider.platform" class="input" placeholder="amazon" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">优先级</span>
                <input v-model.number="selectedProvider.priority" class="input" min="1" type="number" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">策略</span>
                <select :value="selectedProvider.providerStrategy" class="input" @change="setProviderStrategy(eventText($event))">
                  <option v-for="option in strategyOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
            </div>

            <div class="grid gap-3 md:grid-cols-3">
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">支持市场</span>
                <input class="input" :value="joinList(selectedProvider.supportedMarkets)" placeholder="US, UK" @input="updateProviderList('supportedMarkets', eventText($event))" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">语言</span>
                <input class="input" :value="joinList(selectedProvider.supportedLanguages)" placeholder="en" @input="updateProviderList('supportedLanguages', eventText($event))" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">数据类型</span>
                <input class="input" :value="joinList(selectedProvider.supportedDataTypes)" placeholder="marketplace_products" @input="updateProviderList('supportedDataTypes', eventText($event))" />
              </label>
            </div>

            <div class="grid gap-3 md:grid-cols-3">
              <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                <input v-model="selectedProvider.enabled" type="checkbox" />
                <span>启用</span>
              </label>
              <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                <input v-model="selectedProvider.authRequired" type="checkbox" />
                <span>需要授权</span>
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">限速/分钟</span>
                <input v-model.number="selectedProvider.rateLimitPerMinute" class="input" min="0" type="number" />
              </label>
            </div>

            <label class="block">
              <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">说明</span>
              <input v-model="selectedProvider.complianceNote" class="input" placeholder="api从亚马逊获取热榜" />
            </label>

            <div v-if="selectedProvider.sourceType === 'ai_search' || selectedProvider.providerStrategy === 'text_ai_web_search'" class="space-y-3 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="text-sm font-semibold text-accent-950 dark:text-white">AI 搜索配置</h4>
                <div class="flex flex-wrap items-center gap-2">
                  <span :class="textAiReady ? 'badge-success' : 'badge-muted'">文本 AI</span>
                  <span class="badge-info">{{ selectedProvider.providerStrategy || 'text_ai_web_search' }}</span>
                </div>
              </div>

              <div class="grid gap-3 md:grid-cols-3">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">使用 AI 配置</span>
                  <input class="input" value="text_ai" disabled />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">最大结果</span>
                  <input class="input" min="1" max="50" type="number" :value="Number(aiSearchConfigField('max_items', '12')) || 12" @input="updateAiSearchConfigField('max_items', Number(eventText($event)) || 12)" />
                </label>
                <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-accent-50 px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-950/70">
                  <input :checked="aiSearchConfigField('require_source_url', 'true') !== 'false'" type="checkbox" @change="updateAiSearchConfigField('require_source_url', eventChecked($event))" />
                  <span>要求来源 URL</span>
                </label>
              </div>

              <div class="rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/70">
                <div class="grid gap-3 md:grid-cols-[120px_minmax(0,1fr)_auto]">
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试市场</span>
                    <input v-model="providerTestMarket" class="input uppercase" placeholder="US" />
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
              </div>
            </div>

            <div v-if="selectedProvider.sourceType === 'api' || selectedProvider.providerStrategy === 'configured_api'" class="space-y-3 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="text-sm font-semibold text-accent-950 dark:text-white">API 配置</h4>
                <div class="flex flex-wrap items-center gap-2">
                  <span class="badge-info">{{ selectedProvider.providerStrategy || 'configured_api' }}</span>
                  <button class="btn btn-outline py-1.5 text-xs" :disabled="aiCompletingProvider" @click="completeSelectedProviderByAi">
                    {{ aiCompletingProvider ? '补全中' : 'AI 补全' }}
                  </button>
                </div>
              </div>
              <p v-if="aiCompletionMessage" class="rounded-lg border border-success-200 bg-success-50 px-3 py-2 text-sm text-success-700 dark:border-success-500/30 dark:bg-success-500/10 dark:text-success-100">{{ aiCompletionMessage }}</p>

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
                    <input v-model="providerTestMarket" class="input uppercase" placeholder="US" />
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

            <label class="block">
              <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">config_json</span>
              <textarea v-model="selectedConfigText" class="input min-h-32 font-mono text-xs" spellcheck="false" />
            </label>

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
              :key="market.market || index"
              type="button"
              class="w-full rounded-lg border px-3 py-2 text-left text-sm transition"
              :class="selectedMarketIndex === index ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border-accent-200 bg-white text-accent-700 hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:text-accent-200'"
              @click="selectedMarketIndex = index"
            >
              <span class="block font-semibold">{{ market.market || '未命名' }}</span>
              <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ market.providerIds.length }} 个搜索手段</span>
            </button>
          </div>

          <div v-if="selectedMarket" class="space-y-3">
            <div class="grid gap-3 md:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">市场代码</span>
                <input v-model="selectedMarket.market" class="input uppercase" placeholder="US / UK" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">名称</span>
                <input v-model="selectedMarket.name" class="input" placeholder="United States" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">语言</span>
                <input v-model="selectedMarket.language" class="input" placeholder="en" />
              </label>
              <label class="block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">币种</span>
                <input v-model="selectedMarket.currency" class="input" placeholder="USD" />
              </label>
            </div>

            <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
              <input v-model="selectedMarket.enabled" type="checkbox" />
              <span>启用这个市场</span>
            </label>

            <label class="block">
              <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">相近市场</span>
              <input class="input" :value="joinList(selectedMarket.referenceMarkets)" placeholder="GB, CA, AU" @input="updateMarketReferences(eventText($event))" />
            </label>

            <div>
              <div class="mb-2 text-xs font-semibold text-accent-600 dark:text-accent-300">绑定搜索手段</div>
              <div class="grid gap-2">
                <button
                  v-for="provider in settings.searchProviders"
                  :key="provider.id"
                  type="button"
                  class="btn min-h-10 justify-start px-3 text-xs"
                  :class="selectedMarket.providerIds.includes(provider.id) ? 'border border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-100' : 'btn-outline'"
                  @click="toggleMarketProvider(provider.id)"
                >
                  {{ providerLabel(provider) }}
                </button>
              </div>
            </div>

            <button class="btn btn-outline py-1.5 text-sm text-danger-700" @click="removeMarket(selectedMarketIndex)">删除当前市场</button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
