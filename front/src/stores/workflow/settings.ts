import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  buildMercadoLibreAuthLink,
  clearStoreAuth,
  exchangeMercadoLibreCode,
  fetchAiConfig,
  fetchMercadoLibreAuthChecklist,
  openAuthLink,
  refreshMercadoLibreToken,
  runMercadoLibreRealAuthTest,
  saveAiConfig,
  saveStoreCurrency as saveStoreCurrencyRequest,
  saveStoreSettings,
  testAiModel,
  testApiConfig,
  testStoreAuth,
} from '@/api/workflow/settings'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { withAiForeground } from '@/services/withAiForeground'
import { capabilityProbeUserMessage } from '@/constants/aiCapabilityProbe'
import type { AuthResult, Marketplace, MercadoLibreAuthChecklist, MercadoLibreTestMode, UnknownRecord } from '@/types/workflow'
import { sanitizePublicAppConfig } from '@/utils/configSecurity'

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function authSummary(raw: UnknownRecord): UnknownRecord | null {
  const value = raw.storeAuthSummary
  return isRecord(value) ? value : null
}

function authResultError(result: AuthResult, fallback: string): string {
  return result.error || result.message || result.nextAction || fallback
}

function publicSafeAiModel(model: UnknownRecord): UnknownRecord {
  const safe = { ...model }
  if (safe.api_key) {
    delete safe.api_key
    safe.api_key_configured = true
  }
  return safe
}

function mergeAiConfigWithSubmitted(publicConfig: UnknownRecord, submittedConfig: UnknownRecord): UnknownRecord {
  const safePublicConfig = sanitizePublicAppConfig(publicConfig)
  const safeSubmittedConfig = sanitizePublicAppConfig(submittedConfig)
  const merged: UnknownRecord = { ...safePublicConfig }
  if (Array.isArray(submittedConfig.ai_models)) {
    const publicModels = Array.isArray(safePublicConfig.ai_models) ? safePublicConfig.ai_models.filter(isRecord) : []
    const publicById = new Map(publicModels.map((model) => [String(model.id || ''), publicSafeAiModel(model)]))
    const rawSubmittedModels = submittedConfig.ai_models.filter(isRecord)
    const rawSubmittedById = new Map(rawSubmittedModels.map((model) => [String(model.id || ''), model]))
    const submittedModels = Array.isArray(safeSubmittedConfig.ai_models) ? safeSubmittedConfig.ai_models : []
    merged.ai_models = submittedModels
      .filter(isRecord)
      .map((model, index) => {
        const modelId = String(model.id || '')
        const publicModel = publicById.get(modelId) || {}
        const rawSubmittedModel = rawSubmittedById.get(modelId) || rawSubmittedModels[index] || {}
        const mergedModel = { ...publicModel, ...model }
        if (publicModel.api_key_configured !== undefined) {
          mergedModel.api_key_configured = Boolean(publicModel.api_key_configured)
        } else if (String(rawSubmittedModel.api_key || '').trim()) {
          mergedModel.api_key_configured = true
        }
        if (publicModel.api_key_masked !== undefined) {
          mergedModel.api_key_masked = publicModel.api_key_masked
        }
        return publicSafeAiModel(mergedModel)
      })
  }
  if (isRecord(safeSubmittedConfig.ai_use_case_bindings)) merged.ai_use_case_bindings = safeSubmittedConfig.ai_use_case_bindings
  if (isRecord(safeSubmittedConfig.ai_use_case_prompts)) merged.ai_use_case_prompts = safeSubmittedConfig.ai_use_case_prompts
  for (const section of ['1688_api', 'yunexpress', 'pricing_defaults']) {
    const publicSection = isRecord(safePublicConfig[section]) ? safePublicConfig[section] as UnknownRecord : {}
    const submittedSection = isRecord(safeSubmittedConfig[section]) ? safeSubmittedConfig[section] as UnknownRecord : {}
    merged[section] = { ...publicSection, ...submittedSection }
  }
  return sanitizePublicAppConfig(merged)
}

export const useWorkflowSettingsStore = defineStore('workflow-settings', () => {
  const appConfig = ref<UnknownRecord>({})
  const aiConfig = ref<UnknownRecord>({})
  const storeConfig = ref<UnknownRecord>({})
  const storeAuthSummary = ref<UnknownRecord>({})
  const mercadolibreAuthChecklist = ref<MercadoLibreAuthChecklist | null>(null)
  const lastAuthResult = ref<AuthResult | null>(null)
  const authLink = ref('')

  const activity = useWorkflowActivityStore()

  async function loadAiConfig() {
    activity.loading = true
    activity.setError('')
    try {
      const result = await fetchAiConfig()
      aiConfig.value = sanitizePublicAppConfig(result.raw)
      activity.addLog('AI 配置已读取。')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '读取 AI 配置失败')
    } finally {
      activity.loading = false
    }
  }

  async function saveAiSettings(config: UnknownRecord) {
    activity.loading = true
    activity.setError('')
    try {
      const result = await saveAiConfig(config)
      aiConfig.value = mergeAiConfigWithSubmitted(result.raw, config)
      appConfig.value = mergeAiConfigWithSubmitted({ ...appConfig.value, ...result.raw }, config)
      useWorkflowCollectionStore().fillFormFromState(appConfig.value)
      activity.addLog('平台授权设置已保存。')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '保存平台授权设置失败')
    } finally {
      activity.loading = false
    }
  }

  async function testAiSettings(model: UnknownRecord) {
    activity.loading = true
    activity.setError('')
    try {
      const modelLabel = String(model.name || model.model || model.id || 'AI 模型').trim()
      const result = model.probe_capabilities === false
        ? await testAiModel(model)
        : await withAiForeground<AuthResult>(
          {
            displayTitle: `测试 AI 模型 · ${modelLabel}`,
            initialUserMessage: capabilityProbeUserMessage(model),
            successNotice: (probeResult) => (
              probeResult.ok
                ? `${modelLabel} 能力测试通过`
                : `${modelLabel} 能力测试未通过`
            ),
            failureNotice: (error) => (
              error instanceof Error ? error.message : `${modelLabel} 能力测试失败`
            ),
          },
          ({ presentationId }) => testAiModel(model, presentationId),
        )
      lastAuthResult.value = result
      activity.addLog(`AI 模型测试：${result.message || result.error || '完成'}`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '测试 AI 失败'
      lastAuthResult.value = {
        ok: false,
        message: '',
        error: message,
        errorCode: '',
        nextAction: '请检查 API Key、Base URL 和模型名，然后再试一次。',
        raw: {
          ok: false,
          error: message,
          channel: 'ai_model',
          model_id: model.id,
          test_trigger: model.test_trigger,
          probe_only_capability: model.probe_only_capability,
        },
      }
      activity.setError(message)
    } finally {
      activity.loading = false
    }
  }

  async function testPlatformApiConfig(kind: 'exchange_rate' | '1688' | 'yunexpress', config: UnknownRecord, testValue = '') {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await testApiConfig(kind, config, testValue)
      activity.addLog(`${kind} API 测试：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '测试 API 配置失败'
      lastAuthResult.value = {
        ok: false,
        message: '',
        error: message,
        errorCode: '',
        nextAction: '请检查当前卡片里的配置后再试一次。',
        raw: { ok: false, error: message, channel: kind },
      }
      activity.setError(message)
    } finally {
      activity.loading = false
    }
  }

  async function saveStoreConfig(config: UnknownRecord) {
    activity.loading = true
    activity.setError('')
    try {
      const saved = await saveStoreSettings(config)
      storeConfig.value = saved.storeConfig
      storeAuthSummary.value = saved.storeAuthSummary
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      activity.addLog('平台授权配置已保存。')
      // 保存凭据本身不伪造成功态；可信授权与发布币种状态只能通过
      // “测试授权并读取发布货币”的在线测试结果写入。
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '保存平台授权失败')
    } finally {
      activity.loading = false
    }
  }

  async function testAuth(platform: Marketplace, scope = '', config: UnknownRecord = {}) {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await testStoreAuth(platform, scope, config)
      // 是否刷新持久化展示以后端返回的 preview 标志为准：后端在“提交的凭据
      // 与已保存配置一致”时会按已保存配置测试并落库（preview 缺省）；只有
      // 真正改动了未保存凭据的预览测试才带 preview=true，不更新持久化展示。
      const raw = lastAuthResult.value.raw
      if (raw.preview !== true) {
        const summary = authSummary(raw)
        if (summary) storeAuthSummary.value = summary
        const nextStoreConfig = raw.storeConfig
        if (isRecord(nextStoreConfig)) storeConfig.value = nextStoreConfig
      }
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '测试授权失败'))
      activity.addLog(`${platform} 授权测试：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '测试授权失败')
    } finally {
      activity.loading = false
    }
  }

  async function saveStoreCurrency(platform: Marketplace, listingCurrency: string) {
    activity.loading = true
    activity.setError('')
    try {
      const saved = await saveStoreCurrencyRequest(platform, listingCurrency)
      storeConfig.value = saved.storeConfig
      storeAuthSummary.value = saved.storeAuthSummary
      activity.addLog(`${platform} 发布货币已更新为 ${listingCurrency.toUpperCase()}。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '保存发布货币失败')
    } finally {
      activity.loading = false
    }
  }

  async function loadMercadoLibreChecklist() {
    activity.loading = true
    activity.setError('')
    try {
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      activity.addLog('Mercado Libre 授权检查清单已刷新。')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '读取 Mercado Libre 授权清单失败')
    } finally {
      activity.loading = false
    }
  }

  async function generateMercadoLibreAuthLink(appId: string, redirectUri: string) {
    activity.loading = true
    activity.setError('')
    try {
      authLink.value = await buildMercadoLibreAuthLink(appId, redirectUri)
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      activity.addLog('Mercado Libre 授权链接已生成。')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '生成 Mercado Libre 授权链接失败')
    } finally {
      activity.loading = false
    }
  }

  async function openMercadoLibreAuth(url: string, browser = 'default') {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await openAuthLink(url, browser)
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '打开授权链接失败'))
      activity.addLog(lastAuthResult.value.message || '已打开授权链接。')
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '打开授权链接失败')
    } finally {
      activity.loading = false
    }
  }

  async function refreshMercadoLibreAuthToken(params: UnknownRecord = {}) {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await refreshMercadoLibreToken(params)
      const summary = authSummary(lastAuthResult.value.raw)
      if (summary) storeAuthSummary.value = summary
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, '刷新 Mercado Libre token 失败'))
      activity.addLog(`Mercado Libre token 刷新：${lastAuthResult.value.message || lastAuthResult.value.error || '完成'}`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '刷新 Mercado Libre token 失败')
    } finally {
      activity.loading = false
    }
  }

  async function runMercadoLibreAuthTest(mode: MercadoLibreTestMode, categoryId = '') {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await runMercadoLibreRealAuthTest(useWorkflowCatalogStore().product, mode, categoryId)
      const summary = authSummary(lastAuthResult.value.raw)
      if (summary) storeAuthSummary.value = summary
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, 'Mercado Libre 真实接口测试失败'))
      activity.addLog(`Mercado Libre 真实接口测试 ${mode}：${lastAuthResult.value.message || lastAuthResult.value.error || lastAuthResult.value.raw.status || '完成'}`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : 'Mercado Libre 真实接口测试失败')
    } finally {
      activity.loading = false
    }
  }

  async function exchangeMlCode(codeOrUrl: string, params: UnknownRecord = {}) {
    activity.loading = true
    activity.setError('')
    try {
      lastAuthResult.value = await exchangeMercadoLibreCode(codeOrUrl, params)
      mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      if (!lastAuthResult.value.ok) throw new Error(authResultError(lastAuthResult.value, 'Mercado Libre 换 token 失败'))
      activity.addLog(`Mercado Libre 换 token：${lastAuthResult.value.message || '完成'}`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : 'Mercado Libre 换 token 失败')
    } finally {
      activity.loading = false
    }
  }

  async function clearPlatformAuth(platform: Marketplace) {
    activity.loading = true
    activity.setError('')
    try {
      const cleared = await clearStoreAuth(platform)
      storeConfig.value = cleared.storeConfig
      storeAuthSummary.value = cleared.storeAuthSummary
      if (platform === 'mercadolibre') mercadolibreAuthChecklist.value = await fetchMercadoLibreAuthChecklist()
      activity.addLog(`${platform} 授权已清除。`)
    } catch (exc) {
      activity.setError(exc instanceof Error ? exc.message : '清除授权失败')
    } finally {
      activity.loading = false
    }
  }

  return {
    appConfig,
    aiConfig,
    storeConfig,
    storeAuthSummary,
    mercadolibreAuthChecklist,
    lastAuthResult,
    authLink,
    loadAiConfig,
    saveAiSettings,
    testAiSettings,
    testPlatformApiConfig,
    saveStoreConfig,
    saveStoreCurrency,
    testAuth,
    loadMercadoLibreChecklist,
    generateMercadoLibreAuthLink,
    openMercadoLibreAuth,
    refreshMercadoLibreAuthToken,
    runMercadoLibreAuthTest,
    exchangeMlCode,
    clearPlatformAuth,
  }
})
