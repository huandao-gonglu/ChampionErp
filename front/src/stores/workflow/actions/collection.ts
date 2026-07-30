import {

  clean1688Text,
  collectBatch as collectBatchApi,
  collectFromBrowserTab as collectFromBrowserTabApi,
  collectProduct as collectProductApi,
  fetchBrowserDebugStatus,
  importManualProduct,
  open1688Browser as open1688BrowserApi,
  openBrowserProfile,
  saveCollectSettings as saveCollectSettingsApi,
} from '@/api/workflow/catalog'
import { fetchState } from '@/api/workflow/state'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'
import type { TransientCollectCredentials } from '@/types/workflow'
import { sanitizePublicAppConfig } from '@/utils/configSecurity'
import {

  type WorkflowRuntime,
} from '../orchestration/runtime'

type WorkflowCollectionActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'productsIndex'
  | 'collectForm'
  | 'collectDiagnostics'
  | 'collectBatchRows'
  | 'browserDebugStatus'
  | 'fillFormFromState'
  | 'pricingResult'
  | 'category'
  | 'precheck'
  | 'payloadPreview'
  | 'publishJob'
  | 'platformOptions'
  | 'appConfig'
  | 'aiConfig'
  | 'storeConfig'
  | 'storeAuthSummary'
  | 'mercadolibreAuthChecklist'
  | 'lastAuthResult'
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'currentStage'
  | 'applyMutationIndexes'
  | 'restorePrecheckFromProduct'
  | 'restoreCategoryFromProduct'
  | 'syncCollectDiagnosticsFromProduct'
  | 'syncPricingInputFromProduct'
>

export function createWorkflowCollectionActions(runtime: WorkflowCollectionActionsPort) {
  const {
    product, productsIndex, collectForm, collectDiagnostics,
    collectBatchRows, browserDebugStatus, fillFormFromState, pricingResult, category,
    precheck, payloadPreview, publishJob,
    platformOptions, appConfig, aiConfig, storeConfig, storeAuthSummary,
    mercadolibreAuthChecklist, lastAuthResult, loading, addLog, setError,
    currentStage, applyMutationIndexes, restorePrecheckFromProduct, restoreCategoryFromProduct, syncCollectDiagnosticsFromProduct,
    syncPricingInputFromProduct,
  } = runtime

  async function loadState() {
    loading.value = true
    setError('')
    try {
      const state = await fetchState()
      product.value = state.product
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      platformOptions.value = state.platformOptions
      const publicAppConfig = sanitizePublicAppConfig(state.appConfig)
      appConfig.value = publicAppConfig
      storeConfig.value = state.storeConfig
      storeAuthSummary.value = state.storeAuthSummary
      mercadolibreAuthChecklist.value = state.mercadolibreAuthChecklist || null
      aiConfig.value = publicAppConfig
      fillFormFromState(publicAppConfig, state.outputDir)
      syncCollectDiagnosticsFromProduct('后端状态已加载。')
      syncPricingInputFromProduct()
      addLog('已读取后端当前商品、图片池和公共配置。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取后端状态失败')
    } finally {
      loading.value = false
    }
  }

  function resetForm() {
    collectForm.value = createDefaultCollectForm()
    collectDiagnostics.value = createDefaultCollectDiagnostics()
    setError('')
    addLog('已清空采集表单。')
  }

  async function collectProduct(credentials: TransientCollectCredentials = {}) {
    if (!collectForm.value.productUrl.trim() && !collectForm.value.manualTitle.trim() && !collectForm.value.rawText.trim()) {
      setError('请先输入产品网址，或填写手动标题/粘贴原始文本。')
      return
    }
    loading.value = true
    setError('')
    collectDiagnostics.value = {
      ...collectDiagnostics.value,
      status: 'running',
      progress: 20,
      message: '正在提交采集任务...',
      lastSourceUrl: collectForm.value.productUrl,
    }
    try {
      const result = await collectProductApi(collectForm.value, credentials)
      product.value = result.product
      applyMutationIndexes(result)
      syncCollectDiagnosticsFromProduct('采集完成。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(`采集完成：${product.value.source.title || product.value.name || product.value.productId || '未命名商品'}`)
      if (collectForm.value.autoAiRecognition) addLog('已开启自动 AI 识别：请进入“文案”页面生成平台文案。')
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : '采集失败'
      collectDiagnostics.value = {
        ...collectDiagnostics.value,
        status: 'failed',
        progress: 0,
        message,
        antiBotWarning: message.includes('安全验证') || message.includes('Cookie') || message.includes('反爬') || message.includes('验证码'),
      }
      setError(message)
    } finally {
      loading.value = false
    }
  }

  async function collectBatch(credentials: TransientCollectCredentials = {}) {
    if (!collectForm.value.productUrls.trim()) {
      setError('请先输入多链接，每行一个商品链接。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await collectBatchApi(collectForm.value, credentials)
      collectBatchRows.value = result.rows
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      const firstOk = result.rows.find((row) => row.product)
      if (firstOk?.product) product.value = firstOk.product
      syncCollectDiagnosticsFromProduct(`批量采集完成：${result.rows.filter((row) => row.ok).length}/${result.rows.length} 成功。`)
      currentStage.value = 1
      addLog(`批量采集完成：${result.rows.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量采集失败')
    } finally {
      loading.value = false
    }
  }

  async function collectFromBrowserTab(saveOnly = false) {
    loading.value = true
    setError('')
    try {
      const result = await collectFromBrowserTabApi(collectForm.value, saveOnly)
      product.value = result.product
      applyMutationIndexes(result)
      if (result.browserStatus) browserDebugStatus.value = result.browserStatus
      syncCollectDiagnosticsFromProduct(saveOnly ? 'HTML 快照已保存。' : '已从浏览器标签采集。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(saveOnly ? '已保存当前浏览器标签 HTML 快照。' : '已从当前浏览器标签采集商品。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '从浏览器标签采集失败')
    } finally {
      loading.value = false
    }
  }

  async function open1688Browser() {
    loading.value = true
    setError('')
    try {
      const message = await open1688BrowserApi()
      addLog(message)
      await checkBrowserDebugStatus()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '打开 1688 浏览器失败')
    } finally {
      loading.value = false
    }
  }

  async function checkBrowserDebugStatus() {
    try {
      browserDebugStatus.value = await fetchBrowserDebugStatus()
      addLog(browserDebugStatus.value.connected ? '浏览器调试端口已连接。' : `浏览器未连接：${browserDebugStatus.value.errorMessage}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '检测浏览器失败')
    }
  }

  async function openDebugProfile() {
    try {
      lastAuthResult.value = await openBrowserProfile()
      addLog(lastAuthResult.value.message || '已请求打开浏览器 profile。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '打开浏览器 profile 失败')
    }
  }

  async function importManual() {
    loading.value = true
    setError('')
    try {
      const result = await importManualProduct(collectForm.value)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('手动导入完成。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(`手动导入完成：${product.value.source.title || product.value.name || '未命名商品'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '手动导入失败')
    } finally {
      loading.value = false
    }
  }

  async function previewClean1688Text() {
    const raw = `${collectForm.value.rawText || collectForm.value.manualDescription || ''}`.trim()
    if (!raw) {
      setError('请先在“原始文本 / HTML 导入”里粘贴 1688 文本或 HTML。')
      return
    }
    loading.value = true
    setError('')
    try {
      const cleaned = await clean1688Text(raw, collectForm.value.productUrl)
      collectForm.value.platform = '1688'
      collectForm.value.manualTitle = String(cleaned.title || collectForm.value.manualTitle || '')
      collectForm.value.manualPrice = String(cleaned.source_price_cny || cleaned.source_price_cny_for_cost || collectForm.value.manualPrice || '')
      collectForm.value.manualDimensions = String(cleaned.dimensions || collectForm.value.manualDimensions || '')
      collectForm.value.manualWeight = String(cleaned.source_weight_kg || collectForm.value.manualWeight || '')
      collectForm.value.manualDescription = String(cleaned.clean_source_text || cleaned.source_text || collectForm.value.manualDescription || '')
      if (Array.isArray(cleaned.images) && cleaned.images.length) collectForm.value.manualImages = cleaned.images.map(String).join('\n')
      collectDiagnostics.value = {
        ...collectDiagnostics.value,
        status: cleaned.ok === false ? 'failed' : 'success',
        progress: cleaned.ok === false ? 0 : 100,
        message: String(cleaned.message || '1688 文本已清洗，可检查字段后导入商品库。'),
        downloadedImages: Array.isArray(cleaned.images) ? cleaned.images.length : collectDiagnostics.value.downloadedImages,
        extractedBullets: Array.isArray(cleaned.package_includes) ? cleaned.package_includes.length : collectDiagnostics.value.extractedBullets,
        antiBotWarning: Boolean(cleaned.manual_required),
        raw: cleaned,
      }
      addLog(`1688 文本清洗完成：${collectForm.value.manualTitle || '未识别标题'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '1688 文本清洗失败')
    } finally {
      loading.value = false
    }
  }

  async function clearCollectedProduct() {
    product.value = createEmptyProduct()
    pricingResult.value = null
    category.value = null
    precheck.value = null
    publishJob.value = null
    payloadPreview.value = null
    collectDiagnostics.value = {
      ...createDefaultCollectDiagnostics(),
      message: '已清空当前商品，等待重新采集。',
    }
    currentStage.value = 0
    setError('')
    addLog('已清空当前商品。')
  }

  async function saveCollectSettings(credentials: TransientCollectCredentials = {}) {
    loading.value = true
    setError('')
    try {
      await saveCollectSettingsApi(collectForm.value, credentials)
      addLog(`采集设置已保存：模式 ${collectForm.value.mode}，输出目录 ${collectForm.value.outputDir || '默认目录'}。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存采集设置失败')
    } finally {
      loading.value = false
    }
  }


  return {
    loadState, resetForm, collectProduct, collectBatch, collectFromBrowserTab, open1688Browser,
    checkBrowserDebugStatus, openDebugProfile, importManual, previewClean1688Text, clearCollectedProduct, saveCollectSettings,
  }
}
