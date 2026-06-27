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
  testAi: [channel: 'text' | 'image', config: UnknownRecord]
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
  textAiPlatform: '',
  textAiApiKey: '',
  textAiBaseUrl: '',
  textAiModel: '',
  imageAiPlatform: '',
  imageAiApiKey: '',
  imageAiBaseUrl: '',
  imageAiModel: '',
  imageAiQuality: '',
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

const storePlatforms: Array<{ key: Marketplace; label: string; subtitle: string }> = [
  { key: 'mercadolibre', label: 'Mercado Libre', subtitle: 'OAuth、授权链接、店铺授权测试' },
  { key: 'wildberries', label: 'Wildberries', subtitle: 'Content / Prices API Token' },
  { key: 'ozon', label: 'Ozon', subtitle: 'Client ID + API Key' },
]

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

function fillFromProps() {
  const text = asRecord(props.aiConfig.text_ai || props.appConfig.text_ai)
  const image = asRecord(props.aiConfig.image_ai || props.appConfig.image_ai)
  const pricing = asRecord(props.appConfig.pricing_defaults)
  const alibabaApi = asRecord(props.appConfig['1688_api'])
  const appText = asRecord(props.appConfig.text_ai)
  const appImage = asRecord(props.appConfig.image_ai)
  const ml = asRecord(props.storeConfig.mercadolibre)
  const wb = asRecord(props.storeConfig.wildberries)
  const ozon = asRecord(props.storeConfig.ozon)
  form.textAiPlatform = firstText(text.platform, appText.platform, 'DeepSeek')
  const nextTextKey = firstText(text.api_key, appText.api_key)
  if (nextTextKey || !form.textAiApiKey) form.textAiApiKey = nextTextKey
  form.textAiBaseUrl = firstText(text.base_url, appText.base_url, 'https://api.deepseek.com')
  form.textAiModel = firstText(text.model, appText.model, 'deepseek-chat')
  form.imageAiPlatform = firstText(image.platform, appImage.platform, 'OpenAI')
  const nextImageKey = firstText(image.api_key, appImage.api_key)
  if (nextImageKey || !form.imageAiApiKey) form.imageAiApiKey = nextImageKey
  form.imageAiBaseUrl = firstText(image.base_url, appImage.base_url, 'https://api.openai.com/v1')
  form.imageAiModel = firstText(image.model, appImage.model, 'gpt-image-1')
  form.imageAiQuality = firstText(image.quality, appImage.quality, 'medium')
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

const textAiReady = computed(() => Boolean(
  form.textAiPlatform.trim()
  && form.textAiModel.trim()
  && form.textAiBaseUrl.trim()
  && form.textAiApiKey.trim(),
))

const imageAiReady = computed(() => Boolean(
  form.imageAiPlatform.trim()
  && form.imageAiModel.trim()
  && form.imageAiBaseUrl.trim()
  && form.imageAiApiKey.trim(),
))

const textAiHint = computed(() => props.loading ? '正在处理，请稍候' : '请完整填写文本 AI 的平台、模型、Base URL 和 API Key')
const imageAiHint = computed(() => props.loading ? '正在处理，请稍候' : '请完整填写图片 AI 的平台、模型、Base URL 和 API Key')
const exchangeRateReady = computed(() => Boolean(form.exchangeRateApiUrl.trim()))
const exchangeRateHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写汇率 API URL')
const alibabaApiReady = computed(() => Boolean(form.alibabaAppKey.trim() && form.alibabaAppSecret.trim() && form.alibabaApiBaseUrl.trim()))
const alibabaApiHint = computed(() => props.loading ? '正在处理，请稍候' : '请填写 1688 App Key、App Secret 和 API 请求地址')

function aiPayload(): UnknownRecord {
  return {
    text_ai: { platform: form.textAiPlatform.trim(), api_key: form.textAiApiKey.trim(), base_url: form.textAiBaseUrl.trim(), model: form.textAiModel.trim() },
    image_ai: { platform: form.imageAiPlatform.trim(), api_key: form.imageAiApiKey.trim(), base_url: form.imageAiBaseUrl.trim(), model: form.imageAiModel.trim(), quality: form.imageAiQuality.trim() || 'medium' },
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
        <div><h2 class="card-title">平台授权</h2><p class="muted mt-1">平台授权、AI 通道和核价汇率 API 都在这里配置。</p></div>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-primary" :disabled="props.loading" @click="emit('saveAi', aiPayload())">保存设置</button>
        </div>
      </div>
      <div class="mt-5 grid gap-4 xl:grid-cols-2 2xl:grid-cols-4">
        <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h3 class="font-semibold text-accent-950 dark:text-white">文本 AI</h3>
            <button class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !textAiReady" :title="textAiReady ? '' : textAiHint" @click="emit('testAi', 'text', aiPayload())">测试</button>
          </div>
          <div class="mt-3 grid gap-3 md:grid-cols-2">
            <input v-model="form.textAiPlatform" class="input" placeholder="平台，例如 DeepSeek" />
            <input v-model="form.textAiModel" class="input" placeholder="模型，例如 deepseek-chat" />
            <input v-model="form.textAiBaseUrl" class="input md:col-span-2" placeholder="Base URL" />
            <input v-model="form.textAiApiKey" class="input md:col-span-2" placeholder="API Key" autocomplete="off" spellcheck="false" />
          </div>
        </div>
        <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h3 class="font-semibold text-accent-950 dark:text-white">图片 AI</h3>
            <button class="btn btn-outline py-1.5 text-sm" :disabled="props.loading || !imageAiReady" :title="imageAiReady ? '' : imageAiHint" @click="emit('testAi', 'image', aiPayload())">测试</button>
          </div>
          <div class="mt-3 grid gap-3 md:grid-cols-2">
            <input v-model="form.imageAiPlatform" class="input" placeholder="平台，例如 OpenAI" />
            <input v-model="form.imageAiModel" class="input" placeholder="模型" />
            <input v-model="form.imageAiQuality" class="input" placeholder="质量 medium/high" />
            <input v-model="form.imageAiBaseUrl" class="input" placeholder="Base URL" />
            <input v-model="form.imageAiApiKey" class="input md:col-span-2" placeholder="API Key" autocomplete="off" spellcheck="false" />
          </div>
        </div>
        <div class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h3 class="font-semibold text-accent-950 dark:text-white">核价汇率</h3>
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
            <h3 class="font-semibold text-accent-950 dark:text-white">1688 采集 API</h3>
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
      <div v-if="props.lastResult?.raw?.channel" class="mt-4 rounded-lg p-4 text-sm ring-1" :class="props.lastResult.ok ? 'bg-emerald-50 text-emerald-950 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-100 dark:ring-emerald-500/30' : 'bg-rose-50 text-rose-950 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-100 dark:ring-rose-500/30'">
        <div class="font-semibold">最近配置测试：{{ props.lastResult.ok ? '成功' : '失败' }}</div>
        <div class="mt-1 break-words">{{ props.lastResult.message || props.lastResult.error }}</div>
        <div v-if="props.lastResult.nextAction" class="mt-1 text-blue-700">下一步：{{ props.lastResult.nextAction }}</div>
        <pre class="mt-3 max-h-52 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">{{ JSON.stringify(props.lastResult.raw, null, 2) }}</pre>
      </div>
    </section>

    <section class="rounded-lg border border-accent-200 bg-white p-5 shadow-card dark:border-dark-700 dark:bg-dark-900/80">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="card-title">店铺授权</h2>
          <p class="muted mt-1">选择一个平台，只显示该平台的授权配置和检测结果。</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <select v-model="selectedStorePlatform" class="input w-64">
            <option v-for="platform in storePlatforms" :key="platform.key" :value="platform.key">{{ platform.label }}</option>
          </select>
          <button class="btn btn-primary" :disabled="props.loading" @click="emit('saveStore', selectedStorePayload())">保存当前平台授权</button>
        </div>
      </div>

      <div class="mt-5 rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold text-accent-950 dark:text-white">{{ selectedStorePlatformMeta.label }}</h3>
            <p class="mt-1 text-sm text-accent-500 dark:text-accent-400">{{ selectedStorePlatformMeta.subtitle }}</p>
          </div>
          <div v-if="hasStoreSummary" class="rounded-xl bg-slate-900/80 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-600">
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
            <div class="mt-2 text-blue-700">下一步：{{ props.mercadolibreChecklist.nextAction }}</div>
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

    <ProductResearchSettingsPanel :text-ai-config="aiPayload().text_ai as UnknownRecord" />
  </div>
</template>
