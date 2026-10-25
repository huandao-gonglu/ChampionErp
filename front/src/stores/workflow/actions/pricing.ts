import { nextTick } from 'vue'
import { pricingErrorReason } from '@/utils/pricingWorkspace'
import { draftPricingPayload, priceDraft, restoreDraftPricing } from '@/api/workflow/publishing'
import { selectedPricingTargets } from '@/types/pricing'
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
  let preview: { id: string; fingerprint: string } | null = null

  async function restorePricingPreview() {
    if (loading.value || !currentDraft.value.draftId) return
    const draft = JSON.parse(JSON.stringify(currentDraft.value)) as DraftDetail
    const input = JSON.parse(JSON.stringify(pricingInput.value)) as PricingInput
    const fingerprint = JSON.stringify(draftPricingPayload(draft, input))
    preview = null
    if (pricingResult.value) pricingResult.value = { ...pricingResult.value, previewId: undefined }
    loading.value = true
    setError('')
    try {
      const batch = await restoreDraftPricing(draft, input)
      if (JSON.stringify(draftPricingPayload(currentDraft.value, pricingInput.value)) !== fingerprint || !batch) return
      if (batch.items[0]) acceptPreview(batch.items[0].result)
      await nextTick()
      if (JSON.stringify(draftPricingPayload(currentDraft.value, pricingInput.value)) !== fingerprint) return
      for (const row of currentDraft.value.skuItems) {
        const pricing = batch.pricingBySku[row.sku_id]
        if (pricing) row.pricing = pricing
      }
      if (batch.previewId) preview = { id: batch.previewId, fingerprint }
    } catch (exc) {
      if (currentDraft.value.draftId === draft.draftId) setError(exc instanceof Error ? exc.message : '恢复核价预览失败')
    } finally {
      loading.value = false
    }
  }

  function validatePricingContext() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择一个草稿再核价。')
      return false
    }
    if (currentDraft.value.targetSites.some(target => isMercadoLibreCbtTarget(target)
      && (!pricingInput.value.selectedTargetKeys || pricingInput.value.selectedTargetKeys.some(key => key.toLowerCase() === `${target.platform}:${target.site}`.toLowerCase())))) {
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
    if (!selectedPricingTargets(pricingInput.value).length) {
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
        if (manualCurrency && manualCurrency !== resolvedCurrency) {
          // 店铺发布币种已变化，原金额币种含义失效，需要用户重新确认。
          target.manualPrice = null
        }
      }
      target.listingCurrency = resolved.listingCurrency
      target.currencyFingerprint = resolved.currencyFingerprint
      // 自动运费属于 SKU 的结果，不能回填为该市场全部规格的共用默认运费。
    })
    if (pricingInput.value.exchangeRateMode !== 'manual') {
      if (result.usdCnyRate > 0) pricingInput.value.usdCnyRate = result.usdCnyRate
      if (result.mxnUsdRate > 0) pricingInput.value.mxnUsdRate = result.mxnUsdRate
      if (result.rubCnyRate > 0) pricingInput.value.rubCnyRate = result.rubCnyRate
    }
  }

  async function calculateSkus(apply: boolean) {
    if (loading.value) return
    if (!validatePricingContext()) return
    const currentFingerprint = JSON.stringify(draftPricingPayload(currentDraft.value, pricingInput.value))
    if (apply && (!preview || preview.fingerprint !== currentFingerprint)) {
      setError('请先计算核价，确认当前参数的报价后再应用售价。')
      return
    }
    const previewId = preview?.id
    preview = null
    loading.value = true
    pricingResult.value = null
    for (const row of currentDraft.value.skuItems) if (row.pricing.applied) row.pricing.applied = false
    setError('')
    try {
      const draft = JSON.parse(JSON.stringify(currentDraft.value)) as DraftDetail
      const input = JSON.parse(JSON.stringify(pricingInput.value)) as PricingInput
      const rows = draft.skuItems.filter(row => row.selected)
      const fingerprint = JSON.stringify(draftPricingPayload(draft, input))
      addLog(`${apply ? '开始保存售价' : '开始核价'}：${rows.length} 个 SKU × ${selectedPricingTargets(input).length} 个目标市场。`)
      const batch = apply ? await priceDraft(draft, input, true, previewId) : await priceDraft(draft, input, false)
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
        const failedSkus = new Set(batch.errors.map(error => error.sku_id).filter(Boolean)).size
        const reasons = [...new Set(batch.errors.map(error => String(error.message || error.field || '核价失败')))]
        const reason = reasons.length === 1 ? pricingErrorReason(reasons[0]) : `${reasons.length} 类问题，请查看异常详情`
        setError(`${failedSkus ? `${failedSkus} 个 SKU 核价未完成` : '核价未完成'}：${reason}。`)
        return
      }
      if (apply && !batch.applied) throw new Error('核价尚未应用，请处理返回的问题后重试。')
      if (!apply && batch.previewId) preview = { id: batch.previewId, fingerprint }
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
    restorePricingPreview,
    calculatePrice,
    applyPrice,
  }
}
