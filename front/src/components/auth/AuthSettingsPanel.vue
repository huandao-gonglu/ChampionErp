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
  testApi: [kind: 'exchange_rate' | '1688' | 'yunexpress', config: UnknownRecord, testValue?: string]
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
  yunexpressEnvironment: 'sandbox',
  yunexpressBaseUrl: 'https://openapi-sbx.yunexpress.cn',
  yunexpressAppId: '',
  yunexpressAppSecret: '',
  yunexpressSourceKey: '',
  yunexpressProductCode: '',
  yunexpressSourceCode: '',
  yunexpressPlatformAccountCode: '',
  yunexpressLabelType: 'PDF',
  yunexpressWeightUnit: 'KG',
  yunexpressSizeUnit: 'CM',
  yunexpressTimeoutSeconds: '20',
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
const aiUseCasePrompts = ref<Record<string, UnknownRecord>>({})
const API_CONNECTION_TYPE = 'api'
const CLI_CONNECTION_TYPE = 'cli'
const BROWSER_CONNECTION_TYPE = 'browser'
const BROWSER_MODE_MANAGED_PROFILE = 'managed_profile'
const BROWSER_MODE_EXISTING_BROWSER = 'existing_browser'
const modelListDependencyFields = new Set(['provider', 'api_style', 'base_url', 'base_url_env', 'api_key', 'api_key_env'])
const capabilityProbeDependencyFields = new Set([...modelListDependencyFields, 'model', 'connection_type', 'cli_tool', 'command', 'profile', 'sandbox', 'browser_provider', 'browser_mode', 'browser_profile', 'browser_port', 'browser_url'])
const lastAutoModelListSignature = ref('')
const lastAutoCapabilitySignature = ref('')
const aiRequestPending = ref(false)
const aiRequestMessage = ref('')
const capabilityProbeDialog = reactive({
  open: false,
  capability: '',
  payloadText: '',
  pending: false,
  error: '',
  result: null as UnknownRecord | null,
})

const storePlatforms: Array<{ key: Marketplace; label: string; subtitle: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre', subtitle: 'OAuth、授权链接、店铺授权测试' },
  { key: 'wildberries', label: 'Wildberries', subtitle: 'Content / Prices API Token' },
  { key: 'ozon', label: 'Ozon', subtitle: 'Client ID + API Key' },
]

const authSettingsTabs: Array<{ key: AuthSettingsTab; label: string; summary: string }> = [
  { key: 'ai_models', label: 'AI 模型', summary: '配置模型、能力和连接测试' },
  { key: 'ai_bindings', label: '功能绑定', summary: '模型和功能 Prompt' },
  { key: 'stores', label: '店铺授权', summary: 'Mercado Libre、Wildberries、Ozon' },
  { key: 'apis', label: '采集、核价与物流', summary: '汇率、1688 采集和云途物流 API' },
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
const connectionTypeOptions = [
  { value: API_CONNECTION_TYPE, label: 'API 服务' },
  { value: CLI_CONNECTION_TYPE, label: '本地 CLI' },
  { value: BROWSER_CONNECTION_TYPE, label: '浏览器网页' },
]
const browserModeOptions = [
  { value: BROWSER_MODE_MANAGED_PROFILE, label: '项目托管 Profile' },
  { value: BROWSER_MODE_EXISTING_BROWSER, label: '连接已打开浏览器' },
]
const fallbackCliTools = [
  { value: 'codex', label: 'Codex CLI', command: 'codex', installed: false, path: '' },
  { value: 'claude', label: 'Claude CLI', command: 'claude', installed: false, path: '' },
  { value: 'gemini', label: 'Gemini CLI', command: 'gemini', installed: false, path: '' },
  { value: 'glm', label: 'GLM CLI', command: 'glm', installed: false, path: '' },
  { value: 'custom', label: '自定义 CLI', command: '', installed: false, path: '' },
]
const cliSandboxOptions = ['read-only', 'workspace-write', 'danger-full-access']
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

function chinaTodayIso(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const partValue = (type: string) => parts.find((part) => part.type === type)?.value || ''
  return `${partValue('year')}-${partValue('month')}-${partValue('day')}`
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = textValue(value)
    if (text) return text
  }
  return ''
}

function normalizeConnectionType(value: unknown): string {
  const text = firstText(value).toLowerCase()
  if (text === CLI_CONNECTION_TYPE) return CLI_CONNECTION_TYPE
  if (text === BROWSER_CONNECTION_TYPE) return BROWSER_CONNECTION_TYPE
  return API_CONNECTION_TYPE
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

function normalizeCliToolOptions(value: unknown): Array<{ value: string; label: string; command: string; installed: boolean; path: string }> {
  const rawItems = Array.isArray(value) ? value : []
  const source = rawItems.length ? rawItems : fallbackCliTools
  const options: Array<{ value: string; label: string; command: string; installed: boolean; path: string }> = []
  const seen = new Set<string>()
  for (const item of source) {
    const record = asRecord(item)
    const valueText = firstText(record.value, record.id, item)
    if (!valueText || seen.has(valueText)) continue
    seen.add(valueText)
    const fallback = fallbackCliTools.find((tool) => tool.value === valueText)
    options.push({
      value: valueText,
      label: firstText(record.label, fallback?.label, valueText),
      command: firstText(record.command, fallback?.command),
      installed: Boolean(record.installed),
      path: firstText(record.path),
    })
  }
  return options
}

function cliToolDefaultCommand(cliTool: string): string {
  return normalizeCliToolOptions(props.aiConfig.cli_tools).find((tool) => tool.value === cliTool)?.command
    || fallbackCliTools.find((tool) => tool.value === cliTool)?.command
    || ''
}

function cliToolLabel(cliTool: string): string {
  return normalizeCliToolOptions(props.aiConfig.cli_tools).find((tool) => tool.value === cliTool)?.label
    || fallbackCliTools.find((tool) => tool.value === cliTool)?.label
    || cliTool
}

function normalizeAiModelRow(value: unknown, index: number): UnknownRecord {
  const record = asRecord(value)
  const fallbackId = index === 0 ? 'default_text' : index === 1 ? 'default_image' : `ai_model_${index + 1}`
  const model = firstText(record.model)
  const modelOptions = normalizeModelOptions(record.model_options || record.available_models)
  if (model && !modelOptions.some((item) => item.id === model)) {
    modelOptions.unshift({ id: model, label: model })
  }
  const connectionType = normalizeConnectionType(record.connection_type)
  const cliTool = firstText(record.cli_tool, record.cli_provider, 'codex')
  return {
    id: firstText(record.id, fallbackId),
    name: firstText(record.name, record.id, `AI 模型 ${index + 1}`),
    connection_type: connectionType,
    provider: firstText(record.provider, connectionType === CLI_CONNECTION_TYPE ? cliToolLabel(cliTool) : connectionType === BROWSER_CONNECTION_TYPE ? '浏览器 AI' : 'OpenAI-Compatible'),
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
    cli_tool: cliTool,
    command: firstText(record.command, record.cli_command, connectionType === CLI_CONNECTION_TYPE ? cliToolDefaultCommand(cliTool) : ''),
    profile: firstText(record.profile, record.cli_profile),
    sandbox: firstText(record.sandbox, 'read-only'),
    browser_provider: firstText(record.browser_provider, record.browserProvider),
    browser_mode: firstText(record.browser_mode, record.browserMode, BROWSER_MODE_MANAGED_PROFILE),
    browser_profile: firstText(record.browser_profile, record.browserProfile),
    browser_port: firstText(record.browser_port, record.browserPort),
    browser_url: firstText(record.browser_url, record.browserUrl),
    enabled: record.enabled !== false,
    api_key_configured: Boolean(record.api_key_configured),
    model_options: modelOptions,
  }
}

function defaultAiModelRow(index: number): UnknownRecord {
  return normalizeAiModelRow({
    id: `ai_model_${Date.now()}_${index + 1}`,
    name: `AI 模型 ${index + 1}`,
    connection_type: API_CONNECTION_TYPE,
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

function normalizeUseCasePrompts(value: unknown): Record<string, UnknownRecord> {
  const record = asRecord(value)
  const result: Record<string, UnknownRecord> = {}
  for (const [key, raw] of Object.entries(record)) {
    const item = asRecord(raw)
    if (key) result[key] = { ...item }
  }
  return result
}

function fillFromProps() {
  const pricing = asRecord(props.appConfig.pricing_defaults)
  const alibabaApi = asRecord(props.appConfig['1688_api'])
  const yunexpress = asRecord(props.appConfig.yunexpress)
  const ml = asRecord(props.storeConfig.mercadolibre)
  const wb = asRecord(props.storeConfig.wildberries)
  const ozon = asRecord(props.storeConfig.ozon)
  const modelRows = Array.isArray(props.aiConfig.ai_models) ? props.aiConfig.ai_models : Array.isArray(props.appConfig.ai_models) ? props.appConfig.ai_models : []
  aiModels.value = modelRows.length ? modelRows.map((item, index) => normalizeAiModelRow(displayAiModelRecord(item), index)) : [defaultAiModelRow(0)]
  selectedAiModelIndex.value = Math.min(selectedAiModelIndex.value, Math.max(aiModels.value.length - 1, 0))
  aiUseCaseBindings.value = normalizeUseCaseBindings(props.aiConfig.ai_use_case_bindings || props.appConfig.ai_use_case_bindings)
  aiUseCasePrompts.value = normalizeUseCasePrompts(props.aiConfig.ai_use_case_prompts || props.appConfig.ai_use_case_prompts)
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
  form.yunexpressEnvironment = firstText(yunexpress.environment, form.yunexpressEnvironment)
  form.yunexpressBaseUrl = firstText(yunexpress.base_url, form.yunexpressBaseUrl)
  form.yunexpressAppId = firstText(yunexpress.app_id, form.yunexpressAppId)
  form.yunexpressAppSecret = firstText(yunexpress.app_secret, form.yunexpressAppSecret)
  form.yunexpressSourceKey = firstText(yunexpress.source_key, form.yunexpressSourceKey)
  form.yunexpressProductCode = firstText(yunexpress.product_code, form.yunexpressProductCode)
  form.yunexpressSourceCode = firstText(yunexpress.source_code, form.yunexpressSourceCode)
  form.yunexpressPlatformAccountCode = firstText(yunexpress.platform_account_code, form.yunexpressPlatformAccountCode)
  form.yunexpressLabelType = firstText(yunexpress.label_type, form.yunexpressLabelType)
  form.yunexpressWeightUnit = firstText(yunexpress.weight_unit, form.yunexpressWeightUnit)
  form.yunexpressSizeUnit = firstText(yunexpress.size_unit, form.yunexpressSizeUnit)
  form.yunexpressTimeoutSeconds = firstText(yunexpress.timeout_seconds, form.yunexpressTimeoutSeconds)
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
const cliToolOptions = computed(() => normalizeCliToolOptions(props.aiConfig.cli_tools))
const globalPromptUseCases = computed(() => aiUseCases.value.filter((useCase) => {
  const id = String(useCase.id || '')
  return id && Boolean(aiUseCasePrompts.value[id])
}))
const activeAuthSettingsTabMeta = computed(() => authSettingsTabs.find((tab) => tab.key === activeAuthSettingsTab.value) || authSettingsTabs[0])
const capabilityLabelByValue = Object.fromEntries(capabilityOptions.map((item) => [item.value, item.label]))
const selectedAiModelConnectionType = computed(() => normalizeConnectionType(modelField('connection_type', API_CONNECTION_TYPE)))
const selectedAiModelIsApi = computed(() => selectedAiModelConnectionType.value === API_CONNECTION_TYPE)
const selectedAiModelIsCli = computed(() => selectedAiModelConnectionType.value === CLI_CONNECTION_TYPE)
const selectedAiModelIsBrowser = computed(() => selectedAiModelConnectionType.value === BROWSER_CONNECTION_TYPE)
const selectedModelOptions = computed(() => selectedAiModelIsApi.value ? normalizeModelOptions(selectedAiModel.value?.model_options) : [])
const capabilityProbeLabel = computed(() => capabilityLabelByValue[capabilityProbeDialog.capability] || capabilityProbeDialog.capability || '能力')
const selectedCliToolStatus = computed(() => cliToolOptions.value.find((tool) => tool.value === modelField('cli_tool', 'codex')) || cliToolOptions.value[0])
const selectedCliStatusText = computed(() => {
  const tool = selectedCliToolStatus.value
  if (!tool) return '未检测到本地 CLI 工具'
  if (tool.value === 'custom') return '自定义 CLI 已预留，请填写完整命令；当前版本先支持 Codex CLI'
  const status = tool.installed ? `已检测到：${tool.path || tool.command}` : `未检测到 ${tool.command || tool.label}，请先安装或填写完整路径`
  return tool.value === 'codex' ? status : `${status}；当前版本先支持 Codex CLI，其他工具为预留`
})
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
  && selectedAiModelIsApi.value
  && String(selectedAiModel.value.base_url || selectedAiModel.value.base_url_env || '').trim()
  && (String(selectedAiModel.value.api_key || '').trim() || selectedAiModel.value.api_key_configured),
))
const selectedAiModelReady = computed(() => {
  if (!selectedAiModel.value) return false
  if (selectedAiModelIsCli.value) return Boolean(String(selectedAiModel.value.command || cliToolDefaultCommand(String(selectedAiModel.value.cli_tool || 'codex'))).trim())
  if (selectedAiModelIsBrowser.value) return true
  return Boolean(
    String(selectedAiModel.value.provider || '').trim()
    && String(selectedAiModel.value.model || '').trim()
    && String(selectedAiModel.value.base_url || selectedAiModel.value.base_url_env || '').trim()
    && (String(selectedAiModel.value.api_key || '').trim() || selectedAiModel.value.api_key_configured)
  )
})
const aiControlsLocked = computed(() => props.loading || aiRequestPending.value)
const aiBlockingMessage = computed(() => aiRequestMessage.value || '正在检测 AI 配置，请稍候')
const selectedAiHint = computed(() => props.loading ? '正在处理，请稍候' : selectedAiModelIsCli.value ? '请先确认本机 CLI 命令可用' : selectedAiModelIsBrowser.value ? '会打开或连接浏览器网页；首次使用请在浏览器窗口手动登录' : '请先填写 Base URL 和 API Key，再选择模型')
const selectedAiModelImageCapable = computed(() => modelHasImageCapability(selectedAiModel.value))
const exchangeRateReady = computed(() => Boolean(form.exchangeRateApiUrl.trim()))
const exchangeRateHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写汇率 API URL')
const alibabaApiReady = computed(() => Boolean(form.alibabaAppKey.trim() && form.alibabaAppSecret.trim() && form.alibabaApiBaseUrl.trim()))
const alibabaApiHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写 1688 App Key、App Secret 和 API 请求地址')
const yunexpressApiReady = computed(() => Boolean(form.yunexpressAppId.trim() && form.yunexpressAppSecret.trim() && form.yunexpressSourceKey.trim() && form.yunexpressBaseUrl.trim()))
const yunexpressApiHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写云途 App ID、App Secret、SourceKey 和 Base URL')
const lastConfigResultChannel = computed(() => String(props.lastResult?.raw?.channel || ''))
const showAiConfigResult = computed(() => lastConfigResultChannel.value === 'ai_model')
const showApiConfigResult = computed(() => ['exchange_rate', '1688', 'yunexpress'].includes(lastConfigResultChannel.value))

function aiPayload(): UnknownRecord {
  return {
    ai_models: aiModels.value.map((model, index) => {
      const row = normalizeAiModelRow(model, index)
      const copySourceId = firstText(model.copy_source_id)
      if (row.connection_type === API_CONNECTION_TYPE && copySourceId && !firstText(row.api_key)) row.copy_source_id = copySourceId
      if (!modelHasImageCapability(row)) {
        delete row.quality
        delete row.size
      }
      if (row.connection_type === CLI_CONNECTION_TYPE) {
        row.provider = cliToolLabel(String(row.cli_tool || 'codex'))
        row.command = firstText(row.command, cliToolDefaultCommand(String(row.cli_tool || 'codex')))
        row.base_url = ''
        row.base_url_env = ''
        row.api_key = ''
        row.api_key_env = ''
        row.api_style = 'openai_compatible'
        row.api_key_configured = false
        row.api_key_masked = ''
        delete row.browser_provider
        delete row.browser_mode
        delete row.browser_profile
        delete row.browser_port
        delete row.browser_url
      } else if (row.connection_type === BROWSER_CONNECTION_TYPE) {
        row.provider = firstText(row.provider, '浏览器 AI')
        row.browser_provider = firstText(row.browser_provider, 'chatgpt')
        row.browser_mode = firstText(row.browser_mode, BROWSER_MODE_MANAGED_PROFILE)
        row.base_url = ''
        row.base_url_env = ''
        row.api_key = ''
        row.api_key_env = ''
        row.api_style = 'openai_compatible'
        row.api_key_configured = false
        row.api_key_masked = ''
        row.model_env = ''
        delete row.cli_tool
        delete row.command
        delete row.profile
        delete row.sandbox
      } else {
        delete row.cli_tool
        delete row.command
        delete row.profile
        delete row.sandbox
        delete row.browser_provider
        delete row.browser_mode
        delete row.browser_profile
        delete row.browser_port
        delete row.browser_url
      }
      delete row.model_options
      delete row.model_env
      return row
    }),
    ai_use_case_bindings: Object.fromEntries(
      Object.entries(aiUseCaseBindings.value)
        .filter(([useCaseId]) => globalPromptUseCases.value.some((useCase) => String(useCase.id || '') === useCaseId))
        .filter(([, modelId]) => String(modelId || '').trim())
        .map(([useCaseId, modelId]) => [useCaseId, { model_id: modelId }]),
    ),
    ai_use_case_prompts: Object.fromEntries(
      globalPromptUseCases.value.map((useCase) => {
        const useCaseId = String(useCase.id || '')
        const prompt = aiUseCasePrompts.value[useCaseId] || {}
        return [
          useCaseId,
          {
            path: String(prompt.path || '').trim(),
            description: String(prompt.description || ''),
            system_prompt: String(prompt.system_prompt || ''),
            user_prompt: String(prompt.user_prompt || ''),
          },
        ]
      }),
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
    yunexpress: {
      environment: form.yunexpressEnvironment.trim(),
      base_url: form.yunexpressBaseUrl.trim(),
      app_id: form.yunexpressAppId.trim(),
      app_secret: form.yunexpressAppSecret.trim(),
      source_key: form.yunexpressSourceKey.trim(),
      product_code: form.yunexpressProductCode.trim(),
      source_code: form.yunexpressSourceCode.trim(),
      platform_account_code: form.yunexpressPlatformAccountCode.trim(),
      label_type: form.yunexpressLabelType.trim(),
      weight_unit: form.yunexpressWeightUnit.trim(),
      size_unit: form.yunexpressSizeUnit.trim(),
      timeout_seconds: form.yunexpressTimeoutSeconds.trim(),
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
  const nextValue = field === 'connection_type' && typeof value === 'string' ? normalizeConnectionType(value) : value
  const previous = selectedAiModel.value[field]
  if (previous === nextValue) return
  const desiredCapabilities = capabilitySelection(selectedAiModel.value)
  const previousConnectionType = normalizeConnectionType(selectedAiModel.value.connection_type)
  const previousCliTool = String(selectedAiModel.value.cli_tool || 'codex')
  const previousCliDefaultCommand = cliToolDefaultCommand(previousCliTool)
  selectedAiModel.value[field] = nextValue
  if (field === 'connection_type') {
    if (nextValue === CLI_CONNECTION_TYPE) {
      const cliTool = firstText(selectedAiModel.value.cli_tool, 'codex')
      selectedAiModel.value.cli_tool = cliTool
      selectedAiModel.value.command = firstText(selectedAiModel.value.command, cliToolDefaultCommand(cliTool))
      selectedAiModel.value.provider = cliToolLabel(cliTool)
      selectedAiModel.value.model = ''
      selectedAiModel.value.model_options = []
      selectedAiModel.value.base_url = ''
      selectedAiModel.value.base_url_env = ''
      selectedAiModel.value.api_key = ''
      selectedAiModel.value.api_key_env = ''
      selectedAiModel.value.api_style = 'openai_compatible'
      selectedAiModel.value.browser_provider = ''
      selectedAiModel.value.browser_mode = ''
      selectedAiModel.value.browser_profile = ''
      selectedAiModel.value.browser_port = ''
      selectedAiModel.value.browser_url = ''
    } else if (nextValue === BROWSER_CONNECTION_TYPE) {
      selectedAiModel.value.provider = '浏览器 AI'
      selectedAiModel.value.browser_provider = firstText(selectedAiModel.value.browser_provider, 'chatgpt')
      selectedAiModel.value.browser_mode = firstText(selectedAiModel.value.browser_mode, BROWSER_MODE_MANAGED_PROFILE)
      selectedAiModel.value.model = ''
      selectedAiModel.value.model_options = []
      selectedAiModel.value.model_env = ''
      selectedAiModel.value.base_url = ''
      selectedAiModel.value.base_url_env = ''
      selectedAiModel.value.api_key = ''
      selectedAiModel.value.api_key_env = ''
      selectedAiModel.value.api_style = 'openai_compatible'
      selectedAiModel.value.cli_tool = ''
      selectedAiModel.value.command = ''
      selectedAiModel.value.profile = ''
      selectedAiModel.value.sandbox = ''
    } else {
      selectedAiModel.value.provider = previousConnectionType === API_CONNECTION_TYPE ? firstText(selectedAiModel.value.provider, 'OpenAI-Compatible') : 'OpenAI-Compatible'
    }
  }
  if (field === 'cli_tool' && typeof value === 'string') {
    selectedAiModel.value.provider = cliToolLabel(value)
    if (!firstText(selectedAiModel.value.command) || firstText(selectedAiModel.value.command) === previousCliDefaultCommand) {
      selectedAiModel.value.command = cliToolDefaultCommand(value)
    }
  }
  if (typeof value === 'string' && modelListDependencyFields.has(field)) {
    selectedAiModel.value.model = ''
    selectedAiModel.value.model_options = []
  }
  if (typeof value === 'string' && capabilityProbeDependencyFields.has(field)) {
    if (desiredCapabilities.length) selectedAiModel.value.capabilities = []
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
  return asStringArray(model.capabilities)
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

function useCasePrompt(useCaseId: string): UnknownRecord {
  if (!aiUseCasePrompts.value[useCaseId]) aiUseCasePrompts.value[useCaseId] = {}
  return aiUseCasePrompts.value[useCaseId]
}

function useCasePromptField(useCaseId: string, field: string): string {
  return String(useCasePrompt(useCaseId)[field] || '')
}

function setUseCasePromptField(useCaseId: string, field: string, value: string) {
  if (aiControlsLocked.value) return
  aiUseCasePrompts.value[useCaseId] = {
    ...useCasePrompt(useCaseId),
    [field]: value,
  }
}

function hasCapability(capability: string): boolean {
  return selectedCapabilities().includes(capability)
}

function capabilityStateClass(capability: string): string {
  if (hasCapability(capability)) return 'border-primary-300 bg-primary-50 text-primary-800 dark:border-primary-500/50 dark:bg-primary-500/10 dark:text-primary-100'
  return 'border-accent-200 bg-white text-accent-900 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-100'
}

function capabilitySelection(model: UnknownRecord | null): string[] {
  if (!model) return []
  return asStringArray(model.capabilities)
}

function capabilityProbePayload(capability: string): UnknownRecord {
  const base: UnknownRecord = {
    probe_only_capability: capability,
    probe_capabilities: true,
  }
  if (capability === 'chat') {
    base.probe_messages = [
      { role: 'system', content: 'Reply with ok.' },
      { role: 'user', content: 'ok' },
    ]
  } else if (capability === 'json') {
    base.probe_messages = [
      { role: 'system', content: 'Return JSON only.' },
      { role: 'user', content: 'Return {"ok":true}.' },
    ]
  } else if (capability === 'web_search') {
    const probeDate = chinaTodayIso()
    base.probe_messages = [
      {
        role: 'system',
        content: '必须使用实时联网或搜索能力查询当前天气，不要凭记忆回答；只返回 JSON。',
      },
      {
        role: 'user',
        content: `请使用当前会话可用的实时联网或搜索能力，查询中国四川省成都市在 ${probeDate} 的当前天气。只有在已经实时查询成功时，返回 {"can_access_web": true, "source_url": "...", "location": "成都", "date": "${probeDate}", "weather": "...", "temperature": "...", "evidence": "..."}。如果当前模型没有实时联网/搜索能力，或访问失败，返回 {"can_access_web": false, "reason": "..."}。`,
      },
    ]
  } else if (capability === 'image_generate') {
    base.probe_image_prompt = 'single small blue square'
    base.probe_messages = [
      {
        role: 'system',
        content: 'Use an available image generation tool if possible. Do not return SVG, ASCII art, or a textual description as a substitute. Return JSON only when possible.',
      },
      {
        role: 'user',
        content: 'Generate a small test image of a single blue square. After the image exists, return {"can_generate_image": true, "image_path": "file:///..."} or {"can_generate_image": true, "image_url": "https://..."} or {"can_generate_image": true, "image_base64": "...", "mime_type": "image/png"}. If unavailable, return {"can_generate_image": false, "reason": "..."}.',
      },
    ]
  } else if (capability === 'image_edit') {
    base.probe_image_prompt = 'turn the pixel blue'
  } else if (capability === 'tool_calling') {
    base.probe_messages = [{ role: 'user', content: 'Call the noop tool.' }]
  }
  return base
}

function openCapabilityProbe(capability: string) {
  capabilityProbeDialog.open = true
  capabilityProbeDialog.capability = capability
  capabilityProbeDialog.payloadText = JSON.stringify(capabilityProbePayload(capability), null, 2)
  capabilityProbeDialog.pending = false
  capabilityProbeDialog.error = ''
  capabilityProbeDialog.result = null
}

function closeCapabilityProbe() {
  if (capabilityProbeDialog.pending) return
  capabilityProbeDialog.open = false
  capabilityProbeDialog.capability = ''
  capabilityProbeDialog.payloadText = ''
  capabilityProbeDialog.error = ''
  capabilityProbeDialog.result = null
}

function addCapabilityToSelected(capability: string) {
  if (!selectedAiModel.value) return
  const current = new Set(selectedCapabilities())
  current.add(capability)
  selectedAiModel.value.capabilities = Array.from(current)
}

function setCapability(capability: string, checked: boolean) {
  if (!selectedAiModel.value) return
  if (aiControlsLocked.value) return
  if (checked && !hasCapability(capability)) {
    openCapabilityProbe(capability)
    return
  }
  const current = new Set(selectedCapabilities())
  if (checked) current.add(capability)
  else current.delete(capability)
  selectedAiModel.value.capabilities = Array.from(current)
  if (!modelHasImageCapability(selectedAiModel.value)) {
    selectedAiModel.value.quality = ''
    selectedAiModel.value.size = ''
  }
  lastAutoCapabilitySignature.value = ''
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
  if (normalizeConnectionType(model.connection_type) !== API_CONNECTION_TYPE) return ''
  return [
    String(model.id || ''),
    String(model.base_url || model.base_url_env || '').trim(),
    String(model.api_key || '').trim() || (model.api_key_configured ? 'saved' : ''),
  ].join('|')
}

function capabilitySignature(model: UnknownRecord | null): string {
  if (!model) return ''
  const connectionType = normalizeConnectionType(model.connection_type)
  if (connectionType === CLI_CONNECTION_TYPE) {
    return [
      String(model.id || ''),
      String(model.cli_tool || ''),
      String(model.command || ''),
      String(model.profile || ''),
      String(model.sandbox || ''),
      String(model.model || ''),
      allCapabilityValues.join(','),
    ].join('|')
  }
  if (connectionType === BROWSER_CONNECTION_TYPE) {
    return [
      String(model.id || ''),
      String(model.browser_provider || ''),
      String(model.browser_mode || ''),
      String(model.browser_profile || ''),
      String(model.browser_port || ''),
      String(model.browser_url || ''),
      String(model.model || ''),
      allCapabilityValues.join(','),
    ].join('|')
  }
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
  return model
}

function testSelectedAiModel() {
  if (!selectedAiModel.value) return
  requestAiModelCheck(false, '正在测试模型连接', 'manual_connection')
}

function requestAiModelCheck(probeCapabilities: boolean, message: string, trigger: string) {
  if (!selectedAiModel.value || aiControlsLocked.value) return
  aiRequestPending.value = true
  aiRequestMessage.value = message
  emit('testAi', { ...aiModelPayloadForCheck(probeCapabilities), probe_capabilities: probeCapabilities, test_trigger: trigger })
}

function confirmCapabilityProbe() {
  if (!selectedAiModel.value || aiControlsLocked.value || capabilityProbeDialog.pending) return
  const capability = capabilityProbeDialog.capability
  if (!capability) return
  let payload: UnknownRecord
  try {
    payload = asRecord(JSON.parse(capabilityProbeDialog.payloadText || '{}'))
  } catch {
    capabilityProbeDialog.error = '测试内容不是合法 JSON'
    return
  }
  const capabilities = new Set(selectedCapabilities())
  capabilities.add(capability)
  capabilityProbeDialog.pending = true
  capabilityProbeDialog.error = ''
  capabilityProbeDialog.result = null
  aiRequestPending.value = true
  aiRequestMessage.value = `正在测试 ${capabilityLabelByValue[capability] || capability}`
  emit('testAi', {
    ...aiModelPayloadForCheck(false),
    capabilities: Array.from(capabilities),
    ...payload,
    probe_only_capability: capability,
    probe_capabilities: true,
    test_trigger: 'capability_checkbox',
  })
}

function handleAiConfigFieldBlur(_field: 'base_url' | 'api_key' | 'model') {
  lastAutoModelListSignature.value = ''
}

function handleAiModelSelect(value: string) {
  setSelectedModelField('model', value)
}

function applyAiTestResult(result: AuthResult | null) {
  const raw = asRecord(result?.raw)
  if (raw.channel !== 'ai_model') return
  aiRequestPending.value = false
  aiRequestMessage.value = ''
  const capabilityResults = asRecord(raw.capability_results)
  const supported = new Set(asStringArray(raw.supported_capabilities))
  const pendingCapability = capabilityProbeDialog.capability
  const isPendingCapabilityProbe = Boolean(
    capabilityProbeDialog.open
    && pendingCapability
    && (capabilityProbeDialog.pending || String(raw.test_trigger || '') === 'capability_checkbox' || pendingCapability in capabilityResults || supported.has(pendingCapability)),
  )
  if (result?.ok === false) {
    lastAutoModelListSignature.value = ''
    lastAutoCapabilitySignature.value = ''
    if (isPendingCapabilityProbe) {
      capabilityProbeDialog.pending = false
      capabilityProbeDialog.error = result.error || result.message || '能力测试失败'
      capabilityProbeDialog.result = raw
    }
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
  if (isPendingCapabilityProbe) {
    capabilityProbeDialog.pending = false
    capabilityProbeDialog.result = raw
    const capabilityResult = asRecord(capabilityResults[pendingCapability])
    if (supported.has(pendingCapability) && capabilityResult.ok !== false) {
      addCapabilityToSelected(pendingCapability)
      lastAutoCapabilitySignature.value = capabilitySignature(target)
      closeCapabilityProbe()
      return
    }
    capabilityProbeDialog.error = String(capabilityResult.error || raw.error || result?.error || '能力测试未通过')
    capabilityProbeDialog.result = raw
  }
}

watch(() => props.lastResult, applyAiTestResult, { deep: true })
watch(() => props.loading, (loading) => {
  if (!loading) {
    aiRequestPending.value = false
    aiRequestMessage.value = ''
    if (capabilityProbeDialog.open && capabilityProbeDialog.pending) {
      capabilityProbeDialog.pending = false
      capabilityProbeDialog.error = capabilityProbeDialog.error || '测试已结束，但没有收到完整的能力测试结果。请查看最近 AI 测试结果后重试。'
    }
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

function handleYunexpressEnvironmentChange(value: string) {
  form.yunexpressEnvironment = value
  form.yunexpressBaseUrl = value === 'production'
    ? 'https://openapi.yunexpress.cn'
    : 'https://openapi-sbx.yunexpress.cn'
}
</script>

<template>
  <div class="space-y-6">
    <div v-if="capabilityProbeDialog.open" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm">
      <div class="w-full max-w-3xl rounded-lg border border-accent-200 bg-white p-4 shadow-xl dark:border-dark-700 dark:bg-dark-900">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">测试 {{ capabilityProbeLabel }}</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">确认成功后会启用该能力。</p>
          </div>
          <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="capabilityProbeDialog.pending" @click="closeCapabilityProbe">关闭</button>
        </div>
        <label class="mt-4 block">
          <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">测试内容</span>
          <textarea
            class="input min-h-72 font-mono text-xs"
            :value="capabilityProbeDialog.payloadText"
            :disabled="capabilityProbeDialog.pending"
            spellcheck="false"
            @input="capabilityProbeDialog.payloadText = eventText($event)"
          ></textarea>
        </label>
        <div v-if="capabilityProbeDialog.error" class="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-100">
          {{ capabilityProbeDialog.error }}
        </div>
        <pre v-if="capabilityProbeDialog.result" class="mt-3 max-h-56 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(capabilityProbeDialog.result, null, 2) }}</pre>
        <div class="mt-4 flex flex-wrap justify-end gap-2">
          <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="capabilityProbeDialog.pending" @click="closeCapabilityProbe">取消</button>
          <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="capabilityProbeDialog.pending" @click="confirmCapabilityProbe">
            {{ capabilityProbeDialog.pending ? '测试中' : '确定测试' }}
          </button>
        </div>
      </div>
    </div>

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
              <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="aiControlsLocked || !selectedAiModelReady" :title="selectedAiModelReady ? '测试模型连接' : selectedAiHint" @click="testSelectedAiModel">测试当前模型</button>
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
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">接入方式</span>
                  <select class="input" :value="modelField('connection_type', 'api')" :disabled="aiControlsLocked" @change="setSelectedModelField('connection_type', eventText($event))">
                    <option v-for="option in connectionTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </label>
                <input v-if="selectedAiModelIsApi" class="input" :value="modelField('provider')" :disabled="aiControlsLocked" placeholder="Provider，例如 DeepSeek / OpenAI" @input="setSelectedModelField('provider', eventText($event))" />
                <label v-if="selectedAiModelIsApi" class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API 风格</span>
                  <select class="input" :value="modelField('api_style', 'openai_compatible')" :disabled="aiControlsLocked" @change="setSelectedModelField('api_style', eventText($event))">
                    <option value="openai_compatible">OpenAI-Compatible Chat</option>
                    <option value="openai_responses">OpenAI Responses</option>
                  </select>
                </label>
                <label v-else-if="selectedAiModelIsCli" class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">CLI 工具</span>
                  <select class="input" :value="modelField('cli_tool', 'codex')" :disabled="aiControlsLocked" @change="setSelectedModelField('cli_tool', eventText($event))">
                    <option v-for="tool in cliToolOptions" :key="tool.value" :value="tool.value">{{ tool.label }}{{ tool.installed ? '（已安装）' : '' }}</option>
                  </select>
                </label>
                <label v-else class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">网页 Provider</span>
                  <input class="input" :value="modelField('browser_provider')" :disabled="aiControlsLocked" placeholder="例如 chatgpt / claude / gemini" @input="setSelectedModelField('browser_provider', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型质量档位</span>
                  <select class="input" :value="modelField('quality_level', 'balanced')" :disabled="aiControlsLocked" @change="setSelectedModelField('quality_level', eventText($event))">
                    <option v-for="option in modelQualityOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </label>
                <template v-if="selectedAiModelIsApi">
                  <input class="input" :value="modelField('base_url')" :disabled="aiControlsLocked" placeholder="Base URL" @input="setSelectedModelField('base_url', eventText($event))" @blur="handleAiConfigFieldBlur('base_url')" />
                  <input class="input md:col-span-2" :value="modelField('api_key')" :disabled="aiControlsLocked" :placeholder="apiKeyPlaceholder()" autocomplete="off" spellcheck="false" @input="setSelectedModelField('api_key', eventText($event))" @blur="handleAiConfigFieldBlur('api_key')" />
                  <label class="block md:col-span-2">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</span>
                    <select class="input" :value="modelField('model')" :disabled="aiControlsLocked || !selectedModelOptions.length" @change="handleAiModelSelect(eventText($event))">
                      <option value="">{{ selectedModelOptions.length ? '请选择模型' : '填写 URL 和 API Key 后自动加载' }}</option>
                      <option v-for="option in selectedModelOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
                    </select>
                  </label>
                </template>
                <template v-else-if="selectedAiModelIsCli">
                  <input class="input" :value="modelField('command')" :disabled="aiControlsLocked" placeholder="CLI 命令，例如 codex" @input="setSelectedModelField('command', eventText($event))" />
                  <input class="input" :value="modelField('model')" :disabled="aiControlsLocked" placeholder="模型，可留空使用 CLI 默认" @input="setSelectedModelField('model', eventText($event))" @blur="handleAiConfigFieldBlur('model')" />
                  <div class="md:col-span-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-600 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300">
                    {{ selectedCliStatusText }}
                  </div>
                </template>
                <template v-else>
                  <input class="input" :value="modelField('model')" :disabled="aiControlsLocked" placeholder="网页模型，可选" @input="setSelectedModelField('model', eventText($event))" />
                  <div class="md:col-span-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-600 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300">
                    将打开独立的浏览器 Profile；首次使用请在浏览器窗口手动登录，之后复用本机登录态。
                  </div>
                </template>
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
                  <div v-if="selectedAiModelIsApi" class="mt-3 grid gap-3 md:grid-cols-2">
                    <input class="input" :value="modelField('base_url_env')" :disabled="aiControlsLocked" placeholder="Base URL 环境变量名，可选" @input="setSelectedModelField('base_url_env', eventText($event))" />
                    <input class="input" :value="modelField('api_key_env')" :disabled="aiControlsLocked" placeholder="API Key 环境变量名，可选" @input="setSelectedModelField('api_key_env', eventText($event))" />
                  </div>
                  <div v-else-if="selectedAiModelIsCli" class="mt-3 grid gap-3 md:grid-cols-2">
                    <input class="input" :value="modelField('profile')" :disabled="aiControlsLocked" placeholder="CLI Profile，可选" @input="setSelectedModelField('profile', eventText($event))" />
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Sandbox</span>
                      <select class="input" :value="modelField('sandbox', 'read-only')" :disabled="aiControlsLocked" @change="setSelectedModelField('sandbox', eventText($event))">
                        <option v-for="option in cliSandboxOptions" :key="option" :value="option">{{ option }}</option>
                      </select>
                    </label>
                  </div>
                  <div v-else class="mt-3 grid gap-3 md:grid-cols-2">
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">浏览器模式</span>
                      <select class="input" :value="modelField('browser_mode', BROWSER_MODE_MANAGED_PROFILE)" :disabled="aiControlsLocked" @change="setSelectedModelField('browser_mode', eventText($event))">
                        <option v-for="option in browserModeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                      </select>
                    </label>
                    <input class="input" :value="modelField('browser_profile')" :disabled="aiControlsLocked" placeholder="Profile 名称，可留空 default" @input="setSelectedModelField('browser_profile', eventText($event))" />
                    <input class="input" :value="modelField('browser_port')" :disabled="aiControlsLocked" placeholder="调试端口，可选" @input="setSelectedModelField('browser_port', eventText($event))" />
                    <input class="input" :value="modelField('browser_url')" :disabled="aiControlsLocked" placeholder="自定义网页 URL，可选" @input="setSelectedModelField('browser_url', eventText($event))" />
                  </div>
                </details>
              </div>

              <div class="flex flex-wrap gap-2">
                <label
                  v-for="capability in capabilityOptions"
                  :key="capability.value"
                  class="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition"
                  :class="[capabilityStateClass(capability.value), 'cursor-pointer hover:border-primary-200 hover:bg-primary-50/50 dark:hover:border-primary-500/40 dark:hover:bg-primary-500/10']"
                >
                  <input type="checkbox" :checked="hasCapability(capability.value)" :disabled="aiControlsLocked" @change="setCapability(capability.value, eventChecked($event))" />
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
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">功能选择指定模型，并编辑该功能使用的 Prompt 文件。</p>
            </div>
            <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="emit('saveAi', aiPayload())">保存功能绑定</button>
          </div>
          <div v-if="globalPromptUseCases.length" class="mt-4 grid gap-3 xl:grid-cols-2">
            <div v-for="useCase in globalPromptUseCases" :key="String(useCase.id)" class="rounded-lg border border-accent-200 bg-white p-3 dark:border-dark-700 dark:bg-dark-900">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <span class="block text-sm font-semibold text-accent-950 dark:text-white">{{ useCase.label || useCase.id }}</span>
                  <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">需要：{{ useCaseCapabilityText(useCase) }}</span>
                </div>
                <span class="badge-muted">{{ useCase.id }}</span>
              </div>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</span>
                <select class="input" :value="aiUseCaseBindings[String(useCase.id || '')] || ''" @change="setUseCaseBinding(String(useCase.id || ''), eventText($event))">
                  <option value="">自动匹配</option>
                  <option v-for="model in compatibleModelsForUseCase(useCase)" :key="String(model.id)" :value="String(model.id)">{{ model.name || model.id }}</option>
                </select>
              </label>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Prompt JSON 文件</span>
                <input class="input font-mono text-xs" :value="useCasePromptField(String(useCase.id || ''), 'path')" @input="setUseCasePromptField(String(useCase.id || ''), 'path', eventText($event))" />
              </label>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">说明</span>
                <textarea class="input min-h-16 resize-y text-sm leading-5" :value="useCasePromptField(String(useCase.id || ''), 'description')" @input="setUseCasePromptField(String(useCase.id || ''), 'description', eventText($event))" />
              </label>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">System Prompt</span>
                <textarea class="input min-h-24 resize-y font-mono text-xs leading-5" :value="useCasePromptField(String(useCase.id || ''), 'system_prompt')" spellcheck="false" @input="setUseCasePromptField(String(useCase.id || ''), 'system_prompt', eventText($event))" />
              </label>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">User Prompt</span>
                <textarea class="input min-h-40 resize-y font-mono text-xs leading-5" :value="useCasePromptField(String(useCase.id || ''), 'user_prompt')" spellcheck="false" @input="setUseCasePromptField(String(useCase.id || ''), 'user_prompt', eventText($event))" />
              </label>
            </div>
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
              <h3 class="font-semibold text-accent-950 dark:text-white">采集、核价与物流 API</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">维护汇率服务、1688 采集 API 和云途物流 API。</p>
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

            <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70 xl:col-span-2">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 class="font-semibold text-accent-950 dark:text-white">云途物流 API</h4>
                  <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">用于发货时创建云途订单、获取运单号、面单和后续轨迹。</p>
                </div>
                <button class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !yunexpressApiReady" :title="yunexpressApiReady ? '' : yunexpressApiHint" @click="emit('testApi', 'yunexpress', aiPayload().yunexpress as UnknownRecord)">测试 token</button>
              </div>
              <div class="mt-3 grid gap-3 md:grid-cols-2">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">环境</span>
                  <select :value="form.yunexpressEnvironment" class="input" @change="handleYunexpressEnvironmentChange(eventText($event))">
                    <option value="sandbox">沙盒 UAT</option>
                    <option value="production">正式环境</option>
                  </select>
                </label>
                <input v-model="form.yunexpressBaseUrl" class="input font-mono text-xs" placeholder="Base URL，例如 https://openapi-sbx.yunexpress.cn" />
                <input v-model="form.yunexpressAppId" class="input" placeholder="App ID" autocomplete="off" spellcheck="false" />
                <input v-model="form.yunexpressAppSecret" type="password" class="input" placeholder="App Secret / 应用秘钥" autocomplete="off" spellcheck="false" />
                <input v-model="form.yunexpressSourceKey" type="password" class="input" placeholder="SourceKey" autocomplete="off" spellcheck="false" />
                <input v-model="form.yunexpressProductCode" class="input" placeholder="默认物流产品编码，例如 S1002" />
                <input v-model="form.yunexpressSourceCode" class="input" placeholder="订单来源代码，可选" />
                <input v-model="form.yunexpressPlatformAccountCode" class="input" placeholder="平台子账号代码，可选" />
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">面单类型</span>
                  <select v-model="form.yunexpressLabelType" class="input">
                    <option value="PDF">PDF</option>
                    <option value="PNG">PNG</option>
                    <option value="ZPL">ZPL</option>
                  </select>
                </label>
                <div class="grid gap-3 sm:grid-cols-3">
                  <select v-model="form.yunexpressWeightUnit" class="input">
                    <option value="KG">KG</option>
                    <option value="G">G</option>
                    <option value="LBS">LBS</option>
                  </select>
                  <select v-model="form.yunexpressSizeUnit" class="input">
                    <option value="CM">CM</option>
                    <option value="INCH">INCH</option>
                  </select>
                  <input v-model="form.yunexpressTimeoutSeconds" class="input" placeholder="超时秒数" />
                </div>
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
          <ProductResearchSettingsPanel :ai-models="aiModels" :ai-use-case-prompts="aiUseCasePrompts" embedded />
        </section>
      </div>
    </section>
  </div>
</template>
