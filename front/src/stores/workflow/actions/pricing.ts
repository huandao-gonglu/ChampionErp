import { saveDraft as saveDraftApi } from '@/api/workflow/catalog'
import { calculatePrice as calculatePriceApi } from '@/api/workflow/publishing'
import type { DraftDetail, PricingResult, PricingTargetResult, UnknownRecord } from '@/types/workflow'
import type { WorkflowRuntime } from '../orchestration/runtime'

type WorkflowPricingActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'currentDraft'
  | 'currentDraftProductContext'
  | 'pricingInput'
  | 'pricingResult'
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'currentStage'
  | 'applyMutationIndexes'
  | 'syncPricingInputFromProduct'
  | 'syncDraftPackageDimensionsFromPricingInput'
>

export function createWorkflowPricingActions(runtime: WorkflowPricingActionsPort) {
  const {
    product, currentDraft, currentDraftProductContext, pricingInput, pricingResult,
    loading, addLog, setError, currentStage, applyMutationIndexes,
    syncPricingInputFromProduct, syncDraftPackageDimensionsFromPricingInput,
  } = runtime

  function pricingResultRecord(result: PricingTargetResult): UnknownRecord {
    return {
      target_key: result.targetKey,
      platform: result.platform,
      site: result.site,
      currency: result.currency,
      suggested_price: result.suggestedPrice,
      suggested_price_usd: result.suggestedPriceUsd,
      suggested_price_cny: result.suggestedPriceCny,
      applied_price: result.appliedPrice || result.suggestedPrice,
      shipping_cost_usd: result.shippingCostUsd,
      shipping_cost_cny: result.shippingCostCny,
      total_cost_cny: result.totalCostCny,
      net_revenue_cny: result.netRevenueCny,
      profit_cny: result.profitCny,
      margin_percent: result.marginPercent,
      commission_percent: result.commissionPercent,
      payment_fee_percent: result.paymentFeePercent,
      target_margin_percent: result.targetMarginPercent,
      usd_cny_rate: result.usdCnyRate,
      mxn_usd_rate: result.mxnUsdRate,
      rub_cny_rate: result.rubCnyRate,
      is_loss: result.isLoss,
      errors: result.errors,
    }
  }

  function buildDraftPricing(result: PricingResult): UnknownRecord {
    const targets = Object.fromEntries(result.results.map((item) => [item.targetKey, pricingResultRecord(item)]))
    const primary = result.results[0]
    return {
      common: {
        purchase_cost_cny: pricingInput.value.purchaseCostCny,
        domestic_freight_cny: pricingInput.value.domesticFreightCny,
        weight_kg: pricingInput.value.weightKg,
        length_cm: pricingInput.value.lengthCm,
        width_cm: pricingInput.value.widthCm,
        height_cm: pricingInput.value.heightCm,
        commission_percent: pricingInput.value.commissionPercent,
        target_margin_percent: pricingInput.value.targetMarginPercent,
        usd_cny_rate: result.usdCnyRate || pricingInput.value.usdCnyRate,
        mxn_usd_rate: result.mxnUsdRate || pricingInput.value.mxnUsdRate,
        rub_cny_rate: result.rubCnyRate || pricingInput.value.rubCnyRate,
        exchange_rate_mode: result.exchangeRateMode || pricingInput.value.exchangeRateMode,
        display_currency_mode: pricingInput.value.displayCurrencyMode,
      },
      targets,
      suggested_price: primary?.suggestedPrice || 0,
      applied_price: primary ? primary.appliedPrice || primary.suggestedPrice : 0,
      currency: primary?.currency || '',
      target_key: primary?.targetKey || '',
      exchange_rates: {
        mode: result.exchangeRateMode,
        source: result.exchangeRateSource,
        fetched_at: result.exchangeRateFetchedAt,
        cached: result.exchangeRateCached,
      },
      updated_at: new Date().toISOString(),
    }
  }

  async function calculatePrice() {
    if (!currentDraft.value.draftId) {
      setError('请先从草稿箱选择一个草稿再核价。')
      return
    }
    if (!pricingInput.value.targets.length) {
      setError('当前草稿没有可核价的目标市场，请先在草稿箱选择市场。')
      return
    }
    loading.value = true
    setError('')
    try {
      pricingResult.value = await calculatePriceApi(pricingInput.value)
      if (pricingResult.value.usdCnyRate > 0) pricingInput.value.usdCnyRate = pricingResult.value.usdCnyRate
      if (pricingResult.value.mxnUsdRate > 0) pricingInput.value.mxnUsdRate = pricingResult.value.mxnUsdRate
      if (pricingResult.value.rubCnyRate > 0) pricingInput.value.rubCnyRate = pricingResult.value.rubCnyRate
      const primary = pricingResult.value.results[0]
      const packageDimensions = syncDraftPackageDimensionsFromPricingInput()
      const draftToSave: DraftDetail = {
        ...currentDraft.value,
        price: primary ? String(primary.appliedPrice || primary.suggestedPrice || '') : currentDraft.value.price,
        pricing: buildDraftPricing(pricingResult.value),
        packageDimensions,
      }
      const saved = await saveDraftApi(draftToSave)
      currentDraft.value = saved.draft
      currentDraftProductContext.value = saved.productContext
      syncPricingInputFromProduct()
      if (product.value.productId && product.value.productId === saved.draft.productId) {
        product.value.drafts[saved.draft.platform] = saved.draft
      }
      applyMutationIndexes(saved)
      currentStage.value = 5
      addLog(`核价完成：${pricingResult.value.results.length} 个目标市场已写入草稿。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '核价失败')
    } finally {
      loading.value = false
    }
  }


  return {
    calculatePrice,
  }
}
