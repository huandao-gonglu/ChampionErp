import { nextTick } from 'vue'
import { draftPricingPayload, priceDraft } from '@/api/workflow/publishing'
import type {
  DraftDetail,
  PricingInput,
  PricingResult,
} from '@/types/workflow'
import {
  cbtDestinationSelectionReady,
  isMercadoLibreCbtTarget,
  MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE,
  mercadoLibreHasFullyManagedBinding,
  mercadoLibreListingModel,
  mercadoLibreListingModelError,
} from '@/utils/mercadolibreGlobalSelling'
import type { WorkflowRuntime } from '../orchestration/runtime'

type WorkflowPricingActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'currentDraft'
  | 'pricingInput'
  | 'pricingResult'
  | 'storeConfig'
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'currentStage'
  | 'applyMutationIndexes'
>

export function createWorkflowPricingActions(runtime: WorkflowPricingActionsPort) {
  const {
    product, currentDraft, pricingInput, pricingResult, storeConfig,
    loading, addLog, setError, currentStage, applyMutationIndexes,
  } = runtime

  function validatePricingContext() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择一个草稿再核价。')
      return false
    }
    if (currentDraft.value.targetSites.some(isMercadoLibreCbtTarget)) {
      const listingModel = mercadoLibreListingModel(storeConfig.value)
      if (!listingModel) {
        setError(mercadoLibreListingModelError(storeConfig.value))
        return false
      }
      if (mercadoLibreHasFullyManagedBinding(storeConfig.value)) {
        setError(MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE)
        return false
      }
      if (!cbtDestinationSelectionReady(currentDraft.value, storeConfig.value)) {
        setError('CBT 草稿至少需要选择一个当前授权的销售国家/物流后才能核价。')
        return false
      }
    }
    if (!currentDraft.value.skuItems.some(row => row.selected)) {
      setError('请先在 SKU 页选择需要发布的规格。')
      return false
    }
    if (!pricingInput.value.targets.length) {
      setError('当前草稿没有可核价的目标市场，请先在草稿箱选择市场。')
      return false
    }
    return true
  }

  function acceptPreview(result: PricingResult) {
    pricingResult.value = result
    const resultsByTarget = new Map(result.results.map((item) => [item.targetKey.toLowerCase(), item]))
    pricingInput.value.targets.forEach((target) => {
      const resolved = resultsByTarget.get(target.targetKey.toLowerCase())
      if (!resolved) return
      if (target.manualPrice) {
        const manualCurrency = String(target.manualPrice.currency || '').trim().toUpperCase()
        const resolvedCurrency = String(resolved.listingCurrency || '').trim().toUpperCase()
        if (!manualCurrency) {
          // 手动售价在店铺币种解析前录入时币种为空；金额本就以发布币种计，直接补齐。
          target.manualPrice = { ...target.manualPrice, currency: resolved.listingCurrency }
        } else if (manualCurrency !== resolvedCurrency) {
          // 店铺发布币种已变化，原金额币种含义失效，需要用户重新确认。
          target.manualPrice = null
        }
      }
      target.listingCurrency = resolved.listingCurrency
      target.currencyFingerprint = resolved.currencyFingerprint
      // 自动运费属于 SKU 的结果，不能回填为该市场全部规格的共用默认运费。
    })
    if (result.usdCnyRate > 0) pricingInput.value.usdCnyRate = result.usdCnyRate
    if (result.mxnUsdRate > 0) pricingInput.value.mxnUsdRate = result.mxnUsdRate
    if (result.rubCnyRate > 0) pricingInput.value.rubCnyRate = result.rubCnyRate
  }

  async function calculateSkus(apply: boolean) {
    if (!validatePricingContext()) return
    loading.value = true
    setError('')
    try {
      const draft = JSON.parse(JSON.stringify(currentDraft.value)) as DraftDetail
      const input = JSON.parse(JSON.stringify(pricingInput.value)) as PricingInput
      const rows = draft.skuItems.filter(row => row.selected)
      const fingerprint = JSON.stringify(draftPricingPayload(draft, input))
      addLog(`开始核价：${rows.length} 个 SKU × ${input.targets.length} 个目标市场。`)
      const batch = await priceDraft(draft, input, apply)
      if (JSON.stringify(draftPricingPayload(currentDraft.value, pricingInput.value)) !== fingerprint) {
        if (batch.applied && batch.draft && currentDraft.value.draftId === draft.draftId && currentDraft.value.updatedAt === draft.updatedAt) {
          currentDraft.value.updatedAt = batch.draft.updatedAt
          for (const row of currentDraft.value.skuItems) row.pricing.applied = false
          applyMutationIndexes(batch)
        }
        throw new Error(batch.applied
          ? '提交时的核价参数已应用；页面的新修改已保留，请重新核价后继续。'
          : '核价期间 SKU、市场或参数已改变，请重新核价。')
      }
      if (batch.items[0]) acceptPreview(batch.items[0].result)
      if (batch.applied && batch.draft) {
        const saved = batch.draft
        // 保留标题、图片、属性等未提交的页面编辑，只合并本次核价保存的字段。
        currentDraft.value.pricing = saved.pricing
        currentDraft.value.updatedAt = saved.updatedAt
        currentDraft.value.lastPrecheck = saved.lastPrecheck
        currentDraft.value.lastPrecheckTarget = saved.lastPrecheckTarget
        currentDraft.value.publishStatus = saved.publishStatus
        currentDraft.value.status = saved.status
        currentDraft.value.targetSites = currentDraft.value.targetSites.map(target => {
          const result = saved.targetSites.find(item => item.platform === target.platform && item.site === target.site)
          return result ? { ...target, listingCurrency: result.listingCurrency, currencyFingerprint: result.currencyFingerprint,
            lastPrecheck: result.lastPrecheck, lastPrecheckTarget: result.lastPrecheckTarget,
            publishStatus: result.publishStatus, status: result.status } : target
        })
        if (product.value.productId === saved.productId) product.value.drafts[saved.platform] = saved
        for (const row of currentDraft.value.skuItems) {
          const savedRow = saved.skuItems.find(item => item.sku_id === row.sku_id)
          if (savedRow) row.pricing_overrides = savedRow.pricing_overrides
        }
        applyMutationIndexes(batch)
        currentStage.value = 5
      }
      // 等费用与 SKU 表单的失效 watcher 处理系统回填，再写入本轮结果。
      await nextTick()
      for (const row of currentDraft.value.skuItems) {
        const pricing = batch.pricingBySku[row.sku_id]
        if (pricing) row.pricing = pricing
      }
      if (batch.errors.length) {
        setError(`核价需要处理：${batch.errors.map(error => `${error.sku_id || ''}：${error.message || error.field || ''}`).join('；')}`)
        return
      }
      if (apply && !batch.applied) throw new Error('核价尚未应用，请处理返回的问题后重试。')
      addLog(`${batch.applied ? '售价已应用' : '核价预览完成，尚未应用售价'}：${rows.length} 个 SKU；耗时 ${(batch.metrics.durationMs / 1000).toFixed(2)} 秒。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '核价失败')
    } finally {
      loading.value = false
    }
  }

  async function calculatePrice() { await calculateSkus(false) }
  async function applyPrice() { await calculateSkus(true) }


  return {
    calculatePrice,
    applyPrice,
  }
}
