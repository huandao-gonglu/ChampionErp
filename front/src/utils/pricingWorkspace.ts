import type { DraftSku, PricingInput, PricingTargetInput } from '@/types/workflow'
import { pricingRecord } from './skuPricing'
import { selectedPricingTargets } from '@/types/pricing'

export function shippingInputCny(amount: unknown, currency: string, usdCnyRate: number): number | '' {
  if (amount === '' || amount == null || !Number.isFinite(Number(amount))) return ''
  if (currency !== 'CNY' && (!Number.isFinite(usdCnyRate) || !(usdCnyRate > 0))) return ''
  return Math.round(Number(amount) * (currency === 'CNY' ? 1 : usdCnyRate) * 100) / 100
}

export function updateShippingInputCny(input: PricingInput, rows: DraftSku[], targetKey: string, amount: number | '', usdCnyRate: number, skuId?: string) {
  const target = input.targets.find(item => item.targetKey === targetKey)
  if (!target || (amount !== '' && !Number.isFinite(amount))
    || (target.shippingCurrency !== 'CNY' && (!Number.isFinite(usdCnyRate) || !(usdCnyRate > 0)))) return
  // 仅在用户编辑时迁移整个市场的手动运费，避免 SKU 覆盖金额被直接改标币种。
  if (target.shippingCurrency !== 'CNY') {
    target.shippingAmount = shippingInputCny(target.shippingAmount, target.shippingCurrency, usdCnyRate) || 0
    for (const row of rows) {
      for (const [key, value] of Object.entries(pricingRecord(row.pricing_overrides?.targets))) {
        if (key.toLowerCase() !== targetKey.toLowerCase()) continue
        const own = pricingRecord(value)
        if (own.shipping_amount != null && own.shipping_amount !== '') {
          own.shipping_amount = shippingInputCny(own.shipping_amount, target.shippingCurrency, usdCnyRate)
        }
      }
    }
    target.shippingCurrency = 'CNY'
  }
  if (skuId === undefined) target.shippingAmount = amount === '' ? 0 : amount
  else {
    const row = rows.find(item => item.sku_id === skuId)
    if (!row) return
    const overrides = { ...pricingRecord(row.pricing_overrides?.targets) }
    const key = Object.keys(overrides).find(key => key.toLowerCase() === targetKey.toLowerCase()) || targetKey.toLowerCase()
    const own = { ...pricingRecord(overrides[key]) }
    if (amount === '') delete own.shipping_amount
    else own.shipping_amount = amount
    overrides[key] = own
    row.pricing_overrides = { ...row.pricing_overrides, targets: overrides }
  }
}

export function pricingErrorReason(message: string): string {
  if (message.includes('YANDEX_NETWORK_TIMEOUT')) return 'Yandex 费用查询超时，请稍后重试'
  if (message.includes('YANDEX_NETWORK_FAILED')) return 'Yandex 费用查询连接中断，请稍后重试'
  return message
}

function validNumber(value: unknown, minimum = 0) {
  return value !== '' && value !== null && value !== undefined && Number.isFinite(Number(value)) && Number(value) >= minimum
}

function fingerprintNumber(value: unknown) {
  if (value === '' || value === null || value === undefined) return null
  return Number.isFinite(Number(value)) ? Number(value) : String(value)
}

export function pricingCommonErrors(input: PricingInput): string[] {
  const errors: string[] = []
  for (const [label, value] of [['国内物流', input.domesticFreightCny], ['包装耗材', input.packagingCostCny], ['其他成本', input.otherCostCny]] as const) {
    if (!validNumber(value)) errors.push(`${label}需要填写不小于 0 的数值`)
  }
  for (const [key, value] of Object.entries(input.exchangeRateOverrides || {})) {
    if (!validNumber(value) || Number(value) <= 0) errors.push(`${key === 'usd_cny_rate' ? 'USD/CNY' : key === 'rub_cny_rate' ? 'CNY/RUB' : 'USD/MXN'} 汇率必须大于 0`)
  }
  return errors
}

export function pricingTargetErrors(target: PricingTargetInput): string[] {
  const errors: string[] = []
  for (const [label, value] of [['支付 / 结算费', target.paymentFeePercent], ['其他平台费', target.otherFeePercent]] as const) {
    if (!validNumber(value) || Number(value) >= 100) errors.push(`${label}必须在 0% 到 100% 之间`)
  }
  if (target.returnRatePercent !== '' && target.returnRatePercent != null && (!validNumber(target.returnRatePercent) || Number(target.returnRatePercent) >= 100)) errors.push('预计退货率必须不小于 0 且小于 100%')
  if (target.returnHandlingCostCny !== '' && target.returnHandlingCostCny != null && !validNumber(target.returnHandlingCostCny)) errors.push('每次退货额外费用必须不小于 0')
  const fees = Number(target.paymentFeePercent) + Number(target.otherFeePercent)
  if (fees >= 100) errors.push('平台费用合计必须小于 100%')
  if (target.pricingMode === 'margin' && (!validNumber(target.targetMarginPercent) || Number(target.targetMarginPercent) + fees >= 100)) errors.push('目标利润率与平台费用之和必须小于 100%')
  if (target.pricingMode === 'markup' && !validNumber(target.markupPercent)) errors.push('成本加价率需要填写不小于 0 的数值')
  if (target.pricingMode === 'manual' && (!validNumber(target.manualPrice?.amount) || Number(target.manualPrice?.amount) <= 0)) errors.push('手动售价必须大于 0')
  if (target.platform === 'yandex' && target.shippingQuoteMode !== 'auto') errors.push('Yandex 需要选择自动运费，以确定对应的发布仓库')
  if (target.shippingQuoteMode === 'manual' && (!validNumber(target.shippingAmount) || Number(target.shippingAmount) <= 0)) errors.push('手动国际运费必须大于 0')
  if (target.shippingQuoteMode === 'auto' && !['mercadolibre', 'ozon', 'yandex'].includes(target.platform)) errors.push('当前平台需要填写手动运费')
  return errors
}

export function pricingWorkspaceFingerprint(input: PricingInput, rows: DraftSku[]) {
  return JSON.stringify({
    templateSource: input.templateSource || null,
    common: [input.domesticFreightCny, input.packagingCostCny, input.otherCostCny,
      input.exchangeRateMode, input.exchangeRateOverrides,
      ...(input.exchangeRateMode === 'manual' ? [input.usdCnyRate, input.rubCnyRate, input.mxnUsdRate] : [])],
    targets: selectedPricingTargets(input).map(target => [target.targetKey, target.categoryId, target.sitesToSell,
      target.paymentFeePercent, target.otherFeePercent, target.pricingMode, target.targetMarginPercent,
      target.markupPercent, target.shippingQuoteMode, target.shippingCurrency, target.shippingAmount,
      target.returnRatePercent, target.returnDisposition, target.returnHandlingCostCny,
      target.shippingSelection?.warehouse_id, target.shippingSelection?.route_id, target.manualPrice?.amount]),
    skus: rows.map(row => {
      const fixed = pricingRecord(row.pricing_overrides?.common)
      const targets = pricingRecord(row.pricing_overrides?.targets)
      return [row.sku_id, row.selected,
        Object.fromEntries(Object.entries(fixed).map(([key, value]) => [key, fingerprintNumber(value)])),
        Object.fromEntries(Object.entries(targets).map(([key, value]) => {
          const item = pricingRecord(value)
          return [key, {
            ...(item.return_rate_percent == null ? {} : { return_rate_percent: fingerprintNumber(item.return_rate_percent) }),
            ...(item.return_disposition == null ? {} : { return_disposition: item.return_disposition }),
            ...(item.return_handling_cost_cny == null ? {} : { return_handling_cost_cny: fingerprintNumber(item.return_handling_cost_cny) }),
            ...(item.shipping_amount == null ? {} : { shipping_amount: fingerprintNumber(item.shipping_amount) }),
            ...(item.manual_price == null ? {} : { manual_price: fingerprintNumber(pricingRecord(item.manual_price).amount) }) }]
        }))]
    }),
  })
}

export function pricingFactsIssues(cost: unknown, dimensions: Record<string, unknown>): string[] {
  const issues: string[] = []
  if (!validNumber(cost)) issues.push('采购成本')
  for (const [key, label] of [['length_cm', '包装长'], ['width_cm', '包装宽'], ['height_cm', '包装高'], ['weight_kg', '包装重量']]) {
    if (!validNumber(dimensions[key]) || Number(dimensions[key]) <= 0) issues.push(label)
  }
  return issues
}

export function marketName(target: PricingTargetInput) {
  return ({ yandex: 'Yandex', ozon: 'Ozon', mercadolibre: 'Mercado' } as Record<string, string>)[target.platform] || target.platform
}

export function marketSite(target: PricingTargetInput) {
  if (target.platform === 'yandex' || target.platform === 'ozon') return target.site.toLowerCase() === 'global' ? '俄罗斯' : target.site
  const countries: Record<string, string> = { MLM: '墨西哥', MLB: '巴西', MLC: '智利', MCO: '哥伦比亚', MLA: '阿根廷' }
  if (target.sitesToSell?.length) return target.sitesToSell.map(site => `${countries[site.siteId] || site.siteId}${site.logisticType === 'remote' ? ' · 远程' : ''}`).join('、')
  return countries[target.site.toUpperCase()] || target.site
}

export function skuHasPricingOverrides(row: DraftSku, targetKey: string) {
  return Object.keys(pricingRecord(row.pricing_overrides?.common)).length > 0
    || Object.keys(pricingRecord(pricingRecord(row.pricing_overrides?.targets)[targetKey.toLowerCase()])).length > 0
}
