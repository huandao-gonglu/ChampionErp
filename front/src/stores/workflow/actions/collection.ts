import {

  collectBatch as collectBatchApi,
  resumeCollection,
  collectProduct as collectProductApi,
  inspectCollectionVerification,
  type CollectionAttempt,
  saveCollectSettings as saveCollectSettingsApi,
} from '@/api/workflow/catalog'
import { fetchState } from '@/api/workflow/state'
import { createDefaultCollectDiagnostics, createDefaultCollectForm, createEmptyProduct } from '@/constants/initialState'
import type { CollectBatchRow, CollectionVerification } from '@/types/workflow'
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
    product, productsIndex, collectForm, collectDiagnostics,
    collectBatchRows, fillFormFromState, pricingResult, category,
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

  async function waitForVerification(initial: CollectionVerification, row?: Pick<CollectBatchRow, 'status' | 'verification' | 'error' | 'nextAction'>) {
    let verification = initial
    const controller = new AbortController()
    verificationController = controller
    const cancelled = () => { if (controller.signal.aborted) throw new VerificationCancelled() }
    try {
      while (true) {
        cancelled()
        if (row) Object.assign(row, { status: 'waiting_verification', verification, error: '', nextAction: '请在原商品页完成登录或验证。登录后若仍在等待，请刷新原商品页。' })
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
        if (row) row.status = 'running'
        collectDiagnostics.value = { ...collectDiagnostics.value, status: 'running', message: '验证已完成，正在继续采集原商品页…', nextAction: '' }
        const result = await resumeCollection(verification)
        cancelled()
        if (result.verification) { verification = result.verification; continue }
        if (row) row.verification = undefined
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
      if (!Array.isArray(aiConfig.value.ai_use_cases)) aiConfig.value = publicAppConfig
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

  function updateCollectBatchRows(rows: CollectBatchRow[]) {
    if (!loading.value) collectBatchRows.value = rows
  }

  async function collectBatch(rowIds?: string[]) {
    if (loading.value) return
    const requested = rowIds ? new Set(rowIds) : null
    const rows = collectBatchRows.value.filter((row) => requested ? requested.has(row.id) && row.status !== 'running' : ['pending', 'waiting_verification'].includes(row.status))
    if (!rows.length) {
      setError('列表中没有待采集的链接，请先添加 URL 或重试失败项。')
      return
    }
    // 固定本轮参数，避免切换页面或修改表单影响尚未开始的链接。
    const form = { ...collectForm.value, platform: 'unknown' }
    loading.value = true
    setError('')
    collectDiagnostics.value = { ...createDefaultCollectDiagnostics(), status: 'running', message: '正在准备批量采集…' }
    let completed = 0
    let succeeded = 0
    let cancelled = false
    try {
      for (const row of rows) {
        Object.assign(row, { status: 'running', ok: false, error: '', errorCode: '', nextAction: '' })
        collectDiagnostics.value = {
          ...collectDiagnostics.value, status: 'running',
          progress: Math.round(completed / rows.length * 100),
          message: `正在采集第 ${completed + 1} / ${rows.length} 条`, lastSourceUrl: row.url,
        }
        try {
          // 复用后端批量采集能力，每次提交一条，收到实际结果后才推进列表。
          const result = row.verification
            ? { rows: [row], productsIndex: productsIndex.value }
            : await collectBatchApi({ ...form, productUrls: row.url })
          let collected = result.rows.find((item) => item.url === row.url)
          if (!collected) throw new Error('服务端未返回该链接的采集结果。')
          if (collected.verification) {
            const resumed = await waitForVerification(collected.verification, row)
            applyMutationIndexes(resumed)
            const nextProduct = resumed.product
            collected = {
              ...collected, verification: undefined, ok: true, status: 'success', error: '', errorCode: '',
              nextAction: String(resumed.diagnostics?.next_action || ''), product: nextProduct,
              productId: nextProduct.productId, title: nextProduct.source.title || nextProduct.name,
              image: nextProduct.source.imagePool[0]?.previewUrl || nextProduct.source.imagePool[0]?.url || '',
            }
          } else applyMutationIndexes(result)
          Object.assign(row, collected, { status: collected.ok ? 'success' : 'failed', verification: undefined })
          if (collected.status === 'partial') row.error = collected.error || '仅采集到部分资料，请检查采集箱并补充或重试。'
          if (!collected.ok && !row.error) row.error = '采集未完成，请查看失败原因或重试。'
          if (collected.ok) {
            succeeded++
            if (collected.product) {
              product.value = collected.product
              syncPricingInputFromProduct()
              currentStage.value = 1
            }
          }
        } catch (exc) {
          if (exc instanceof VerificationCancelled) {
            row.nextAction = '已取消等待；保留原商品页，点击开始采集可继续。'
            cancelled = true
            break
          }
          row.verification = undefined
          row.status = 'failed'
          row.error = exc instanceof Error ? exc.message : '采集失败'
          row.nextAction = '请检查失败原因后重试当前链接。'
        }
        completed++
      }
      if (cancelled) { showVerificationCancelled(); return }
      const message = `本轮采集结束：${succeeded} 条完成，${completed - succeeded} 条失败。`
      collectDiagnostics.value = {
        ...collectDiagnostics.value, status: succeeded === completed ? 'success' : 'failed',
        progress: Math.round(completed / rows.length * 100), message,
        nextAction: succeeded < completed ? '查看列表中的失败原因，可编辑链接后重试。' : '可前往采集箱检查采集结果。',
      }
      addLog(message)
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
    loadState, resetForm, collectProduct, collectBatch, updateCollectBatchRows, clearCollectedProduct, saveCollectSettings,
  }
}
