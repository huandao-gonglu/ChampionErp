import { saveDraft as saveDraftApi } from '@/api/workflow/catalog'
import { calculatePrice as calculatePriceApi } from '@/api/workflow/publishing'
import type {
  DraftDetail,
  MarketplaceSiteToSell,
  MarketplaceTargetSite,
  PricingDestinationResult,
  PricingResult,
  PricingTargetResult,
  UnknownRecord,
} from '@/types/workflow'
import {
  cbtDestinationSelectionReady,
  isMercadoLibreCbtTarget,
  MERCADOLIBRE_FULLY_MANAGED_UNSUPPORTED_MESSAGE,
  mercadoLibreHasFullyManagedBinding,
  mercadoLibreBindingPricingMode,
  mercadoLibreDestinationKey,
  mercadoLibreListingModel,
  mercadoLibreListingModelError,
  mercadoLibreSelectableBindings,
} from '@/utils/mercadolibreGlobalSelling'
import type { WorkflowRuntime } from '../orchestration/runtime'

type WorkflowPricingActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'currentDraft'
  | 'currentDraftProductContext'
  | 'pricingInput'
  | 'pricingResult'
  | 'storeConfig'
  | 'platformOptions'
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
    product, currentDraft, currentDraftProductContext, pricingInput, pricingResult, storeConfig,
    platformOptions, loading, addLog, setError, currentStage, applyMutationIndexes,
    syncPricingInputFromProduct, syncDraftPackageDimensionsFromPricingInput,
  } = runtime

  function pricingResultRecord(result: PricingTargetResult): UnknownRecord {
    return {
      target_key: result.targetKey,
      platform: result.platform,
      site: result.site,
      listing_currency: result.listingCurrency,
      currency_fingerprint: result.currencyFingerprint || '',
      suggested_price: result.suggestedPrice,
      applied_price: result.appliedPrice,
      applied_net_proceeds: result.appliedNetProceeds,
      destination_results: result.destinationResults.map((destination) => ({
        site_id: destination.siteId,
        logistic_type: destination.logisticType,
        pricing_model: destination.pricingModel,
        price: destination.price,
        net_proceeds: destination.netProceeds,
        calculation_fingerprint: destination.calculationFingerprint || '',
      })),
      converted_prices: result.convertedPrices,
      calculation_basis: result.calculationBasis,
      calculation_fingerprint: result.calculationFingerprint,
      shipping_cost_usd: result.shippingCostUsd,
      shipping_cost_cny: result.shippingCostCny,
      total_cost_cny: result.totalCostCny,
      net_revenue_cny: result.netRevenueCny,
      profit_cny: result.profitCny,
      margin_percent: result.marginPercent,
      commission_percent: result.commissionPercent,
      payment_fee_percent: result.paymentFeePercent,
      other_fee_percent: result.otherFeePercent,
      pricing_mode: result.pricingMode,
      target_margin_percent: result.targetMarginPercent,
      markup_percent: result.markupPercent,
      shipping_quote_mode: result.shippingQuoteMode,
      shipping_currency: result.shippingCurrency,
      shipping_amount: result.shippingAmount,
      shipping_source: result.shippingSource,
      commission_cny: result.commissionCny,
      payment_fee_cny: result.paymentFeeCny,
      other_fee_cny: result.otherFeeCny,
      minimum_price: result.minimumPrice,
      billable_weight_kg: result.billableWeightKg,
      usd_cny_rate: result.usdCnyRate,
      mxn_usd_rate: result.mxnUsdRate,
      rub_cny_rate: result.rubCnyRate,
      is_loss: result.isLoss,
      errors: result.errors,
    }
  }

  function buildDraftPricing(result: PricingResult): UnknownRecord {
    const targets = Object.fromEntries(result.results.map((item) => [item.targetKey, pricingResultRecord(item)]))
    return {
      common: {
        purchase_cost_cny: pricingInput.value.purchaseCostCny,
        domestic_freight_cny: pricingInput.value.domesticFreightCny,
        packaging_cost_cny: pricingInput.value.packagingCostCny,
        other_cost_cny: pricingInput.value.otherCostCny,
        weight_kg: pricingInput.value.weightKg,
        length_cm: pricingInput.value.lengthCm,
        width_cm: pricingInput.value.widthCm,
        height_cm: pricingInput.value.heightCm,
        usd_cny_rate: result.usdCnyRate || pricingInput.value.usdCnyRate,
        mxn_usd_rate: result.mxnUsdRate || pricingInput.value.mxnUsdRate,
        rub_cny_rate: result.rubCnyRate || pricingInput.value.rubCnyRate,
        exchange_rate_mode: result.exchangeRateMode || pricingInput.value.exchangeRateMode,
      },
      targets,
      exchange_rates: {
        mode: result.exchangeRateMode,
        source: result.exchangeRateSource,
        fetched_at: result.exchangeRateFetchedAt,
        cached: result.exchangeRateCached,
      },
      updated_at: new Date().toISOString(),
    }
  }

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
    if (!pricingInput.value.targets.length) {
      setError('当前草稿没有可核价的目标市场，请先在草稿箱选择市场。')
      return false
    }
    return true
  }

  function targetResultLabel(result: PricingTargetResult) {
    const option = platformOptions.value.find((item) => item.key === result.platform)
    const site = option?.sites.find((item) => item.code.toLowerCase() === result.site.toLowerCase())
    return `${option?.label || result.platform} · ${site?.label || result.site}`
  }

  function pricingErrors(result: PricingResult) {
    return result.results.flatMap((item) => item.errors.map((error) => {
      const message = typeof error === 'string' ? error : String(error.message || error.field || '')
      return message ? `${targetResultLabel(item)}：${message}` : ''
    }).filter(Boolean))
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
    })
    if (result.usdCnyRate > 0) pricingInput.value.usdCnyRate = result.usdCnyRate
    if (result.mxnUsdRate > 0) pricingInput.value.mxnUsdRate = result.mxnUsdRate
    if (result.rubCnyRate > 0) pricingInput.value.rubCnyRate = result.rubCnyRate
  }

  function resultMoneyAmount(result: PricingDestinationResult): number {
    const money = result.pricingModel === 'price' ? result.price : result.netProceeds
    return Number(money?.amount || 0)
  }

  function sitesToSellWithPricingResult(
    target: MarketplaceTargetSite,
    targetResult: PricingTargetResult,
  ): MarketplaceSiteToSell[] {
    if (!isMercadoLibreCbtTarget(target)) {
      return (target.sitesToSell || []).map((destination) => ({ ...destination }))
    }
    const bindings = new Map(mercadoLibreSelectableBindings(storeConfig.value).map((binding) => [
      mercadoLibreDestinationKey(binding.siteId, binding.logisticType),
      binding,
    ]))
    const resultByKey = new Map<string, PricingDestinationResult>()
    for (const destinationResult of targetResult.destinationResults) {
      const key = mercadoLibreDestinationKey(destinationResult.siteId, destinationResult.logisticType)
      if (resultByKey.has(key)) throw new Error(`核价结果包含重复销售目标 ${key}，请重新核价。`)
      resultByKey.set(key, destinationResult)
    }
    const destinations = target.sitesToSell || []
    if (!destinations.length || resultByKey.size !== destinations.length) {
      throw new Error('Mercado CBT 核价结果与当前销售市场不一致，请重新核价。')
    }
    return destinations.map((destination) => {
      const key = mercadoLibreDestinationKey(destination.siteId, destination.logisticType)
      const destinationResult = resultByKey.get(key)
      const binding = bindings.get(key)
      const expectedMode = binding ? mercadoLibreBindingPricingMode(binding, storeConfig.value) : ''
      if (!destinationResult || !expectedMode || destinationResult.pricingModel !== expectedMode) {
        throw new Error(`销售目标 ${key} 的核价模式与当前店铺授权不一致，请重新核价。`)
      }
      const hasPrice = destinationResult.price !== null
      const hasNetProceeds = destinationResult.netProceeds !== null
      const selectedMoney = expectedMode === 'price' ? destinationResult.price : destinationResult.netProceeds
      if (
        hasPrice === hasNetProceeds
        || !selectedMoney
        || selectedMoney.currency.toUpperCase() !== targetResult.listingCurrency.toUpperCase()
        || !Number.isFinite(resultMoneyAmount(destinationResult))
        || resultMoneyAmount(destinationResult) <= 0
      ) {
        throw new Error(`销售目标 ${key} 的核价金额无效，请重新核价。`)
      }
      const preserved = { ...destination }
      delete preserved.price
      delete preserved.netProceeds
      return {
        ...preserved,
        ...(expectedMode === 'price'
          ? { price: selectedMoney.amount }
          : { netProceeds: selectedMoney.amount }),
      }
    })
  }

  async function calculatePrice() {
    if (!validatePricingContext()) return
    loading.value = true
    setError('')
    try {
      const result = await calculatePriceApi(pricingInput.value)
      acceptPreview(result)
      const errors = pricingErrors(result)
      if (errors.length) {
        setError(`核价数据需要处理：${errors.join('；')}`)
        return
      }
      addLog(`核价预览完成：${result.results.length} 个目标市场，尚未写入草稿。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '核价失败')
    } finally {
      loading.value = false
    }
  }

  async function applyPrice() {
    if (!validatePricingContext()) return
    loading.value = true
    setError('')
    try {
      const result = await calculatePriceApi(pricingInput.value)
      acceptPreview(result)
      const errors = pricingErrors(result)
      if (errors.length) {
        setError(`无法应用售价：${errors.join('；')}`)
        return
      }
      if (result.results.some((item) => item.isLoss)) {
        setError('无法应用售价：至少一个目标市场会亏损，请提高售价后重新计算预览。')
        return
      }
      const primary = result.results[0]
      if (!primary || Number(primary.appliedPrice.amount) <= 0) {
        setError('无法应用售价：请先生成或填写有效售价。')
        return
      }
      const packageDimensions = syncDraftPackageDimensionsFromPricingInput()
      const resultsByTarget = new Map(result.results.map((item) => [item.targetKey.toLowerCase(), item]))
      const draftToSave: DraftDetail = {
        ...currentDraft.value,
        targetSites: currentDraft.value.targetSites.map((target) => {
          const key = `${target.platform}:${target.site}`.toLowerCase()
          const targetResult = resultsByTarget.get(key)
          return targetResult ? {
            ...target,
            listingCurrency: targetResult.listingCurrency,
            currencyFingerprint: targetResult.currencyFingerprint,
            sitesToSell: sitesToSellWithPricingResult(target, targetResult),
          } : target
        }),
        pricing: buildDraftPricing(result),
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
      addLog(`售价已应用：${result.results.length} 个目标市场已写入草稿。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '核价失败')
    } finally {
      loading.value = false
    }
  }


  return {
    calculatePrice,
    applyPrice,
  }
}
