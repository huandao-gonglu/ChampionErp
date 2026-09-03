<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import ProductResearchSettingsPanel from '@/components/auth/ProductResearchSettingsPanel.vue'
import { jsonProbeMessages } from '@/constants/aiCapabilityProbe'
import type { AuthResult, Marketplace, MarketplaceOption, MercadoLibreAuthChecklist, MercadoLibreTestMode, UnknownRecord } from '@/types/workflow'
import {
  MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE,
  mercadoLibreAccountSiteId,
  mercadoLibreHasFullyManagedBinding,
  mercadoLibreListingModel as resolveMercadoLibreListingModel,
  mercadoLibreMarketplaceBindings,
  type MercadoLibreMarketplaceBinding,
} from '@/utils/mercadolibreGlobalSelling'

const DEFAULT_ML_REDIRECT_URI = 'https://example.com/callback'

const props = defineProps<{
  appConfig: UnknownRecord
  aiConfig: UnknownRecord
  storeConfig: UnknownRecord
  storeAuthSummary: UnknownRecord
  platformOptions: MarketplaceOption[]
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
  saveCurrency: [platform: Marketplace, currency: string]
  testAuth: [platform: Marketplace, scope?: string, config?: UnknownRecord]
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
  yandexApiToken: '',
  yandexCampaignId: '',
  ozonClientId: '',
  ozonApiKey: '',
})

const selectedStorePlatform = ref<Marketplace>('mercadolibre')
type AuthSettingsTab = 'ai_models' | 'ai_bindings' | 'stores' | 'apis' | 'research'
const activeAuthSettingsTab = ref<AuthSettingsTab>('ai_models')
const selectedAiModelIndex = ref(0)
const aiModels = ref<UnknownRecord[]>([])
const aiUseCaseBindings = ref<Record<string, UnknownRecord>>({})
const aiUseCasePrompts = ref<Record<string, UnknownRecord>>({})
const API_CONNECTION_TYPE = 'api'
const CLI_CONNECTION_TYPE = 'cli'
const BROWSER_CONNECTION_TYPE = 'browser'
const BROWSER_MODE_MANAGED_PROFILE = 'managed_profile'
const BROWSER_MODE_EXISTING_BROWSER = 'existing_browser'
const PROVIDER_ID_OPENAI = 'openai'
const PROVIDER_ID_DEEPSEEK = 'deepseek'
const PROVIDER_ID_ALIBABA = 'alibaba'
const fallbackProviders = [
  { id: PROVIDER_ID_OPENAI, label: 'OpenAI', description: 'OpenAI 服务预设；代理可修改 Base URL，API 协议可选 Chat Completions 或 Responses', provider_family: 'openai', default_base_url: 'https://api.openai.com/v1', default_api_style: 'openai_responses', supported_api_styles: ['openai_compatible', 'openai_responses'], base_url_editable: true },
  { id: PROVIDER_ID_DEEPSEEK, label: 'DeepSeek', description: '使用 Pydantic AI 的 DeepSeekProvider', provider_family: 'generic_openai', default_base_url: 'https://api.deepseek.com', default_api_style: 'openai_compatible', supported_api_styles: ['openai_compatible'], base_url_editable: false },
  { id: PROVIDER_ID_ALIBABA, label: '阿里云百炼 / Qwen', description: '使用 Pydantic AI 的 AlibabaProvider', provider_family: 'alibaba', default_base_url: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', default_api_style: 'openai_compatible', supported_api_styles: ['openai_compatible', 'openai_responses'], base_url_editable: true },
]
const modelListDependencyFields = new Set(['provider_id', 'api_style', 'base_url', 'base_url_env', 'api_key', 'api_key_env'])
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
  originalCapabilities: [] as string[],
})
const customRequestBodyEditor = reactive({
  modelId: '',
  text: '',
  error: '',
})

const storePlatforms = computed(() => props.platformOptions.map((platform) => ({
  key: platform.key,
  label: platform.label,
  subtitle: `${platform.sites.map((site) => `${site.label}（${site.code}）`).join('、')}共用同一套授权`,
})))

const authSettingsTabs: Array<{ key: AuthSettingsTab; label: string; summary: string }> = [
  { key: 'ai_models', label: 'AI 模型', summary: '配置模型、能力和连接测试' },
  { key: 'ai_bindings', label: '功能绑定', summary: '模型和功能 Prompt' },
  { key: 'stores', label: '店铺授权', summary: '按一级平台保存，子站点共用凭证' },
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
const fallbackImageQualityValues = ['auto', 'low', 'medium', 'high']
const imageQualityLabels: Record<string, string> = {
  auto: '自动',
  low: '低',
  medium: '中',
  high: '高',
}
const reasoningEffortLabels: Record<string, string> = {
  minimal: '最小（minimal）',
  low: '低（low）',
  medium: '中（medium）',
  high: '高（high）',
  xhigh: '极高（xhigh）',
  max: '最大（max）',
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

function normalizeConnectionType(value: unknown): string {
  const text = firstText(value).toLowerCase()
  if (text === CLI_CONNECTION_TYPE) return CLI_CONNECTION_TYPE
  if (text === BROWSER_CONNECTION_TYPE) return BROWSER_CONNECTION_TYPE
  return API_CONNECTION_TYPE
}

function normalizeProviderId(value: unknown): string {
  return firstText(value).toLowerCase()
}

function providerLabel(providerId: string): string {
  if (providerId === PROVIDER_ID_OPENAI) return 'OpenAI'
  if (providerId === PROVIDER_ID_DEEPSEEK) return 'DeepSeek'
  if (providerId === PROVIDER_ID_ALIBABA) return '阿里云百炼 / Qwen'
  return providerId || '未选择服务商'
}

function providerDefaultApiStyle(providerId: string): string {
  return providerId === PROVIDER_ID_OPENAI ? 'openai_responses' : 'openai_compatible'
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

function normalizeCapabilityProfiles(value: unknown): UnknownRecord {
  const source = asRecord(value)
  const profiles: UnknownRecord = {}
  for (const capability of allCapabilityValues) {
    const profile = asRecord(source[capability])
    if (!Object.keys(profile).length) continue
    profiles[capability] = JSON.parse(JSON.stringify(profile)) as UnknownRecord
  }
  return profiles
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
  const providerId = normalizeProviderId(record.provider_id)
  return {
    id: firstText(record.id, fallbackId),
    name: firstText(record.name, record.id, `AI 模型 ${index + 1}`),
    connection_type: connectionType,
    provider: connectionType === CLI_CONNECTION_TYPE
      ? firstText(record.provider, cliToolLabel(cliTool))
      : connectionType === BROWSER_CONNECTION_TYPE
        ? firstText(record.provider, '浏览器 AI')
        : providerLabel(providerId),
    provider_id: connectionType === API_CONNECTION_TYPE ? providerId : '',
    api_style: firstText(record.api_style, providerDefaultApiStyle(providerId)),
    base_url: firstText(record.base_url),
    base_url_env: firstText(record.base_url_env),
    api_key: firstText(record.api_key),
    api_key_env: firstText(record.api_key_env),
    api_key_masked: firstText(record.api_key_masked),
    model,
    model_env: firstText(record.model_env),
    quality: firstText(record.quality),
    size: firstText(record.size),
    timeout_seconds: firstText(record.timeout_seconds),
    capabilities: asStringArray(record.capabilities),
    capability_profiles: normalizeCapabilityProfiles(record.capability_profiles),
    cli_tool: cliTool,
    command: firstText(record.command, record.cli_command, connectionType === CLI_CONNECTION_TYPE ? cliToolDefaultCommand(cliTool) : ''),
    profile: firstText(record.profile, record.cli_profile),
    sandbox: firstText(record.sandbox, 'read-only'),
    browser_provider: firstText(record.browser_provider, record.browserProvider),
    browser_mode: firstText(record.browser_mode, record.browserMode, BROWSER_MODE_MANAGED_PROFILE),
    browser_profile: firstText(record.browser_profile, record.browserProfile),
    browser_port: firstText(record.browser_port, record.browserPort),
    browser_url: firstText(record.browser_url, record.browserUrl),
    extra: asRecord(record.extra),
    enabled: record.enabled !== false,
    api_key_configured: Boolean(record.api_key_configured),
    model_options: modelOptions,
    generation_capabilities: JSON.parse(JSON.stringify(asRecord(record.generation_capabilities))) as UnknownRecord,
  }
}

function defaultAiModelRow(index: number): UnknownRecord {
  return normalizeAiModelRow({
    id: `ai_model_${Date.now()}_${index + 1}`,
    name: `AI 模型 ${index + 1}`,
    connection_type: API_CONNECTION_TYPE,
    provider: 'OpenAI',
    provider_id: PROVIDER_ID_OPENAI,
    api_style: 'openai_responses',
    base_url: 'https://api.openai.com/v1',
    api_key: '',
    model: '',
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

function normalizeUseCaseBindings(value: unknown): Record<string, UnknownRecord> {
  const record = asRecord(value)
  const result: Record<string, UnknownRecord> = {}
  for (const [key, raw] of Object.entries(record)) {
    const item = asRecord(raw)
    const modelId = firstText(item.model_id, typeof raw === 'string' ? raw : '')
    const timeoutOverrideSeconds = firstText(item.timeout_override_seconds)
    const generation = asRecord(item.generation)
    const reasoning = asRecord(generation.reasoning)
    const normalizedGeneration: UnknownRecord = {
      temperature: firstText(generation.temperature),
      max_output_tokens: firstText(generation.max_output_tokens),
      reasoning: {
        mode: firstText(reasoning.mode),
        effort: firstText(reasoning.effort),
        budget_tokens: firstText(reasoning.budget_tokens),
      },
    }
    const hasGeneration = Boolean(
      firstText(normalizedGeneration.temperature, normalizedGeneration.max_output_tokens)
      || firstText(asRecord(normalizedGeneration.reasoning).mode, asRecord(normalizedGeneration.reasoning).effort, asRecord(normalizedGeneration.reasoning).budget_tokens),
    )
    if (key && (modelId || timeoutOverrideSeconds || hasGeneration)) {
      result[key] = {
        model_id: modelId,
        timeout_override_seconds: timeoutOverrideSeconds,
        generation: normalizedGeneration,
      }
    }
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
  const yandex = asRecord(props.storeConfig.yandex)
  const ozon = asRecord(props.storeConfig.ozon)
  const modelRows = Array.isArray(props.aiConfig.ai_models) ? props.aiConfig.ai_models : Array.isArray(props.appConfig.ai_models) ? props.appConfig.ai_models : []
  aiModels.value = modelRows.length ? modelRows.map((item, index) => normalizeAiModelRow(displayAiModelRecord(item), index)) : [defaultAiModelRow(0)]
  selectedAiModelIndex.value = Math.min(selectedAiModelIndex.value, Math.max(aiModels.value.length - 1, 0))
  aiUseCaseBindings.value = normalizeUseCaseBindings(props.aiConfig.ai_use_case_bindings || props.appConfig.ai_use_case_bindings)
  aiUseCasePrompts.value = normalizeUseCasePrompts(props.aiConfig.ai_use_case_prompts || props.appConfig.ai_use_case_prompts)
  form.exchangeRateApiUrl = firstText(pricing.exchange_rate_api_url, 'https://open.er-api.com/v6/latest/USD')
  form.exchangeRateTimeoutSeconds = firstText(pricing.exchange_rate_timeout_seconds, '10')
  form.exchangeRateCacheTtlSeconds = firstText(pricing.exchange_rate_cache_ttl_seconds, '3600')
  form.alibabaAppKey = ''
  form.alibabaAppSecret = ''
  form.alibabaAccessToken = ''
  form.alibabaApiBaseUrl = firstText(alibabaApi.base_url, form.alibabaApiBaseUrl)
  form.alibabaApiMethod = firstText(alibabaApi.method, form.alibabaApiMethod)
  form.alibabaApiVersion = firstText(alibabaApi.api_version, form.alibabaApiVersion)
  form.alibabaApiTimeoutSeconds = firstText(alibabaApi.timeout_seconds, form.alibabaApiTimeoutSeconds)
  form.yunexpressEnvironment = firstText(yunexpress.environment, form.yunexpressEnvironment)
  form.yunexpressBaseUrl = firstText(yunexpress.base_url, form.yunexpressBaseUrl)
  form.yunexpressAppId = ''
  form.yunexpressAppSecret = ''
  form.yunexpressSourceKey = ''
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
  form.yandexApiToken = String(yandex.api_token || '')
  form.yandexCampaignId = String(yandex.campaign_id || '')
  form.ozonClientId = String(ozon.client_id || '')
  form.ozonApiKey = String(ozon.api_key || '')
}

watch(() => [props.appConfig, props.aiConfig, props.storeConfig], fillFromProps, { immediate: true, deep: true })

const selectedAiModel = computed(() => aiModels.value[selectedAiModelIndex.value] || null)
const aiUseCases = computed(() => Array.isArray(props.aiConfig.ai_use_cases) ? props.aiConfig.ai_use_cases.map(asRecord) : [])
const cliToolOptions = computed(() => normalizeCliToolOptions(props.aiConfig.cli_tools))
const providerOptions = computed(() => {
  const configuredItems = Array.isArray(props.aiConfig.providers) ? props.aiConfig.providers : []
  const rawItems = configuredItems.length ? configuredItems : fallbackProviders
  return rawItems.map((item) => {
    const record = asRecord(item)
    return {
      id: firstText(record.id, record.value),
      label: firstText(record.label, record.id, record.value),
      description: firstText(record.description),
      providerFamily: firstText(record.provider_family, 'generic_openai'),
      defaultBaseUrl: firstText(record.default_base_url),
      defaultApiStyle: firstText(record.default_api_style, 'openai_compatible'),
      supportedApiStyles: asStringArray(record.supported_api_styles).length ? asStringArray(record.supported_api_styles) : ['openai_compatible'],
      baseUrlEditable: record.base_url_editable !== false,
      modelDiscovery: firstText(record.model_discovery, 'manual'),
    }
  }).filter((item) => item.id)
})

function aiModelProviderLabel(model: UnknownRecord): string {
  const connectionType = normalizeConnectionType(model.connection_type)
  if (connectionType === CLI_CONNECTION_TYPE) {
    return cliToolLabel(firstText(model.cli_tool, 'codex'))
  }
  if (connectionType === BROWSER_CONNECTION_TYPE) return '浏览器 AI'
  const providerId = normalizeProviderId(model.provider_id)
  return providerOptions.value.find((item) => item.id === providerId)?.label
    || providerLabel(providerId)
}
const globalPromptUseCases = computed(() => aiUseCases.value.filter((useCase) => {
  const id = String(useCase.id || '')
  return id && Boolean(aiUseCasePrompts.value[id])
}))
const activeAuthSettingsTabMeta = computed(() => authSettingsTabs.find((tab) => tab.key === activeAuthSettingsTab.value) || authSettingsTabs[0])
const capabilityLabelByValue = Object.fromEntries(capabilityOptions.map((item) => [item.value, item.label]))
const selectedAiModelConnectionType = computed(() => normalizeConnectionType(modelField('connection_type', API_CONNECTION_TYPE)))
const selectedAiModelIsApi = computed(() => selectedAiModelConnectionType.value === API_CONNECTION_TYPE)
const selectedAiModelIsCli = computed(() => selectedAiModelConnectionType.value === CLI_CONNECTION_TYPE)
const selectedProviderSpec = computed(() => providerOptions.value.find((item) => item.id === modelField('provider_id')))
const selectedProviderApiStyleOptions = computed(() => selectedProviderSpec.value?.supportedApiStyles || ['openai_compatible'])
const selectedProviderBaseUrlEditable = computed(() => selectedProviderSpec.value?.baseUrlEditable !== false)
const selectedProviderSupportsModelDiscovery = computed(() => selectedProviderSpec.value?.modelDiscovery === 'openai_models')
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
  && selectedProviderSupportsModelDiscovery.value
  && String(selectedAiModel.value.base_url || selectedAiModel.value.base_url_env || '').trim()
  && (String(selectedAiModel.value.api_key || '').trim() || selectedAiModel.value.api_key_configured),
))
const aiControlsLocked = computed(() => props.loading || aiRequestPending.value)
const aiBlockingMessage = computed(() => aiRequestMessage.value || '正在检测 AI 配置，请稍候')
const selectedAiModelImageCapable = computed(() => modelHasImageCapability(selectedAiModel.value))
const exchangeRateReady = computed(() => Boolean(form.exchangeRateApiUrl.trim()))
const exchangeRateHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写汇率 API URL')
const savedAlibabaApi = computed(() => asRecord(props.appConfig['1688_api']))
const savedYunexpressApi = computed(() => asRecord(props.appConfig.yunexpress))
const savedAlibabaCredentialsReady = computed(() => (
  firstText(savedAlibabaApi.value.status) === '已配置'
  || Boolean(firstText(savedAlibabaApi.value.masked_app_key) && firstText(savedAlibabaApi.value.masked_app_secret))
))
const savedYunexpressCredentialsReady = computed(() => (
  firstText(savedYunexpressApi.value.status) === '已配置'
  || Boolean(
    firstText(savedYunexpressApi.value.masked_app_id)
    && firstText(savedYunexpressApi.value.masked_app_secret)
    && firstText(savedYunexpressApi.value.masked_source_key),
  )
))
const alibabaApiReady = computed(() => Boolean(
  form.alibabaApiBaseUrl.trim()
  && (
    savedAlibabaCredentialsReady.value
    || (form.alibabaAppKey.trim() && form.alibabaAppSecret.trim())
  )
))
const alibabaApiHint = computed(() => {
  if (props.loading) return '正在处理，请稍候'
  return savedAlibabaCredentialsReady.value
    ? '将使用已保存凭据；也可填写一组仅供本次请求使用的新凭据'
    : '请填写 1688 App Key、App Secret 和 API 请求地址'
})
const yunexpressApiReady = computed(() => Boolean(
  form.yunexpressBaseUrl.trim()
  && (
    savedYunexpressCredentialsReady.value
    || (
      form.yunexpressAppId.trim()
      && form.yunexpressAppSecret.trim()
      && form.yunexpressSourceKey.trim()
    )
  )
))
const yunexpressApiHint = computed(() => {
  if (props.loading) return '正在处理，请稍候'
  return savedYunexpressCredentialsReady.value
    ? '将使用已保存凭据；也可填写一组仅供本次请求使用的新凭据'
    : '请填写云途 App ID、App Secret、SourceKey 和 Base URL'
})
const alibabaAppKeyPlaceholder = computed(() => {
  const masked = firstText(savedAlibabaApi.value.masked_app_key)
  return masked ? `已配置 ${masked}；留空沿用` : 'App Key'
})
const alibabaAppSecretPlaceholder = computed(() => {
  const masked = firstText(savedAlibabaApi.value.masked_app_secret)
  return masked ? `已配置 ${masked}；留空沿用` : 'App Secret'
})
const alibabaAccessTokenPlaceholder = computed(() => {
  const masked = firstText(savedAlibabaApi.value.masked_access_token)
  return masked ? `已配置 ${masked}；留空沿用` : 'Access Token / Session，可选'
})
const yunexpressAppIdPlaceholder = computed(() => {
  const masked = firstText(savedYunexpressApi.value.masked_app_id)
  return masked ? `已配置 ${masked}；留空沿用` : 'App ID'
})
const yunexpressAppSecretPlaceholder = computed(() => {
  const masked = firstText(savedYunexpressApi.value.masked_app_secret)
  return masked ? `已配置 ${masked}；留空沿用` : 'App Secret / 应用秘钥'
})
const yunexpressSourceKeyPlaceholder = computed(() => {
  const masked = firstText(savedYunexpressApi.value.masked_source_key)
  return masked ? `已配置 ${masked}；留空沿用` : 'SourceKey'
})
const lastConfigResultChannel = computed(() => String(props.lastResult?.raw?.channel || ''))
const showApiConfigResult = computed(() => ['exchange_rate', '1688', 'yunexpress'].includes(lastConfigResultChannel.value))

function useCaseBindingGenerationPayload(binding: UnknownRecord): UnknownRecord | null {
  const generation = asRecord(binding.generation)
  const reasoning = asRecord(generation.reasoning)
  const payload: UnknownRecord = {}
  const temperature = firstText(generation.temperature)
  const maxOutputTokens = firstText(generation.max_output_tokens)
  const reasoningMode = firstText(reasoning.mode)
  const reasoningEffort = firstText(reasoning.effort)
  const reasoningBudgetTokens = firstText(reasoning.budget_tokens)
  if (temperature) payload.temperature = temperature
  if (maxOutputTokens) payload.max_output_tokens = maxOutputTokens
  if (reasoningMode || reasoningEffort || reasoningBudgetTokens) {
    payload.reasoning = {
      ...(reasoningMode ? { mode: reasoningMode } : {}),
      ...(reasoningEffort ? { effort: reasoningEffort } : {}),
      ...(reasoningBudgetTokens ? { budget_tokens: reasoningBudgetTokens } : {}),
    }
  }
  return Object.keys(payload).length ? payload : null
}

function aiPayload(): UnknownRecord {
  return {
    ai_models: aiModels.value.map((model, index) => {
      const row = normalizeAiModelRow(model, index)
      delete row.provider_family
      const copySourceId = firstText(model.copy_source_id)
      if (row.connection_type === API_CONNECTION_TYPE && copySourceId && !firstText(row.api_key)) row.copy_source_id = copySourceId
      if (!Object.keys(asRecord(row.capability_profiles)).length) delete row.capability_profiles
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
        delete row.provider_id
        delete row.browser_provider
        delete row.browser_mode
        delete row.browser_profile
        delete row.browser_port
        delete row.browser_url
        delete row.extra
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
        delete row.provider_id
        row.model_env = ''
        delete row.cli_tool
        delete row.command
        delete row.extra
        delete row.profile
        delete row.sandbox
      } else {
        const provider = providerOptions.value.find((item) => item.id === firstText(row.provider_id))
        row.provider = provider?.label || firstText(row.provider, row.provider_id)
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
      delete row.generation_capabilities
      return row
    }),
    ai_use_case_bindings: Object.fromEntries(
      Object.entries(aiUseCaseBindings.value)
        .filter(([useCaseId]) => globalPromptUseCases.value.some((useCase) => String(useCase.id || '') === useCaseId))
        .map(([useCaseId, binding]) => [
          useCaseId,
          {
            model_id: firstText(binding.model_id),
            timeout_override_seconds: firstText(binding.timeout_override_seconds),
            ...(useCaseBindingGenerationPayload(binding) ? { generation: useCaseBindingGenerationPayload(binding) } : {}),
          },
        ])
        .filter(([, binding]) => {
          const record = asRecord(binding)
          return Boolean(firstText(record.model_id, record.timeout_override_seconds) || Object.keys(asRecord(record.generation)).length)
        }),
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

function syncCustomRequestBodyEditor() {
  const modelId = firstText(selectedAiModel.value?.id)
  if (customRequestBodyEditor.modelId === modelId) return
  const extra = asRecord(selectedAiModel.value?.extra)
  const requestBody = asRecord(extra.request_body)
  customRequestBodyEditor.modelId = modelId
  customRequestBodyEditor.text = Object.keys(requestBody).length ? JSON.stringify(requestBody, null, 2) : ''
  customRequestBodyEditor.error = ''
}

function updateCustomRequestBodyText(value: string) {
  customRequestBodyEditor.text = value
  customRequestBodyEditor.error = ''
}

function clearCapabilityProofs(model: UnknownRecord) {
  model.capabilities = []
  model.capability_profiles = {}
  lastAutoCapabilitySignature.value = ''
}

function commitCustomRequestBody(): boolean {
  if (!selectedAiModelIsApi.value || !selectedAiModel.value) return true
  const text = customRequestBodyEditor.text.trim()
  const extra = { ...asRecord(selectedAiModel.value.extra) }
  const previousRequestBody = JSON.stringify(asRecord(extra.request_body))
  if (!text) {
    delete extra.request_body
    selectedAiModel.value.extra = extra
    customRequestBodyEditor.error = ''
    if (previousRequestBody !== '{}') clearCapabilityProofs(selectedAiModel.value)
    return true
  }
  try {
    const parsed: unknown = JSON.parse(text)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('根节点必须是 JSON 对象')
    }
    const forbiddenProtocolFields = new Set([
      'input',
      'instructions',
      'messages',
      'model',
      'parallel_tool_calls',
      'response_format',
      'stream',
      'text',
      'tool_choice',
      'tools',
    ])
    const forbidden = Object.keys(parsed).filter((key) => forbiddenProtocolFields.has(key)).sort()
    if (forbidden.length) {
      throw new Error(`不得覆盖 Pydantic 请求协议字段：${forbidden.join(', ')}`)
    }
    if (Object.keys(parsed).length) extra.request_body = parsed
    else delete extra.request_body
    selectedAiModel.value.extra = extra
    customRequestBodyEditor.error = ''
    if (previousRequestBody !== JSON.stringify(asRecord(parsed))) clearCapabilityProofs(selectedAiModel.value)
    return true
  } catch (error) {
    customRequestBodyEditor.error = error instanceof Error ? `自定义请求 JSON 无效：${error.message}` : '自定义请求 JSON 无效'
    return false
  }
}

function clearTransientPlatformApiCredentials() {
  form.alibabaAppKey = ''
  form.alibabaAppSecret = ''
  form.alibabaAccessToken = ''
  form.yunexpressAppId = ''
  form.yunexpressAppSecret = ''
  form.yunexpressSourceKey = ''
}

function emitAiSettingsAndClearTransientCredentials() {
  const payload = aiPayload()
  emit('saveAi', payload)
  clearTransientPlatformApiCredentials()
}

function saveAiSettings() {
  if (aiControlsLocked.value || !commitCustomRequestBody()) return
  emitAiSettingsAndClearTransientCredentials()
}

function saveApiSettings() {
  if (props.loading) return
  emitAiSettingsAndClearTransientCredentials()
}

function testAlibabaApi() {
  const payload = asRecord(aiPayload()['1688_api'])
  const testOfferId = form.alibabaTestOfferId
  emit('testApi', '1688', payload, testOfferId)
  form.alibabaAppKey = ''
  form.alibabaAppSecret = ''
  form.alibabaAccessToken = ''
}

function testYunexpressApi() {
  const payload = asRecord(aiPayload().yunexpress)
  emit('testApi', 'yunexpress', payload)
  form.yunexpressAppId = ''
  form.yunexpressAppSecret = ''
  form.yunexpressSourceKey = ''
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
  const nextValue = field === 'connection_type' && typeof value === 'string'
    ? normalizeConnectionType(value)
    : field === 'provider_id' && typeof value === 'string'
      ? firstText(value).toLowerCase()
      : value
  const previous = selectedAiModel.value[field]
  if (previous === nextValue) return
  const desiredCapabilities = capabilitySelection(selectedAiModel.value)
  const previousConnectionType = normalizeConnectionType(selectedAiModel.value.connection_type)
  const previousProviderId = normalizeProviderId(selectedAiModel.value.provider_id)
  const previousProviderSpec = providerOptions.value.find((item) => item.id === previousProviderId)
  const previousBaseUrl = firstText(selectedAiModel.value.base_url)
  const previousApiStyle = firstText(selectedAiModel.value.api_style, 'openai_compatible')
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
      selectedAiModel.value.provider_id = ''
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
      selectedAiModel.value.provider_id = ''
      selectedAiModel.value.cli_tool = ''
      selectedAiModel.value.command = ''
      selectedAiModel.value.profile = ''
      selectedAiModel.value.sandbox = ''
    } else {
      selectedAiModel.value.provider_id = previousConnectionType === API_CONNECTION_TYPE
        ? normalizeProviderId(selectedAiModel.value.provider_id)
        : PROVIDER_ID_OPENAI
      const apiProvider = providerOptions.value.find((item) => item.id === selectedAiModel.value?.provider_id)
      selectedAiModel.value.provider = apiProvider?.label || 'OpenAI'
      if (previousConnectionType !== API_CONNECTION_TYPE && apiProvider) {
        selectedAiModel.value.api_style = apiProvider.defaultApiStyle
        selectedAiModel.value.base_url = apiProvider.defaultBaseUrl
      }
    }
  }
  if (field === 'provider_id' && typeof nextValue === 'string') {
    const nextProvider = providerOptions.value.find((item) => item.id === nextValue)
    if (nextProvider) {
      selectedAiModel.value.provider = nextProvider.label
      if (
        previousApiStyle === previousProviderSpec?.defaultApiStyle
        || !nextProvider.supportedApiStyles.includes(firstText(selectedAiModel.value.api_style))
      ) {
        selectedAiModel.value.api_style = nextProvider.defaultApiStyle
      }
      if (!previousBaseUrl || previousBaseUrl === previousProviderSpec?.defaultBaseUrl) {
        selectedAiModel.value.base_url = nextProvider.defaultBaseUrl
      }
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
    if (desiredCapabilities.length || Object.keys(asRecord(selectedAiModel.value.capability_profiles)).length) {
      clearCapabilityProofs(selectedAiModel.value)
    }
    lastAutoModelListSignature.value = ''
  }
}

function selectedCapabilities(): string[] {
  return asStringArray(selectedAiModel.value?.capabilities)
}

function webSearchRequestMode(): string {
  const extra = asRecord(selectedAiModel.value?.extra)
  const fallback = modelField('provider_id') === PROVIDER_ID_ALIBABA ? 'enable_search' : 'web_search_options'
  const mode = firstText(extra.web_search_request_mode, fallback)
  return ['enable_search', 'web_search_options'].includes(mode) ? mode : fallback
}

function applyWebSearchRequestMode(mode: string) {
  if (!selectedAiModel.value) return
  const extra = { ...asRecord(selectedAiModel.value.extra) }
  if (mode === 'enable_search') delete extra.web_search_request_mode
  else extra.web_search_request_mode = mode
  selectedAiModel.value.extra = extra
  lastAutoCapabilitySignature.value = ''
}

function setWebSearchRequestMode(mode: string) {
  if (aiControlsLocked.value) return
  const previousMode = webSearchRequestMode()
  applyWebSearchRequestMode(mode)
  if (previousMode !== webSearchRequestMode() && selectedAiModel.value) {
    const capabilities = new Set(selectedCapabilities())
    capabilities.delete('web_search')
    selectedAiModel.value.capabilities = Array.from(capabilities)
    removeCapabilityProfile(selectedAiModel.value, 'web_search')
  }
}

watch(() => firstText(selectedAiModel.value?.id), syncCustomRequestBodyEditor, { immediate: true })

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
  return aiModels.value.filter((model) => modelSupportsUseCase(model, useCase))
}

function effectiveModelForUseCase(useCase: UnknownRecord): UnknownRecord | null {
  const useCaseId = firstText(useCase.id)
  const boundModelId = useCaseBindingField(useCaseId, 'model_id')
  const compatible = compatibleModelsForUseCase(useCase)
  if (boundModelId) return compatible.find((model) => firstText(model.id) === boundModelId) || null
  return compatible[0] || null
}

function derivedGenerationCapabilities(model: UnknownRecord | null): UnknownRecord {
  if (!model || normalizeConnectionType(model.connection_type) !== API_CONNECTION_TYPE) {
    return {
      status: 'unsupported',
      temperature: { status: 'unsupported' },
      max_output_tokens: { status: 'unsupported' },
      reasoning: { status: 'unsupported', modes: [], efforts: [], supports_budget_tokens: false, note: '当前连接没有统一生成参数通道。' },
    }
  }
  const providerId = normalizeProviderId(model.provider_id)
  const providerSpec = providerOptions.value.find((item) => item.id === providerId)
  const providerFamily = providerSpec?.providerFamily || 'generic_openai'
  const apiStyle = firstText(model.api_style, 'openai_compatible')
  const saved = asRecord(model.generation_capabilities)
  if (
    firstText(saved.provider_id) === providerId
    && firstText(saved.api_style) === apiStyle
  ) return saved
  const base: UnknownRecord = {
    status: 'supported',
    provider_id: providerId,
    api_style: apiStyle,
    temperature: { status: 'supported', minimum: 0, maximum: 2 },
    max_output_tokens: { status: 'supported', minimum: 1 },
  }
  if (providerFamily === 'generic_openai') {
    base.reasoning = { status: 'unknown', modes: [], efforts: [], supports_budget_tokens: false, note: '通用兼容接口没有统一的推理字段；请先选择准确厂商。' }
  } else if (providerFamily === 'openai') {
    base.reasoning = { status: 'supported', modes: ['disabled', 'enabled'], efforts: ['minimal', 'low', 'medium', 'high', 'xhigh'], supports_budget_tokens: false, note: '参数会按所选 API 协议转换，模型是否接受该强度由厂商校验。' }
  } else if (apiStyle === 'openai_responses') {
    base.reasoning = { status: 'supported', modes: ['disabled', 'enabled'], efforts: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'], supports_budget_tokens: false, note: 'Responses 使用 reasoning.effort；关闭推理转换为 effort=none。' }
  } else {
    base.reasoning = { status: 'supported', modes: ['disabled', 'enabled'], efforts: [], supports_budget_tokens: true, note: 'Chat Completions 使用 enable_thinking 和 thinking_budget。' }
  }
  return base
}

function useCaseGenerationCapabilities(useCase: UnknownRecord): UnknownRecord {
  return derivedGenerationCapabilities(effectiveModelForUseCase(useCase))
}

function useCaseReasoningCapabilities(useCase: UnknownRecord): UnknownRecord {
  return asRecord(useCaseGenerationCapabilities(useCase).reasoning)
}

function useCaseSupportsTextGeneration(useCase: UnknownRecord): boolean {
  return useCaseRequiredCapabilities(useCase).includes('chat')
}

function useCaseGenerationFieldSupported(useCase: UnknownRecord, field: 'temperature' | 'max_output_tokens'): boolean {
  return firstText(asRecord(useCaseGenerationCapabilities(useCase)[field]).status) === 'supported'
}

function useCaseReasoningSupported(useCase: UnknownRecord): boolean {
  return firstText(useCaseReasoningCapabilities(useCase).status) === 'supported'
}

function useCaseReasoningEfforts(useCase: UnknownRecord): string[] {
  return asStringArray(useCaseReasoningCapabilities(useCase).efforts)
}

function reasoningEffortLabel(effort: string): string {
  return reasoningEffortLabels[effort] || effort
}

function useCaseReasoningBudgetSupported(useCase: UnknownRecord): boolean {
  return Boolean(useCaseReasoningCapabilities(useCase).supports_budget_tokens)
}

function useCaseGenerationModelText(useCase: UnknownRecord): string {
  const model = effectiveModelForUseCase(useCase)
  if (!model) return '没有可用模型，生成参数不会生效'
  const providerId = normalizeProviderId(model.provider_id)
  const provider = providerOptions.value.find((item) => item.id === providerId)
  return `${firstText(model.name, model.id)} · ${provider?.label || providerId}`
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
  if (capability === 'json') {
    base.probe_messages = jsonProbeMessages()
  } else if (capability === 'web_search') {
    base.probe_messages = [
      {
        role: 'system',
        content: '必须调用实时联网或搜索能力查询天气，不要凭记忆回答；只返回 JSON。',
      },
      {
        role: 'user',
        content: '请使用当前会话可用的实时联网或搜索能力，查询中国四川省成都市此刻的实时天气。不要把当前日期理解成未来天气预报；请从实时搜索结果中取得信息对应的中国日期。只有在已经实时查询成功时，返回 {"can_access_web": true, "source_url": "...", "location": "成都", "date": "YYYY-MM-DD", "weather": "...", "temperature": "...", "evidence": "..."}。如果当前模型没有实时联网/搜索能力，或访问失败，返回 {"can_access_web": false, "reason": "..."}。',
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
    base.probe_image_prompt = 'Change the red image to blue while preserving its dimensions.'
  }
  return base
}

function openCapabilityProbe(capability: string) {
  const originalCapabilities = selectedCapabilities()
  if (selectedAiModel.value) selectedAiModel.value.capabilities = [...originalCapabilities]
  capabilityProbeDialog.open = true
  capabilityProbeDialog.capability = capability
  capabilityProbeDialog.payloadText = JSON.stringify(capabilityProbePayload(capability), null, 2)
  capabilityProbeDialog.pending = false
  capabilityProbeDialog.error = ''
  capabilityProbeDialog.originalCapabilities = originalCapabilities
}

function restoreCapabilityProbeSelection() {
  if (!selectedAiModel.value) return
  selectedAiModel.value.capabilities = [...capabilityProbeDialog.originalCapabilities]
}

function closeCapabilityProbe(restoreSelection = true) {
  if (capabilityProbeDialog.pending) return
  if (restoreSelection) restoreCapabilityProbeSelection()
  capabilityProbeDialog.open = false
  capabilityProbeDialog.capability = ''
  capabilityProbeDialog.payloadText = ''
  capabilityProbeDialog.error = ''
  capabilityProbeDialog.originalCapabilities = []
}

function addCapability(model: UnknownRecord, capability: string) {
  const current = new Set(asStringArray(model.capabilities))
  current.add(capability)
  model.capabilities = Array.from(current)
}

function saveCapabilityProfile(model: UnknownRecord, capability: string, value: unknown) {
  const profile = asRecord(value)
  if (!Object.keys(profile).length) return
  model.capability_profiles = {
    ...normalizeCapabilityProfiles(model.capability_profiles),
    [capability]: JSON.parse(JSON.stringify(profile)) as UnknownRecord,
  }
}

function removeCapabilityProfile(model: UnknownRecord, capability: string) {
  const profiles = normalizeCapabilityProfiles(model.capability_profiles)
  delete profiles[capability]
  model.capability_profiles = profiles
}

function setCapability(capability: string, checked: boolean, event?: Event) {
  if (!selectedAiModel.value) return
  if (aiControlsLocked.value) return
  if (checked && !hasCapability(capability)) {
    const input = event?.target
    if (input instanceof HTMLInputElement) input.checked = false
    openCapabilityProbe(capability)
    return
  }
  const current = new Set(selectedCapabilities())
  if (checked) current.add(capability)
  else {
    current.delete(capability)
    removeCapabilityProfile(selectedAiModel.value, capability)
  }
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
    capability_profiles: normalizeCapabilityProfiles(current.capability_profiles),
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
  for (const [useCaseId, binding] of Object.entries(aiUseCaseBindings.value)) {
    if (firstText(binding.model_id) === removedId) {
      aiUseCaseBindings.value[useCaseId] = {
        ...binding,
        model_id: '',
      }
    }
  }
  selectedAiModelIndex.value = Math.min(selectedAiModelIndex.value, aiModels.value.length - 1)
}

function useCaseBindingField(useCaseId: string, field: string): string {
  return firstText(aiUseCaseBindings.value[useCaseId]?.[field])
}

function setUseCaseBindingField(useCaseId: string, field: string, value: string) {
  if (aiControlsLocked.value) return
  const next = {
    ...asRecord(aiUseCaseBindings.value[useCaseId]),
    [field]: value,
  }
  if (!firstText(next.model_id, next.timeout_override_seconds) && !useCaseBindingGenerationPayload(next)) delete aiUseCaseBindings.value[useCaseId]
  else aiUseCaseBindings.value[useCaseId] = next
}

function useCaseGenerationField(useCaseId: string, field: string, reasoning = false): string {
  const generation = asRecord(aiUseCaseBindings.value[useCaseId]?.generation)
  return firstText(reasoning ? asRecord(generation.reasoning)[field] : generation[field])
}

function setUseCaseGenerationField(useCaseId: string, field: string, value: string, reasoning = false) {
  if (aiControlsLocked.value) return
  const binding = { ...asRecord(aiUseCaseBindings.value[useCaseId]) }
  const generation = { ...asRecord(binding.generation) }
  if (reasoning) {
    const nextReasoning = { ...asRecord(generation.reasoning), [field]: value }
    if (field === 'mode' && value !== 'enabled') {
      nextReasoning.effort = ''
      nextReasoning.budget_tokens = ''
    } else if (field !== 'mode' && value && !firstText(nextReasoning.mode)) {
      nextReasoning.mode = 'enabled'
    }
    generation.reasoning = nextReasoning
  } else {
    generation[field] = value
  }
  binding.generation = generation
  if (!firstText(binding.model_id, binding.timeout_override_seconds) && !useCaseBindingGenerationPayload(binding)) delete aiUseCaseBindings.value[useCaseId]
  else aiUseCaseBindings.value[useCaseId] = binding
}

function setUseCaseBinding(useCaseId: string, modelId: string) {
  setUseCaseBindingField(useCaseId, 'model_id', modelId)
}

function modelListSignature(model: UnknownRecord | null): string {
  if (!model) return ''
  if (normalizeConnectionType(model.connection_type) !== API_CONNECTION_TYPE) return ''
  return [
    String(model.id || ''),
    String(model.provider_id || ''),
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

function aiModelPayloadForCheck(): UnknownRecord {
  const model = selectedAiModel.value ? { ...selectedAiModel.value } : {}
  delete model.model_options
  return model
}

function refreshSelectedModelList(force = false) {
  if (!selectedAiModel.value || aiControlsLocked.value || !selectedModelListReady.value) return
  const signature = modelListSignature(selectedAiModel.value)
  if (!signature || (!force && signature === lastAutoModelListSignature.value)) return
  lastAutoModelListSignature.value = signature
  requestAiModelCheck(false, '正在加载模型列表', force ? 'manual_model_list' : 'auto_model_list')
}

function requestAiModelCheck(probeCapabilities: boolean, message: string, trigger: string) {
  if (!selectedAiModel.value || aiControlsLocked.value) return
  aiRequestPending.value = true
  aiRequestMessage.value = message
  emit('testAi', { ...aiModelPayloadForCheck(), probe_capabilities: probeCapabilities, test_trigger: trigger })
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
  capabilityProbeDialog.pending = true
  capabilityProbeDialog.error = ''
  aiRequestPending.value = true
  aiRequestMessage.value = `正在测试 ${capabilityLabelByValue[capability] || capability}`
  emit('testAi', {
    ...aiModelPayloadForCheck(),
    ...payload,
    probe_only_capability: capability,
    probe_capabilities: true,
    test_trigger: 'capability_checkbox',
  })
}

function handleAiConfigFieldBlur(field: 'base_url' | 'api_key' | 'model') {
  lastAutoModelListSignature.value = ''
  if (field === 'base_url' || field === 'api_key') refreshSelectedModelList()
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
      restoreCapabilityProbeSelection()
      capabilityProbeDialog.pending = false
      const capabilityResult = asRecord(capabilityResults[pendingCapability])
      capabilityProbeDialog.error = firstText(
        capabilityResult.error,
        result.error,
        result.message,
        '能力测试失败',
      )
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
    const capabilityResult = asRecord(capabilityResults[pendingCapability])
    if (supported.has(pendingCapability) && capabilityResult.ok !== false) {
      saveCapabilityProfile(target, pendingCapability, capabilityResult.capability_profile)
      addCapability(target, pendingCapability)
      lastAutoCapabilitySignature.value = capabilitySignature(target)
      closeCapabilityProbe(false)
      return
    }
    restoreCapabilityProbeSelection()
    capabilityProbeDialog.error = String(capabilityResult.error || raw.error || result?.error || '能力测试未通过')
  }
}

watch(() => props.lastResult, applyAiTestResult, { deep: true })
watch(() => props.loading, (loading) => {
  if (!loading) {
    aiRequestPending.value = false
    aiRequestMessage.value = ''
    if (capabilityProbeDialog.open && capabilityProbeDialog.pending) {
      restoreCapabilityProbeSelection()
      capabilityProbeDialog.pending = false
      capabilityProbeDialog.error = capabilityProbeDialog.error || '测试已结束，但没有收到完整的能力测试结果。请重新测试或打开 AI Work 查看最新会话。'
    }
  }
})

function storePayload(): UnknownRecord {
  return {
    mercadolibre: { app_id: form.mlAppId, client_secret: form.mlClientSecret, app_secret: form.mlClientSecret, redirect_uri: form.mlRedirectUri, notification_url: form.mlNotificationUrl },
    yandex: { api_token: form.yandexApiToken, campaign_id: form.yandexCampaignId },
    ozon: { client_id: form.ozonClientId, api_key: form.ozonApiKey },
  }
}

function selectedStorePayload(): UnknownRecord {
  return { [selectedStorePlatform.value]: asRecord(storePayload()[selectedStorePlatform.value]) }
}

function testOzonAuth(scope = ''): void {
  emit('testAuth', 'ozon', scope, selectedStorePayload())
}

function testYandexAuth(): void {
  // 预览测试：提交未保存表单值，后端使用内存副本校验，不落盘。
  emit('testAuth', 'yandex', '', selectedStorePayload())
}

const yandexStoreMeta = computed(() => {
  const store = asRecord(props.storeConfig.yandex)
  const scopes = asStringArray(store.auth_scopes)
  const warehouses = asStringArray(store.warehouse_ids)
  return {
    businessId: firstText(store.business_id),
    shopName: firstText(store.shop_name, store.business_name),
    placementType: firstText(store.placement_type),
    apiAvailability: firstText(store.api_availability),
    apiKeyName: firstText(store.api_key_name),
    scopes,
    onlyDefaultPrice: Boolean(store.only_default_price),
    stockUpdateMode: firstText(store.stock_update_mode),
    warehouses,
    verifiedAt: firstText(store.capabilities_verified_at),
    hasMetadata: Boolean(firstText(store.business_id) || firstText(store.placement_type) || scopes.length),
  }
})

const selectedStorePlatformMeta = computed(() => (
  storePlatforms.value.find((item) => item.key === selectedStorePlatform.value)
  || storePlatforms.value[0]
  || {
    key: selectedStorePlatform.value,
    label: '店铺平台',
    subtitle: '平台配置加载中',
  }
))

const CURRENCY_STATUS_LABELS: Record<string, string> = {
  ready: '已就绪',
  selection_required: '请选择',
  manual_required: '需人工填写',
  refresh_failed: '读取失败',
  unresolved: '未验证',
}
const CURRENCY_SOURCE_LABELS: Record<string, string> = {
  account_api: '平台账户',
  business_settings: 'Business',
  site_api: '站点 API',
  global_selling_contract: 'Global Selling 官方契约',
  manual: '人工配置',
}
const MERCADOLIBRE_USER_PRODUCTS_REQUIRED_ERROR_CODE = 'MERCADOLIBRE_USER_PRODUCTS_REQUIRED'
const MERCADOLIBRE_TRADITIONAL_MODEL_MESSAGE = '当前授权已明确使用传统 CBT 刊登模型（POST /global/items）。该模型与 User Products 模型互斥，是有效刊登路径，并非账号能力缺失。重新验证仅用于刷新远端账户能力结果。'

const currencySelection = ref('')

const mercadoLibreListingModel = computed(() => resolveMercadoLibreListingModel(props.storeConfig))

const storeCurrencyState = computed(() => {
  const store = asRecord(props.storeConfig[selectedStorePlatform.value])
  return {
    listingCurrency: firstText(store.listing_currency),
    allowedCurrencies: asStringArray(store.allowed_currencies),
    mode: firstText(store.currency_mode) || 'unresolved',
    status: firstText(store.currency_status) || 'unresolved',
    source: firstText(store.currency_source),
    verifiedAt: firstText(store.currency_verified_at),
    errorCode: firstText(store.currency_error_code),
    errorMessage: firstText(store.currency_error_message),
  }
})

const isMercadoLibreTraditionalModelNotice = computed(() => (
  selectedStorePlatform.value === 'mercadolibre'
  && mercadoLibreListingModel.value === 'traditional_global_items'
  && storeCurrencyState.value.errorCode === MERCADOLIBRE_USER_PRODUCTS_REQUIRED_ERROR_CODE
))

function currencyStatusLabelFor(status: string): string {
  return CURRENCY_STATUS_LABELS[status] || CURRENCY_STATUS_LABELS.unresolved
}

const storeCurrencyStatusLabel = computed(() => (
  isMercadoLibreTraditionalModelNotice.value
    ? '传统 CBT 刊登模型'
    : currencyStatusLabelFor(storeCurrencyState.value.status)
))

const storeCurrencyStatusClass = computed(() => {
  if (isMercadoLibreTraditionalModelNotice.value) {
    return 'bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-200'
  }
  switch (storeCurrencyState.value.status) {
    case 'ready':
      return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200'
    case 'refresh_failed':
      return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-200'
    case 'unresolved':
      return 'bg-slate-200 text-slate-600 dark:bg-dark-700 dark:text-slate-300'
    default:
      return 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200'
  }
})

const storeCurrencySourceLabel = computed(() => {
  const source = storeCurrencyState.value.source
  if (!source) return '-'
  return CURRENCY_SOURCE_LABELS[source] || source
})

const mercadoLibreGlobalSellingMeta = computed(() => ({
  accountSiteId: mercadoLibreAccountSiteId(props.storeConfig),
  bindings: mercadoLibreMarketplaceBindings(props.storeConfig),
  fullyManaged: mercadoLibreHasFullyManagedBinding(props.storeConfig),
  listingModel: mercadoLibreListingModel.value,
}))

const mercadoLibreListingModelLabel = computed(() => {
  if (mercadoLibreListingModel.value === 'user_products') {
    return 'User Products 模型'
  }
  if (mercadoLibreListingModel.value === 'traditional_global_items') {
    return '传统 CBT 刊登模型'
  }
  return '缺失或无效（已阻断）'
})

function mercadoLibreBindingSiteLabel(binding: MercadoLibreMarketplaceBinding): string {
  const platform = props.platformOptions.find((option) => option.key === 'mercadolibre')
  const site = platform?.sites.find((option) => option.code.toUpperCase() === binding.siteId)
  return site ? `${site.label}（${binding.siteId}）` : binding.siteId
}

function mercadoLibreBindingMeta(binding: MercadoLibreMarketplaceBinding): string {
  const operationModel = mercadoLibreListingModel.value === 'traditional_global_items'
    ? '传统 CBT operation'
    : binding.userProduct === false
      ? 'User Products operation 不可用'
      : binding.userProduct === null
        ? 'User Products 能力继承父账号'
        : 'User Products operation'
  return [
    `物流 ${binding.logisticType}`,
    operationModel,
    binding.businessModel ? `业务 ${binding.businessModel}` : '',
    binding.pricingModel ? `计价 ${binding.pricingModel}` : '',
    binding.sellerId ? `Seller ${binding.sellerId}` : '',
  ].filter(Boolean).join(' · ')
}

const currencyPreview = computed(() => {
  const result = selectedLastStoreResult.value
  if (!result) return null
  const raw = asRecord(result.raw)
  if (raw.preview !== true) return null
  const configuration = asRecord(raw.currency_configuration)
  if (!Object.keys(configuration).length) return null
  return {
    listingCurrency: firstText(configuration.listing_currency),
    status: firstText(configuration.currency_status),
  }
})

watch(
  () => [
    selectedStorePlatform.value,
    storeCurrencyState.value.listingCurrency,
    storeCurrencyState.value.allowedCurrencies.join(','),
  ],
  () => {
    currencySelection.value = storeCurrencyState.value.listingCurrency
  },
  { immediate: true },
)

function saveCurrencySelection(): void {
  emit('saveCurrency', selectedStorePlatform.value, currencySelection.value.trim())
}

function retryStoreCurrency(): void {
  // 读取失败后的重试针对已保存配置执行，不提交未保存表单副本。
  emit('testAuth', selectedStorePlatform.value)
}

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
    // storeConfig 已用于刷新卡片展示，不在结果详情里重复展示。
    delete details.storeConfig
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
          <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="capabilityProbeDialog.pending" @click="closeCapabilityProbe()">关闭</button>
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
        <div class="mt-4 flex flex-wrap justify-end gap-2">
          <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="capabilityProbeDialog.pending" @click="closeCapabilityProbe()">取消</button>
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
            :data-testid="`auth-settings-tab-${tab.key}`"
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
              <button class="btn btn-outline py-1.5 text-sm text-rose-700" type="button" :disabled="aiControlsLocked || aiModels.length <= 1" @click="removeSelectedAiModel">删除当前模型</button>
              <button class="btn btn-outline py-1.5 text-sm" type="button" :disabled="aiControlsLocked || !selectedModelListReady" :title="selectedModelListReady ? '从 Provider 加载可用模型列表' : selectedProviderSupportsModelDiscovery ? '请先填写 Base URL 和 API Key' : '当前 Provider 不提供模型目录，请手动填写模型 ID'" @click="refreshSelectedModelList(true)">加载模型列表</button>
              <button class="btn btn-primary py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="saveAiSettings">保存 AI 设置</button>
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
                <span class="mt-1 block truncate text-xs text-accent-500 dark:text-accent-400">{{ aiModelProviderLabel(model) }} · {{ model.model || '未选模型' }}</span>
              </button>
            </div>

            <div v-if="selectedAiModel" class="ai-model-form space-y-3">
              <div class="grid gap-3 md:grid-cols-2">
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型 ID</span>
                  <input class="input" :value="modelField('id')" :disabled="aiControlsLocked" placeholder="例如 deepseek_text" @input="setSelectedModelField('id', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">显示名称</span>
                  <input class="input" :value="modelField('name')" :disabled="aiControlsLocked" placeholder="例如 DeepSeek 文本模型" @input="setSelectedModelField('name', eventText($event))" />
                </label>
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">接入方式</span>
                  <select class="input" :value="modelField('connection_type', 'api')" :disabled="aiControlsLocked" @change="setSelectedModelField('connection_type', eventText($event))">
                    <option v-for="option in connectionTypeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </label>
                <label v-if="selectedAiModelIsApi" class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">服务商</span>
                  <select data-testid="ai-provider-id" class="input" :value="modelField('provider_id')" :disabled="aiControlsLocked" @change="setSelectedModelField('provider_id', eventText($event))">
                    <option value="" disabled>请选择服务商</option>
                    <option v-for="option in providerOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
                  </select>
                </label>
                <label v-if="selectedAiModelIsApi" class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API 协议</span>
                  <select data-testid="ai-api-style" class="input" :value="modelField('api_style', 'openai_compatible')" :disabled="aiControlsLocked" @change="setSelectedModelField('api_style', eventText($event))">
                    <option v-for="apiStyle in selectedProviderApiStyleOptions" :key="apiStyle" :value="apiStyle">{{ apiStyle === 'openai_responses' ? 'Responses' : 'Chat Completions' }}</option>
                  </select>
                </label>
                <span v-if="selectedAiModelIsApi" class="block md:col-span-2 text-xs text-accent-500 dark:text-accent-400"><template v-if="selectedProviderSpec?.description">{{ selectedProviderSpec.description }}。</template>服务商决定端点预设和厂商参数映射；API 协议独立选择，底层统一通过 Pydantic AI 调用。</span>
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
                <template v-if="selectedAiModelIsApi">
                  <label class="block md:col-span-2">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Base URL</span>
                    <input class="input" :value="modelField('base_url')" :disabled="aiControlsLocked || !selectedProviderBaseUrlEditable" placeholder="https://api.example.com/v1" @input="setSelectedModelField('base_url', eventText($event))" @blur="handleAiConfigFieldBlur('base_url')" />
                    <span v-if="!selectedProviderBaseUrlEditable" class="mt-1 block text-xs text-accent-500 dark:text-accent-400">该服务商使用固定官方地址。</span>
                  </label>
                  <label class="block md:col-span-2">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API Key</span>
                    <input type="password" class="input" :value="modelField('api_key')" :disabled="aiControlsLocked" :placeholder="apiKeyPlaceholder()" autocomplete="off" spellcheck="false" @input="setSelectedModelField('api_key', eventText($event))" @blur="handleAiConfigFieldBlur('api_key')" />
                  </label>
                  <label class="block md:col-span-2">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</span>
                    <select v-if="selectedModelOptions.length" class="input" :value="modelField('model')" :disabled="aiControlsLocked" @change="handleAiModelSelect(eventText($event))">
                      <option value="">{{ selectedModelOptions.length ? '请选择模型' : '填写 URL 和 API Key 后自动加载' }}</option>
                      <option v-for="option in selectedModelOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
                    </select>
                    <input v-else class="input" :value="modelField('model')" :disabled="aiControlsLocked" placeholder="先加载模型列表，或直接填写模型 ID" @input="setSelectedModelField('model', eventText($event))" @blur="handleAiConfigFieldBlur('model')" />
                    <span v-if="!selectedModelOptions.length" class="mt-1 block text-xs text-accent-500 dark:text-accent-400">Provider 不支持模型列表时，可直接填写模型 ID。</span>
                  </label>
                </template>
                <template v-else-if="selectedAiModelIsCli">
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">CLI 命令</span>
                    <input class="input" :value="modelField('command')" :disabled="aiControlsLocked" placeholder="例如 codex" @input="setSelectedModelField('command', eventText($event))" />
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">模型</span>
                    <input class="input" :value="modelField('model')" :disabled="aiControlsLocked" placeholder="可留空使用 CLI 默认" @input="setSelectedModelField('model', eventText($event))" @blur="handleAiConfigFieldBlur('model')" />
                  </label>
                  <div class="md:col-span-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm text-accent-600 dark:border-dark-700 dark:bg-dark-900 dark:text-accent-300">
                    {{ selectedCliStatusText }}
                  </div>
                </template>
                <template v-else>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">网页模型</span>
                    <input class="input" :value="modelField('model')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('model', eventText($event))" />
                  </label>
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
                <label class="block">
                  <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">超时秒数</span>
                  <input class="input" :value="modelField('timeout_seconds')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('timeout_seconds', eventText($event))" />
                </label>
                <details class="md:col-span-2 rounded-lg border border-dashed border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                  <summary class="cursor-pointer font-semibold text-accent-700 dark:text-accent-200">高级配置</summary>
                  <div v-if="selectedAiModelIsApi" class="mt-3 grid gap-3 md:grid-cols-2">
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Base URL 环境变量</span>
                      <input class="input" :value="modelField('base_url_env')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('base_url_env', eventText($event))" />
                    </label>
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">API Key 环境变量</span>
                      <input class="input" :value="modelField('api_key_env')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('api_key_env', eventText($event))" />
                    </label>
                    <label class="block md:col-span-2">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">自定义请求 JSON</span>
                      <textarea
                        v-model="customRequestBodyEditor.text"
                        class="input min-h-28 resize-y font-mono text-xs leading-5"
                        :disabled="aiControlsLocked"
                        placeholder="例如 {&quot;top_p&quot;: 0.8, &quot;enable_thinking&quot;: true}"
                        spellcheck="false"
                        @input="updateCustomRequestBodyText(eventText($event))"
                        @blur="commitCustomRequestBody"
                      ></textarea>
                      <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">仅支持 JSON 对象。后端会把厂商扩展字段转换为 Pydantic ModelSettings.extra_body；messages、model、tools、stream 等 Pydantic 协议字段不能在这里覆盖。功能绑定中的 temperature、最大输出和推理配置拥有更高优先级。</span>
                      <span v-if="customRequestBodyEditor.error" class="mt-1 block text-xs text-rose-600 dark:text-rose-300">{{ customRequestBodyEditor.error }}</span>
                    </label>
                    <label v-if="modelField('api_style', 'openai_compatible') === 'openai_compatible'" class="block md:col-span-2">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">联网搜索请求格式</span>
                      <select class="input" :value="webSearchRequestMode()" :disabled="aiControlsLocked" @change="setWebSearchRequestMode(eventText($event))">
                        <option value="enable_search">enable_search + 强制搜索（通义或兼容网关）</option>
                        <option value="web_search_options">web_search_options（兼容网关）</option>
                      </select>
                      <span class="mt-1 block text-xs text-accent-500 dark:text-accent-400">仅在功能需要“联网搜索”时生效。后端按这里选择的稳定策略生成受控 ModelSettings；测试与正式请求使用同一个 Pydantic Model。通义兼容接口使用 enable_search 与 search_options.forced_search。</span>
                    </label>
                    <span v-else class="block md:col-span-2 text-xs text-accent-500 dark:text-accent-400">Responses 协议由 Pydantic WebSearchTool 转译。测试只记录能力和 Pydantic 策略，不保存或复用厂商请求体。</span>
                  </div>
                  <div v-else-if="selectedAiModelIsCli" class="mt-3 grid gap-3 md:grid-cols-2">
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">CLI Profile</span>
                      <input class="input" :value="modelField('profile')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('profile', eventText($event))" />
                    </label>
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
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">浏览器 Profile</span>
                      <input class="input" :value="modelField('browser_profile')" :disabled="aiControlsLocked" placeholder="留空使用 default" @input="setSelectedModelField('browser_profile', eventText($event))" />
                    </label>
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">远程调试端口</span>
                      <input class="input" :value="modelField('browser_port')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('browser_port', eventText($event))" />
                    </label>
                    <label class="block">
                      <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">网页 URL</span>
                      <input class="input" :value="modelField('browser_url')" :disabled="aiControlsLocked" placeholder="可选" @input="setSelectedModelField('browser_url', eventText($event))" />
                    </label>
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
                  <input type="checkbox" :checked="hasCapability(capability.value)" :disabled="aiControlsLocked" @change="setCapability(capability.value, eventChecked($event), $event)" />
                  <span>{{ capability.label }}</span>
                </label>
                <label class="flex items-center gap-2 rounded-lg border border-accent-200 bg-white px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-900">
                  <input type="checkbox" :checked="selectedAiModel.enabled !== false" :disabled="aiControlsLocked" @change="setSelectedModelField('enabled', eventChecked($event))" />
                  <span>启用</span>
                </label>
              </div>
            </div>
          </div>
        </section>

        <section v-show="activeAuthSettingsTab === 'ai_bindings'" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold text-accent-950 dark:text-white">功能绑定</h3>
              <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">功能声明所需能力；这里只显示满足能力要求的模型，并可单独覆盖超时。</p>
            </div>
            <button data-testid="save-ai-bindings" class="btn btn-primary py-1.5 text-sm" type="button" :disabled="aiControlsLocked" @click="saveAiSettings">保存功能绑定</button>
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
                <select class="input" :value="useCaseBindingField(String(useCase.id || ''), 'model_id')" @change="setUseCaseBinding(String(useCase.id || ''), eventText($event))">
                  <option value="">自动匹配</option>
                  <option v-for="model in compatibleModelsForUseCase(useCase)" :key="String(model.id)" :value="String(model.id)">{{ model.name || model.id }} · {{ model.provider }}</option>
                </select>
              </label>
              <label class="mt-3 block">
                <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">超时覆盖（秒）</span>
                <input
                  class="input"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="留空继承模型设置"
                  :value="useCaseBindingField(String(useCase.id || ''), 'timeout_override_seconds')"
                  @input="setUseCaseBindingField(String(useCase.id || ''), 'timeout_override_seconds', eventText($event))"
                />
              </label>
              <details v-if="useCaseSupportsTextGeneration(useCase)" :data-testid="`generation-settings-${String(useCase.id || '')}`" class="mt-3 rounded-lg border border-dashed border-accent-200 bg-accent-50/60 px-3 py-2 text-sm dark:border-dark-700 dark:bg-dark-950/50">
                <summary class="cursor-pointer font-semibold text-accent-700 dark:text-accent-200">高级生成设置</summary>
                <div class="mt-2 text-xs text-accent-500 dark:text-accent-400">当前：{{ useCaseGenerationModelText(useCase) }}。留空表示继承业务默认值。</div>
                <div class="mt-3 grid gap-3 sm:grid-cols-2">
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">Temperature</span>
                    <input
                      class="input"
                      type="number"
                      min="0"
                      max="2"
                      step="0.1"
                      placeholder="继承"
                      :data-testid="`generation-temperature-${String(useCase.id || '')}`"
                      :disabled="aiControlsLocked || !useCaseGenerationFieldSupported(useCase, 'temperature')"
                      :value="useCaseGenerationField(String(useCase.id || ''), 'temperature')"
                      @input="setUseCaseGenerationField(String(useCase.id || ''), 'temperature', eventText($event))"
                    />
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">最大输出 Token</span>
                    <input
                      class="input"
                      type="number"
                      min="1"
                      step="1"
                      placeholder="继承"
                      :data-testid="`generation-max-output-${String(useCase.id || '')}`"
                      :disabled="aiControlsLocked || !useCaseGenerationFieldSupported(useCase, 'max_output_tokens')"
                      :value="useCaseGenerationField(String(useCase.id || ''), 'max_output_tokens')"
                      @input="setUseCaseGenerationField(String(useCase.id || ''), 'max_output_tokens', eventText($event))"
                    />
                  </label>
                  <label class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">推理开关</span>
                    <select
                      class="input"
                      :data-testid="`generation-reasoning-mode-${String(useCase.id || '')}`"
                      :disabled="aiControlsLocked || !useCaseReasoningSupported(useCase)"
                      :value="useCaseGenerationField(String(useCase.id || ''), 'mode', true)"
                      @change="setUseCaseGenerationField(String(useCase.id || ''), 'mode', eventText($event), true)"
                    >
                      <option value="">继承模型默认</option>
                      <option value="disabled">关闭</option>
                      <option value="enabled">开启</option>
                    </select>
                  </label>
                  <label v-if="useCaseReasoningEfforts(useCase).length" class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">推理强度</span>
                    <select
                      class="input"
                      :data-testid="`generation-reasoning-effort-${String(useCase.id || '')}`"
                      :disabled="aiControlsLocked || useCaseGenerationField(String(useCase.id || ''), 'mode', true) === 'disabled'"
                      :value="useCaseGenerationField(String(useCase.id || ''), 'effort', true)"
                      @change="setUseCaseGenerationField(String(useCase.id || ''), 'effort', eventText($event), true)"
                    >
                      <option value="">厂商默认（转换为 medium）</option>
                      <option v-for="effort in useCaseReasoningEfforts(useCase)" :key="effort" :value="effort">{{ reasoningEffortLabel(effort) }}</option>
                    </select>
                  </label>
                  <label v-if="useCaseReasoningBudgetSupported(useCase)" class="block">
                    <span class="mb-1 block text-xs font-semibold text-accent-600 dark:text-accent-300">推理预算 Token</span>
                    <input
                      class="input"
                      type="number"
                      min="1"
                      step="1"
                      placeholder="厂商默认"
                      :disabled="aiControlsLocked || useCaseGenerationField(String(useCase.id || ''), 'mode', true) === 'disabled'"
                      :value="useCaseGenerationField(String(useCase.id || ''), 'budget_tokens', true)"
                      @input="setUseCaseGenerationField(String(useCase.id || ''), 'budget_tokens', eventText($event), true)"
                    />
                  </label>
                </div>
                <p class="mt-3 text-xs text-accent-500 dark:text-accent-400">{{ useCaseReasoningCapabilities(useCase).note || '当前模型未声明推理参数映射。' }}</p>
                <p class="mt-1 text-xs text-amber-700 dark:text-amber-300">关闭推理通常能降低首字延迟，但不能保证翻译准确率；温度设为 0 也只提高稳定性，不等于结果必然正确。</p>
              </details>
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
              <select v-model="selectedStorePlatform" data-testid="store-platform-select" class="input w-64">
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
                <button data-testid="test-mercadolibre-auth" class="btn btn-outline py-1.5" :disabled="props.loading" @click="emit('testAuth', 'mercadolibre')">重新验证并刷新账户能力</button>
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
              <div
                v-if="mercadoLibreGlobalSellingMeta.accountSiteId === 'CBT'"
                data-testid="ml-global-selling-capabilities"
                class="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-100"
              >
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <div class="font-semibold">CBT Global Selling 能力</div>
                  <div class="flex flex-wrap gap-2">
                    <span data-testid="ml-listing-model" class="rounded bg-white/80 px-2 py-0.5 text-xs dark:bg-dark-900/70">刊登模型：{{ mercadoLibreListingModelLabel }}</span>
                    <span class="rounded bg-white/80 px-2 py-0.5 text-xs dark:bg-dark-900/70">已同步 {{ mercadoLibreGlobalSellingMeta.bindings.length }} 个子市场/物流组合</span>
                  </div>
                </div>
                <p v-if="mercadoLibreGlobalSellingMeta.listingModel === 'user_products' && mercadoLibreGlobalSellingMeta.fullyManaged" data-testid="ml-fully-managed-warning" class="mt-2 rounded bg-amber-50 p-2 text-xs font-semibold text-amber-800 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30">
                  {{ MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE }}
                </p>
                <p v-if="mercadoLibreGlobalSellingMeta.listingModel === 'traditional_global_items'" data-testid="ml-traditional-listing-model-notice" class="mt-2 rounded bg-blue-50 p-2 text-xs font-semibold text-blue-800 ring-1 ring-blue-200 dark:bg-blue-500/10 dark:text-blue-200 dark:ring-blue-500/30">
                  {{ MERCADOLIBRE_TRADITIONAL_MODEL_MESSAGE }}
                </p>
                <div v-if="mercadoLibreGlobalSellingMeta.bindings.length" class="mt-2 grid gap-2 sm:grid-cols-2">
                  <div
                    v-for="binding in mercadoLibreGlobalSellingMeta.bindings"
                    :key="`${binding.siteId}:${binding.logisticType}`"
                    data-testid="ml-marketplace-binding"
                    class="rounded bg-white/80 px-2.5 py-2 ring-1 ring-blue-100 dark:bg-dark-900/70 dark:ring-blue-500/20"
                  >
                    <div class="font-semibold">{{ mercadoLibreBindingSiteLabel(binding) }}</div>
                    <div class="mt-0.5 text-xs text-blue-700 dark:text-blue-200">{{ mercadoLibreBindingMeta(binding) }}</div>
                  </div>
                </div>
                <p v-else-if="mercadoLibreGlobalSellingMeta.listingModel === 'user_products'" class="mt-2 text-xs text-amber-700 dark:text-amber-200">尚未同步到可用子市场。请重新验证并刷新账户能力；同步完成前不能为 CBT 草稿选择销售国家。</p>
                <p v-else class="mt-2 text-xs text-accent-500 dark:text-accent-300">尚未验证账户刊登模型，请刷新账户能力。</p>
              </div>
            </template>

            <template v-else-if="selectedStorePlatform === 'yandex'">
              <input v-model="form.yandexApiToken" data-testid="yandex-api-token" type="password" class="input mt-3" placeholder="Yandex API-Key Token" autocomplete="off" />
              <input v-model="form.yandexCampaignId" data-testid="yandex-campaign-id" class="input mt-2" placeholder="Campaign ID，例如 123456" />
              <p data-testid="yandex-campaign-id-hint" class="mt-1 text-xs text-accent-500 dark:text-accent-400">请填写 Campaign ID（店铺 ID），不要填写 Business ID（柜台 ID）。可在 Yandex 卖家后台 → 设置 → API 和模块中查看。</p>
              <div class="mt-3 flex flex-wrap gap-2">
                <button data-testid="test-yandex-auth" class="btn btn-outline py-1.5" :disabled="props.loading" @click="testYandexAuth()">测试授权并读取发布货币</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="emit('clearAuth', 'yandex')">清除授权</button>
              </div>
              <p class="mt-2 text-xs text-accent-500">保存前可直接测试未保存的 Token 与 Campaign ID；预览结果不落库，界面会标注“预览，尚未保存”。可信授权与发布货币状态只能通过在线测试结果写入。</p>
              <div v-if="yandexStoreMeta.hasMetadata" class="mt-3 rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm dark:border-dark-700 dark:bg-dark-950/60">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <div class="font-semibold text-accent-950 dark:text-white">已验证店铺信息</div>
                  <span v-if="yandexStoreMeta.verifiedAt" class="text-xs text-accent-500 dark:text-accent-400">验证于 {{ yandexStoreMeta.verifiedAt }}</span>
                </div>
                <dl class="mt-2 grid gap-x-8 gap-y-1.5 text-xs text-accent-700 dark:text-accent-200 sm:grid-cols-2">
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">Business ID</dt><dd class="font-mono">{{ yandexStoreMeta.businessId || '-' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">店铺名</dt><dd class="truncate">{{ yandexStoreMeta.shopName || '-' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">投放模型</dt><dd>{{ yandexStoreMeta.placementType || '-' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">API 可用状态</dt><dd>{{ yandexStoreMeta.apiAvailability || '-' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">价格模式</dt><dd>{{ yandexStoreMeta.onlyDefaultPrice ? 'Business 级默认价' : 'Campaign 级价格' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">库存更新</dt><dd>{{ yandexStoreMeta.stockUpdateMode || '-' }}{{ yandexStoreMeta.warehouses.length ? `（仓库 ${yandexStoreMeta.warehouses.join('、')}）` : '' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">Token 名称</dt><dd class="truncate">{{ yandexStoreMeta.apiKeyName || '-' }}</dd></div>
                  <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">Scope 检查</dt><dd class="truncate" :title="yandexStoreMeta.scopes.join('、')">{{ yandexStoreMeta.scopes.length ? yandexStoreMeta.scopes.join('、') : '-' }}</dd></div>
                </dl>
              </div>
            </template>

            <template v-else>
              <input v-model="form.ozonClientId" data-testid="ozon-client-id" class="input mt-3" placeholder="Client ID" />
              <input v-model="form.ozonApiKey" data-testid="ozon-api-key" type="password" class="input mt-2" placeholder="API Key" />
              <div class="mt-3 flex flex-wrap gap-2">
                <button data-testid="test-ozon-auth" class="btn btn-outline py-1.5" :disabled="props.loading" @click="testOzonAuth()">测试授权并读取发布货币</button>
                <button data-testid="test-ozon-category-auth" class="btn btn-outline py-1.5" :disabled="props.loading" @click="testOzonAuth('category')">读取类目测试</button>
                <button class="btn btn-outline py-1.5 text-rose-700" :disabled="props.loading" @click="emit('clearAuth', 'ozon')">清除授权</button>
              </div>
            </template>

            <div data-testid="store-currency-block" class="mt-4 rounded-lg border border-accent-200 bg-accent-50 p-3 dark:border-dark-700 dark:bg-dark-950/60">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="font-semibold text-accent-950 dark:text-white">发布货币</div>
                <span data-testid="store-currency-status" class="rounded px-2 py-0.5 text-xs" :class="storeCurrencyStatusClass">{{ storeCurrencyStatusLabel }}</span>
              </div>
              <dl class="mt-2 grid gap-x-8 gap-y-1.5 text-xs text-accent-700 dark:text-accent-200 sm:grid-cols-2">
                <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">当前发布货币</dt><dd data-testid="store-currency-value" class="font-mono">{{ storeCurrencyState.listingCurrency || '-' }}</dd></div>
                <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">来源</dt><dd data-testid="store-currency-source">{{ storeCurrencySourceLabel }}</dd></div>
                <div class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">最近验证时间</dt><dd>{{ storeCurrencyState.verifiedAt || '-' }}</dd></div>
                <div v-if="storeCurrencyState.allowedCurrencies.length" class="flex items-center justify-between gap-2"><dt class="text-accent-500 dark:text-accent-400">允许币种</dt><dd>{{ storeCurrencyState.allowedCurrencies.join('、') }}</dd></div>
              </dl>

              <div v-if="isMercadoLibreTraditionalModelNotice" class="mt-2 rounded bg-blue-50 p-2 text-xs text-blue-800 ring-1 ring-blue-100 dark:bg-blue-500/10 dark:text-blue-200 dark:ring-blue-500/30">
                {{ MERCADOLIBRE_TRADITIONAL_MODEL_MESSAGE }}
                <button data-testid="store-currency-retry" class="btn btn-outline ml-2 py-1 text-xs" :disabled="props.loading" @click="retryStoreCurrency()">重新验证并刷新账户能力</button>
              </div>

              <div v-else-if="storeCurrencyState.status === 'refresh_failed'" class="mt-2 rounded bg-rose-50 p-2 text-xs text-rose-700 ring-1 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-200 dark:ring-rose-500/30">
                读取失败：{{ storeCurrencyState.errorMessage || storeCurrencyState.errorCode || '远端未返回可用币种' }}。上次读取值仅供参考，核价与发布已阻断。
                <button data-testid="store-currency-retry" class="btn btn-outline ml-2 py-1 text-xs" :disabled="props.loading" @click="retryStoreCurrency()">重新验证授权并读取币种</button>
              </div>

              <div v-else-if="storeCurrencyState.mode === 'selectable'" class="mt-2 flex flex-wrap items-center gap-2">
                <select v-model="currencySelection" data-testid="store-currency-select" class="input w-48" :disabled="props.loading">
                  <option value="" disabled>请选择发布货币</option>
                  <option v-for="code in storeCurrencyState.allowedCurrencies" :key="code" :value="code">{{ code }}</option>
                </select>
                <button data-testid="store-currency-save" class="btn btn-outline py-1.5 text-xs" :disabled="props.loading || !currencySelection" @click="saveCurrencySelection()">保存选择</button>
                <span v-if="storeCurrencyState.status === 'selection_required'" class="text-xs text-amber-700 dark:text-amber-300">必须从允许币种中选择后才能核价与发布。</span>
              </div>

              <div v-else-if="storeCurrencyState.mode === 'manual'" class="mt-2 flex flex-wrap items-center gap-2">
                <input v-model="currencySelection" data-testid="store-currency-manual" class="input w-56" placeholder="ISO 4217 三位代码，例如 USD" :disabled="props.loading" />
                <button data-testid="store-currency-save" class="btn btn-outline py-1.5 text-xs" :disabled="props.loading || !currencySelection.trim()" @click="saveCurrencySelection()">保存发布货币</button>
                <span v-if="storeCurrencyState.status === 'ready'" class="text-xs text-accent-500 dark:text-accent-400">修改后旧核价将失效，需要重新核价。</span>
                <span v-else class="text-xs text-accent-500 dark:text-accent-400">平台不提供币种查询能力，请人工填写。</span>
              </div>

              <div v-else-if="storeCurrencyState.mode === 'locked'" class="mt-2 text-xs text-accent-500 dark:text-accent-400">
                发布货币由平台账户锁定，不允许修改。
              </div>

              <div v-else class="mt-2 text-xs text-accent-500 dark:text-accent-400">
                请先测试授权并读取发布货币；未就绪前不能核价与发布。
              </div>

              <div v-if="currencyPreview" data-testid="store-currency-preview" class="mt-2 rounded bg-blue-50 p-2 text-xs text-blue-900 ring-1 ring-blue-100 dark:bg-blue-500/10 dark:text-blue-100 dark:ring-blue-500/30">
                预览，尚未保存：{{ currencyPreview.listingCurrency || '无可用币种' }}（{{ currencyStatusLabelFor(currencyPreview.status) }}）。保存凭据后重新测试才会落库。
              </div>
            </div>

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
            <button data-testid="save-platform-api-settings" class="btn btn-primary py-1.5 text-sm" type="button" :disabled="props.loading" @click="saveApiSettings">保存 API 设置</button>
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
                <button data-testid="test-1688-api" class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !alibabaApiReady" :title="alibabaApiReady ? '' : alibabaApiHint" @click="testAlibabaApi">测试</button>
              </div>
              <div class="mt-3 grid gap-3 md:grid-cols-2">
                <input v-model="form.alibabaAppKey" data-testid="transient-1688-app-key" class="input" :placeholder="alibabaAppKeyPlaceholder" autocomplete="off" spellcheck="false" />
                <input v-model="form.alibabaAppSecret" data-testid="transient-1688-app-secret" type="password" class="input" :placeholder="alibabaAppSecretPlaceholder" autocomplete="off" spellcheck="false" />
                <input v-model="form.alibabaAccessToken" data-testid="transient-1688-access-token" type="password" class="input md:col-span-2" :placeholder="alibabaAccessTokenPlaceholder" autocomplete="off" spellcheck="false" />
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
                <button data-testid="test-yunexpress-api" class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !yunexpressApiReady" :title="yunexpressApiReady ? '' : yunexpressApiHint" @click="testYunexpressApi">测试 token</button>
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
                <input v-model="form.yunexpressAppId" data-testid="transient-yunexpress-app-id" class="input" :placeholder="yunexpressAppIdPlaceholder" autocomplete="off" spellcheck="false" />
                <input v-model="form.yunexpressAppSecret" data-testid="transient-yunexpress-app-secret" type="password" class="input" :placeholder="yunexpressAppSecretPlaceholder" autocomplete="off" spellcheck="false" />
                <input v-model="form.yunexpressSourceKey" data-testid="transient-yunexpress-source-key" type="password" class="input" :placeholder="yunexpressSourceKeyPlaceholder" autocomplete="off" spellcheck="false" />
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
          <ProductResearchSettingsPanel :ai-use-case-prompts="aiUseCasePrompts" embedded />
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ai-model-form input.input,
.ai-model-form select.input {
  height: 2.5rem;
}
</style>
