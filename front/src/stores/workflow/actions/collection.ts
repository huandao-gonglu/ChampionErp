import {

  resumeCollection,
  collectProduct as collectProductApi,
  inspectCollectionVerification,
  type CollectionAttempt,
  saveCollectSettings as saveCollectSettingsApi,
} from '@/api/workflow/catalog'
import { CollectionResultUnconfirmed } from '@/api/workflow/collectionReceipt'
import { fetchState } from '@/api/workflow/state'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'
import type { CollectionVerification } from '@/types/workflow'
import { sanitizePublicAppConfig } from '@/utils/configSecurity'
import {

  type WorkflowRuntime,
} from '../orchestration/runtime'

type WorkflowCollectionActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'collectForm'
  | 'collectDiagnostics'
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
    product, collectForm, collectDiagnostics,
    fillFormFromState, pricingResult, category,
    precheck, payloadPreview, publishJob,
    platformOptions, appConfig, aiConfig, storeConfig, storeAuthSummary,
    mercadolibreAuthChecklist, loading, addLog, setError,
    currentStage, applyMutationIndexes, restorePrecheckFromProduct, restoreCategoryFromProduct, syncCollectDiagnosticsFromProduct,
    syncPricingInputFromProduct,
  } = runtime

  class VerificationCancelled extends Error {}
  let verificationController: AbortController | null = null

  function cancelCollectionVerification() {
    if (collectDiagnostics.value.status === 'waiting_verification') verificationController?.abort()
  }

  async function waitForVerification(initial: CollectionVerification) {
    let verification = initial
    const controller = new AbortController()
    verificationController = controller
    const cancelled = () => { if (controller.signal.aborted) throw new VerificationCancelled() }
    try {
      while (true) {
        cancelled()
        collectDiagnostics.value = {
          ...collectDiagnostics.value, status: 'waiting_verification', errorCode: '',
          message: '等待人工验证', antiBotWarning: false, lastSourceUrl: verification.sourceUrl,
          nextAction: '请在采集浏览器完成登录或验证码，返回原商品页后会自动继续。若仍在等待，请刷新原商品页。',
        }
        // 等待只做只读检查，不重新发起 URL 采集，也不刷新目标页面。
        await new Promise<void>((resolve, reject) => {
          const abort = () => { clearTimeout(timer); reject(new VerificationCancelled()) }
          const timer = setTimeout(() => { controller.signal.removeEventListener('abort', abort); resolve() }, 2000)
          controller.signal.addEventListener('abort', abort, { once: true })
        })
        cancelled()
        const state = await inspectCollectionVerification(verification, controller.signal)
        cancelled()
        if (state.status === 'unavailable') throw new Error(state.message)
        if (state.status !== 'ready') continue
        collectDiagnostics.value = { ...collectDiagnostics.value, status: 'running', message: '验证已完成，正在继续采集原商品页…', nextAction: '' }
        const result = await resumeCollection(verification)
        cancelled()
        if (result.verification) { verification = result.verification; continue }
        return result
      }
    } catch (error) {
      cancelled()
      throw error
    } finally {
      verificationController = null
    }
  }

  async function completeCollectionAttempt(result: CollectionAttempt) {
    return result.verification ? waitForVerification(result.verification) : result
  }

  function showVerificationCancelled() {
    collectDiagnostics.value = { ...collectDiagnostics.value, status: 'idle', message: '已取消等待，后续链接尚未开始。', errorCode: '', nextAction: '原商品页已保留，可继续采集；批量列表中的等待项可点击开始采集恢复。' }
    setError('')
  }

  async function loadState(settingsOnly = false) {
    if (!settingsOnly) loading.value = true
    setError('')
    try {
      const state = await fetchState(settingsOnly)
      platformOptions.value = state.platformOptions
      const publicAppConfig = sanitizePublicAppConfig(state.appConfig)
      appConfig.value = publicAppConfig
      storeConfig.value = state.storeConfig
      storeAuthSummary.value = state.storeAuthSummary
      mercadolibreAuthChecklist.value = state.mercadolibreAuthChecklist || null
      if (!Array.isArray(aiConfig.value.ai_use_cases)) aiConfig.value = publicAppConfig
      if (!('product' in state)) {
        fillFormFromState(publicAppConfig, state.outputDir)
        return
      }
      product.value = state.product
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      fillFormFromState(publicAppConfig, state.outputDir)
      syncCollectDiagnosticsFromProduct('后端状态已加载。')
      syncPricingInputFromProduct()
      addLog('已读取后端当前商品、图片池和公共配置。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '读取后端状态失败')
    } finally {
      if (!settingsOnly) loading.value = false
    }
  }

  function resetForm() {
    collectForm.value = createDefaultCollectForm()
    collectDiagnostics.value = createDefaultCollectDiagnostics()
    setError('')
    addLog('已清空采集表单。')
  }

  async function collectProduct() {
    if (loading.value) return
    if (!collectForm.value.productUrl.trim()) {
      setError('请先输入商品链接。')
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
      const result = await completeCollectionAttempt(await collectProductApi(collectForm.value))
      product.value = result.product
      applyMutationIndexes(result)
      syncCollectDiagnosticsFromProduct('采集完成。', result.diagnostics)
      syncPricingInputFromProduct()
      currentStage.value = 1
      addLog(`采集完成：${product.value.source.title || product.value.name || product.value.productId || '未命名商品'}`)
    } catch (exc) {
      if (exc instanceof VerificationCancelled) { showVerificationCancelled(); return }
      const message = exc instanceof Error ? exc.message : '采集失败'
      collectDiagnostics.value = {
        ...collectDiagnostics.value,
        status: exc instanceof CollectionResultUnconfirmed ? 'unconfirmed' : 'failed',
        progress: 0,
        message,
        antiBotWarning: message.includes('安全验证') || message.includes('Cookie') || message.includes('反爬') || message.includes('验证码'),
      }
      setError(message)
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

  async function saveCollectSettings() {
    loading.value = true
    setError('')
    try {
      await saveCollectSettingsApi(collectForm.value)
      addLog(`已保存采集输出目录 ${collectForm.value.outputDir || '默认目录'}。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存采集设置失败')
    } finally {
      loading.value = false
    }
  }


  return {
    cancelCollectionVerification,
    loadState, resetForm, collectProduct, clearCollectedProduct, saveCollectSettings,
  }
}
