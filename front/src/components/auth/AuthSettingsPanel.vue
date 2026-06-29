<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import ProductResearchSettingsPanel from '@/components/auth/ProductResearchSettingsPanel.vue'
import type { AuthResult, Marketplace, MercadoLibreAuthChecklist, MercadoLibreTestMode, UnknownRecord } from '@/types/workflow'

const DEFAULT_ML_REDIRECT_URI = 'https://example.com/callback'

const props = defineProps<{
  appConfig: UnknownRecord
  aiConfig: UnknownRecord
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
  mercadolibreChecklist: MercadoLibreAuthChecklist | null
  lastResult: AuthResult | null
  authLink: string
  loading: boolean
}>()

const emit = defineEmits<{
  saveAi: [config: UnknownRecord]
  testAi: [model: UnknownRecord]
  testApi: [kind: 'exchange_rate' | '1688', config: UnknownRecord, testValue?: string]
  saveStore: [config: UnknownRecord]
  testAuth: [platform: Marketplace, scope?: string]
  refreshChecklist: []
  generateMlLink: [appId: string, redirectUri: string]
  openMlLink: [url: string, browser?: string]
  refreshMlToken: [params: UnknownRecord]
  realMlTest: [mode: MercadoLibreTestMode, categoryId?: string]
  exchangeMlCode: [codeOrUrl: string, params: UnknownRecord]
  clearAuth: [platform: Marketplace]
}>()

const form = reactive({
  exchangeRateApiUrl: '',
  exchangeRateTimeoutSeconds: '10',
  exchangeRateCacheTtlSeconds: '3600',
  alibabaAppKey: '',
  alibabaAppSecret: '',
  alibabaAccessToken: '',
  alibabaApiBaseUrl: 'https://gw.open.1688.com/openapi/param2/1/com.alibaba.product/alibaba.product.get',
  alibabaApiMethod: 'alibaba.product.get',
  alibabaApiVersion: '1.0',
  alibabaApiTimeoutSeconds: '20',
  alibabaTestOfferId: '',
  mlAppId: '',
  mlClientSecret: '',
  mlRedirectUri: DEFAULT_ML_REDIRECT_URI,
  mlNotificationUrl: '',
  mlCode: '',
  mlCategoryId: '',
  wbContentToken: '',
  wbPricesToken: '',
  ozonClientId: '',
  ozonApiKey: '',
})

const selectedStorePlatform = ref<Marketplace>('mercadolibre')
type AuthSettingsTab = 'ai_models' | 'ai_bindings' | 'stores' | 'apis' | 'research'
const activeAuthSettingsTab = ref<AuthSettingsTab>('ai_models')
const selectedAiModelIndex = ref(0)
const aiModels = ref<UnknownRecord[]>([])
const aiUseCaseBindings = ref<Record<string, string>>({})
const modelListDependencyFields = new Set(['provider', 'api_style', 'base_url', 'base_url_env', 'api_key', 'api_key_env'])
const capabilityProbeDependencyFields = new Set([...modelListDependencyFields, 'model'])
const lastAutoModelListSignature = ref('')
const lastAutoCapabilitySignature = ref('')
const aiRequestPending = ref(false)
const aiRequestMessage = ref('')

const storePlatforms: Array<{ key: Marketplace; label: string; subtitle: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre', subtitle: 'OAuth、授权链接、店铺授权测试' },
  { key: 'wildberries', label: 'Wildberries', subtitle: 'Content / Prices API Token' },
  { key: 'ozon', label: 'Ozon', subtitle: 'Client ID + API Key' },
]

const authSettingsTabs: Array<{ key: AuthSettingsTab; label: string; summary: string }> = [
  { key: 'ai_models', label: 'AI 模型', summary: '配置模型、能力和连接测试' },
  { key: 'ai_bindings', label: '功能绑定', summary: '为功能指定模型或自动匹配' },
  { key: 'stores', label: '店铺授权', summary: 'Mercado Libre、Wildberries、Ozon' },
  { key: 'apis', label: '采集与核价', summary: '汇率 API 和 1688 采集 API' },
  { key: 'research', label: '调研来源', summary: '选品调研搜索手段和市场' },
]

const capabilityOptions = [
  { value: 'chat', label: '文本' },
  { value: 'json', label: 'JSON 模式' },
  { value: 'web_search', label: '联网搜索' },
  { value: 'image_generate', label: '图像生成' },
  { value: 'image_edit', label: '图像编辑' },
  { value: 'tool_calling', label: 'Function Call' },
]
const allCapabilityValues = capabilityOptions.map((item) => item.value)
const imageCapabilityValues = new Set(['image_generate', 'image_edit'])
const fallbackModelQualityLevels = ['fast', 'balanced', 'high_quality']
const fallbackImageQualityValues = ['auto', 'low', 'medium', 'high']
const modelQualityLabels: Record<string, string> = {
  fast: '速度优先',
  balanced: '均衡',
  high_quality: '质量优先',
}
const imageQualityLabels: Record<string, string> = {
  auto: '自动',
  low: '低',
  medium: '中',
  high: '高',
}

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {}
}

function textValue(value: unknown): string {
  return String(value ?? '').trim()
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = textValue(value)
    if (text) return text
  }
  return ''
}

function maskSecret(value: unknown): string {
  const text = firstText(value)
  if (!text) return ''
  if (text.length <= 8) return '*'.repeat(text.length)
  return `${text.slice(0, 4)}...${text.slice(-4)}`
}

function displayAiModelRecord(value: unknown): UnknownRecord {
  const record = { ...asRecord(value) }
  const apiKey = firstText(record.api_key)
  if (apiKey) {
    record.api_key = ''
    record.api_key_configured = true
    record.api_key_masked = firstText(record.api_key_masked, maskSecret(apiKey))
  }
  return record
}

function eventText(event: Event): string {
  return (event.target as HTMLInputElement | HTMLSelectElement).value
}

function eventChecked(event: Event): boolean {
  return (event.target as HTMLInputElement).checked
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : []
}

function normalizeModelOptions(value: unknown): Array<{ id: string; label: string }> {
  const rawItems = Array.isArray(value) ? value : []
  const options: Array<{ id: string; label: string }> = []
  const seen = new Set<string>()
  for (const item of rawItems) {
    const record = asRecord(item)
    const id = firstText(record.id, item)
    if (!id || seen.has(id)) continue
    seen.add(id)
    options.push({ id, label: firstText(record.label, record.name, id) })
  }
  return options
}

function normalizeAiModelRow(value: unknown, index: number): UnknownRecord {
  const record = asRecord(value)
  const fallbackId = index === 0 ? 'default_text' : index === 1 ? 'default_image' : `ai_model_${index + 1}`
  const model = firstText(record.model)
  const modelOptions = normalizeModelOptions(record.model_options || record.available_models)
  if (model && !modelOptions.some((item) => item.id === model)) {
    modelOptions.unshift({ id: model, label: model })
  }
  return {
    id: firstText(record.id, fallbackId),
    name: firstText(record.name, record.id, `AI 模型 ${index + 1}`),
    provider: firstText(record.provider, 'OpenAI-Compatible'),
    api_style: firstText(record.api_style, 'openai_compatible'),
    base_url: firstText(record.base_url),
    base_url_env: firstText(record.base_url_env),
    api_key: firstText(record.api_key),
    api_key_env: firstText(record.api_key_env),
    api_key_masked: firstText(record.api_key_masked),
    model,
    model_env: firstText(record.model_env),
    quality_level: firstText(record.quality_level, record.model_quality_level, 'balanced'),
    quality: firstText(record.quality),
    size: firstText(record.size),
    timeout_seconds: firstText(record.timeout_seconds),
    capabilities: asStringArray(record.capabilities),
    detected_capabilities: asStringArray(record.detected_capabilities || record.supported_capabilities),
    unsupported_capabilities: asStringArray(record.unsupported_capabilities),
    capability_results: asRecord(record.capability_results),
    effective_capabilities: asStringArray(record.effective_capabilities),
    enabled: record.enabled !== false,
    api_key_configured: Boolean(record.api_key_configured),
    model_options: modelOptions,
  }
}

function defaultAiModelRow(index: number): UnknownRecord {
  return normalizeAiModelRow({
    id: `ai_model_${Date.now()}_${index + 1}`,
    name: `AI 模型 ${index + 1}`,
    provider: 'OpenAI-Compatible',
    api_style: 'openai_compatible',
    base_url: '',
    api_key: '',
    model: '',
    quality_level: 'balanced',
    capabilities: [],
    enabled: true,
  }, index)
}

function uniqueAiModelId(baseId: string): string {
  const existing = new Set(aiModels.value.map((model) => String(model.id || '').trim()).filter(Boolean))
  const base = (baseId || 'ai_model').replace(/\s+/g, '_').replace(/[^\w.-]/g, '_') || 'ai_model'
  let candidate = `${base}_copy`
  let suffix = 2
  while (existing.has(candidate)) {
    candidate = `${base}_copy_${suffix}`
    suffix += 1
  }
  return candidate
}

function uniqueAiModelName(baseName: string): string {
  const existing = new Set(aiModels.value.map((model) => String(model.name || '').trim()).filter(Boolean))
  const base = baseName || 'AI 模型'
  let candidate = `${base} 副本`
  let suffix = 2
  while (existing.has(candidate)) {
    candidate = `${base} 副本 ${suffix}`
    suffix += 1
  }
  return candidate
}

function normalizeUseCaseBindings(value: unknown): Record<string, string> {
  const record = asRecord(value)
  const result: Record<string, string> = {}
  for (const [key, raw] of Object.entries(record)) {
    const item = asRecord(raw)
    const modelId = firstText(item.model_id, raw)
    if (key && modelId) result[key] = modelId
  }
  return result
}

function fillFromProps() {
  const pricing = asRecord(props.appConfig.pricing_defaults)
  const alibabaApi = asRecord(props.appConfig['1688_api'])
  const ml = asRecord(props.storeConfig.mercadolibre)
  const wb = asRecord(props.storeConfig.wildberries)
  const ozon = asRecord(props.storeConfig.ozon)
  const modelRows = Array.isArray(props.aiConfig.ai_models) ? props.aiConfig.ai_models : Array.isArray(props.appConfig.ai_models) ? props.appConfig.ai_models : []
  aiModels.value = modelRows.length ? modelRows.map((item, index) => normalizeAiModelRow(displayAiModelRecord(item), index)) : [defaultAiModelRow(0)]
  selectedAiModelIndex.value = Math.min(selectedAiModelIndex.value, Math.max(aiModels.value.length - 1, 0))
  aiUseCaseBindings.value = normalizeUseCaseBindings(props.aiConfig.ai_use_case_bindings || props.appConfig.ai_use_case_bindings)
  form.exchangeRateApiUrl = firstText(pricing.exchange_rate_api_url, 'https://open.er-api.com/v6/latest/USD')
  form.exchangeRateTimeoutSeconds = firstText(pricing.exchange_rate_timeout_seconds, '10')
  form.exchangeRateCacheTtlSeconds = firstText(pricing.exchange_rate_cache_ttl_seconds, '3600')
  form.alibabaAppKey = firstText(alibabaApi.app_key, form.alibabaAppKey)
  form.alibabaAppSecret = firstText(alibabaApi.app_secret, form.alibabaAppSecret)
  form.alibabaAccessToken = firstText(alibabaApi.access_token, form.alibabaAccessToken)
  form.alibabaApiBaseUrl = firstText(alibabaApi.base_url, form.alibabaApiBaseUrl)
  form.alibabaApiMethod = firstText(alibabaApi.method, form.alibabaApiMethod)
  form.alibabaApiVersion = firstText(alibabaApi.api_version, form.alibabaApiVersion)
  form.alibabaApiTimeoutSeconds = firstText(alibabaApi.timeout_seconds, form.alibabaApiTimeoutSeconds)
  form.mlAppId = String(ml.app_id || '')
  form.mlClientSecret = String(ml.client_secret || ml.app_secret || '')
  form.mlRedirectUri = String(ml.redirect_uri || DEFAULT_ML_REDIRECT_URI)
  form.mlNotificationUrl = String(ml.notification_url || ml.notifications_url || ml.webhook_url || '')
  form.wbContentToken = String(wb.content_token || '')
  form.wbPricesToken = String(wb.prices_token || '')
  form.ozonClientId = String(ozon.client_id || '')
  form.ozonApiKey = String(ozon.api_key || '')
}

watch(() => [props.appConfig, props.aiConfig, props.storeConfig], fillFromProps, { immediate: true, deep: true })

const selectedAiModel = computed(() => aiModels.value[selectedAiModelIndex.value] || null)
const aiUseCases = computed(() => Array.isArray(props.aiConfig.ai_use_cases) ? props.aiConfig.ai_use_cases.map(asRecord) : [])
const activeAuthSettingsTabMeta = computed(() => authSettingsTabs.find((tab) => tab.key === activeAuthSettingsTab.value) || authSettingsTabs[0])
const capabilityLabelByValue = Object.fromEntries(capabilityOptions.map((item) => [item.value, item.label]))
const selectedModelOptions = computed(() => normalizeModelOptions(selectedAiModel.value?.model_options))
const modelQualityOptions = computed(() => {
  const values = asStringArray(props.aiConfig.model_quality_levels)
  const source = values.length ? values : fallbackModelQualityLevels
  const current = modelField('quality_level')
  const seen = new Set<string>()
  const result: Array<{ value: string; label: string }> = []
  for (const value of [...source, current]) {
    if (!value || seen.has(value)) continue
    seen.add(value)
    result.push({ value, label: modelQualityLabels[value] || value })
  }
  return result
})
const imageQualityOptions = computed(() => {
  const values = asStringArray(props.aiConfig.image_quality_options)
  const source = values.length ? values : fallbackImageQualityValues
  const current = modelField('quality')
  const seen = new Set<string>()
  const result: Array<{ value: string; label: string }> = []
  for (const value of [...source, current]) {
    if (!value || seen.has(value)) continue
    seen.add(value)
    result.push({ value, label: imageQualityLabels[value] || value })
  }
  return result
})
const selectedModelListReady = computed(() => Boolean(
  selectedAiModel.value
  && String(selectedAiModel.value.base_url || selectedAiModel.value.base_url_env || '').trim()
  && (String(selectedAiModel.value.api_key || '').trim() || selectedAiModel.value.api_key_configured),
))
const selectedAiModelReady = computed(() => Boolean(
  selectedAiModel.value
  && String(selectedAiModel.value.provider || '').trim()
  && String(selectedAiModel.value.model || '').trim()
  && String(selectedAiModel.value.base_url || selectedAiModel.value.base_url_env || '').trim()
  && (String(selectedAiModel.value.api_key || '').trim() || selectedAiModel.value.api_key_configured),
))
const aiControlsLocked = computed(() => props.loading || aiRequestPending.value)
const aiBlockingMessage = computed(() => aiRequestMessage.value || '正在检测 AI 配置，请稍候')
const selectedAiHint = computed(() => props.loading ? '正在处理，请稍候' : '请先填写 Base URL 和 API Key，再选择模型')
const selectedAiModelImageCapable = computed(() => modelHasImageCapability(selectedAiModel.value))
const exchangeRateReady = computed(() => Boolean(form.exchangeRateApiUrl.trim()))
const exchangeRateHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写汇率 API URL')
const alibabaApiReady = computed(() => Boolean(form.alibabaAppKey.trim() && form.alibabaAppSecret.trim() && form.alibabaApiBaseUrl.trim()))
const alibabaApiHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写 1688 App Key、App Secret 和 API 请求地址')
const lastConfigResultChannel = computed(() => String(props.lastResult?.raw?.channel || ''))
const showAiConfigResult = computed(() => lastConfigResultChannel.value === 'ai_model')
const showApiConfigResult = computed(() => ['exchange_rate', '1688'].includes(lastConfigResultChannel.value))

function aiPayload(): UnknownRecord {
  return {
    ai_models: aiModels.value.map((model, index) => {
      const row = normalizeAiModelRow(model, index)
      const copySourceId = firstText(model.copy_source_id)
      if (copySourceId && !firstText(row.api_key)) row.copy_source_id = copySourceId
      if (!modelHasImageCapability(row)) {
        delete row.quality
        delete row.size
      }
      delete row.model_options
      delete row.model_env
      return row
    }),
    ai_use_case_bindings: Object.fromEntries(
      Object.entries(aiUseCaseBindings.value)
        .filter(([, modelId]) => String(modelId || '').trim())
        .map(([useCaseId, modelId]) => [useCaseId, { model_id: modelId }]),
    ),
    pricing_defaults: {
      exchange_rate_api_url: form.exchangeRateApiUrl.trim(),
      exchange_rate_timeout_seconds: form.exchangeRateTimeoutSeconds.trim(),
      exchange_rate_cache_ttl_seconds: form.exchangeRateCacheTtlSeconds.trim(),
    },
    '1688_api': {
      app_key: form.alibabaAppKey.trim(),
      app_secret: form.alibabaAppSecret.trim(),
      access_token: form.alibabaAccessToken.trim(),
      base_url: form.alibabaApiBaseUrl.trim(),
      method: form.alibabaApiMethod.trim(),
      api_version: form.alibabaApiVersion.trim(),
      timeout_seconds: form.alibabaApiTimeoutSeconds.trim(),
    },
  }
}

function modelField(field: string, fallback = ''): string {
  return String(selectedAiModel.value?.[field] ?? fallback)
}

function apiKeyPlaceholder(): string {
  const masked = modelField('api_key_masked')
  return masked ? `已配置 ${masked}，留空保持原值` : 'API Key'
}

function setSelectedModelField(field: string, value: string | boolean) {
  if (!selectedAiModel.value) return
  if (aiControlsLocked.value) return
  const previous = selectedAiModel.value[field]
  if (previous === value) return
  const desiredCapabilities = capabilitySelection(selectedAiModel.value)
  selectedAiModel.value[field] = value
  if (typeof value === 'string' && modelListDependencyFields.has(field)) {
    selectedAiModel.value.model = ''
    selectedAiModel.value.model_options = []
  }
  if (typeof value === 'string' && capabilityProbeDependencyFields.has(field)) {
    if (desiredCapabilities.length) selectedAiModel.value.capabilities = desiredCapabilities
    selectedAiModel.value.detected_capabilities = []
    selectedAiModel.value.unsupported_capabilities = []
    lastAutoModelListSignature.value = ''
    lastAutoCapabilitySignature.value = ''
  }
}

function selectedCapabilities(): string[] {
  return asStringArray(selectedAiModel.value?.capabilities)
}

function modelHasImageCapability(model: UnknownRecord | null): boolean {
  return asStringArray(model?.capabilities).some((capability) => imageCapabilityValues.has(capability))
}

function modelCapabilities(model: UnknownRecord): string[] {
  const effective = asStringArray(model.effective_capabilities)
  return effective.length ? effective : asStringArray(model.capabilities)
}

function useCaseRequiredCapabilities(useCase: UnknownRecord): string[] {
  return asStringArray(useCase.required_capabilities)
}

function modelSupportsUseCase(model: UnknownRecord, useCase: UnknownRecord): boolean {
  if (model.enabled === false) return false
  const capabilities = new Set(modelCapabilities(model))
  return useCaseRequiredCapabilities(useCase).every((capability) => capabilities.has(capability))
}

function compatibleModelsForUseCase(useCase: UnknownRecord): UnknownRecord[] {
  const compatible = aiModels.value.filter((model) => modelSupportsUseCase(model, useCase))
  const boundId = String(aiUseCaseBindings.value[String(useCase.id || '')] || '')
  if (!boundId || compatible.some((model) => String(model.id || '') === boundId)) return compatible
  const bound = aiModels.value.find((model) => String(model.id || '') === boundId)
  return bound ? [bound, ...compatible] : compatible
}

function useCaseCapabilityText(useCase: UnknownRecord): string {
  const labels = useCaseRequiredCapabilities(useCase).map((capability) => capabilityLabelByValue[capability] || capability)
  return labels.length ? labels.join(' / ') : '无特殊能力要求'
}

function hasCapability(capability: string): boolean {
  return selectedCapabilities().includes(capability)
}

function unsupportedCapabilities(): string[] {
  return asStringArray(selectedAiModel.value?.unsupported_capabilities)
}

function isCapabilityUnsupported(capability: string): boolean {
  return unsupportedCapabilities().includes(capability)
}

function capabilitySelection(model: UnknownRecord | null): string[] {
  if (!model) return []
  return asStringArray(model.capabilities)
}

function setCapability(capability: string, checked: boolean) {
  if (!selectedAiModel.value) return
  if (aiControlsLocked.value) return
  if (checked && isCapabilityUnsupported(capability)) return
  const current = new Set(selectedCapabilities())
  if (checked) current.add(capability)
  else current.delete(capability)
  selectedAiModel.value.capabilities = Array.from(current)
  if (!modelHasImageCapability(selectedAiModel.value)) {
    selectedAiModel.value.quality = ''
    selectedAiModel.value.size = ''
  }
  lastAutoCapabilitySignature.value = ''
  if (selectedAiModelReady.value) autoTestSelectedAiModel()
}

function addAiModel() {
  if (aiControlsLocked.value) return
  aiModels.value.push(defaultAiModelRow(aiModels.value.length))
  selectedAiModelIndex.value = aiModels.value.length - 1
}

function duplicateSelectedAiModel() {
  if (aiControlsLocked.value) return
  const current = selectedAiModel.value
  if (!current) return
  const sourceId = firstText(current.id)
  const cloned = normalizeAiModelRow({
    ...current,
    id: uniqueAiModelId(sourceId || firstText(current.model) || `ai_model_${aiModels.value.length + 1}`),
    name: uniqueAiModelName(firstText(current.name, current.id, `AI 模型 ${aiModels.value.length + 1}`)),
    capabilities: asStringArray(current.capabilities),
    detected_capabilities: [],
    unsupported_capabilities: [],
    model_options: normalizeModelOptions(current.model_options),
  }, aiModels.value.length)
  if (sourceId && !firstText(cloned.api_key)) cloned.copy_source_id = sourceId
  aiModels.value.splice(selectedAiModelIndex.value + 1, 0, cloned)
  selectedAiModelIndex.value += 1
  lastAutoModelListSignature.value = ''
  lastAutoCapabilitySignature.value = ''
}

function removeSelectedAiModel() {
  if (aiControlsLocked.value) return
  const current = selectedAiModel.value
  if (!current || aiModels.value.length <= 1) return
  const removedId = String(current.id || '')
  aiModels.value.splice(selectedAiModelIndex.value, 1)
  for (const [useCaseId, modelId] of Object.entries(aiUseCaseBindings.value)) {
    if (modelId === removedId) delete aiUseCaseBindings.value[useCaseId]
  }
  selectedAiModelIndex.value = Math.min(selectedAiModelIndex.value, aiModels.value.length - 1)
}

function setUseCaseBinding(useCaseId: string, modelId: string) {
  if (aiControlsLocked.value) return
  if (!modelId) delete aiUseCaseBindings.value[useCaseId]
  else aiUseCaseBindings.value[useCaseId] = modelId
}

function modelListSignature(model: UnknownRecord | null): string {
  if (!model) return ''
  return [
    String(model.id || ''),
    String(model.base_url || model.base_url_env || '').trim(),
    String(model.api_key || '').trim() || (model.api_key_configured ? 'saved' : ''),
  ].join('|')
}

function capabilitySignature(model: UnknownRecord | null): string {
  if (!model) return ''
  return [
    modelListSignature(model),
    String(model.model || '').trim(),
    allCapabilityValues.join(','),
  ].join('|')
}

function aiModelPayloadForCheck(probeCapabilities: boolean): UnknownRecord {
  const model = selectedAiModel.value ? { ...selectedAiModel.value } : {}
  if (probeCapabilities) {
    model.capabilities = allCapabilityValues
  }
  delete model.model_options
  delete model.detected_capabilities
  delete model.unsupported_capabilities
  return model
}

function autoRefreshModelList() {
  if (!selectedAiModel.value || aiControlsLocked.value || !selectedModelListReady.value) return
  const signature = modelListSignature(selectedAiModel.value)
  if (!signature || signature === lastAutoModelListSignature.value) return
  lastAutoModelListSignature.value = signature
  requestAiModelCheck(false, '正在拉取模型列表', 'auto_model_list')
}

function testSelectedAiModel() {
  if (!selectedAiModel.value) return
  requestAiModelCheck(true, '正在检测模型能力', 'manual')
}

function autoTestSelectedAiModel() {
  if (!selectedAiModel.value || aiControlsLocked.value || !selectedAiModelReady.value) return
  const signature = capabilitySignature(selectedAiModel.value)
  if (!signature || signature === lastAutoCapabilitySignature.value) return
  lastAutoCapabilitySignature.value = signature
  requestAiModelCheck(true, '正在检测模型能力', 'auto_capability')
}

function requestAiModelCheck(probeCapabilities: boolean, message: string, trigger: string) {
  if (!selectedAiModel.value || aiControlsLocked.value) return
  aiRequestPending.value = true
  aiRequestMessage.value = message
  emit('testAi', { ...aiModelPayloadForCheck(probeCapabilities), probe_capabilities: probeCapabilities, test_trigger: trigger })
}

function handleAiConfigFieldBlur(field: 'base_url' | 'api_key' | 'model') {
  if (!selectedAiModel.value || aiControlsLocked.value) return
  if (field === 'base_url' || field === 'api_key') {
    if (selectedAiModelReady.value) autoTestSelectedAiModel()
    else autoRefreshModelList()
    return
  }
  autoTestSelectedAiModel()
}

function handleAiModelSelect(value: string) {
  const selected = selectedAiModel.value
  const desiredCapabilities = capabilitySelection(selected)
  setSelectedModelField('model', value)
  if (selectedAiModel.value && desiredCapabilities.length) {
    selectedAiModel.value.capabilities = desiredCapabilities
    selectedAiModel.value.unsupported_capabilities = []
  }
  autoTestSelectedAiModel()
}

function applyAiTestResult(result: AuthResult | null) {
  const raw = asRecord(result?.raw)
  if (raw.channel !== 'ai_model') return
  aiRequestPending.value = false
  aiRequestMessage.value = ''
  if (result?.ok === false) {
    lastAutoModelListSignature.value = ''
    lastAutoCapabilitySignature.value = ''
    return
  }
  const rawModelId = String(raw.model_id || '').trim()
  const target = aiModels.value.find((model) => String(model.id || '') === rawModelId) || selectedAiModel.value
  if (!target) return
  const options = normalizeModelOptions(raw.available_models)
  if (options.length) {
    target.model_options = options
    const selectedModel = String(target.model || '').trim()
    if (selectedModel && !options.some((option) => option.id === selectedModel)) {
      target.model = ''
    }
  }
  const unsupported = new Set(asStringArray(raw.unsupported_capabilities))
  const supported = new Set(asStringArray(raw.supported_capabilities))
  const capabilityResults = asRecord(raw.capability_results)
  const testedCapabilities = new Set([...Object.keys(capabilityResults), ...supported, ...unsupported])
  if (testedCapabilities.size) {
    const previousSelection = capabilitySelection(target)
    const nextCapabilities = previousSelection.length
      ? previousSelection.filter((capability) => supported.has(capability))
      : Array.from(supported)
    target.detected_capabilities = Array.from(supported)
    target.unsupported_capabilities = Array.from(unsupported)
    target.capability_results = capabilityResults
    target.effective_capabilities = nextCapabilities
    target.capabilities = nextCapabilities
    lastAutoCapabilitySignature.value = capabilitySignature(target)
  }
}

watch(() => props.lastResult, applyAiTestResult, { deep: true })
watch(() => props.loading, (loading) => {
  if (!loading) {
    aiRequestPending.value = false
    aiRequestMessage.value = ''
  }
})

function storePayload(): UnknownRecord {
  return {
    mercadolibre: { app_id: form.mlAppId, client_secret: form.mlClientSecret, app_secret: form.mlClientSecret, redirect_uri: form.mlRedirectUri, notification_url: form.mlNotificationUrl },
    wildberries: { content_token: form.wbContentToken, prices_token: form.wbPricesToken },
    ozon: { client_id: form.ozonClientId, api_key: form.ozonApiKey },
  }
}

function selectedStorePayload(): UnknownRecord {
  return { [selectedStorePlatform.value]: asRecord(storePayload()[selectedStorePlatform.value]) }
}

const selectedStorePlatformMeta = computed(() => storePlatforms.find((item) => item.key === selectedStorePlatform.value) || storePlatforms[0])

const selectedStoreSummary = computed(() => asRecord(props.storeAuthSummary[selectedStorePlatform.value]))

const hasStoreSummary = computed(() => Object.keys(selectedStoreSummary.value).length > 0)

const selectedLastStoreResult = computed(() => {
  if (!props.lastResult || props.lastResult.raw?.channel) return null
  const raw = asRecord(props.lastResult.raw)
  const platform = String(raw.platform || '').trim()
  return !platform || platform === selectedStorePlatform.value ? props.lastResult : null
})

const selectedStoreResultDetails = computed(() => {
  if (selectedLastStoreResult.value) {
    const raw = asRecord(selectedLastStoreResult.value.raw)
    const details = { ...raw }
    delete details.storeAuthSummary
    return details
  }
  return selectedStoreSummary.value
})

const hasSelectedStoreResult = computed(() => Object.keys(selectedStoreResultDetails.value).length > 0)

const selectedStoreResultStatus = computed(() => {
  if (selectedLastStoreResult.value) return selectedLastStoreResult.value.ok ? '成功' : '失败'
  return String(selectedStoreSummary.value.status || selectedStoreSummary.value.message || '已记录')
})

const selectedStoreResultMessage = computed(() => {
  if (selectedLastStoreResult.value) return selectedLastStoreResult.value.message || selectedLastStoreResult.value.error || ''
  return String(selectedStoreSummary.value.next_action || selectedStoreSummary.value.error_message || selectedStoreSummary.value.masked_account || selectedStoreSummary.value.shop_name || '')
})

const mlCanGenerateLink = computed(() => Boolean(form.mlAppId.trim() && form.mlRedirectUri.trim().startsWith('https://')))
const mlCanExchangeCode = computed(() => Boolean(mlCanGenerateLink.value && form.mlClientSecret.trim() && form.mlCode.trim()))
const mlHasRefreshToken = computed(() => Boolean(props.mercadolibreChecklist?.fields.find((field) => field.key === 'refresh_token')?.ok))

function copy(text: string) {
  if (text) void navigator.clipboard?.writeText(text)
}
</script>

<template>
  <div class="space-y-6">
    <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">授权配置</h2>
          <p class="muted mt-1">{{ activeAuthSettingsTabMeta.summary }}</p>
        </div>
      </div>

      <div class="mt-5 overflow-x-auto rounded-lg border border-accent-200 bg-accent-50 p-1 dark:border-dark-700 dark:bg-dark-950/70">
        <nav class="flex min-w-max gap-1" aria-label="授权配置分类">
          <button
            v-for="tab in authSettingsTabs"
            :key="tab.key"
            type="button"
            class="min-w-32 rounded-lg px-4 py-3 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-primary-300"
            :class="activeAuthSettingsTab === tab.key ? 'border border-primary-200 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border border-transparent text-accent-600 hover:bg-white/80 hover:text-accent-950 dark:text-accent-300 dark:hover:bg-dark-900 dark:hover:text-white'"
            role="tab"
            :aria-selected="activeAuthSettingsTab === tab.key"
            @click="activeAuthSettingsTab = tab.key"
          >
            <span class="block whitespace-nowrap font-semibold">{{ tab.label }}</span>
            <span class="mt-1 hidden whitespace-nowrap text-xs text-accent-500 dark:text-accent-400 md:block">{{ tab.summary }}</span>
          </button>
        </nav>
      </div>

      <div class="mt-5">
        <section v-show="activeAuthSettingsTab === 'ai_models'" class="relative rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div v-if="aiControlsLocked" class="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-slate-950/15 p-4 backdrop-blur-sm dark:bg-slate-950/45">
            <div class="flex min-w-64 flex-col items-center rounded-lg border border-accent-200 bg-white px-6 py-5 text-center shadow-lg dark:border-dark-700 dark:bg-dark-900">
              <span class="size-8 animate-spin rounded-full border-4 border-primary-100 border-t-primary-600 dark:border-primary-500/20 dark:border-t-primary-300"></span>
              <span class="mt-3 text-sm font-semibold text-accent-900 dark:text-accent-100">{{ aiBlockingMessage }}</span>
              <span class="mt-1 text-xs text-accent-500 dark:text-accent-400">请稍候，当前配置区已锁定</span>
            </div>
          </div>

          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 class="font-semibold text-accent-950 dark:text-white">AI Provider / 模型</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ aiModels.length }} 个模型，维护连接、模型名和能力。</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="addAiModel">添加模型</button>
              <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="aiControlsLocked || !selectedAiModel" @click="duplicateSelectedAiModel">复制当前模型</button>
              <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="aiControlsLocked || !selectedAiModelReady" :title="selectedAiModelReady ? '自动测试后也可以手动重试' : selectedAiHint" @click="testSelectedAiModel">测试当前模型</button>
              <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="emit('saveAi', aiPayload())">保存 AI 设置</button>
            </div>
          </div>

          <div class="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <div class="space-y-2">
              <button
                v-for="(model, index) in aiModels"
                :key="String(model.id || index)"
                type="button"
                class="w-full rounded-lg border px-3 py-2 text-left text-sm transition"
                :class="selectedAiModelIndex === index ? 'border-primary-300 bg-white text-primary-700 shadow-sm dark:border-primary-500/40 dark:bg-primary-500/10 dark:text-primary-100' : 'border-accent-200 bg-white text-accent-700 hover:bg-accent-50 dark:border-dark-700 dark:bg-dark-900/70 dark:text-accent-200'"
                :disabled="aiControlsLocked"
                @click="selectedAiModelIndex = index"
              >
                <span class="block truncate font-semibold">{{ model.name || model.id || '未命名模型' }}</span>
                <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ model.provider || 'Provider' }} · {{ model.model || '未选模型' }}</span>
              </button>
            </div>

            <div v-if="selectedAiModel" class="space-y-3">
              <div class="grid gap-3 md:grid-cols-2">
                <input class="input" :value="modelField('id')" :disabled="aiControlsLocked" placeholder="模型 ID，例如 deepseek_text" @input="setSelectedModelField('id', eventText($event))" />
                <input class="input" :value="modelField('name')" :disabled="aiControlsLocked" placeholder="显示名称" @input="setSelectedModelField('name', eventText($event))" />
                <input class="input" :value="modelField('provider')" :disabled="aiControlsLocked" placeholder="Provider，例如 DeepSeek / OpenAI" @input="setSelectedModelField('provider', eventText($event))" />
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API 风格</span>
                  <select class="input" :value="modelField('api_style', 'openai_compatible')" :disabled="aiControlsLocked" @change="setSelectedModelField('api_style', eventText($event))">
                    <option value="openai_compatible">OpenAI-Compatible Chat</option>
                    <option value="openai_responses">OpenAI Responses</option>
                  </select>
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型质量档位</span>
                  <select class="input" :value="modelField('quality_level', 'balanced')" :disabled="aiControlsLocked" @change="setSelectedModelField('quality_level', eventText($event))">
                    <option v-for="option in modelQualityOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </label>
                <input class="input" :value="modelField('base_url')" :disabled="aiControlsLocked" placeholder="Base URL" @input="setSelectedModelField('base_url', eventText($event))" @blur="handleAiConfigFieldBlur('base_url')" />
                <input class="input md:col-span-2" :value="modelField('api_key')" :disabled="aiControlsLocked" :placeholder="apiKeyPlaceholder()" autocomplete="off" spellcheck="false" @input="setSelectedModelField('api_key', eventText($event))" @blur="handleAiConfigFieldBlur('api_key')" />
                <label class="block md:col-span-2">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</span>
                  <select class="input" :value="modelField('model')" :disabled="aiControlsLocked || !selectedModelOptions.length" @change="handleAiModelSelect(eventText($event))">
                    <option value="">{{ selectedModelOptions.length ? '请选择模型' : '填写 URL 和 API Key 后自动加载' }}</option>
                    <option v-for="option in selectedModelOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
                  </select>
                </label>
                <label v-if="selectedAiModelImageCapable" class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">图片质量</span>
                  <select
                    class="input"
                    :value="modelField('quality', 'auto')"
                    :disabled="aiControlsLocked"
                    title="OpenAI 图片接口常用质量选项；模型列表 API 不返回该枚举"
                    @change="setSelectedModelField('quality', eventText($event))"
                  >
                    <option v-for="option in imageQualityOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </label>
                <input class="input" :value="modelField('timeout_seconds')" :disabled="aiControlsLocked" placeholder="超时秒数，可选" @input="setSelectedModelField('timeout_seconds', eventText($event))" />
                <details class="md:col-span-2 rounded-lg border border-dashed border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                  <summary class="cursor-pointer font-semibold text-accent-700 dark:text-accent-200">高级配置</summary>
                  <div class="mt-3 grid gap-3 md:grid-cols-2">
                    <input class="input" :value="modelField('base_url_env')" :disabled="aiControlsLocked" placeholder="Base URL 环境变量名，可选" @input="setSelectedModelField('base_url_env', eventText($event))" />
                    <input class="input" :value="modelField('api_key_env')" :disabled="aiControlsLocked" placeholder="API Key 环境变量名，可选" @input="setSelectedModelField('api_key_env', eventText($event))" />
                  </div>
                </details>
              </div>

              <div class="flex flex-wrap gap-2">
                <label
                  v-for="capability in capabilityOptions"
                  :key="capability.value"
                  class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
                  :class="isCapabilityUnsupported(capability.value) ? 'cursor-not-allowed border-accent-200 bg-accent-100 text-accent-400 opacity-80 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-500' : 'border-accent-200 bg-white dark:border-dark-700 dark:bg-dark-900'"
                  :title="isCapabilityUnsupported(capability.value) ? '当前模型测试不支持该能力' : ''"
                >
                  <input type="checkbox" :checked="hasCapability(capability.value)" :disabled="aiControlsLocked || isCapabilityUnsupported(capability.value)" @change="setCapability(capability.value, eventChecked($event))" />
                  <span>{{ capability.label }}</span>
                </label>
                <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                  <input type="checkbox" :checked="selectedAiModel.enabled !== false" :disabled="aiControlsLocked" @change="setSelectedModelField('enabled', eventChecked($event))" />
                  <span>启用</span>
                </label>
              </div>

              <button class="btn btn-outline py-1.5 text-sm text-rose-700" type="button" :disabled="aiControlsLocked || aiModels.length <= 1" @click="removeSelectedAiModel">删除当前模型</button>
            </div>
          </div>

          <div v-if="showAiConfigResult && props.lastResult" class="mt-4 rounded-lg p-4 text-sm ring-1" :class="props.lastResult.ok ? 'bg-emerald-50 text-emerald-950 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-100 dark:ring-emerald-500/30' : 'bg-rose-50 text-rose-950 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-100 dark:ring-rose-500/30'">
            <div class="font-semibold">最近 AI 测试：{{ props.lastResult.ok ? '成功' : '失败' }}</div>
            <div class="mt-1 break-words">{{ props.lastResult.message || props.lastResult.error }}</div>
            <div v-if="props.lastResult.nextAction" class="mt-1 text-blue-700 dark:text-blue-200">下一步：{{ props.lastResult.nextAction }}</div>
            <pre class="mt-3 max-h-52 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(props.lastResult.raw, null, 2) }}</pre>
          </div>
        </section>

        <section v-show="activeAuthSettingsTab === 'ai_bindings'" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold text-accent-950 dark:text-white">功能绑定</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">功能选择指定模型；留空时按能力自动匹配。</p>
            </div>
            <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="emit('saveAi', aiPayload())">保存功能绑定</button>
          </div>
          <div v-if="aiUseCases.length" class="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <label v-for="useCase in aiUseCases" :key="String(useCase.id)" class="block rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
              <span class="block text-sm font-semibold text-accent-950 dark:text-white">{{ useCase.label || useCase.id }}</span>
              <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">需要：{{ useCaseCapabilityText(useCase) }}</span>
              <select class="input mt-2" :value="aiUseCaseBindings[String(useCase.id || '')] || ''" @change="setUseCaseBinding(String(useCase.id || ''), eventText($event))">
                <option value="">自动匹配</option>
                <option v-for="model in compatibleModelsForUseCase(useCase)" :key="String(model.id)" :value="String(model.id)">{{ model.name || model.id }}</option>
              </select>
            </label>
          </div>
          <div v-else class="mt-4 rounded-lg border border-dashed border-accent-300 bg-white p-6 text-center text-sm text-accent-500 dark:border-dark-600 dark:bg-dark-900 dark:text-accent-300">
            当前没有可配置的 AI 功能。
          </div>
        </section>

        <section v-show="activeAuthSettingsTab === 'stores'" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold text-accent-950 dark:text-white">店铺授权</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">选择一个平台，只显示该平台的授权配置和检测结果。</p>
            </div>
            <div class="flex flex-wrap gap-2">
              <select v-model="selectedStorePlatform" class="input w-64">
                <option v-for="platform in storePlatforms" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
              </select>
              <button class="btn btn-primary" :disabled="props.loading" @click="emit('saveStore', selectedStorePayload())">保存当前平台授权</button>
            </div>
          </div>

          <div class="mt-5 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 class="font-semibold text-accent-950 dark:text-white">{{ selectedStorePlatformMeta.label }}</h4>
                <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ selectedStorePlatformMeta.subtitle }}</p>
              </div>
              <div v-if="hasStoreSummary" class="rounded-lg bg-slate-900/80 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-600">
                <span class="font-semibold text-white">授权测试：</span>
                <span class="text-slate-100">{{ selectedStoreSummary.status || selectedStoreSummary.message || '已记录' }}</span>
              </div>
            </div>

            <template v-if="selectedStorePlatform === 'mercadolibre'">
              <input v-model="form.mlAppId" class="input mt-3" placeholder="App ID" />
              <input v-model="form.mlClientSecret" type="password" class="input mt-2" placeholder="Client Secret" />
              <input v-model="form.mlRedirectUri" class="input mt-2" placeholder="Redirect URI" />
              <input v-model="form.mlNotificationUrl" class="input mt-2" placeholder="订单通知回调 URL，例如 https://erp.example.com/api/mercadolibre/notifications" />
              <input v-model="form.mlCode" class="input mt-2" placeholder="回跳 URL 或 code=TG-xxxx" />
              <input v-model="form.mlCategoryId" class="input mt-2" placeholder="真实类目 ID，可用于 07D 类目测试" />
              <div class="mt-3 flex flex-wrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('refreshChecklist')">刷新清单</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading || !mlCanGenerateLink" title="需要 App ID 和 https:// Redirect URI" @click="emit('generateMlLink', form.mlAppId, form.mlRedirectUri)">生成授权链接</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading || !props.authLink" @click="emit('openMlLink', props.authLink)">打开授权链接</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading || !mlCanExchangeCode" title="需要 App ID、Client Secret、https:// Redirect URI 和回跳 code" @click="emit('exchangeMlCode', form.mlCode, { app_id: form.mlAppId, client_secret: form.mlClientSecret, redirect_uri: form.mlRedirectUri })">用 code 换 token</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading || !mlHasRefreshToken" title="需要先用 code 换到 Refresh Token" @click="emit('refreshMlToken', { app_id: form.mlAppId, client_secret: form.mlClientSecret })">刷新 token</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'mercadolibre')">测试店铺授权</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('realMlTest', 'user_info')">07D 用户信息</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('realMlTest', 'category_attrs', form.mlCategoryId)">07D 类目属性</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('realMlTest', 'payload_generate')">07D Payload</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="emit('clearAuth', 'mercadolibre')">清除 ML 授权</button>
              </div>
              <div v-if="props.authLink" class="mt-3 rounded-lg bg-blue-50 p-3 text-xs text-blue-900 ring-1 ring-blue-100 dark:bg-blue-500/10 dark:text-blue-100 dark:ring-blue-500/30">
                <div class="font-semibold">授权链接</div>
                <div class="mt-1 break-all">{{ props.authLink }}</div>
                <button class="btn btn-outline mt-2 py-1.5 text-xs" @click="copy(props.authLink)">复制链接</button>
              </div>
              <div v-if="props.mercadolibreChecklist" class="mt-3 rounded-lg bg-slate-900/80 p-3 text-xs text-slate-100 ring-1 ring-slate-600 dark:bg-dark-900 dark:ring-dark-700">
                <div class="flex items-center justify-between gap-2">
                  <div class="font-semibold">授权清单：{{ props.mercadolibreChecklist.tokenReady ? 'Token 已保存' : props.mercadolibreChecklist.readyForAuthLink ? '可生成授权链接' : '配置不完整' }}</div>
                  <button class="btn btn-outline py-1 text-xs" @click="copy(props.mercadolibreChecklist.copyText)">复制清单</button>
                </div>
                <ul class="mt-2 space-y-1">
                  <li v-for="field in props.mercadolibreChecklist.fields" :key="field.key" class="flex items-center justify-between gap-2">
                    <span>{{ field.label }}</span>
                    <span :class="field.ok ? 'text-emerald-700' : 'text-amber-700'">{{ field.ok ? 'OK' : '需检查' }} · {{ field.value }}</span>
                  </li>
                </ul>
                <div class="mt-2 text-blue-700 dark:text-blue-200">下一步：{{ props.mercadolibreChecklist.nextAction }}</div>
              </div>
            </template>

            <template v-else-if="selectedStorePlatform === 'wildberries'">
              <input v-model="form.wbContentToken" type="password" class="input mt-3" placeholder="Content API Token" />
              <input v-model="form.wbPricesToken" type="password" class="input mt-2" placeholder="Prices API Token，可选" />
              <div class="mt-3 flex flex-wrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'wildberries', 'content')">测试 Content</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'wildberries', 'prices')">测试价格</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="emit('clearAuth', 'wildberries')">清除授权</button>
              </div>
            </template>

            <template v-else>
              <input v-model="form.ozonClientId" class="input mt-3" placeholder="Client ID" />
              <input v-model="form.ozonApiKey" type="password" class="input mt-2" placeholder="API Key" />
              <div class="mt-3 flex flex-wrap gap-2">
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'ozon')">测试授权</button>
                <button class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'ozon', 'category')">读取类目测试</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="emit('clearAuth', 'ozon')">清除授权</button>
              </div>
            </template>

            <div v-if="hasSelectedStoreResult" class="mt-4 rounded-lg bg-slate-900/80 p-3 text-sm text-slate-100 ring-1 ring-slate-600 dark:bg-dark-900 dark:ring-dark-700">
              <div class="font-semibold text-white">授权测试结果：{{ selectedStoreResultStatus }}</div>
              <div v-if="selectedStoreResultMessage" class="mt-1 text-slate-200">{{ selectedStoreResultMessage }}</div>
              <pre class="mt-2 max-h-52 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(selectedStoreResultDetails, null, 2) }}</pre>
            </div>
          </div>
        </section>

        <section v-show="activeAuthSettingsTab === 'apis'" class="space-y-4">
          <div class="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
            <div>
              <h3 class="font-semibold text-accent-950 dark:text-white">采集与核价 API</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">维护汇率服务和 1688 采集 API。</p>
            </div>
            <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="props.loading" @click="emit('saveAi', aiPayload())">保存 API 设置</button>
          </div>

          <div class="grid gap-4 xl:grid-cols-2">
            <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="font-semibold text-accent-950 dark:text-white">核价汇率</h4>
                <button class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !exchangeRateReady" :title="exchangeRateReady ? '' : exchangeRateHint" @click="emit('testApi', 'exchange_rate', aiPayload().pricing_defaults as UnknownRecord)">测试</button>
              </div>
              <div class="mt-3 grid gap-3">
                <input v-model="form.exchangeRateApiUrl" class="input" placeholder="汇率 API URL" />
                <div class="grid gap-3 sm:grid-cols-2">
                  <input v-model="form.exchangeRateTimeoutSeconds" class="input" placeholder="超时秒数，例如 10" />
                  <input v-model="form.exchangeRateCacheTtlSeconds" class="input" placeholder="缓存秒数，例如 3600" />
                </div>
              </div>
            </div>

            <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h4 class="font-semibold text-accent-950 dark:text-white">1688 采集 API</h4>
                <button class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !alibabaApiReady" :title="alibabaApiReady ? '' : alibabaApiHint" @click="emit('testApi', '1688', aiPayload()['1688_api'] as UnknownRecord, form.alibabaTestOfferId)">测试</button>
              </div>
              <div class="mt-3 grid gap-3 md:grid-cols-2">
                <input v-model="form.alibabaAppKey" class="input" placeholder="App Key" autocomplete="off" spellcheck="false" />
                <input v-model="form.alibabaAppSecret" type="password" class="input" placeholder="App Secret" autocomplete="off" spellcheck="false" />
                <input v-model="form.alibabaAccessToken" type="password" class="input md:col-span-2" placeholder="Access Token / Session，可选" autocomplete="off" spellcheck="false" />
                <input v-model="form.alibabaApiMethod" class="input" placeholder="alibaba.product.get" />
                <input v-model="form.alibabaApiVersion" class="input" placeholder="API 版本" />
                <input v-model="form.alibabaApiBaseUrl" class="input md:col-span-2 font-mono text-xs" placeholder="API 请求地址" />
                <input v-model="form.alibabaApiTimeoutSeconds" class="input" placeholder="超时秒数" />
                <input v-model="form.alibabaTestOfferId" class="input md:col-span-2" placeholder="测试商品 ID / 详情链接，可选" />
              </div>
            </div>
          </div>

          <div v-if="showApiConfigResult && props.lastResult" class="rounded-lg p-4 text-sm ring-1" :class="props.lastResult.ok ? 'bg-emerald-50 text-emerald-950 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-100 dark:ring-emerald-500/30' : 'bg-rose-50 text-rose-950 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-100 dark:ring-rose-500/30'">
            <div class="font-semibold">最近 API 测试：{{ props.lastResult.ok ? '成功' : '失败' }}</div>
            <div class="mt-1 break-words">{{ props.lastResult.message || props.lastResult.error }}</div>
            <div v-if="props.lastResult.nextAction" class="mt-1 text-blue-700 dark:text-blue-200">下一步：{{ props.lastResult.nextAction }}</div>
            <pre class="mt-3 max-h-52 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(props.lastResult.raw, null, 2) }}</pre>
          </div>
        </section>

        <section v-show="activeAuthSettingsTab === 'research'">
          <ProductResearchSettingsPanel :ai-models="aiModels" embedded />
        </section>
      </div>
    </section>
  </div>
</template>
