import { saveDraft as saveDraftApi } from '@/api/workflow/catalog'
import { calculatePrice as calculatePriceApi } from '@/api/workflow/publishing'
import type {
  DraftDetail,
  DraftSku,
  PricingInput,
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
>

export function createWorkflowPricingActions(runtime: WorkflowPricingActionsPort) {
  const {
    product, currentDraft, currentDraftProductContext, pricingInput, pricingResult, storeConfig,
    platformOptions, loading, addLog, setError, currentStage, applyMutationIndexes,
    syncPricingInputFromProduct,
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

  function buildDraftPricing(result: PricingResult, input: PricingInput): UnknownRecord {
    const targets = Object.fromEntries(result.results.map((item) => [item.targetKey, pricingResultRecord(item)]))
    return {
      common: {
        purchase_cost_cny: input.purchaseCostCny,
        domestic_freight_cny: input.domesticFreightCny,
        packaging_cost_cny: input.packagingCostCny,
        other_cost_cny: input.otherCostCny,
        weight_kg: input.weightKg,
        length_cm: input.lengthCm,
        width_cm: input.widthCm,
        height_cm: input.heightCm,
        usd_cny_rate: result.usdCnyRate || input.usdCnyRate,
        mxn_usd_rate: result.mxnUsdRate || input.mxnUsdRate,
        rub_cny_rate: result.rubCnyRate || input.rubCnyRate,
        exchange_rate_mode: result.exchangeRateMode || input.exchangeRateMode,
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

  function inputForSku(row: DraftSku): PricingInput {
    const sku = currentDraftProductContext.value.skuItems.find(sku => sku.id === row.sku_id)
    if (!sku || !sku.active) throw new Error('已选择的 SKU 已停用或不存在，请重新选品。')
    const dims = { ...sku.package_dimensions, ...(row.overrides.package_dimensions as UnknownRecord || {}) }
    const cost = row.overrides.cost_cny ?? sku.cost_cny
    if (String(cost).trim() === '' || ['length_cm', 'width_cm', 'height_cm', 'weight_kg'].some(key => String(dims[key] ?? '').trim() === '')) {
      throw new Error(`${sku.name || row.sku_id}：请先补齐采购成本和包装长、宽、高、重量。`)
    }
    const overrides = row.pricing_overrides || {}
    const common = (overrides.common || {}) as UnknownRecord
    const targets = (overrides.targets || {}) as Record<string, UnknownRecord>
    return {
      ...JSON.parse(JSON.stringify(pricingInput.value)),
      purchaseCostCny: Number(cost),
      weightKg: Number(dims.weight_kg), lengthCm: Number(dims.length_cm),
      widthCm: Number(dims.width_cm), heightCm: Number(dims.height_cm),
      domesticFreightCny: Number(common.domestic_freight_cny ?? pricingInput.value.domesticFreightCny),
      packagingCostCny: Number(common.packaging_cost_cny ?? pricingInput.value.packagingCostCny),
      otherCostCny: Number(common.other_cost_cny ?? pricingInput.value.otherCostCny),
      targets: pricingInput.value.targets.map(target => {
        const own = targets[target.targetKey.toLowerCase()] || {}
        return { ...target,
          ...(own.shipping_amount !== undefined ? { shippingAmount: Number(own.shipping_amount), shippingQuoteMode: 'manual' as const } : {}),
          ...(own.manual_price ? { manualPrice: own.manual_price as PricingInput['targets'][number]['manualPrice'], pricingMode: 'manual' as const } : {}),
        }
      }),
    }
  }

  async function calculateSkus(apply: boolean) {
    if (!validatePricingContext()) return
    loading.value = true
    setError('')
    try {
      const rows = currentDraft.value.skuItems.filter(row => row.selected)
      const completed: { row: DraftSku; input: PricingInput; result: PricingResult }[] = []
      // 每项独立取物理资料；共享参数只作默认值，不能将首个 SKU 的报价复制给其他规格。
      for (const row of rows) {
        const input = inputForSku(row)
        const result = await calculatePriceApi(input)
        completed.push({ row, input, result })
      }
      if (completed[0]) acceptPreview(completed[0].result)
      const errors = completed.flatMap(({ row, result }) => [
        ...pricingErrors(result).map(error => `${row.sku || row.sku_id}：${error}`),
        ...(result.results.some(item => item.isLoss) ? [`${row.sku || row.sku_id}：售价会亏损`] : []),
        ...(apply && (!result.results.length || result.results.some(item => Number(item.appliedPrice.amount) <= 0)) ? [`${row.sku || row.sku_id}：缺少有效售价`] : []),
      ])
      const canApply = apply && !errors.length
      const staged: { row: DraftSku; pricing: UnknownRecord }[] = []
      for (const { row, input, result } of completed) {
        const pricing = { ...buildDraftPricing(result, input), applied: canApply } as UnknownRecord
        for (const target of currentDraft.value.targetSites) {
          const calculated = result.results.find(item => item.targetKey.toLowerCase() === `${target.platform}:${target.site}`.toLowerCase())
          if (calculated) {
            const savedTargets = pricing.targets as Record<string, UnknownRecord>
            savedTargets[calculated.targetKey].sites_to_sell = sitesToSellWithPricingResult(target, calculated).map(destination => ({
              site_id: destination.siteId, logistic_type: destination.logisticType,
              ...(destination.price !== undefined ? { price: destination.price } : {}),
              ...(destination.netProceeds !== undefined ? { net_proceeds: destination.netProceeds } : {}),
            }))
          }
        }
        staged.push({ row, pricing })
      }
      // 整组验证通过后再替换本地结果，避免中途异常留下部分已应用的售价。
      for (const { row, pricing } of staged) row.pricing = pricing
      if (errors.length) {
        setError(`核价需要处理：${errors.join('；')}`)
        return
      }
      if (!apply) {
        addLog(`核价预览完成：${rows.length} 个 SKU，尚未应用售价。`)
        return
      }
      const first = completed[0]!
      const shared = buildDraftPricing(first.result, pricingInput.value)
      for (const target of pricingInput.value.targets) {
        const record = (shared.targets as Record<string, UnknownRecord>)[target.targetKey]
        if (!record) continue
        Object.assign(record, { commission_percent: target.commissionPercent, payment_fee_percent: target.paymentFeePercent,
          other_fee_percent: target.otherFeePercent, pricing_mode: target.pricingMode, target_margin_percent: target.targetMarginPercent,
          markup_percent: target.markupPercent, shipping_quote_mode: target.shippingQuoteMode, shipping_currency: target.shippingCurrency,
          shipping_amount: target.shippingAmount })
        if (target.pricingMode === 'manual' && target.manualPrice) record.applied_price = { ...target.manualPrice }
      }
      // 草稿保存共用核价模板；实际发布只读取 SKU × 目标的已应用结果。
      const draftToSave: DraftDetail = { ...currentDraft.value, pricing: shared,
        targetSites: currentDraft.value.targetSites.map(target => {
          const result = first.result.results.find(item => item.targetKey.toLowerCase() === `${target.platform}:${target.site}`.toLowerCase())
          return result ? { ...target, listingCurrency: result.listingCurrency, currencyFingerprint: result.currencyFingerprint } : target
        }),
      }
      const saved = await saveDraftApi(draftToSave)
      currentDraft.value = saved.draft
      currentDraftProductContext.value = saved.productContext
      syncPricingInputFromProduct()
      if (product.value.productId && product.value.productId === saved.draft.productId) product.value.drafts[saved.draft.platform] = saved.draft
      applyMutationIndexes(saved)
      currentStage.value = 5
      addLog(`售价已应用：${rows.length} 个 SKU × ${first.result.results.length} 个目标市场。`)
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
